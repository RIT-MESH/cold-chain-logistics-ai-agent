"""Task 2 — Fair model comparison on the cold-chain agent workload (2026-11-08).

Five configurations, identical prompts and fixtures:
  1. deepseek-ai/deepseek-v4.1-flash  — production default (create_llm mirror)
  2. deepseek-ai/deepseek-v4.1-flash + thinking=false (HARNESS-LEVEL
     extra_body; production default config untouched)
  3. z-ai/glm-5.3-flash
  4. nvidia/nemotron-3.5-lightning-30b-a3b
  5. nvidia/nemotron-3.5-bolt-e4b

Per config: (a) greeting via direct streaming (first-token latency), (b) shipment
lookup via the mirrored agent graph, (c) temperature-excursion investigation via
the mirrored agent graph (this is the LangGraph tool round trip).

FIXTURES ARE GROUND-TRUTH CORRECTED (see SHIPMENT_ACCURACY_AUDIT): the real
dataset has ZERO readings above 8.0 C and exactly ONE low-side excursion
(1.2 C at 2026-07-06 23:45:09). The fixture emulates real SQL behavior: an
unfiltered TOP 10 returns chronological rows WITHOUT the excursion, so the
model must issue a filtered query to find it — exactly like production SQL.

ACCEPTANCE CHECKS are programmatic and objective: an answer passes only if
it agrees with the database ground truth (no fabricated high excursions, must
cite 1.2 C and 23:45, must not claim "no excursion"). Fluency cannot mask a
database contradiction.

Budget: ~35 sequential requests total.
Output: docs/evidence/NVIDIA_NIM_MODEL_COMPARISON.json (incremental).
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
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
KEY = os.getenv("NVIDIA_API_KEY", "")
if not KEY:
    sys.exit("NVIDIA_API_KEY missing — aborting before any request.")
BASE_URL = "https://integrate.api.nvidia.com/v1"
DEEPSEEK = os.getenv("NVIDIA_MODEL", "deepseek-ai/deepseek-v4.1-flash")
OUT = ROOT / "docs/evidence/NVIDIA_NIM_MODEL_COMPARISON.json"
PROMPT = (ROOT / "src/prompts/system_prompt.txt").read_text(encoding="utf-8")

CONFIGS = [
    {"key": "deepseek_v4.1_flash_default", "model": DEEPSEEK, "extra_body": None, "production_mirror": True},
    {"key": "deepseek_v4.1_flash_thinking_off", "model": DEEPSEEK,
     "extra_body": {"chat_template_kwargs": {"thinking": False}}, "production_mirror": False},
    {"key": "zai_glm_5_3_flash", "model": "z-ai/glm-5.3-flash", "extra_body": None, "production_mirror": False},
    {"key": "nemotron_3_5_lightning_30b_a3b", "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
     "extra_body": None, "production_mirror": False},
    {"key": "nemotron_3_5_bolt_e4b", "model": "nvidia/nemotron-3.5-bolt-e4b",
     "extra_body": None, "production_mirror": False},
]

QUESTIONS = {
    "greeting": "Hello! What can you help me investigate in this cold-chain environment?",
    "lookup": "Find shipment UC-003241 in the database and tell me its cargo type, allowed temperature range, and receiver verdict.",
    "excursion": "Did shipment UC-003241 have any temperature excursions outside its allowed range? Cite the readings.",
}

# ------------------------------------------------- ground-truth fixtures --
FX_SHIPMENTS = (
    "COLUMNS: shipment_id, cargo_type, vehicle_id, departed_at, receiver_verdict, temperature_min_c, temperature_max_c\n"
    "('UC-003241', 'insulin pens', 'VEH-0111', '2026-07-06 17:15:09', 'accepted', 2.0, 8.0)\n"
)
# First 10 chronological rows of the REAL dataset (excursion NOT in this window):
FX_TELEMETRY_TOP10 = (
    "COLUMNS: shipment_id, recorded_at, latitude, longitude, temperature_c, humidity_pct, traffic_congestion, fuel_level_pct, risk_level\n"
    "('UC-003241', '2026-07-06 17:15:09', 35.73731, 136.78864, 6.3, 79.0, 2.68, 94.54, 'low')\n"
    "('UC-003241', '2026-07-06 17:45:09', 35.738242, 136.800779, 6.4, 81.0, 2.91, 93.10, 'low')\n"
    "('UC-003241', '2026-07-06 18:15:09', 35.739881, 136.812981, 7.3, 84.0, 3.35, 91.80, 'medium')\n"
    "('UC-003241', '2026-07-06 18:45:09', 35.740912, 136.829311, 5.5, 81.0, 3.11, 90.20, 'low')\n"
    "('UC-003241', '2026-07-06 19:15:09', 35.742501, 136.841902, 5.5, 79.0, 3.02, 88.95, 'low')\n"
    "('UC-003241', '2026-07-06 19:45:09', 35.743812, 136.854221, 7.8, 77.0, 2.77, 87.40, 'low')\n"
    "('UC-003241', '2026-07-06 20:15:09', 35.74502, 136.856921, 6.4, 81.0, 4.02, 86.10, 'medium')\n"
    "('UC-003241', '2026-07-06 20:45:09', 35.746011, 136.868102, 3.6, 84.0, 4.31, 84.75, 'medium')\n"
    "('UC-003241', '2026-07-06 21:15:09', 35.746822, 136.879001, 2.1, 77.0, 3.94, 83.30, 'low')\n"
    "('UC-003241', '2026-07-06 21:45:09', 35.747501, 136.889211, 5.8, 81.0, 3.62, 82.05, 'low')\n"
)
# The single REAL excursion row (returned for filtered low-side queries):
FX_EXCURSION = (
    "COLUMNS: shipment_id, recorded_at, latitude, longitude, temperature_c, humidity_pct, risk_level\n"
    "('UC-003241', '2026-07-06 23:45:09', 35.75204, 136.9201, 1.2, 69.0, 'high')\n"
)
NO_RECORDS = "No records matched the query criteria."
FX_ALERTS = (
    "COLUMNS: shipment_id, alert_at, alert_type, severity, message\n"
    "('UC-003241', '2026-07-06 23:45:09', 'temperature_excursion', 'medium', 'Observed 1.2 C outside configured 2 to 8 C')\n"
)
FX_CUSTODY = (
    "COLUMNS: shipment_id, handoff_no, received_by, handed_at, seal_intact\n"
    "('UC-003241', 1, 'line-haul carrier', '2026-07-06 17:28:40', 1)\n"
)

GROUND_TRUTH = {
    "cargo_type": "insulin pens",
    "band": "2.0-8.0 C",
    "receiver_verdict": "accepted",
    "excursions": "exactly one: 1.2 C at 2026-07-06 23:45:09 (LOW side, below the 2.0 C minimum)",
    "readings_above_8C": 0,
}


def fixture_sql(sql_query, metrics):
    """Emulate real SQL result-shape for the corrected ground truth."""
    q = sql_query.upper()
    if not q.strip().startswith("SELECT"):
        return "SECURITY BLOCK: only SELECT queries on approved FDE_VIEWS are allowed"
    if "VW_SHIPMENTS" in q:
        return FX_SHIPMENTS
    if "VW_TELEMETRY" in q:
        low = ("< 2" in q) or ("<2" in q) or ("<= 2" in q) or ("NOT BETWEEN" in q)
        high = ("> 8" in q) or (">8" in q) or (">= 8" in q)
        combined = " OR " in q
        if low or (combined and high) or ("NOT BETWEEN" in q):
            return FX_EXCURSION if low else NO_RECORDS
        if high:
            return NO_RECORDS  # truthful: the dataset has ZERO readings above 8.0 C
        return FX_TELEMETRY_TOP10
    if "VW_ALERTS" in q:
        return FX_ALERTS
    if "VW_CUSTODY" in q:
        return FX_CUSTODY
    return NO_RECORDS


# ------------------------------------------------------------ acceptance --
def check_answer(test, answer):
    """Objective acceptance checks against DB ground truth. Pass = ALL true."""
    low = (answer or "").lower()
    if test == "lookup":
        checks = {
            "contains_cargo_insulin": "insulin" in low,
            "contains_band_2.0_8.0": ("2.0" in low and "8.0" in low),
            "contains_verdict_accepted": "accepted" in low,
            "cites_shipment_id": "uc-003241" in low,
        }
    elif test == "excursion":
        checks = {
            "cites_1.2C_reading": "1.2" in (answer or ""),
            "cites_2345_timestamp": "23:45" in (answer or ""),
            "identifies_low_side": any(w in low for w in
                ("below", "under", "beneath", "less than", "lower", "low excursion", "dropped")),
            "no_false_high_excursion": not any(v in (answer or "") for v in ("9.2", "8.9", "above 8.0", "above 8 C", "over 8.0")),
            "does_not_claim_no_excursion": not re.search(r"no (temperature )?excursion", low),
        }
    else:
        checks = {"non_empty": bool(answer and answer.strip())}
    return {"checks": checks, "pass": all(checks.values())}


# ------------------------------------------------------------- instruments --
class Metrics:
    def __init__(self):
        self.turns, self.tool_calls, self.tool_queries, self.error = [], [], [], None

    def llm_turn(self, seconds, usage):
        self.turns.append({
            "duration_s": round(seconds, 2),
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "reasoning_tokens": usage["reasoning_tokens"],
        })

    def tool_exec(self, name, seconds, query=None):
        self.tool_calls.append(name)
        if name == "query_telemetry_db" and query:
            self.tool_queries.append(query)

    def summary(self, total_s):
        return {
            "total_s": round(total_s, 2),
            "llm_turns": len(self.turns),
            "tool_calls": len(self.tool_calls),
            "tool_query_list": self.tool_queries,
            "llm_wait_s": round(sum(t["duration_s"] for t in self.turns), 2),
            "input_tokens": sum(t["input_tokens"] for t in self.turns),
            "output_tokens": sum(t["output_tokens"] for t in self.turns),
            "reasoning_tokens": sum(t["reasoning_tokens"] for t in self.turns),
            "turn_durations": [t["duration_s"] for t in self.turns],
            "error": self.error,
        }


def usage_of(msg):
    meta = getattr(msg, "usage_metadata", None) or {}
    details = meta.get("output_token_details") or {}
    return {
        "input_tokens": int(meta.get("input_tokens") or 0),
        "output_tokens": int(meta.get("output_tokens") or 0),
        "reasoning_tokens": int(details.get("reasoning") or 0),
    }


def make_llm(cfg):
    from langchain_openai import ChatOpenAI
    if cfg["production_mirror"]:
        from src.orchestrator import create_llm
        return create_llm()  # exact production params (timeout 180, retries 2, 4096 cap)
    return ChatOpenAI(
        model=cfg["model"], base_url=BASE_URL, api_key=KEY, temperature=0,
        timeout=180, max_retries=1, max_tokens=4096,
        extra_body=cfg["extra_body"],
    )


def make_tools(metrics):
    from langchain_core.tools import tool

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
        out = fixture_sql(sql_query, metrics)
        metrics.tool_exec("query_telemetry_db", time.perf_counter() - t0, sql_query)
        return out

    return [query_telemetry_db]


def run_loop(cfg, test, question):
    """One full ReAct conversation through the mirrored graph (SQL tool only,
    matching the lookup/excursion tasks; no weather/SOP needed)."""
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
    from langgraph.graph import StateGraph, START
    from langgraph.graph.message import add_messages
    from langgraph.prebuilt import ToolNode, tools_condition
    from typing import Annotated, TypedDict

    rec = {"test": test, "question": question}
    metrics = Metrics()
    try:
        llm = make_llm(cfg)
        bound = llm.bind_tools(make_tools(metrics))
    except Exception as exc:
        rec.update({"status": "bind_error", "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        print(f"    {cfg['key']}/{test}: BIND ERROR {rec['error']}", flush=True)
        return rec

    class AgentState(TypedDict):
        messages: Annotated[list, add_messages]

    def reasoning_node(state):
        t0 = time.perf_counter()
        response = bound.invoke(state["messages"])
        metrics.llm_turn(time.perf_counter() - t0, usage_of(response))
        return {"messages": [response]}

    gb = StateGraph(AgentState)
    gb.add_node("reasoner", reasoning_node)
    gb.add_node("tools", ToolNode(make_tools(metrics)))
    gb.add_edge(START, "reasoner")
    gb.add_conditional_edges("reasoner", tools_condition)
    gb.add_edge("tools", "reasoner")
    agent = gb.compile()

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
        acc = check_answer(test, answer)
        rec.update(metrics.summary(total))
        rec.update({
            "status": "ok",
            "answer_chars": len(answer or ""),
            "answer_excerpt": (answer or "")[:400],
            "acceptance": acc,
        })
        print(f"    {cfg['key']}/{test}: {total:.1f}s turns={rec['llm_turns']} tools={rec['tool_calls']} "
              f"acc={'PASS' if acc['pass'] else 'FAIL ' + str([k for k,v in acc['checks'].items() if not v])}", flush=True)
    except Exception as exc:
        total = time.perf_counter() - t0
        metrics.error = f"{type(exc).__name__}: {str(exc)[:200]}"
        rec.update(metrics.summary(total))
        rec["status"] = "error"
        print(f"    {cfg['key']}/{test}: ERROR after {total:.1f}s: {metrics.error}", flush=True)
    return rec


def run_greeting_stream(cfg):
    """Greeting with direct streaming: first-token latency + total."""
    from openai import OpenAI
    client = OpenAI(base_url=BASE_URL, api_key=KEY, timeout=180, max_retries=1)
    rec = {"test": "greeting", "question": QUESTIONS["greeting"]}
    t0 = time.perf_counter()
    first_tok = None
    parts, leak = [], set()
    try:
        stream = client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "system", "content": PROMPT},
                      {"role": "user", "content": QUESTIONS["greeting"]}],
            max_tokens=4096, stream=True, stream_options={"include_usage": True},
            extra_body=cfg["extra_body"] or {},
        )
        usage = None
        for ev in stream:
            if ev.usage:
                usage = ev.usage
            if not ev.choices:
                continue
            d = ev.choices[0].delta
            for k in ("reasoning_content", "reasoning"):
                if getattr(d, k, None):
                    leak.add(k)
            if d and d.content:
                if first_tok is None:
                    first_tok = time.perf_counter() - t0
                parts.append(d.content)
        total = time.perf_counter() - t0
        det = getattr(usage, "completion_tokens_details", None) if usage else None
        rec.update({
            "status": "ok", "total_s": round(total, 2),
            "first_token_s": round(first_tok, 2) if first_tok else None,
            "reasoning_leak_keys": sorted(leak),
            "prompt_tokens": usage.prompt_tokens if usage else None,
            "completion_tokens": usage.completion_tokens if usage else None,
            "reasoning_tokens": getattr(det, "reasoning_tokens", None) if det else None,
            "answer_chars": len("".join(parts)),
        })
        print(f"    {cfg['key']}/greeting(stream): total={rec['total_s']}s first_tok={rec['first_token_s']}s "
              f"leak={sorted(leak)}", flush=True)
    except Exception as exc:
        rec.update({"status": "error", "total_s": round(time.perf_counter() - t0, 2),
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        print(f"    {cfg['key']}/greeting(stream): ERROR {rec['error']}", flush=True)
    return rec


def main():
    if OUT.exists():
        data = json.loads(OUT.read_text(encoding="utf-8"))
    else:
        data = {"meta": {"date": "2026-11-08", "base_url": BASE_URL}, "servability": [], "comparison": {}}
    data.setdefault("comparison", {})
    data["meta"]["ground_truth"] = GROUND_TRUTH
    data["meta"]["fixture_note"] = ("Fixture corrected per SHIPMENT_ACCURACY_AUDIT: unfiltered TOP 10 hides the "
                                    "excursion; filtered low-side query returns the single real 1.2 C row; "
                                    "high-side filter truthfully returns no records.")
    for cfg in CONFIGS:
        print(f"\n=== CONFIG {cfg['key']} ({time.strftime('%H:%M:%S')}) ===", flush=True)
        entry = data["comparison"].get(cfg["key"], {})
        entry["config"] = {"model": cfg["model"], "extra_body": cfg["extra_body"],
                          "production_mirror": cfg["production_mirror"]}
        entry["greeting_stream"] = run_greeting_stream(cfg)
        entry["lookup_loop"] = run_loop(cfg, "lookup", QUESTIONS["lookup"])
        entry["excursion_loop"] = run_loop(cfg, "excursion", QUESTIONS["excursion"])
        data["comparison"][cfg["key"]] = entry
        OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Bonus single-turn probes: can reasoning be disabled on the fast hybrids?
    from openai import OpenAI
    client = OpenAI(base_url=BASE_URL, api_key=KEY, timeout=120, max_retries=0)
    data["reasoning_disable_probes"] = []
    for model in ("nvidia/nemotron-3.5-lightning-30b-a3b", "z-ai/glm-5.3-flash"):
        try:
            t0 = time.perf_counter()
            r = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": "Reply with OK only."}],
                max_tokens=256, extra_body={"chat_template_kwargs": {"thinking": False}})
            u = r.usage
            det = getattr(u, "completion_tokens_details", None) if u else None
            data["reasoning_disable_probes"].append({
                "model": model, "probe": "chat_template_kwargs.thinking=false", "status": "ok",
                "total_s": round(time.perf_counter() - t0, 2),
                "reasoning_tokens": getattr(det, "reasoning_tokens", None) if det else None,
                "content": (r.choices[0].message.content or "")[:60],
            })
        except Exception as exc:
            data["reasoning_disable_probes"].append({
                "model": model, "probe": "chat_template_kwargs.thinking=false", "status": "error",
                "error": f"{type(exc).__name__}: {str(exc)[:150]}"})
        print("reasoning-disable probe done:", model, flush=True)
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nDONE ->", OUT, flush=True)


if __name__ == "__main__":
    main()
