"""Follow-up probes for the latency benchmark (3-4 requests max):
1. One live LangChain invoke to locate reasoning tokens in message metadata
   (usage_metadata vs response_metadata.token_usage) — the gateway C12
   counter reads usage_metadata, which showed reasoning_tokens=0 in the
   baseline while direct API probes showed ~110-200. Where do they live?
2-3. Alternative-model single-turn latency probes chosen from the LIVE
   catalog (the harness's preferred candidates all 404'd)."""
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
KEY = os.getenv("NVIDIA_API_KEY", "")
BASE_URL = "https://integrate.api.nvidia.com/v1"
MODEL = os.getenv("NVIDIA_MODEL", "deepseek-ai/deepseek-v4.1-flash")
OUT = ROOT / "docs/evidence/NVIDIA_NIM_LATENCY_RESULTS.json"
data = json.loads(OUT.read_text(encoding="utf-8"))

# ---- Probe 1: LangChain metadata locations (1 live request) ----
from src.orchestrator import create_llm
from langchain_core.messages import HumanMessage

llm = create_llm()
t0 = time.perf_counter()
resp = llm.invoke([HumanMessage(content="In one sentence, what is cold-chain logistics?")])
dt = time.perf_counter() - t0
um = getattr(resp, "usage_metadata", None)
rm = getattr(resp, "response_metadata", None) or {}
tu = rm.get("token_usage") if isinstance(rm, dict) else None
tu = tu or {}
det = tu.get("completion_tokens_details") or {}
probe = {
    "purpose": "locate reasoning tokens in LangChain message metadata (gateway C12 counter reads usage_metadata)",
    "duration_s": round(dt, 2),
    "usage_metadata": dict(um) if um else None,
    "response_metadata_keys": sorted(rm.keys()) if isinstance(rm, dict) else None,
    "response_metadata_token_usage": {
        "prompt_tokens": tu.get("prompt_tokens"),
        "completion_tokens": tu.get("completion_tokens"),
        "total_tokens": tu.get("total_tokens"),
    },
    "completion_tokens_details": {
        "reasoning_tokens": det.get("reasoning_tokens"),
        "raw": str(det)[:200],
    },
}
print(json.dumps(probe, indent=2, default=str))
data["experiments"]["langchain_usage_metadata_probe"] = probe

# ---- Probes 2-3: alternative models from the LIVE catalog ----
from openai import OpenAI

client = OpenAI(base_url=BASE_URL, api_key=KEY, timeout=120, max_retries=1)
ids = [m.id for m in client.models.list().data]
data["meta"]["models_catalog_ids"] = ids  # public, unauthenticated listing
prefer = [m for m in ids if re.search(r"(llama|qwen|mistral|gemma|phi|nemotron)", m, re.I) and "instruct" in m.lower()]
probes = []
for cand in prefer[:3]:
    if len([p for p in probes if p.get("status") == "ok"]) >= 2:
        break
    try:
        t0 = time.perf_counter()
        r = client.chat.completions.create(
            model=cand,
            messages=[{"role": "user", "content": "Reply with OK only."}],
            max_tokens=64,
        )
        u = r.usage
        probes.append({
            "model": cand, "status": "ok",
            "total_s": round(time.perf_counter() - t0, 2),
            "completion_tokens": u.completion_tokens if u else None,
            "content": (r.choices[0].message.content or "")[:80],
            "note": "trivial single turn; tool-calling NOT verified for this model",
        })
        print("alt ok:", cand, probes[-1]["total_s"], "s")
    except Exception as exc:
        probes.append({"model": cand, "status": "error", "error": str(exc)[:150]})
        print("alt fail:", cand)
data["experiments"]["alternative_models"]["followup_probes"] = probes

OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
print("saved ->", OUT)
