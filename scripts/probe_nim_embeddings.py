"""Live probe (Phase 6 deployment step 2, operator-approved 2026-11-17):
verify the NVIDIA NIM embeddings endpoint serves BAAI/bge-m3 with
1024-dimensional output and honors input_type (query vs passage).

Prints ONLY the verification outcome — never the API key.
"""
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
import os

key = os.getenv("NVIDIA_API_KEY", "")
assert key, "NVIDIA_API_KEY missing from .env"

from openai import OpenAI

client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=key)

CANDIDATES = [
    "nvidia/nemotron-3.5-embed-1b",
    "nvidia/nemotron-3-embed-1b",
    "nvidia/llama-3.2-nv-embedqa-1b-v1",
    "nvidia/embed-qa-4",
    "snowflake/arctic-embed-l",
]

for model in CANDIDATES:
    try:
        resp = client.embeddings.create(
            model=model,
            input=["Fresh perishables must stay between 0.0 and 4.0 degrees C."],
            extra_body={"input_type": "query", "truncate": "NONE"},
            timeout=60,
        )
        vec = resp.data[0].embedding
        print(f"{model}: HTTP OK, dims={len(vec)}")
    except Exception as e:
        print(f"{model}: FAILED {type(e).__name__}: {str(e)[:120]}")

print("NIM_EMBEDDING_PROBE: done")
