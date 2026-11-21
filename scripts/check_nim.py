"""Opt-in billed connectivity smoke test: python scripts/check_nim.py

NVIDIA NIM is the application's only runtime LLM (operator decision
2026-11-08, blueprint R6-1; production model z-ai/glm-5.3-flash adopted
2026-11-14, blueprint P-8). Uses ONE small billed request against
https://integrate.api.nvidia.com/v1. Note: GET /v1/models is UNauthenticated
(verification F-2), so a models listing is NOT proof of valid credentials —
this chat probe is.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
key = os.getenv("NVIDIA_API_KEY")
if not key:
    sys.exit("Set NVIDIA_API_KEY in your private .env or environment first")
from openai import OpenAI
client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=key, timeout=180, max_retries=1)
try:
    result = client.chat.completions.create(
        model=os.getenv("NVIDIA_MODEL", "z-ai/glm-5.3-flash"),
        messages=[{"role": "user", "content": "Reply with OK only."}],
        # 512, not 32: reasoning tokens consume this budget and a small cap
        # returns EMPTY content with finish_reason=length (verification F-3).
        max_tokens=512,
    )
    content = result.choices[0].message.content
    if not content:
        sys.exit(
            f"NIM returned empty content (finish_reason={result.choices[0].finish_reason}); "
            "max_tokens is too small for the reasoning budget — do not lower NVIDIA_MAX_OUTPUT_TOKENS"
        )
    usage = getattr(result, "usage", None)
    print("NIM model response:", content)
    if usage:
        print("token usage:", usage.prompt_tokens, "prompt /", usage.completion_tokens, "completion")
except Exception as exc:
    sys.exit(f"NVIDIA NIM check failed ({type(exc).__name__}). Check access, key, model and quota (40 RPM org-wide).")
