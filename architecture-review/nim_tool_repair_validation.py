"""Limited live validation of the SQL tool-interface repair (2026-11-09).

Per the repair commission §5: ONE shipment lookup and ONE temperature-excursion
investigation per candidate (z-ai/glm-5.3-flash, nvidia/nemotron-3.5-lightning-30b-a3b),
through the REPAIRED production interface:
- the tool docstring is imported FROM PRODUCTION (src.agent_tools) — the
  repair under test, not a copy;
- the bounded-rejection logic is the PRODUCTION register_sql_rejection with a
  fresh per-conversation budget (as the gateway's per-request context does);
- the REAL src/sql_guard validates every query; guard-passing T-SQL executes
  against the REAL unified SQLite dataset read-only (10-row cap, real view
  columns per setup_unified_views.sql);
- database-anchored acceptance checks + the five-outcome classifier
  (nim_validation_common, repair commission §3).

Measured: completion time, guard rejections, successful SQL queries, exact
numerical accuracy, token usage, outcome class. Budget: ~10-16 requests.
Output: docs/evidence/SQL_TOOL_REPAIR_VALIDATION.json (incremental).
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
OUT = ROOT / "docs/evidence/SQL_TOOL_REPAIR_VALIDATION.json"
PROMPT = (ROOT / "src/prompts/system_prompt.txt").read_text(encoding="utf-8")

from src import agent_tools
from src.agent_tools import register_sql_rejection, SQLRejectionBudget
from src.agent_tools import query_telemetry_db as PRODUCTION_TOOL

from nim_validation_common import execute_query, check_answer, classify_outcome

PRODUCTION_DOC = PRODUCTION_TOOL.description  # the repaired instructions

CANDIDATES = {
    "glm_5_3_flash": "z-ai/glm-5.3-flash",
    "nemotron_lightning_30b": "nvidia/nemotron-3.5-lightning-30b-a3b",
}
SCENARIOS = {
    "lookup": "Find shipment UC-003241 in the database and tell me its cargo type, allowed temperature range, and receiver verdict.",
    "excursion": "Did shipment UC-003241 have any temperature excursions outside its allowed range? Cite the readings.",
}

FX_SOP = (
    "--- COMPLIANCE SOP CONTEXT ---\n"
    "[Source: Cold_Chain_Incident_SOP_v2.md | Format: RAW]\n"
    "### 1. Temperature Control & Spoilage Prevention (Cold-Chain)\n"
    "* **Fresh Perishables:** IoT temperature must remain between 0.0C and 4.0C.\n"
    "* **Mitigation Protocol:** The dispatcher must immediately contact the driver to restart the auxiliary cooling unit. "
    "If ETA delay is greater than 1 hour, divert the vehicle to the nearest emergency cold-storage facility.\n"
    "------------------------------"
)


class Metrics:
    def __init__(self):
        self.turns, self.queries, self.sop_calls, self.weather_temp, self.error = [], [], 0, None, None


def make_tools(metrics):
    from langchain_core.tools import tool
    from src.agent_tools import fetch_corridor_conditions as real_weather

    def _sql_body(sql_query: str) -> str:
        result, outcome = execute_query(sql_query)
        if outcome == "blocked":
            # PRODUCTION bounded-recovery logic meters the rejection
            override = register_sql_rejection(sql_query)
            if override:
                result = override
        metrics.queries.append({
            "sql": sql_query,
            "outcome": outcome if isinstance(outcome, str) else "ok",
            "rows": outcome.get("ok") if isinstance(outcome, dict) else None,
            "result_excerpt": result[:80],
        })
        return result
    _sql_body.__doc__ = PRODUCTION_DOC  # the repaired production instructions
    sql_tool = tool(_sql_body)

    def _sop_body(query: str) -> str:
        metrics.sop_calls += 1
        return FX_SOP
    _sop_body.__doc__ = PRODUCTION_TOOL.description  # placeholder, replaced below
    sop_doc = ("\n    Searches enterprise Standard Operating Procedures (SOPs) indexed in the Pinecone Vector DB.\n"
               "    Use this to retrieve regulatory thresholds, cold-chain breach mitigations, and rerouting rules.\n    ")
    _sop_body.__doc__ = sop_doc
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


def run_conversation(model, scenario):
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
    from langchain_openai import ChatOpenAI
    from langgraph.graph import StateGraph, START
    from langgraph.graph.message import add_messages
    from langgraph.prebuilt import ToolNode, tools_condition
    from typing import Annotated, TypedDict

    metrics = Metrics()
    rec = {"model": model, "scenario": scenario}

    try:
        llm = ChatOpenAI(model=model, base_url=BASE_URL, api_key=KEY,
                         temperature=0, timeout=180, max_retries=1, max_tokens=4096)
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

    # fresh per-request budget, exactly as the gateway's per-request context
    agent_tools._sql_retry_budget.set(SQLRejectionBudget())

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

        ok_queries = [q for q in metrics.queries if q["outcome"] in ("ok", "empty")]
        blocks = [q for q in metrics.queries if q["outcome"] == "blocked"]
        budget_msgs = [q for q in metrics.queries if "already rejected" in q["result_excerpt"] or "budget" in q["result_excerpt"]]

        ctx = {"weather_temp": metrics.weather_temp,
               "sql_evidence": any(q["outcome"] == "ok" for q in ok_queries),
               "telemetry_sampled": any(q.get("rows") and q["rows"] >= 10 for q in metrics.queries)}
        acc = check_answer(scenario, answer, ctx)
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
            "queries": metrics.queries,
            "answer_chars": len(answer or ""),
            "answer_excerpt": (answer or "")[:600],
            "acceptance": acc,
            "outcome_class": classify_outcome({"status": "ok", "acceptance": acc,
                                               "answer_excerpt": answer or ""}),
        })
        print(f"  {model} / {scenario}: {total:.1f}s turns={rec['llm_turns']} "
              f"ok_sql={rec['successful_sql_queries']} blocks={rec['guard_rejections']} "
              f"budget_hits={rec['budget_interventions']} outcome={rec['outcome_class']}", flush=True)
    except Exception as exc:
        total = time.perf_counter() - t0
        rec.update({"status": "error", "total_s": round(total, 2),
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                    "llm_turns": len(metrics.turns),
                    "queries": metrics.queries,
                    "guard_rejections": sum(1 for q in metrics.queries if q["outcome"] == "blocked")})
        rec["outcome_class"] = classify_outcome(rec)
        print(f"  {model} / {scenario}: ERROR after {total:.1f}s: {rec['error']}", flush=True)
    return rec


def main():
    data = {
        "meta": {
            "date": "2026-11-09",
            "purpose": "Limited live validation of the SQL tool-interface repair (commission §5)",
            "tool_docstring_source": "imported live from src.agent_tools (production)",
            "recovery_logic": "production register_sql_rejection, fresh budget per conversation",
            "sql_execution": "real guard -> real unified SQLite dataset (read-only, 10-row cap)",
            "budget_note": "4 conversations, ~10-16 requests; 40 RPM not approached",
        },
        "conversations": [],
    }
    for key, model in CANDIDATES.items():
        for scenario in SCENARIOS:
            print(f"\n=== {key} / {scenario} ({time.strftime('%H:%M:%S')}) ===", flush=True)
            data["conversations"].append(run_conversation(model, scenario))
            OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False,
                                      default=lambda o: sorted(o) if isinstance(o, set) else str(o)),
                           encoding="utf-8")
    print("\nDONE ->", OUT, flush=True)


if __name__ == "__main__":
    main()
