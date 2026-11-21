"""Byte-exact file staging (Phase 7 dashboard repair, 2026-11-18).

ROOT-CAUSE CONTEXT: the previous PowerShell push pipeline used
`Get-Content -Raw`, which under PS 5.1 decodes BOM-less files as ANSI/cp1252
— every non-ASCII character (🧊, 🚪, em-dashes, °C, §) in every file pushed
that way was DOUBLE-ENCODED in the deployed containers (sidebar showed
'ðŸ§Š', dashboards showed 'â€'). This script stages files for the host push
using BYTE-LEVEL copies only ([IO.File]::Copy + tar bytes) — no text
decoding anywhere — so the deployed bytes are identical to the repository.

Run from the project root; writes the tarball + base64 for the SSM push.
"""
import base64
import hashlib
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FILES = [
    "src/ui.py",
    "src/agent_tools.py",
    "src/audit.py",
    "src/sop_embeddings.py",
    "src/evidence_prefetch.py",
    "src/sql_guard.py",
    "src/answer_validator.py",
    "api/main.py",
    "src/prompts/system_prompt.txt",
    "docker/compose.yml",
    "docker/fetch-secrets.sh",
    "scripts/ingest_sop_pinecone.py",
    "integrations/grafana/dashboards/agent-audit.json",
    "integrations/grafana/dashboards/telemetry-incidents.json",
]

MOJIBAKE = ["ðŸ", "â€", "Ã¢", "Ã‚", "Â "]


def main():
    stage = Path(tempfile.mkdtemp(prefix="p7byte-"))
    for rel in FILES:
        src = ROOT / rel
        data = src.read_bytes()
        # repo-side guard: the repository must be CLEAN of mojibake
        text = data.decode("utf-8")
        for bad in MOJIBAKE:
            assert bad not in text, f"repo file {rel} contains {bad!r} — fix the source first"
        dest = stage / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)  # BYTE copy — no encoding conversion

    tar_path = Path(tempfile.gettempdir()) / "p7byte.tar.gz"
    subprocess.run(["tar", "-czf", str(tar_path), "-C", str(stage), "."], check=True)

    b64 = base64.b64encode(tar_path.read_bytes()).decode("ascii")
    b64_path = Path(tempfile.gettempdir()) / "p7byte.b64"
    b64_path.write_text(b64, encoding="ascii")

    # manifest for verification: file -> sha256 of the REPO bytes
    manifest = {rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() for rel in FILES}
    manifest_path = Path(tempfile.gettempdir()) / "p7byte_manifest.json"
    manifest_path.write_text(__import__("json").dumps(manifest, indent=1), encoding="utf-8")

    print(f"staged {len(FILES)} files; tar {tar_path.stat().st_size} bytes; "
          f"b64 {len(b64)} chars; manifest at {manifest_path}")
    for rel, digest in manifest.items():
        print(f"  {digest[:16]}… {rel}")


if __name__ == "__main__":
    main()
