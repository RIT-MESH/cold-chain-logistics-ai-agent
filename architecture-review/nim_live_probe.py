"""NVIDIA NIM DeepSeek live verification probe (Phase: architecture review).

Run:  python -I architecture-review/nim_live_probe.py
Requires the project .env to contain:
    NVIDIA_API_KEY=...            (secret - never printed)
    NVIDIA_MODEL=deepseek-ai/deepseek-v4.1-flash   (user-supplied target)
    NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1

Performs the 10 approved checks with a handful of small requests; no load
testing. All outputs are secret-redacted. Results append to
architecture-review/NVIDIA_NIM_API_VERIFICATION.md sections and a JSON
evidence file docs/evidence/nvidia_nim_live_results.json.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
RESULTS = []


_SECRET = ""  # set in main(); scrubbed from all recorded output


def redact(text):
    """Strip anything secret-shaped, including the API key value itself."""
    if not isinstance(text, str):
        text = str(text)
    if _SECRET and _SECRET in text:
        text = text.replace(_SECRET, "<REDACTED-KEY>")
    return text[:600]


def check(label, status, detail):
    RESULTS.append({"check": label, "status": status, "detail": redact(detail)})
    print(f"[{status}] {label}: {redact(detail)[:300]}")


def load_env():
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def main():
    env = load_env()
    key = env.get("NVIDIA_API_KEY") or os.environ.get("NVIDIA_API_KEY")
    base = (env.get("NVIDIA_BASE_URL") or os.environ.get("NVIDIA_BASE_URL")
            or "https://integrate.api.nvidia.com/v1").rstrip("/")
    model = env.get("NVIDIA_MODEL") or os.environ.get("NVIDIA_MODEL") or "deepseek-ai/deepseek-v4.1-flash"

    if not key:
        print("NVIDIA_API_KEY not found in project .env or environment. "
              "Add it to E:\\cold-chain-logistics-FDE-Project-main\\.env "
              "(gitignored). No requests were sent.")
        sys.exit(2)

    global _SECRET
    _SECRET = key

    from openai import OpenAI
    # NVIDIA hosted generations can take minutes (the user's own prior cosmos
    # MCP used timeout=600); use a generous chat timeout so we measure the
    # API instead of our own client deadline.
    client = OpenAI(base_url=base, api_key=key, timeout=300, max_retries=1)
    fast = OpenAI(base_url=base, api_key=key, timeout=60, max_retries=1)
    say = lambda m: print(m, flush=True)

    # 1 + 3: connectivity, auth, model availability
    try:
        t0 = time.time()
        models = client.models.list()
        ids = [m.id for m in models.data]
        check("HTTPS connectivity + auth (GET /models)", "PASS",
              f"{len(ids)} models listed in {time.time()-t0:.2f}s")
        exact = model in ids
        check(f"Exact model identifier available: {model}", "PASS" if exact else "FAIL",
              "identifier served by the endpoint" if exact
              else f"NOT in live model list; DeepSeek models present: "
                   f"{[i for i in ids if 'deepseek' in i.lower()][:10]} (no substitution made)")
    except Exception as exc:
        check("HTTPS connectivity + auth (GET /models)", "FAIL", f"{type(exc).__name__}: {exc}")
        write_results()
        return

    # 4 + 5: minimal chat completion
    say("starting: minimal chat completion (max 300s)...")
    try:
        t0 = time.time()
        r = client.chat.completions.create(
            model=model, max_tokens=16,
            messages=[{"role": "user", "content": "Reply with the single word: OK"}])
        content = r.choices[0].message.content
        usage = getattr(r, "usage", None)
        check("Minimal chat completion", "PASS" if content else "FAIL",
              f"content={content!r} usage={'present' if usage else 'absent'} "
              f"finish={r.choices[0].finish_reason} elapsed={time.time()-t0:.1f}s")
    except Exception as exc:
        check("Minimal chat completion", "FAIL", f"{type(exc).__name__}: {exc}")

    # 6: multi-turn history
    say("starting: multi-turn conversation check...")
    try:
        t0 = time.time()
        r = client.chat.completions.create(
            model=model, max_tokens=32,
            messages=[{"role": "user", "content": "My codename is Frost-7. Remember it."},
                      {"role": "assistant", "content": "Understood, Frost-7."},
                      {"role": "user", "content": "What is my codename? Reply with the codename only."}])
        content = r.choices[0].message.content or ""
        check("Multi-turn conversation handling", "PASS" if "Frost-7" in content else "FAIL",
              f"recalled={'Frost-7' if 'Frost-7' in content else content[:60]!r} "
              f"elapsed={time.time()-t0:.1f}s")
    except Exception as exc:
        check("Multi-turn conversation handling", "FAIL", f"{type(exc).__name__}: {exc}")

    # 7: tool calling with a harmless mock function
    tools = [{"type": "function", "function": {
        "name": "get_current_utc_time",
        "description": "Return the current UTC time.",
        "parameters": {"type": "object",
                       "properties": {"tz_label": {"type": "string", "description": "Timezone label, e.g. UTC"}},
                       "required": ["tz_label"]}}}]
    try:
        say("starting: tool-call probe...")
        t0 = time.time()
        r = client.chat.completions.create(
            model=model, max_tokens=100, tools=tools,
            messages=[{"role": "user", "content": "Use the get_current_utc_time tool with tz_label 'UTC'."}])
        msg = r.choices[0].message
        tcs = msg.tool_calls or []
        if tcs:
            fn = tcs[0].function
            check("Tool-calling (mock function)", "PASS",
                  f"tool={fn.name} args={fn.arguments} elapsed={time.time()-t0:.1f}s")
            # complete the round trip
            say("starting: tool-result round trip...")
            t1 = time.time()
            r2 = client.chat.completions.create(
                model=model, max_tokens=60, tools=tools,
                messages=[{"role": "user", "content": "Use the get_current_utc_time tool with tz_label 'UTC'."},
                          msg.model_dump(),
                          {"role": "tool", "tool_call_id": tcs[0].id,
                           "content": json.dumps({"utc_time": "2026-11-03T12:00:00Z"})}])
            check("Tool round-trip (submit tool result)", "PASS" if r2.choices[0].message.content else "FAIL",
                  f"final={r2.choices[0].message.content[:80]!r} elapsed={time.time()-t1:.1f}s")
        else:
            check("Tool-calling (mock function)", "FAIL",
                  f"no tool_calls returned; content={msg.content[:80]!r}")
    except Exception as exc:
        check("Tool-calling (mock function)", "FAIL", f"{type(exc).__name__}: {exc}")

    # 8: LangGraph compatibility - same round trip through langchain-openai,
    # then execute the tool via LangGraph's ToolNode with a harmless mock.
    try:
        from langchain_core.messages import HumanMessage, ToolMessage
        from langgraph.prebuilt import ToolNode
        from langchain_core.tools import tool as lc_tool

        @lc_tool
        def get_current_utc_time(tz_label: str) -> str:
            """Return the current UTC time."""
            return json.dumps({"utc_time": "2026-11-03T12:00:00Z", "tz": tz_label})

        node = ToolNode([get_current_utc_time])
        from langchain_openai import ChatOpenAI
        say("starting: LangGraph tool-call round trip...")
        t0 = time.time()
        llm = ChatOpenAI(base_url=base, api_key=key, model=model,
                         temperature=0, max_retries=1, timeout=300).bind_tools([get_current_utc_time])
        ai_msg = llm.invoke([HumanMessage("Use the get_current_utc_time tool with tz_label 'UTC'.")])
        if getattr(ai_msg, "tool_calls", None):
            state = {"messages": [HumanMessage("Use the tool"), ai_msg]}
            after = node.invoke(state)
            tool_msgs = [m for m in after["messages"] if isinstance(m, ToolMessage)]
            check("LangGraph tool-call processing", "PASS" if tool_msgs and tool_msgs[0].content else "FAIL",
                  f"LangGraph ToolNode executed tool; tool_msg={tool_msgs[0].content[:80]!r}" if tool_msgs
                  else "ToolNode produced no ToolMessage")
        else:
            check("LangGraph tool-call processing", "FAIL",
                  f"langchain-openai returned no tool_calls; content={ai_msg.content[:80]!r}")
    except Exception as exc:
        check("LangGraph tool-call processing", "FAIL", f"{type(exc).__name__}: {exc}")

    # 9: error handling - invalid key -> expect 401/403; tiny timeout -> local timeout
    say("starting: error probes (invalid key, timeout)...")
    try:
        OpenAI(base_url=base, api_key=os.environ["NVIDIA_API_KEY"],
               timeout=15, max_retries=0).models.list()
        check("Error handling (invalid key)", "FAIL", "invalid key was accepted (unexpected)")
    except Exception as exc:
        code = getattr(exc, "status_code", None)
        check("Error handling (invalid key)", "PASS" if code in (401, 403) else "FAIL",
              f"status={code} type={type(exc).__name__}")
    try:
        OpenAI(base_url=base, api_key=key, timeout=0.001, max_retries=0).chat.completions.create(
            model=model, max_tokens=8, messages=[{"role": "user", "content": "OK"}])
        check("Error handling (timeout)", "FAIL", "impossibly small timeout did not raise")
    except Exception as exc:
        check("Error handling (timeout)", "PASS", f"raised {type(exc).__name__} as expected")

    # 10: rate limits/quotas - read headers/fields from a response, no deliberate triggering
    say("starting: rate-limit header inspection...")
    try:
        t0 = time.time()
        r = client.chat.completions.with_raw_response.create(
            model=model, max_tokens=8, messages=[{"role": "user", "content": "OK"}])
        hdrs = {k: v for k, v in r.headers.items()
                if any(t in k.lower() for t in ("ratelimit", "rate-limit", "x-", "retry"))}
        check("Rate limits / quota signals", "INFO" if hdrs else "INFO-NONE",
              (f"response headers: {hdrs} elapsed={time.time()-t0:.1f}s" if hdrs
               else f"no rate-limit headers exposed on this response elapsed={time.time()-t0:.1f}s"))
    except Exception as exc:
        check("Rate limits / quota signals", "INFO-NONE", f"{type(exc).__name__}: {exc}")

    write_results()


def write_results():
    out = ROOT / "docs/evidence/nvidia_nim_live_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                               "results": RESULTS}, indent=2), encoding="utf-8")
    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    print(f"\nSummary: {passed} PASS / "
          f"{sum(1 for r in RESULTS if r['status'] == 'FAIL')} FAIL / "
          f"{len(RESULTS)} checks. Evidence: {out}")


if __name__ == "__main__":
    main()