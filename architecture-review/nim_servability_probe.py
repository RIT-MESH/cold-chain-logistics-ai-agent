"""Task 1 — Servability verification of candidate NVIDIA NIM chat models.

The public GET /v1/models catalog demonstrably lists non-servable entries
(5/5 probes 404'd in the latency benchmark). This probe verifies ACTUAL
servability with authenticated chat-completion requests and classifies every
failure (404 / auth / rate limit / timeout / other).

Priority order = "fast tool-calling chat" rationale (documented in the
report). Probes stop once 3 servable models are confirmed or the candidate
list is exhausted. Each probe is one tiny request (max_tokens 256).
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
KEY = os.getenv("NVIDIA_API_KEY", "")
if not KEY:
    sys.exit("NVIDIA_API_KEY missing — aborting before any request.")
BASE_URL = "https://integrate.api.nvidia.com/v1"
OUT = ROOT / "docs/evidence/NVIDIA_NIM_MODEL_COMPARISON.json"

CANDIDATES = [
    # (model_id, rationale)
    ("z-ai/glm-5.3-flash", "flash tier of the GLM family; strong tool-calling heritage"),
    ("nvidia/nemotron-3.5-lightning-30b-a3b", "30B MoE with only 3B active params — built for speed"),
    ("openai/gpt-oss-20b", "open-weight, native function calling, designed for fast inference"),
    ("nvidia/nemotron-3.5-bolt-e4b", "small effective-size Nemotron, speed-oriented"),
    ("mistralai/mistral-large-2-instruct", "Mistral instruct line, documented tool calling"),
    ("nv-mistralai/mistral-nemo-12b-instruct", "small Mistral instruct, tool capable"),
    ("moonshotai/kimi-k2.6", "frontier chat model, strong agentic/tool support"),
    ("nvidia/llama-3.1-nemotron-70b-instruct", "known instruct Nemotron with tools"),
]

data = {
    "meta": {
        "date": "2026-11-08",
        "base_url": BASE_URL,
        "verification_method": "authenticated chat-completion requests (GET /v1/models is not servability proof — see latency benchmark)",
    },
    "servability": [],
    "comparison": {},
}
if OUT.exists():
    data = json.loads(OUT.read_text(encoding="utf-8"))

from openai import OpenAI

client = OpenAI(base_url=BASE_URL, api_key=KEY, timeout=120, max_retries=0)
servable = []

for model, rationale in CANDIDATES:
    rec = {"model": model, "rationale": rationale}
    t0 = time.perf_counter()
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with OK only."}],
            max_tokens=256,
        )
        dt = time.perf_counter() - t0
        u = r.usage
        content = (r.choices[0].message.content or "")
        det = getattr(u, "completion_tokens_details", None) if u else None
        rec.update({
            "servable": True,
            "status": "ok",
            "probe_total_s": round(dt, 2),
            "completion_tokens": u.completion_tokens if u else None,
            "reasoning_tokens": getattr(det, "reasoning_tokens", None) if det else None,
            "finish_reason": r.choices[0].finish_reason,
            "content_excerpt": content[:80],
        })
        servable.append(model)
        print(f"OK      {model}: {dt:.1f}s, reasoning={rec['reasoning_tokens']}, says: {content[:40]!r}")
    except Exception as exc:
        dt = time.perf_counter() - t0
        name = type(exc).__name__
        status = getattr(exc, "status_code", None)
        if status == 404 or "NotFound" in name:
            cls = "not_found_404"
        elif status in (401, 403) or "Auth" in name or "Permission" in name:
            cls = "authentication_failure"
        elif status == 429 or "RateLimit" in name:
            cls = "rate_limited"
        elif "Timeout" in name or "timeout" in str(exc).lower():
            cls = "timeout"
        else:
            cls = "other_error"
        rec.update({"servable": False, "status": cls, "http_code": status,
                    "error": f"{name}: {str(exc)[:150]}", "probe_total_s": round(dt, 2)})
        print(f"{cls:22s} {model} (http={status})")
    data["servability"].append(rec)
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    if len(servable) >= 3:
        print("\n3 servable models confirmed — stopping probes.")
        break

print(f"\nServable: {servable}")
