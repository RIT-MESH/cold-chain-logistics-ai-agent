"""NVIDIA NIM final model validation gate (2026-11-08).

Candidates: nvidia/nemotron-3.5-lightning-30b-a3b and z-ai/glm-5.3-flash
(production default deepseek-v4.1-flash is NOT touched).

Battery (§3): 5 scenarios x 3 controlled repetitions per candidate, through
the mirrored LangGraph agent with the PRODUCTION-SHAPED SQL tool from
nim_validation_common: the REAL src/sql_guard validator runs first (blocked
SQL returns its actual error to the agent), guard-passing queries execute
against the REAL unified SQLite dataset read-only, results capped at 10 rows
like production. Weather tool is REAL (Open-Meteo). SOP tool is a labeled
fixture (Pinecone unavailable) returning a real SOP excerpt.

Lightning accuracy experiment (§4): two variants on the excursion scenario,
2 repetitions each —
  V1 narrow-column + explicit column/unit instructions (generic prompt
     addendum; no test-shipment answers hardcoded);
  V2 thinking=false (chat_template_kwargs), tested separately.

Measured per conversation: total/turn latency, tokens (input/output/
reasoning), every SQL query with its guard outcome, guard rejections,
recovery after rejection, weather temp (injected into the answer allowlist),
and the programmatic accuracy acceptance checks.

Budget: ~34 conversations, ~100 sequential requests (org limit 40 RPM
never approached). Output (incremental):
docs/evidence/NVIDIA_NIM_FINAL_MODEL_VALIDATION.json
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
OUT = ROOT / "docs/evidence/NVIDIA_NIM_FINAL_MODEL_VALIDATION.json"
PROMPT = (ROOT / "src/prompts/system_prompt.txt").read_text(encoding="utf-8")

from nim_validation_common import execute_query, check_answer, GROUND_TRUTH  # noqa: E402

CANDIDATES = {
    "glm_5_3_flash": {"model": "z-ai/glm-5.3-flash", "extra_body": None},
    "nemotron_lightning_30b": {"model": "nvidia/nemotron-3.5-lightning-30b-a3b", "extra_body": None},
}

# §4 lightning variants (excursion scenario only, 2 reps each)
NARROW_COLUMN_ADDENDUM = (
    "\nSQL result discipline: When querying the database, SELECT only the columns the "
    "question needs — never wide column lists. Column units in FDE_VIEWS: temperature_c "
    "is degrees Celsius; humidity_pct is percent relative humidity; fuel_level_pct is "
    "percent. Never interpret a humidity or fuel value as a temperature. If a query "
    "result does not cover the shipment's full timeline, say so explicitly."
)
LIGHTNING_VARIANTS = {
    "narrow_column_instruction": {"model": "nvidia/nemotron-3.5-lightning-30b-a3b",
                                  "extra_body": None, "prompt_addendum": NARROW_COLUMN_ADDENDUM},
    "thinking_off": {"model": "nvidia/nemotron-3.5-lightning-30b-a3b",
                    "extra_body": {"chat_template_kwargs": {"thinking": False}}, "prompt_addendum": None},
}

SCENARIOS = {
    "lookup": "Find shipment UC-003241 in the database and tell me its cargo type, allowed temperature range, and receiver verdict.",
    "excursion": "Did shipment UC-003241 have any temperature excursions outside its allowed range? Cite the readings.",
    "delay": "Analyze any delivery delays for shipment UC-003241 using database evidence.",
    "sop": "Investigate shipment UC-003241's temperature compliance and recommend mitigations, citing compliance SOP evidence.",
    "weather": "Analyze shipment UC-003241's route conditions including current weather at its last known position.",
}
REPS = 3
VARIANT_REPS = 2

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


class LoopMetrics:
    def __init__(self):
        self.turns = []
        self.queries = []          # {sql, outcome, rows, error_excerpt}
        self.sop_calls = 0
        self.weather_temp = None
        self.error = None

    def llm_turn(self, seconds, usage):
        self.turns.append({"duration_s": round(seconds, 2), **usage})


def make_tools(metrics):
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
        result, outcome = execute_query(sql_query)
        entry = {"sql": sql_query, "outcome": outcome if isinstance(outcome, str) else "ok"}
        if isinstance(outcome, dict):
            entry["rows"] = outcome["ok"]
            entry["truncated"] = outcome["truncated"]
        else:
            entry["error_excerpt"] = result[:80]
        metrics.queries.append(entry)
        return result

    @tool
    def search_compliance_sop(query: str) -> str:
        """
        Searches enterprise Standard Operating Procedures (SOPs) indexed in the Pinecone Vector DB.
        Use this to retrieve regulatory thresholds, cold-chain breach mitigations, and rerouting rules.
        """
        metrics.sop_calls += 1
        return FX_SOP

    @tool
    def fetch_corridor_conditions(latitude: float, longitude: float) -> str:
        """
        Fetches external current weather for coordinates. Does not provide traffic, cargo telemetry, or congestion.
        """
        out = real_weather.invoke({"latitude": latitude, "longitude": longitude})
        m = re.search(r"External Temp:\s*(-?\d+(?:\.\d+)?)", out)
        if m:
            metrics.weather_temp = float(m.group(1))
        return out

    return [query_telemetry_db, fetch_corridor_conditions, search_compliance_sop]


def run_conversation(cfg, prompt_addendum, scenario, rep):
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
    from langchain_openai import ChatOpenAI
    from langgraph.graph import StateGraph, START
    from langgraph.graph.message import add_messages
    from langgraph.prebuilt import ToolNode, tools_condition
    from typing import Annotated, TypedDict

    metrics = LoopMetrics()
    system_prompt = PROMPT + (prompt_addendum or "")
    rec = {"scenario": scenario, "rep": rep, "model": cfg["model"]}

    try:
        llm = ChatOpenAI(model=cfg["model"], base_url=BASE_URL, api_key=KEY,
                         temperature=0, timeout=180, max_retries=1, max_tokens=4096,
                         extra_body=cfg["extra_body"])
        tools = make_tools(metrics)
        bound = llm.bind_tools(tools)
    except Exception as exc:
        rec.update({"status": "bind_error", "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        return rec

    class AgentState(TypedDict):
        messages: Annotated[list, add_messages]

    def reasoning_node(state):
        t0 = time.perf_counter()
        response = bound.invoke(state["messages"])
        meta = getattr(response, "usage_metadata", None) or {}
        det = meta.get("output_token_details") or {}
        metrics.llm_turn(time.perf_counter() - t0, {
            "input_tokens": int(meta.get("input_tokens") or 0),
            "output_tokens": int(meta.get("output_tokens") or 0),
            "reasoning_tokens": int(det.get("reasoning") or 0),
        })
        return {"messages": [response]}

    gb = StateGraph(AgentState)
    gb.add_node("reasoner", reasoning_node)
    gb.add_node("tools", ToolNode(tools))
    gb.add_edge(START, "reasoner")
    gb.add_conditional_edges("reasoner", tools_condition)
    gb.add_edge("tools", "reasoner")
    agent = gb.compile()

    t0 = time.perf_counter()
    try:
        result = agent.invoke(
            {"messages": [SystemMessage(content=system_prompt),
                         HumanMessage(content=SCENARIOS[scenario])]},
            config={"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": 20},
        )
        total = time.perf_counter() - t0
        answer = None
        for item in reversed(result["messages"]):
            if isinstance(item, AIMessage) and item.content and not getattr(item, "tool_calls", None):
                answer = item.content if isinstance(item.content, str) else str(item.content)
                break

        # guard-rejection analysis (§1): blocks + recovery
        outcomes = [q["outcome"] for q in metrics.queries]
        blocks = [q for q in metrics.queries if q["outcome"] == "blocked"]
        recovered = False
        if blocks:
            first_block = metrics.queries.index(blocks[0])
            recovered = any(o in ("ok", "empty") for o in outcomes[first_block + 1:])

        ctx = {"weather_temp": metrics.weather_temp,
               "telemetry_sampled": any(q.get("outcome") == "ok" and q.get("truncated")
                                        for q in metrics.queries)}
        acc = check_answer(scenario, answer, ctx)

        rec.update({
            "status": "ok", "total_s": round(total, 2),
            "llm_turns": len(metrics.turns),
            "turn_durations": [t["duration_s"] for t in metrics.turns],
            "input_tokens": sum(t["input_tokens"] for t in metrics.turns),
            "output_tokens": sum(t["output_tokens"] for t in metrics.turns),
            "reasoning_tokens": sum(t["reasoning_tokens"] for t in metrics.turns),
            "tool_calls": len(metrics.queries) + metrics.sop_calls,
            "queries": metrics.queries,
            "sop_calls": metrics.sop_calls,
            "weather_temp": metrics.weather_temp,
            "guard_blocks": len(blocks),
            "block_errors": [q["error_excerpt"] for q in blocks],
            "recovered_after_block": recovered if blocks else None,
            "answer_chars": len(answer or ""),
            "answer_excerpt": (answer or "")[:600],
            "acceptance": acc,
        })
        print(f"  {scenario} rep{rep}: {total:.1f}s turns={rec['llm_turns']} blocks={rec['guard_blocks']} "
              f"recovered={rec['recovered_after_block']} acc={'PASS' if acc['pass'] else 'FAIL:' + str([k for k, v in acc['checks'].items() if not v])}",
              flush=True)
    except Exception as exc:
        total = time.perf_counter() - t0
        rec.update({"status": "error", "total_s": round(total, 2),
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                    "queries": metrics.queries, "guard_blocks":
                    sum(1 for q in metrics.queries if q["outcome"] == "blocked")})
        print(f"  {scenario} rep{rep}: ERROR after {total:.1f}s: {rec['error']}", flush=True)
    return rec


def save(data):
    try:
        OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False,
                                  default=lambda o: sorted(o) if isinstance(o, set) else str(o)),
                        encoding="utf-8")
    except Exception as exc:  # a save failure must never kill the battery
        print(f"WARNING: incremental save failed: {exc}", flush=True)


def main():
    data = {
        "meta": {
            "date": "2026-11-08",
            "base_url": BASE_URL,
            "harness": "architecture-review/nim_final_validation.py",
            "sql_tool": "REAL src/sql_guard validation -> transpiled execution against the REAL unified SQLite dataset (read-only, 10-row cap); blocked SQL returns the actual guard error",
            "sop_tool": "SIMULATED fixture (real SOP excerpt; Pinecone unavailable)",
            "weather_tool": "REAL live Open-Meteo call",
            "ground_truth": {"shipment_id": GROUND_TRUTH["shipment_id"],
                             "telemetry_count": GROUND_TRUTH["telemetry_count"],
                             "temperatures": sorted(GROUND_TRUTH["temperatures"])},
            "budget_note": "~34 conversations, ~100 sequential requests; 40 RPM org limit not approached.",
        },
        "candidates": {},
        "lightning_variants": {},
    }
    for key, cfg in CANDIDATES.items():
        print(f"\n=== CANDIDATE {key} ({time.strftime('%H:%M:%S')}) ===", flush=True)
        entry = {}
        data["candidates"][key] = entry
        for scenario in SCENARIOS:
            entry[scenario] = []
            save(data)
            for rep in range(1, REPS + 1):
                entry[scenario].append(run_conversation(cfg, None, scenario, rep))
                save(data)  # per-rep incremental save: never lose a completed conversation
        print(f"--- {key} battery complete", flush=True)

    print(f"\n=== LIGHTNING VARIANTS (§4) ({time.strftime('%H:%M:%S')}) ===", flush=True)
    for vkey, vcfg in LIGHTNING_VARIANTS.items():
        print(f"  variant {vkey}", flush=True)
        data["lightning_variants"][vkey] = {
            "config": {"model": vcfg["model"], "extra_body": vcfg["extra_body"],
                       "prompt_addendum": bool(vcfg["prompt_addendum"])},
            "excursion": [],
        }
        for rep in range(1, VARIANT_REPS + 1):
            data["lightning_variants"][vkey]["excursion"].append(
                run_conversation(vcfg, vcfg["prompt_addendum"], "excursion", rep))
            save(data)

    save(data)
    print("\nDONE ->", OUT, flush=True)


if __name__ == "__main__":
    main()
