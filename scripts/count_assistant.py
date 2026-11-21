"""Count assistant capabilities for the Phase 8 report."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
js = (ROOT / "hf-space" / "assistant.js").read_text(encoding="utf-8")
data = (ROOT / "hf-space" / "dataset.js").read_text(encoding="utf-8")

intents = sorted(set(re.findall(r'(\w+):\s*/', js)))
gens = re.findall(r'function gen(\w+)', js)
sug_block = js.split("SUGGESTIONS")[1].split("];")[0]
suggestions = re.findall(r'"([^"]+)"', sug_block)
m = re.search(r'meta:\{shipments:(\d+),telemetry:(\d+),alerts:(\d+),custody:(\d+),sops:(\d+),detailed_shipments:(\d+)', data)

print(f"Intent types: {len(intents)}")
print(f"  {intents}")
print(f"Response generators: {len(gens)}")
print(f"  {gens}")
print(f"Suggestion chips: {len(suggestions)}")
if m:
    print(f"Dataset: {m.group(1)} shipments, {m.group(2)} telemetry, "
          f"{m.group(3)} alerts, {m.group(4)} custody, "
          f"{m.group(5)} SOPs, {m.group(6)} detailed-shipment charts")
sec_count = data.count('"title"') if '"title"' in data else data.count('"id"')
print(f"SOP section references: {sec_count}")
