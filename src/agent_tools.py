import os
import time
import urllib
import requests
from contextvars import ContextVar
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from src.audit import audit_tool_event, sql_fingerprint
from src.sql_guard import validate_query

from langchain_core.tools import tool
from langchain_openai import OpenAIEmbeddings  # noqa: F401  (kept for OPENAI embedding path via sop_embeddings)

# ==========================================
# 1. ENVIRONMENT & DYNAMIC INDEX ATTACHMENT
# ==========================================
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent  

load_dotenv(dotenv_path=project_root / ".env")

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")


def get_cached_huggingface_embeddings(model_name: str):
    """
    Loads and locks the HuggingFace model weights into the machine's global RAM.
    If called again during any subsequent script rerun, it returns instantly.
    """
    import streamlit as st
    
    # We wrap the inner call with st.cache_resource dynamically 
    @st.cache_resource(show_spinner=False)
    def _load_model(name: str):
        print(f"🧠 MEMORY SEED: Permanently caching local model [{name}] in global RAM...")
        from langchain_huggingface import HuggingFaceEmbeddings
        return HuggingFaceEmbeddings(
            model_name=name,
            model_kwargs={'device': 'cpu'}
        )
    return _load_model(model_name)

EMBEDDINGS_MODEL_SETTING = os.getenv("Embeddings_model", "LOCAL").strip().upper()

db_host = os.getenv("SQL_SERVER_HOST", "localhost")
db_port = os.getenv("SQL_SERVER_PORT", "1433")
db_user = os.getenv("SQL_AGENT_USER", "USR_FDE_RO")
db_password = os.getenv("SQL_AGENT_PASSWORD")

# Load embeddings and Pinecone lazily. SQL/chat stays available if SOP search is not configured.
# Phase 6: provider-abstracted embeddings (NIM-hosted bge-m3 recommended —
# same NVIDIA key as the chat model; blueprint 06 §2 forbids local bge-m3 on
# the app host: ~2.5-3 GB torch+weights would not fit the gateway limits).
from functools import lru_cache

@lru_cache(maxsize=1)
def _sop_store():
    """Build the SOP vector store ONCE per process, from configuration only
    (operator Phase 6 directive §9): index name, namespace and access filter
    are read from environment — a user prompt can never select the index,
    the namespace, or relax the document-level access filter."""
    if not os.getenv("PINECONE_API_KEY"):
        raise RuntimeError("PINECONE_API_KEY is not configured; SOP search is unavailable")
    from langchain_pinecone import PineconeVectorStore
    from src.sop_embeddings import make_embeddings
    embeddings, index_name, dimension = make_embeddings()
    return PineconeVectorStore(index_name=index_name, embedding=embeddings,
                               namespace=os.getenv("PINECONE_NAMESPACE", "sops"),
                               text_key="page_content")

# Fixed retrieval configuration (separation of configuration from user input).
SOP_ACCESS_FILTER = {"access_classification": {"$in": [
    c.strip() for c in os.getenv("SOP_ACCESS_CLASSIFICATIONS", "internal-operations,internal-demo").split(",")
    if c.strip()]}}
SOP_MIN_SCORE = float(os.getenv("SOP_MIN_SCORE", "0.35"))
SOP_TOP_K = int(os.getenv("SOP_TOP_K", "3"))
SOP_CHUNK_DISPLAY_LIMIT = int(os.getenv("SOP_CHUNK_DISPLAY_LIMIT", "700"))

# ==========================================
# 2. BOUNDED SQL REJECTION RECOVERY + SUCCESS-LOOP METERING
#    (2026-11-09 tool-interface repair; 2026-11-14 F-B extension, blueprint P-8)
# ==========================================
# Repeated identical guard rejections previously burned the LangGraph
# recursion budget (final-validation gate: 19/19 lightning conversations and
# 2/15 glm conversations died at the recursion limit). This meters rejections
# per agent request WITHOUT rewriting SQL and WITHOUT relaxing any sql_guard
# restriction — exhausted budgets produce a clear instruction to stop
# querying and answer from evidence already retrieved (or state that the
# evidence could not be retrieved).
#
# F-B extension (2026-11-14, post-repair qualification finding F-B): the
# rejection budget did NOT meter repeats of SUCCESSFUL queries, so a model
# could loop on identical already-executed queries (lightning delay r2:
# 9 x ok, 0 blocks) until the recursion limit. Identical queries are now
# metered REGARDLESS of guard outcome: a repeat of an already-executed query
# returns a do-not-repeat instruction instead of executing again.

class SQLRejectionBudget:
    """Meters SQL queries within ONE agent request.

    - The exact same REJECTED query may be attempted at most MAX_IDENTICAL
      times; a repeat gets a do-not-repeat instruction.
    - At most MAX_DISTINCT distinct rejected queries per request; further
      rejections get a stop-querying instruction.
    - F-B: the exact same query may be EXECUTED at most once per request;
      an identical repeat (regardless of previous guard outcome) gets a
      do-not-repeat instruction instead of a second execution. Distinct
      valid queries are unlimited and never blocked.
    - Rejected SQL is never rewritten and never executed.
    """
    MAX_IDENTICAL = 2
    MAX_DISTINCT = 5

    def __init__(self):
        self._identical = {}
        self._distinct = 0
        self._executed = {}

    def register_rejection(self, sql_query: str) -> str | None:
        """Record a rejected query. Returns None (return the original guard
        error), or an override instruction string when the budget says the
        retry loop must stop."""
        key = " ".join((sql_query or "").split()).lower()
        self._identical[key] = self._identical.get(key, 0) + 1
        self._distinct += 1
        if self._identical[key] >= self.MAX_IDENTICAL:
            return (
                "SECURITY BLOCK: this exact query was already rejected and will be rejected again. "
                "Do not repeat it. Correct the syntax per the tool description (FDE_VIEWS. schema "
                "prefix, TOP 1-100, explicit columns, no LIMIT), or answer from the evidence already retrieved."
            )
        if self._distinct >= self.MAX_DISTINCT:
            return (
                "SECURITY BLOCK: SQL correction budget for this request is exhausted. Stop issuing "
                "queries and answer only from the evidence already retrieved; if required evidence "
                "is missing, state that it could not be retrieved."
            )
        return None

    def register_query(self, sql_query: str) -> str | None:
        """Record one guard-PASSING query about to execute (F-B). Returns None
        (proceed to execution), or an override instruction when the exact same
        query was already executed in this request — a repeated successful
        query is a loop, not new evidence."""
        key = " ".join((sql_query or "").split()).lower()
        self._executed[key] = self._executed.get(key, 0) + 1
        if self._executed[key] >= 2:
            return (
                "This exact query was already executed in this request and its "
                "results are already in the conversation. Do not repeat it. "
                "Query a different view/columns if you need NEW evidence, or "
                "answer from the evidence already retrieved."
            )
        return None


# One budget per agent request. The FastAPI gateway serves each chat request
# in a copied async context (run_in_threadpool), so each request starts with a
# fresh budget; explicit set() is used by tests and validation harnesses.
_sql_retry_budget: ContextVar = ContextVar("fde_sql_retry_budget", default=None)


def register_sql_rejection(sql_query: str) -> str | None:
    """Meter one guard rejection within the current request context. Used by
    the tool below and by validation harnesses that exercise the production
    recovery logic."""
    budget = _sql_retry_budget.get()
    if budget is None:
        budget = SQLRejectionBudget()
        _sql_retry_budget.set(budget)
    return budget.register_rejection(sql_query)


def register_sql_query(sql_query: str) -> str | None:
    """Meter one guard-passing query within the current request context (F-B).
    Returns an override instruction when the exact query already executed in
    this request; used by the tool below and by validation harnesses."""
    budget = _sql_retry_budget.get()
    if budget is None:
        budget = SQLRejectionBudget()
        _sql_retry_budget.set(budget)
    return budget.register_query(sql_query)


# ==========================================
# 3. CORE FDE AGENT TOOLS
# ==========================================

@tool
def query_telemetry_db(sql_query: str) -> str:
    """
    Query the unified 2021-2026 SYNTHETIC TEST database (Microsoft SQL Server, T-SQL) through READ-ONLY approved views. Every query is validated by a security guard; rejected queries return "SECURITY BLOCK" and are never executed.

    SYNTAX RULES (T-SQL, strictly enforced):
    1. Schema prefix MANDATORY: exactly one approved view per query, always written FDE_VIEWS.<view>. Unqualified view names are rejected.
    2. TOP MANDATORY for non-aggregate SELECTs: use TOP 1 to TOP 100 (e.g. SELECT TOP 10 ...). LIMIT is NOT valid T-SQL and is always rejected.
    3. Explicit column lists only: SELECT * is rejected. No joins, CTEs, subqueries, UNION, or data modifications (read-only access).
    4. Aggregate-only queries (e.g. SELECT COUNT(*)) may omit TOP.

    APPROVED VIEWS AND COLUMNS:
    - FDE_VIEWS.VW_SHIPMENTS: shipment_id, cargo_type, vehicle_id, departed_at, expected_arrival_at, arrived_at, receiver_verdict, temperature_min_c, temperature_max_c, origin_lat, origin_lon, destination_lat, destination_lon, pallets, source_category, synthetic_test_data
    - FDE_VIEWS.VW_TELEMETRY: shipment_id, sensor_id, vehicle_id, recorded_at, latitude, longitude, speed_kmh, temperature_c, humidity_pct, traffic_congestion, fuel_consumption_l_per_100km, fuel_level_pct, risk_level, synthetic_test_data
    - FDE_VIEWS.VW_ALERTS: shipment_id, alert_at, alert_type, severity, message
    - FDE_VIEWS.VW_CUSTODY: shipment_id, handoff_no, received_by, handed_at, seal_intact
    - FDE_VIEWS.VW_SENSORS: sensor_id, shipment_id, serial_no, position, installed_at, device_type
    - FDE_VIEWS.VW_VEHICLES: vehicle_id, vehicle_type, capacity_pallets, fuel_type, fleet_key
    - FDE_VIEWS.VW_ROUTES: shipment_id, telemetry_id, route_sequence, recorded_at, latitude, longitude, traffic_congestion, weather_severity
    - FDE_VIEWS.VW_TELEMETRY_EVENTS (ingested event stream — SYNTHETIC unless provenance says real): EventId, ShipmentId, ObservedAtUtc, TemperatureC, Provenance, IngestedAtUtc
    - FDE_VIEWS.VW_TELEMETRY_INCIDENTS (temperature-excursion incidents): IncidentId, EventId, ShipmentId, ObservedAtUtc, TemperatureC, ThresholdC, Provenance, Status, CreatedAtUtc
    - FDE_VIEWS.VW_INCIDENT_NOTIFICATIONS (incidents + derived notification state): IncidentId, EventId, ShipmentId, ObservedAtUtc, TemperatureC, ThresholdC, Provenance, Status, CreatedAtUtc, AttemptCount, AckedCount, FirstAttemptAtUtc, LastAttemptAtUtc, NotificationState
    - FDE_VIEWS.VW_EVENT_LINK_VALIDATION (ingested events referencing an UNKNOWN shipment — should normally be empty): EventId, ShipmentId, ObservedAtUtc, Provenance, IngestedAtUtc

    INCIDENT INVESTIGATION NOTES:
    - Incident severity is NOT stored as a column: derive it from TemperatureC minus ThresholdC (excursion magnitude). State that severity is derived.
    - NotificationState='sns_acked' means the SNS TOPIC accepted the notification message — NOT that email was delivered. Pending means no acknowledged publish attempt yet.
    - Provenance='synthetic' rows are separately ingested replay/demo events, NOT historical dataset measurements. Always label them.
    - Correlate an incident with its shipment/custody/telemetry by ShipmentId/EventId across separate single-view queries.

    Correlate across views by shipment_id only; query each view separately.
    Never repeat an identical query within one request: its previous result is already in the conversation, and an identical repeat returns a do-not-repeat instruction instead of executing again. Query different views or columns when you need NEW evidence.

    VALID EXAMPLES (follow this exact form, substituting your shipment_id):
    - SELECT TOP 10 shipment_id, cargo_type, temperature_min_c, temperature_max_c, receiver_verdict FROM FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id = '<shipment_id>'
    - SELECT TOP 100 recorded_at, temperature_c, humidity_pct, risk_level FROM FDE_VIEWS.VW_TELEMETRY WHERE shipment_id = '<shipment_id>' ORDER BY recorded_at
    - SELECT TOP 5 alert_at, alert_type, severity, message FROM FDE_VIEWS.VW_ALERTS WHERE shipment_id = '<shipment_id>' ORDER BY alert_at
    - SELECT TOP 20 IncidentId, EventId, ShipmentId, ObservedAtUtc, TemperatureC, ThresholdC, Provenance, Status FROM FDE_VIEWS.VW_TELEMETRY_INCIDENTS WHERE ShipmentId = '<shipment_id>' ORDER BY ObservedAtUtc
    - SELECT TOP 20 IncidentId, NotificationState, AttemptCount, AckedCount, LastAttemptAtUtc FROM FDE_VIEWS.VW_INCIDENT_NOTIFICATIONS WHERE ShipmentId = '<shipment_id>'
    - SELECT TOP 50 EventId, ObservedAtUtc, TemperatureC, Provenance, IngestedAtUtc FROM FDE_VIEWS.VW_TELEMETRY_EVENTS WHERE ShipmentId = '<shipment_id>' ORDER BY ObservedAtUtc
    - SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_EVENT_LINK_VALIDATION
    """
    connection_string = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={db_host},{db_port};"
            f"DATABASE={os.getenv('SQL_DATABASE', 'ColdChainLogistics')};"
            f"UID={db_user};"
            f"PWD={db_password};"
            f"Encrypt={os.getenv('SQL_ENCRYPT', 'yes')};"
            f"TrustServerCertificate={os.getenv('SQL_TRUST_SERVER_CERTIFICATE', 'no')};"
        )

    started = time.monotonic()

    def _duration_ms():
        return int((time.monotonic() - started) * 1000)

    try:
        # Guard FIRST: validate the query before any database resources are
        # created or drivers are loaded (Phase 0 defect fix — previously
        # create_engine ran before validate_query, so a disallowed query
        # could crash the tool before the SECURITY BLOCK was returned).
        try:
            validate_query(sql_query)
        except ValueError as exc:
            # Bounded recovery (2026-11-09): meter the rejection so repeated
            # identical/numerous failed attempts terminate with a clear
            # instruction instead of burning the graph recursion budget.
            override = register_sql_rejection(sql_query)
            audit_tool_event("query_telemetry_db", "guard_rejected",
                             guard_decision=str(exc)[:120],
                             sql_fingerprint=sql_fingerprint(sql_query),
                             duration_ms=_duration_ms())
            return override or f"SECURITY BLOCK: {exc}"

        # F-B success-loop metering (2026-11-14): an identical guard-passing
        # query repeated within the same request returns a do-not-repeat
        # instruction instead of executing again — repeated identical
        # successes previously looped to the graph recursion limit.
        override = register_sql_query(sql_query)
        if override:
            return override

        views = [t.lower() for t in _allowed_views_in(sql_query)]

        params = urllib.parse.quote_plus(connection_string)
        # Execution timeout (Phase 5 §4): 10 s connect/execution cap on the
        # read-only agent engine — a slow query can no longer hold a chat
        # request indefinitely (matches the telemetry-store engine).
        engine = create_engine(f"mssql+pyodbc:///?odbc_connect={params}",
                                connect_args={"timeout": 10})

        with engine.connect() as conn:
            cursor = conn.execute(text(sql_query))
            columns = list(cursor.keys())
            rows = cursor.fetchmany(10)

            audit_tool_event("query_telemetry_db", "executed",
                             duration_ms=_duration_ms(), views=views, rows=len(rows))

            if not rows:
                return "No records matched the query criteria."

            formatted_output = f"COLUMNS: {', '.join(columns)}\n"
            for row in rows:
                formatted_output += str(tuple(row)) + "\n"

            return formatted_output
    except Exception as exc:
        audit_tool_event("query_telemetry_db", "error",
                         error_class=type(exc).__name__,
                         duration_ms=_duration_ms())
        return "Database query failed; inspect service logs."


def _allowed_views_in(sql_query: str) -> list:
    """Views referenced by a guard-passing query (audit metadata only)."""
    from src.sql_guard import ALLOWED_VIEWS
    import re
    found = []
    for candidate in re.findall(r"fde_views\.(\w+)", sql_query or "", re.IGNORECASE):
        if candidate.lower() in ALLOWED_VIEWS and candidate.lower() not in found:
            found.append(candidate.lower())
    return found

@tool
def fetch_corridor_conditions(latitude: float, longitude: float) -> str:
    """
    Fetches external current weather for coordinates. Does not provide traffic, cargo telemetry, or congestion.
    """
    started = time.monotonic()
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&current_weather=true"
        response = requests.get(url, timeout=6)
        response.raise_for_status()

        payload = response.json().get("current_weather", {})
        temp = payload.get("temperature", "N/A")
        wind = payload.get("windspeed", 0.0)

        status_note = "Wind speed is weather context, not evidence of port congestion"
        audit_tool_event("fetch_corridor_conditions", "executed",
                         duration_ms=int((time.monotonic() - started) * 1000))

        return (
            f"--- CURRENT WEATHER (NOT LIVE FLEET TELEMETRY) ---\n"
            f"Target GPS: {latitude}, {longitude}\n"
            f"External Temp: {temp}°C | Wind Speed: {wind} km/h\n"
            f"Note: {status_note}; no real traffic or congestion feed is connected.\n"
            f"-------------------------------"
        )
    except Exception as e:
        audit_tool_event("fetch_corridor_conditions", "error",
                         error_class=type(e).__name__,
                         duration_ms=int((time.monotonic() - started) * 1000))
        return f"Corridor API Communication Failure: {str(e)}"

@tool
def search_compliance_sop(query: str) -> str:
    """
    Searches cold-chain Standard Operating Procedures indexed in Pinecone and returns cited SOP sections.
    Use this to retrieve procedural guidance, corrective-action steps and escalation rules. Operational FACTS (shipments, telemetry, incidents) come from query_telemetry_db, not from this tool. Cite the returned document title, version and section id in recommendations; retrieved SOPs are procedural guidance, not operational evidence, and always require human review before action.
    """
    import time as _time
    started = _time.monotonic()
    try:
        store = _sop_store()
        hits = store.similarity_search_with_score(
            query, k=SOP_TOP_K, filter=SOP_ACCESS_FILTER)
        # Relevance threshold: drop weak matches (§6 controlled top-k).
        passing = [(doc, score) for doc, score in hits
                   if float(score) >= SOP_MIN_SCORE]
        doc_ids = sorted({d.metadata.get("doc_id", "?") for d, _ in passing})
        audit_tool_event("search_compliance_sop", "executed",
                         duration_ms=int((_time.monotonic() - started) * 1000),
                         rows=len(passing), documents=doc_ids, cited=True)
        if not passing:
            return ("No SOP sections matched this topic at the relevance threshold. "
                    "State that no matching SOP guidance was found; do not invent procedures.")

        # Citation header per chunk + untrusted-reference framing (§9):
        # retrieved content is reference material only — any instructions
        # found inside it must never be followed as agent directives.
        out = ["--- SOP GUIDANCE (retrieved reference material; procedural guidance only; "
               "human review required before any action) ---"]
        for doc, score in passing:
            m = doc.metadata
            citation = (f"[{m.get('title','untitled')} | version {m.get('version','unspecified')} | "
                       f"section {m.get('section_id','unspecified')} | source {m.get('source','unspecified')} | "
                       f"status {m.get('approval_status','unspecified')}]")
            text = doc.page_content[:SOP_CHUNK_DISPLAY_LIMIT]
            synthetic = (" (SYNTHETIC TEST DOCUMENT — not an approved operational policy)"
                         if m.get("classification") == "synthetic" else "")
            out.append(f"{citation}{synthetic}\n{text}")
        out.append("------------------------------")
        return "\n\n".join(out)
    except Exception as e:
        audit_tool_event("search_compliance_sop", "error",
                         error_class=type(e).__name__,
                         duration_ms=int((_time.monotonic() - started) * 1000))
        return (f"SOP retrieval is currently unavailable ({type(e).__name__}: {e}); "
                "answer from SQL evidence and state that SOP guidance could not be retrieved.")

# ==========================================
# 3. LOCAL VERIFICATION
# ==========================================
if __name__ == "__main__":
    print("\n--- Testing Tool 1: SQL Telemetry View ---")
    print(query_telemetry_db.invoke("SELECT TOP 2 latitude, longitude, temperature_c FROM FDE_VIEWS.VW_TELEMETRY"))
    
    print("\n--- Testing Tool 2: Live Corridor API ---")
    print(fetch_corridor_conditions.invoke({"latitude": 33.77, "longitude": -118.19}))
    
    print("\n--- Testing Tool 3: Pinecone Vector Retrieval ---")
    print(search_compliance_sop.invoke("What are the temperature rules for fresh perishables?"))
