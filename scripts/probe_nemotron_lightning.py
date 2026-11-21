"""Single servability probe (operator Phase 6 directive §3): is
nvidia/nemotron-3.5-lightning-30b-a3b currently SERVABLE on our NIM account,
and at what trivial-turn latency? One call — not a benchmark."""
import os
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from openai import OpenAI

client = OpenAI(base_url="https://integrate.api.nvidia.com/v1",
                api_key=os.environ["NVIDIA_API_KEY"])
start = time.monotonic()
resp = client.chat.completions.create(
    model="nvidia/nemotron-3.5-lightning-30b-a3b",
    messages=[{"role": "user", "content": "Say OK"}],
    max_tokens=20, timeout=240)
elapsed = time.monotonic() - start
msg = resp.choices[0].message
extra = getattr(msg, "model_extra", None) or {}
print(f"HTTP OK | latency {elapsed:.1f}s | content: {str(msg.content)[:40]!r} "
      f"| reasoning_content present: {'reasoning_content' in extra}")
