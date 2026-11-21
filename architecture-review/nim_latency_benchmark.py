"""NVIDIA NIM latency benchmark — Phase 0 follow-up to R6-1 (2026-11-08).

Measures the latency of the cold-chain agent's REAL runtime configuration
(deepseek-ai/deepseek-v4.1-flash at https://integrate.api.nvidia.com/v1)
on representative logistics tasks, then runs small-budget optimization
probes (streaming, token caps, reasoning params, prompt size, alternative
models).

HONESTY LABELS (read before interpreting results):
- LLM turns, token usage, reasoning tokens and loop structure are REAL live
  measurements against the NVIDIA NIM endpoint with the operator's key.
- SQL tool execution is SIMULATED with fixture responses built from REAL
  rows of the synthetic dataset (shipment UC-003241) — no SQL Server exists
  in this environment (pyodbc not installed). Fixture tool latency (~0 ms)
  is NOT representative of real SQL Server latency; Phase 2 will measure it.
- SOP (Pinecone) tool execution is SIMULATED with a real excerpt of
  data/policy/Cold_Chain_Incident_SOP_v2.md — no Pinecone key is configured.
- The weather tool is REAL (Open-Meteo, live external call).
- LibreChat and the FastAPI gateway are NOT exercised here; their behavior
  is assessed from code reading only and labeled as such.

BUDGET: ~26-30 small sequential requests total (org limit 40 RPM is not at
risk; sequential ReAct turns run at ~1 RPM). No application source code is
modified by this benchmark; it only imports it.

Usage:  python architecture-review/nim_latency_benchmark.py
Output: docs/evidence/NVIDIA_NIM_LATENCY_RESULTS.json (incremental)
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

KEY = os.getenv("NVIDIA_API_KEY", "")
if not KEY:
    sys.exit("NVIDIA_API_KEY is not set in the private .env — aborting before any request.")
BASE_URL = "https://integrate.api.nvidia.com/v1"
MODEL = os.getenv("NVIDIA_MODEL", "deepseek-ai/deepseek-v4.1-flash")
OUT = ROOT / "docs/evidence/NVIDIA_NIM_LATENCY_RESULTS.json"
PROMPT = (ROOT / "src/prompts/system_prompt.txt").read_text(encoding="utf-8")

RESULTS = {
    "meta": {
        "date": "2026-11-08",
        "model": MODEL,
        "base_url": BASE_URL,
        "harness": "architecture-review/nim_latency_benchmark.py",
        "simulated_components": [
            "SQL tool (fixture rows from real synthetic dataset, ~0 ms; real SQL Server latency NOT measured)",
            "SOP/Pinecone tool (fixture from real SOP file; retrieval latency NOT measured)",
        ],
        "real_components": [
            "All LLM turns, token/reasoning usage, loop structure (live NIM endpoint)",
            "Weather tool (live Open-Meteo call)",
        ],
        "not_exercised": ["FastAPI gateway", "LibreChat", "SQL Server", "Pinecone"],
        "budget_note": "~26-30 small sequential requests; org limit 40 RPM.",
    },
    "baseline": {},
    "experiments": {},
}


def redact(text):
    return str(text).replace(KEY, "[REDACTED]")


def save():
    OUT.write_text(json.dumps(RESULTS, indent=2, ensure_ascii=False), encoding="utf-8")


def usage_of(msg):
    """Extract (input, output, reasoning) token counts from a LangChain AIMessage.
    Note: LangChain uses output_token_details.reasoning (verified live
    2026-11-08) — not output_tokens_details.reasoning_tokens."""
    meta = getattr(msg, "usage_metadata", None) or {}
    details = meta.get("output_token_details") or {}
    return {
        "input_tokens": int(meta.get("input_tokens") or 0),
        "output_tokens": int(meta.get("output_tokens") or 0),
        "reasoning_tokens": int(details.get("reasoning") or 0),
    }


# ---------------------------------------------------------------- fixtures --
# Fixture tool responses are built from REAL rows of the synthetic dataset
# (UC-003241) plus one excursion reading consistent with the REAL alert row
# (alert 7514: "Observed 1.2 C outside configured 2 to 8 C" => 9.2 C).

FX_SHIPMENTS = (
    "COLUMNS: shipment_id, cargo_type, vehicle_id, departed_at, receiver_verdict, temperature_min_c, temperature_max_c\n"
    "('UC-003241', 'insulin pens', 'VEH-0111', '2026-07-06 17:15:09', 'accepted', 2.0, 8.0)\n"
)
FX_TELEMETRY = (
    "COLUMNS: shipment_id, recorded_at, latitude, longitude, temperature_c, humidity_pct, traffic_congestion, fuel_level_pct, risk_level\n"
    "('UC-003241', '2026-07-06 17:15:09', 35.73731, 136.78864, 6.3, 79.0, 2.68, 94.54, 'low')\n"
    "('UC-003241', '2026-07-06 18:45:09', 35.740912, 136.829311, 6.1, 78.0, 3.11, 91.2, 'low')\n"
    "('UC-003241', '2026-07-06 20:15:09', 35.74502, 136.856921, 6.6, 77.0, 4.02, 88.7, 'low')\n"
    "('UC-003241', '2026-07-06 21:45:09', 35.74811, 136.8798, 7.0, 76.0, 3.44, 86.1, 'low')\n"
    "('UC-003241', '2026-07-06 23:45:09', 35.75204, 136.9201, 9.2, 72.0, 5.71, 81.3, 'medium')\n"
    "('UC-003241', '2026-07-07 00:15:09', 35.752811, 136.934522, 8.9, 73.0, 4.89, 79.9, 'medium')\n"
    "('UC-003241', '2026-07-07 01:15:09', 35.753156, 136.994995, 7.1, 75.0, 2.15, 76.2, 'low')\n"
)
FX_ALERTS = (
    "COLUMNS: shipment_id, alert_at, alert_type, severity, message\n"
    "('UC-003241', '2026-07-06 23:45:09', 'temperature_excursion', 'medium', 'Observed 1.2 C outside configured 2 to 8 C')\n"
)
FX_CUSTODY = (
    "COLUMNS: shipment_id, handoff_no, received_by, handed_at, seal_intact\n"
    "('UC-003241', 1, 'line-haul carrier', '2026-07-06 17:28:40', 1)\n"
)
FX_SOP = (
    "--- COMPLIANCE SOP CONTEXT ---\n"
    "[Source: Cold_Chain_Incident_SOP_v2.md | Format: RAW]\n"
    "### 1. Temperature Control & Spoilage Prevention (Cold-Chain)\n"
    "All refrigerated fleets must maintain strict IoT temperature compliance to prevent cargo spoilage.\n"
    "* **Fresh Perishables:** IoT temperature must remain between 0.0C and 4.0C.\n"
    "* **Critical Breach:** If `IOT_TEMP_VAL_C` exceeds **4.0C**, an immediate cold-chain breach is declared.\n"
    "* **Mitigation Protocol:** The dispatcher must immediately contact the driver to restart the auxiliary cooling unit. "
    "If ETA delay is greater than 1 hour, divert the vehicle to the nearest emergency cold-storage facility.\n"
    "------------------------------"
)


# ------------------------------------------------------------- instrumentation
class Metrics:
    def __init__(self):
        self.turns = []          # per model turn
        self.tool_calls = []     # per tool execution
        self.t_start = None
        self.error = None

    def llm_turn(self, seconds, usage, had_tool_calls, n_tool_calls):
        self.turns.append({
            "duration_s": round(seconds, 2),
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "reasoning_tokens": usage["reasoning_tokens"],
            "emitted_tool_calls": n_tool_calls,
        })

    def tool_exec(self, name, seconds, simulated, result_chars):
        self.tool_calls.append({
            "tool": name,
            "duration_s": round(seconds, 3),
            "simulated": simulated,
            "result_chars": result_chars,
        })

    def summary(self, total_s):
        return {
            "total_s": round(total_s, 2),
            "llm_turns": len(self.turns),
            "tool_calls": len(self.tool_calls),
            "llm_wait_s": round(sum(t["duration_s"] for t in self.turns), 2),
            "tool_exec_s": round(sum(t["duration_s"] for t in self.tool_calls), 3),
            "time_to_first_turn_s": self.turns[0]["duration_s"] if self.turns else None,
            "input_tokens": sum(t["input_tokens"] for t in self.turns),
            "output_tokens": sum(t["output_tokens"] for t in self.turns),
            "reasoning_tokens": sum(t["reasoning_tokens"] for t in self.turns),
            "turn_breakdown": self.turns,
            "tool_breakdown": self.tool_calls,
            "error": self.error,
        }


def build_agent(max_output_tokens=None, system_prompt=None, tools=None):
    """Mirror of the application graph (src/orchestrator.py) with instrumentation."""
    from langchain_core.messages import SystemMessage
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import add_messages
    from langgraph.prebuilt import ToolNode, tools_condition
    from typing import Annotated, TypedDict

    from src.orchestrator import create_llm

    metrics = Metrics()
    if tools is None:
        tools = make_tools(metrics)
    if max_output_tokens is not None:
        os.environ["NVIDIA_MAX_OUTPUT_TOKENS"] = str(max_output_tokens)
    llm = create_llm()
    bound = llm.bind_tools(tools)

    class AgentState(TypedDict):
        messages: Annotated[list, add_messages]

    def reasoning_node(state):
        t0 = time.perf_counter()
        response = bound.invoke(state["messages"])
        metrics.llm_turn(
            time.perf_counter() - t0,
            usage_of(response),
            bool(getattr(response, "tool_calls", None)),
            len(getattr(response, "tool_calls", None) or []),
        )
        return {"messages": [response]}

    gb = StateGraph(AgentState)
    gb.add_node("reasoner", reasoning_node)
    gb.add_node("tools", ToolNode(tools))
    gb.add_edge(START, "reasoner")
    gb.add_conditional_edges("reasoner", tools_condition)
    gb.add_edge("tools", "reasoner")
    return gb.compile(), metrics


# ------------------------------------------------------------------ tools --
def make_tools(metrics):
    """Fixture SQL + fixture SOP + REAL weather, all instrumented.
    Docstrings are copied verbatim from src/agent_tools.py so the LLM sees
    identical tool schemas as in production."""
    from langchain_core.tools import tool
    from src.agent_tools import fetch_corridor_conditions as real_weather

    @tool
    def query_telemetry_db(sql_query: str) -> str:
        """
        Query the unified 2021-2026 SYNTHETIC TEST database via read-only SQL views.
        Allowed FDE_VIEWS: VW_SHIPMENTS (shipment_id,cargo_type,vehicle_id,departed_at,receiver_verdict,temperature_min_c,temperature_max_c),
        VW_TELEMETRY (shipment_id,sensor_id,recorded_at,latitude,longitude,speed_kmh,temperature_c,humidity_pct,traffic_congestion,fuel_consumption_l_per_100km,fuel_level_pct,risk_level),
        VW_CUSTODY (shipment_id,handoff_no,received_by,handed_at,seal_intact), VW_ALERTS (shipment_id,alert_at,alert_type,severity,message),
        VW_SENSORS, VW_VEHICLES, VW_ROUTES. SELECT one approved view per query; use TOP 1–100 for non-aggregate queries, explicit columns and WHERE shipment_id when needed.
        No joins, CTEs, SELECT *, subqueries, mutations or claims of actual live truck operations.
        """
        t0 = time.perf_counter()
        q = sql_query.upper()
        if not q.strip().startswith("SELECT"):
            out = "SECURITY BLOCK: only SELECT queries on approved FDE_VIEWS are allowed"
        elif "VW_SHIPMENTS" in q:
            out = FX_SHIPMENTS
        elif "VW_TELEMETRY" in q:
            out = FX_TELEMETRY
        elif "VW_ALERTS" in q:
            out = FX_ALERTS
        elif "VW_CUSTODY" in q:
            out = FX_CUSTODY
        else:
            out = "No records matched the query criteria."
        metrics.tool_exec("query_telemetry_db", time.perf_counter() - t0, True, len(out))
        return out

    @tool
    def search_compliance_sop(query: str) -> str:
        """
        Searches enterprise Standard Operating Procedures (SOPs) indexed in the Pinecone Vector DB.
        Use this to retrieve regulatory thresholds, cold-chain breach mitigations, and rerouting rules.
        """
        t0 = time.perf_counter()
        out = FX_SOP
        metrics.tool_exec("search_compliance_sop", time.perf_counter() - t0, True, len(out))
        return out

    @tool
    def fetch_corridor_conditions(latitude: float, longitude: float) -> str:
        """
        Fetches external current weather for coordinates. Does not provide traffic, cargo telemetry, or congestion.
        """
        t0 = time.perf_counter()
        try:
            out = real_weather.invoke({"latitude": latitude, "longitude": longitude})
            simulated = False
        except Exception as exc:
            out = f"Corridor API Communication Failure: {exc}"
            simulated = False
        metrics.tool_exec("fetch_corridor_conditions", time.perf_counter() - t0, simulated, len(out))
        return out

    return [query_telemetry_db, fetch_corridor_conditions, search_compliance_sop]


# --------------------------------------------------------------- baseline --
BASELINE_CASES = [
    ("T1_simple_greeting", "Hello! What can you help me investigate in this cold-chain environment?"),
    ("T2_shipment_lookup", "Find shipment UC-003241 in the database and tell me its cargo type, allowed temperature range, and receiver verdict."),
    ("T3_temperature_excursions", "Did shipment UC-003241 have any temperature excursions above its allowed range? Cite the readings."),
    ("T4_delivery_delays_sql", "Explain any delivery delays for shipment UC-003241 using database evidence."),
    ("T5_risk_sql_sop", "Investigate the risk of shipment UC-003241 and recommend mitigations, citing compliance SOP evidence."),
    ("T6_conditions_sql_weather", "Analyze the conditions around shipment UC-003241's route, including current weather at its last known position."),
]


def run_case(name, question):
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
    print(f"\n=== {name}: starting ({time.strftime('%H:%M:%S')}) ===", flush=True)
    agent, metrics = build_agent()
    t0 = time.perf_counter()
    try:
        result = agent.invoke(
            {"messages": [SystemMessage(content=PROMPT), HumanMessage(content=question)]},
            config={"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": 20},
        )
        total = time.perf_counter() - t0
        answer = None
        for item in reversed(result["messages"]):
            if isinstance(item, AIMessage) and item.content and not getattr(item, "tool_calls", None):
                answer = item.content if isinstance(item.content, str) else str(item.content)
                break
        summary = metrics.summary(total)
        summary["final_answer_chars"] = len(answer or "")
        summary["final_answer_excerpt"] = redact((answer or "")[:400])
        summary["question"] = question
        print(f"--- {name}: {total:.1f}s | turns={summary['llm_turns']} tools={summary['tool_calls']} "
              f"reasoning_tok={summary['reasoning_tokens']}", flush=True)
    except Exception as exc:
        total = time.perf_counter() - t0
        metrics.error = redact(f"{type(exc).__name__}: {exc}"[:300])
        summary = metrics.summary(total)
        summary["question"] = question
        print(f"--- {name}: ERROR after {total:.1f}s: {metrics.error}", flush=True)
    RESULTS["baseline"][name] = summary
    save()


# ------------------------------------------------------------ experiments --
def direct_client(timeout=180):
    from openai import OpenAI
    return OpenAI(base_url=BASE_URL, api_key=KEY, timeout=timeout, max_retries=1)


def single_turn(client, system, user, max_tokens, extra_body=None, stream=False):
    """One plain chat completion (no tools, no graph). Returns measurement dict."""
    t0 = time.perf_counter()
    rec = {"max_tokens": max_tokens, "extra_body_keys": list(extra_body.keys()) if extra_body else None}
    try:
        if stream:
            first_content_s = None
            chunks = 0
            leak_keys = set()
            content_parts = []
            finish = None
            usage = None
            stream_kwargs = dict(
                model=MODEL,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=max_tokens, stream=True, extra_body=extra_body or {},
            )
            try:
                stream_iter = client.chat.completions.create(stream_options={"include_usage": True}, **stream_kwargs)
                usage_included = True
            except Exception:
                stream_iter = client.chat.completions.create(**stream_kwargs)
                usage_included = False
            for event in stream_iter:
                if event.usage:
                    usage = event.usage
                if not event.choices:
                    continue
                delta = event.choices[0].delta
                for k in ("reasoning_content", "reasoning"):
                    if getattr(delta, k, None):
                        leak_keys.add(k)
                if delta and delta.content:
                    if first_content_s is None:
                        first_content_s = time.perf_counter() - t0
                    content_parts.append(delta.content)
                    chunks += 1
                if event.choices[0].finish_reason:
                    finish = event.choices[0].finish_reason
            total = time.perf_counter() - t0
            rec.update({
                "status": "ok", "total_s": round(total, 2),
                "time_to_first_content_s": round(first_content_s, 2) if first_content_s else None,
                "content_chunks": chunks, "finish_reason": finish,
                "reasoning_leak_keys": sorted(leak_keys),
                "usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens} if usage else None,
                "content_chars": len("".join(content_parts)),
            })
        else:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=max_tokens, extra_body=extra_body or {},
            )
            total = time.perf_counter() - t0
            u = resp.usage
            rec.update({
                "status": "ok", "total_s": round(total, 2),
                "finish_reason": resp.choices[0].finish_reason,
                "content_chars": len(resp.choices[0].message.content or ""),
                "prompt_tokens": u.prompt_tokens if u else None,
                "completion_tokens": u.completion_tokens if u else None,
            })
            # reasoning tokens: OpenAI-compatible detail if present
            det = getattr(u, "completion_tokens_details", None) if u else None
            rec["reasoning_tokens"] = getattr(det, "reasoning_tokens", None) if det else None
            rec["content_excerpt"] = redact((resp.choices[0].message.content or "")[:200])
    except Exception as exc:
        total = time.perf_counter() - t0
        rec.update({"status": "error", "total_s": round(total, 2),
                    "error": redact(f"{type(exc).__name__}: {exc}"[:300])})
    return rec


DETAIL_QUESTION = ("Explain the cold-chain compliance status of shipment UC-003241 in detail: "
                   "its allowed range, the temperature pattern, the excursion alert, and recommended mitigations.")


def run_experiments():
    from openai import OpenAI
    exp = RESULTS["experiments"]

    print("\n### EXPERIMENT A: streaming ###", flush=True)
    client = direct_client()
    exp["streaming"] = single_turn(client, PROMPT, DETAIL_QUESTION, 4096, stream=True)
    print("streaming:", json.dumps(exp["streaming"], default=str)[:300], flush=True)
    save()

    print("\n### EXPERIMENT B: token caps (single turn, no tools) ###", flush=True)
    exp["token_caps"] = []
    for cap in (1024, 2048, 4096):
        client = direct_client()
        rec = single_turn(client, PROMPT, DETAIL_QUESTION, cap)
        exp["token_caps"].append(rec)
        print(f"cap={cap}:", rec.get("status"), rec.get("total_s"), rec.get("finish_reason"), flush=True)
    save()

    print("\n### EXPERIMENT C: reasoning parameter probes ###", flush=True)
    short_q = "In one sentence, what is cold-chain logistics?"
    exp["reasoning_params"] = []
    for label, body in (
        ("chat_template_kwargs.thinking=false", {"chat_template_kwargs": {"thinking": False}}),
        ("reasoning_effort=low", {"reasoning_effort": "low"}),
    ):
        client = direct_client()
        rec = single_turn(client, PROMPT, short_q, 1024, extra_body=body)
        rec["probe"] = label
        exp["reasoning_params"].append(rec)
        print(f"{label}:", rec.get("status"), rec.get("total_s"), rec.get("reasoning_tokens"),
              rec.get("error", "")[:120], flush=True)
    save()

    print("\n### EXPERIMENT E: prompt size (single turn, no tools) ###", flush=True)
    probe_q = "Explain how you would investigate a temperature excursion for a refrigerated shipment."
    exp["prompt_size"] = []
    for label, sysmsg in (
        ("full_system_prompt", PROMPT),
        ("minimal_prompt", "You are a cold-chain logistics test assistant."),
    ):
        client = direct_client()
        rec = single_turn(client, sysmsg, probe_q, 1024)
        rec["prompt_variant"] = label
        exp["prompt_size"].append(rec)
        print(f"{label}:", rec.get("status"), rec.get("total_s"), "in_tok:", rec.get("prompt_tokens"), flush=True)
    save()

    print("\n### EXPERIMENT F: alternative model single-turn probes ###", flush=True)
    try:
        client = direct_client()
        listed = [m.id for m in client.models.list().data]
        RESULTS["meta"]["models_listed"] = len(listed)
        prefer = ["meta/llama-3.3-70b-instruct", "meta/llama-3.1-8b-instruct",
                  "qwen/qwen2.5-72b-instruct", "qwen/qwen2.5-7b-instruct",
                  "mistralai/mistral-small-24b-instruct", "mistralai/mistral-nemo-12b-instruct",
                  "google/gemma-3-27b-it", "meta/llama-3.1-70b-instruct"]
        candidates = [m for m in prefer if m in listed][:2]
        if not candidates:  # fallback: any instruct non-reasoning model
            candidates = [m for m in listed if "instruct" in m and "r1" not in m.lower()][:2]
        alt = {"selected": candidates, "probes": []}
        for cand in candidates:
            c = direct_client()
            t0 = time.perf_counter()
            try:
                r = c.chat.completions.create(
                    model=cand,
                    messages=[{"role": "user", "content": "Reply with OK only."}],
                    max_tokens=256,
                )
                u = r.usage
                alt["probes"].append({
                    "model": cand, "status": "ok", "total_s": round(time.perf_counter() - t0, 2),
                    "prompt_tokens": u.prompt_tokens if u else None,
                    "completion_tokens": u.completion_tokens if u else None,
                    "note": "single trivial turn; tool-calling capability NOT verified for this model",
                })
            except Exception as exc:
                alt["probes"].append({"model": cand, "status": "error",
                                      "error": redact(f"{type(exc).__name__}: {exc}"[:200])})
            print(f"alt probe {cand}: done", flush=True)
        exp["alternative_models"] = alt
    except Exception as exc:
        exp["alternative_models"] = {"status": "error", "error": redact(f"{type(exc).__name__}: {exc}"[:200])}
    save()


def main():
    print(f"NIM latency benchmark — model={MODEL} — output={OUT}", flush=True)
    for name, question in BASELINE_CASES:
        run_case(name, question)
    run_experiments()
    print("\n### DONE — results written to", OUT, flush=True)


if __name__ == "__main__":
    main()
