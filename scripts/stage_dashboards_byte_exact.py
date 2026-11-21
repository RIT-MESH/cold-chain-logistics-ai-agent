"""Byte-exact staging for the Phase 7 dashboard-repair push (see
stage_byte_exact.py for the root-cause context: PS 5.1 Get-Content -Raw
ANSI-decodes BOM-less files — never stage text through PowerShell)."""
import base64
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "scripts/generate_grafana_dashboards.py",
    "integrations/grafana/dashboards/cold-chain-unified.json",
    "integrations/grafana/dashboards/cold-chain-temp-humidity.json",
    "integrations/grafana/dashboards/cold-chain-logistics-custody.json",
    "integrations/grafana/dashboards/cold-chain-infra-ai.json",
    "integrations/grafana/dashboards/agent-audit.json",
]
MOJIBAKE = ["ðŸ", "â€", "Ã¢", "Ã‚"]


def main():
    stage = Path(tempfile.mkdtemp(prefix="p7dash-"))
    for rel in FILES:
        data = (ROOT / rel).read_bytes()
        text = data.decode("utf-8")
        for bad in MOJIBAKE:
            assert bad not in text, f"{rel} contains {bad!r}"
        dest = stage / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    tar_path = Path(tempfile.gettempdir()) / "p7dash.tar.gz"
    subprocess.run(["tar", "-czf", str(tar_path), "-C", str(stage), "."], check=True)
    b64 = base64.b64encode(tar_path.read_bytes()).decode("ascii")
    Path(tempfile.gettempdir(), "p7dash.b64").write_text(b64, encoding="ascii")
    manifest = {rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() for rel in FILES}
    Path(tempfile.gettempdir(), "p7dash_manifest.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"staged {len(FILES)} files byte-exact; b64 {len(b64)} chars")


if __name__ == "__main__":
    main()
