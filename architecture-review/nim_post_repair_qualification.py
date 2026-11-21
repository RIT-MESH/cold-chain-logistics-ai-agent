"""Post-repair final model qualification (2026-11-09).

Candidates: z-ai/glm-5.3-flash, nvidia/nemotron-3.5-lightning-30b-a3b.
Battery: 5 scenarios x 3 repetitions per candidate, through the REPAIRED
production interface (docstring + bounded recovery imported live from
src/agent_tools), the REAL production SQL guard, dataset-derived execution
against the real unified SQLite (read-only, 10-row cap, real view columns),
and the corrected validation checks (post-repair qualification §3):
  - complete answers are stored IN FULL (no excerpt truncation) — the
    finalized checker always runs on the entire response (resolves the
    repair-validation excerpt artifact);
  - successful tool execution is REQUIRED for a complete verdict;
  - humidity citations verified against real records;
  - five outcome classes: complete_correct / partial / safe_refusal /
    incorrect / api_or_recursion_failure.

Also runs the §4 security/retry verification: a dedicated tool-level recovery
test driving the PRODUCTION tool's rejection budget against the REAL guard
with deliberately invalid queries (no LLM, no API cost), plus per-
conversation guard/budget telemetry.

Budget: ~100-130 sequential requests across the battery (40 RPM org limit
not approached; sequential ReAct loops run at ~1-2 requests/min).
Output: docs/evidence/NVIDIA_NIM_POST_REPAIR_QUALIFICATION.json (incremental,
per-rep saves; answers secret-redacted).
"""
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "architecture-review"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
KEY = os.getenv("NVIDIA_API_KEY", "")
if not KEY:
    sys.exit("NVIDIA_API_KEY missing — aborting before any request.")
BASE_URL = "https://integrate.api.nvidia.com/v1"
OUT = ROOT / "docs/evidence/NVIDIA_NIM_POST_REPAIR_QUALIFICATION.json"
PROMPT = (ROOT / "src/prompts/system_prompt.txt").read_text(encoding="utf-8")

from src import agent_tools
from src.agent_tools import register_sql_rejection, SQLRejectionBudget
from src.agent_tools import query_telemetry_db as PRODUCTION_TOOL
from nim_validation_common import check_answer, classify_outcome, execute_query

PRODUCTION_DOC = PRODUCTION_TOOL.description

CANDIDATES = {
    "glm_5_3_flash": "z-ai/glm-5.3-flash",
    "nemotron_lightning_30b": "nvidia/nemotron-3.5-lightning-30b-a3b",
}
SCENARIOS = {
    "lookup": "Find shipment UC-003241 in the database and tell me its cargo type, allowed temperature range, and receiver verdict.",
    "excursion": "Did shipment UC-003241 have any temperature excursions outside its allowed range? Cite the readings.",
    "delay": "Analyze any delivery delays for shipment UC-003241 using database evidence.",
    "sop": "Investigate shipment UC-003241's temperature compliance and recommend mitigations, citing compliance SOP evidence.",
    "weather": "Analyze shipment UC-003241's route conditions including current weather at its last known position.",
}
REPS = 3

FX_SOP = (
    "--- COMPLIANCE SOP CONTEXT ---\n"
    "[Source: Cold_Chain_Incident_SOP_v2.md | Format: RAW]\n"
    "### 1. Temperature Control & Spoilage Prevention (Cold-Chain)\n"
    "* **Fresh Perishables:** IoT temperature must remain between 0.0C and 4.0C.\n"
    "* **Mitigation Protocol:** The dispatcher must immediately contact the driver to restart the auxiliary cooling unit. "
    "If ETA delay is greater than 1 hour, divert the vehicle to the nearest emergency cold-storage facility.\n"
    "------------------------------"
)


def redact(text):
    return str(text).replace(KEY, "[REDACTED]")


class Metrics:
    def __init__(self):
        self.turns, self.queries, self.sop_calls, self.weather_temp, self.error = [], [], 0, None, None


def make_tools(metrics):
    from langchain_core.tools import tool
    from src.agent_tools import fetch_corridor_conditions as real_weather

    def _sql_body(sql_query: str) -> str:
        result, outcome = execute_query(sql_query)
        if outcome == "blocked":
            override = register_sql_rejection(sql_query)  # PRODUCTION recovery logic
            if override:
                result = override
        metrics.queries.append({
            "sql": sql_query,
            "outcome": outcome if isinstance(outcome, str) else "ok",
            "rows": outcome.get("ok") if isinstance(outcome, dict) else None,
            "result_excerpt": redact(result[:80]),
        })
        return result
    _sql_body.__doc__ = PRODUCTION_DOC
    sql_tool = tool(_sql_body)

    def _sop_body(query: str) -> str:
        metrics.sop_calls += 1
        return FX_SOP
    _sop_body.__doc__ = ("\n    Searches enterprise Standard Operating Procedures (SOPs) indexed in the Pinecone Vector DB.\n"
                         "    Use this to retrieve regulatory thresholds, cold-chain breach mitigations, and rerouting rules.\n    ")
    sop_tool = tool(_sop_body)

    def _weather_body(latitude: float, longitude: float) -> str:
        out = real_weather.invoke({"latitude": latitude, "longitude": longitude})
        m = re.search(r"External Temp:\s*(-?\d+(?:\.\d+)?)", out)
        if m:
            metrics.weather_temp = float(m.group(1))
        return out
    _weather_body.__doc__ = ("\n    Fetches external current weather for coordinates. Does not provide traffic, cargo telemetry, or congestion.\n    ")
    weather_tool = tool(_weather_body)

    return [sql_tool, weather_tool, sop_tool]


def run_conversation(model, scenario, rep):
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
    from langchain_openai import ChatOpenAI
    from langgraph.graph import StateGraph, START
    from langgraph.graph.message import add_messages
    from langgraph.prebuilt import ToolNode, tools_condition
    from typing import Annotated, TypedDict

    metrics = Metrics()
    rec = {"model": model, "scenario": scenario, "rep": rep}

    try:
        llm = ChatOpenAI(model=model, base_url=BASE_URL, api_key=KEY,
                         temperature=0, timeout=180, max_retries=1, max_tokens=4096)
        tools = make_tools(metrics)
        bound = llm.bind_tools(tools)
    except Exception as exc:
        rec.update({"status": "bind_error", "error": redact(f"{type(exc).__name__}: {str(exc)[:200]}")})
        rec["outcome_class"] = classify_outcome(rec)
        return rec

    class AgentState(TypedDict):
        messages: Annotated[list, add_messages]

    def reasoning_node(state):
        t0 = time.perf_counter()
        response = bound.invoke(state["messages"])
        meta = getattr(response, "usage_metadata", None) or {}
        det = meta.get("output_token_details") or {}
        metrics.turns.append({"duration_s": round(time.perf_counter() - t0, 2),
                              "input_tokens": int(meta.get("input_tokens") or 0),
                              "output_tokens": int(meta.get("output_tokens") or 0),
                              "reasoning_tokens": int(det.get("reasoning") or 0)})
        return {"messages": [response]}

    gb = StateGraph(AgentState)
    gb.add_node("reasoner", reasoning_node)
    gb.add_node("tools", ToolNode(tools))
    gb.add_edge(START, "reasoner")
    gb.add_conditional_edges("reasoner", tools_condition)
    gb.add_edge("tools", "reasoner")
    agent = gb.compile()

    agent_tools._sql_retry_budget.set(SQLRejectionBudget())  # fresh per request

    t0 = time.perf_counter()
    try:
        result = agent.invoke(
            {"messages": [SystemMessage(content=PROMPT), HumanMessage(content=SCENARIOS[scenario])]},
            config={"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": 20},
        )
        total = time.perf_counter() - t0
        answer = None
        for item in reversed(result["messages"]):
            if isinstance(item, AIMessage) and item.content and not getattr(item, "tool_calls", None):
                answer = item.content if isinstance(item.content, str) else str(item.content)
                break
        answer = redact(answer or "")

        ok_queries = [q for q in metrics.queries if q["outcome"] in ("ok", "empty")]
        blocks = [q for q in metrics.queries if q["outcome"] == "blocked"]
        budget_msgs = [q for q in metrics.queries
                       if "already rejected" in q["result_excerpt"] or "budget" in q["result_excerpt"]]

        ctx = {"weather_temp": metrics.weather_temp,
               "sql_evidence": any(q["outcome"] == "ok" for q in ok_queries),
               "telemetry_sampled": any((q.get("rows") or 0) >= 10 for q in metrics.queries)}
        acc = check_answer(scenario, answer, ctx)  # on the FULL answer
        rec.update({
            "status": "ok", "total_s": round(total, 2),
            "llm_turns": len(metrics.turns),
            "turn_durations": [t["duration_s"] for t in metrics.turns],
            "input_tokens": sum(t["input_tokens"] for t in metrics.turns),
            "output_tokens": sum(t["output_tokens"] for t in metrics.turns),
            "reasoning_tokens": sum(t["reasoning_tokens"] for t in metrics.turns),
            "successful_sql_queries": len(ok_queries),
            "guard_rejections": len(blocks),
            "budget_interventions": len(budget_msgs),
            "recovered_after_block": (any(q["outcome"] == "ok" for q in metrics.queries[metrics.queries.index(blocks[0]):]) if blocks else None),
            "sop_tool_calls": metrics.sop_calls,
            "weather_temp": metrics.weather_temp,
            "queries": metrics.queries,
            "answer_chars": len(answer),
            "answer_full": answer,  # COMPLETE answer, per §1 — checker ran on this
            "acceptance": acc,
            "outcome_class": classify_outcome({"status": "ok", "acceptance": acc, "answer_excerpt": answer}),
        })
        print(f"  {scenario} r{rep}: {total:.1f}s turns={rec['llm_turns']} ok_sql={rec['successful_sql_queries']} "
              f"blocks={rec['guard_rejections']} acc={acc['pass']} outcome={rec['outcome_class']}", flush=True)
    except Exception as exc:
        total = time.perf_counter() - t0
        rec.update({"status": "error", "total_s": round(total, 2),
                    "error": redact(f"{type(exc).__name__}: {str(exc)[:200]}"),
                    "llm_turns": len(metrics.turns),
                    "queries": metrics.queries,
                    "guard_rejections": sum(1 for q in metrics.queries if q["outcome"] == "blocked")})
        rec["outcome_class"] = classify_outcome(rec)
        print(f"  {scenario} r{rep}: ERROR after {total:.1f}s: {rec['error']}", flush=True)
    return rec


def tool_level_recovery_test():
    """§4: dedicated recovery verification — deliberately invalid queries
    drive the PRODUCTION tool's rejection budget against the REAL guard.
    No LLM, no API cost. Terminal behavior recorded."""
    agent_tools._sql_retry_budget.set(SQLRejectionBudget())
    steps = []
    script = [
        ("deliberately invalid: unqualified view, no TOP",
         "SELECT cargo_type FROM VW_SHIPMENTS WHERE shipment_id = 'UC-003241'"),
        ("exact duplicate of the rejected query",
         "SELECT cargo_type FROM VW_SHIPMENTS WHERE shipment_id = 'UC-003241'"),
        ("corrected query (schema prefix + TOP) — guard must accept",
         "SELECT TOP 5 cargo_type FROM FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id = 'UC-003241'"),
        ("distinct invalid variant (LIMIT — invalid T-SQL)",
         "SELECT TOP 5 cargo_type FROM FDE_VIEWS.VW_SHIPMENTS LIMIT 5"),
        ("mutation attempt (must always be blocked)",
         "DELETE FROM FDE_VIEWS.VW_SHIPMENTS"),
        ("distinct invalids towards exhaustion #4",
         "SELECT * FROM VW_TELEMETRY"),
        ("distinct invalids towards exhaustion #5",
         "SELECT TOP 5 cargo_type FROM VW_ROUTES WHERE shipment_id = 'UC-003241'"),
        ("post-exhaustion attempt — budget instruction expected",
         "SELECT TOP 5 a FROM VW_ALERTS"),
    ]
    for label, q in script:
        out = PRODUCTION_TOOL.invoke({"sql_query": q})
        steps.append({"step": label, "sql": q,
                      "response": redact(out[:200]),
                      "is_budget_intervention": ("already rejected" in out or "budget" in out),
                      "is_guard_block": out.startswith("SECURITY BLOCK"),
                      "is_execution_path": out.startswith("COLUMNS") or out.startswith("No records") or out.startswith("Database query failed")})
    return {"note": "drives the PRODUCTION tool (real guard + real budget); execution against SQL Server unavailable locally, so guard-accepted queries return the production 'Database query failed' path",
            "steps": steps}


def save(data):
    try:
        OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False,
                                  default=lambda o: sorted(o) if isinstance(o, set) else str(o)),
                       encoding="utf-8")
    except Exception as exc:
        print(f"WARNING: save failed: {exc}", flush=True)


def main():
    data = {
        "meta": {
            "date": "2026-11-09",
            "purpose": "Post-repair final model qualification",
            "tool_docstring_source": "imported live from src.agent_tools (production)",
            "recovery_logic": "production register_sql_rejection, fresh budget per conversation",
            "sql_execution": "real guard -> real unified SQLite dataset (read-only, 10-row cap, real view columns)",
            "answer_storage": "COMPLETE answers stored (no truncation); checker runs on the full response",
            "budget_note": "~100-130 sequential requests; 40 RPM org limit not approached",
        },
        "recovery_verification": tool_level_recovery_test(),
        "candidates": {},
    }
    save(data)
    print("=== §4 tool-level recovery verification recorded ===", flush=True)

    for key, model in CANDIDATES.items():
        print(f"\n=== CANDIDATE {key} ({time.strftime('%H:%M:%S')}) ===", flush=True)
        entry = {}
        data["candidates"][key] = entry
        for scenario in SCENARIOS:
            entry[scenario] = []
            save(data)
            for rep in range(1, REPS + 1):
                entry[scenario].append(run_conversation(model, scenario, rep))
                save(data)  # per-rep: never lose a completed conversation
        print(f"--- {key} battery complete", flush=True)

    save(data)
    print("\nDONE ->", OUT, flush=True)


if __name__ == "__main__":
    main()
