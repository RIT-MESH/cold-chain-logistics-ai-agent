"""Operator-private Pinecone key entry (Phase 6).

Run in YOUR terminal:   python scripts/enter_pinecone_key.py

- The prompt uses getpass: what you type/paste is NOT echoed, not recorded
  in terminal output, not in conversation logs, not in any command line.
- Writes ONLY the local .env PINECONE_API_KEY line (gitignored).
- Verifies by equality + shape, prints only the LENGTH — never the key.
"""
import getpass
import pathlib
import re

key = getpass.getpass("Paste/type your Pinecone API key (input is hidden): ").strip()
if not (key.startswith("pcsk_") and len(key) > 40):
    raise SystemExit("That does not look like a Pinecone API key (expected pcsk_…). Nothing was written.")

env_path = pathlib.Path(__file__).resolve().parents[1] / ".env"
text = env_path.read_text(encoding="utf-8")
line = f"PINECONE_API_KEY={key}"
if re.search(r"(?m)^PINECONE_API_KEY=.*$", text):
    text = re.sub(r"(?m)^PINECONE_API_KEY=.*$", line, text)
else:
    text = text.rstrip("\n") + "\n" + line + "\n"
env_path.write_text(text, encoding="utf-8")

from dotenv import dotenv_values  # verify by equality only, never print
stored = dotenv_values(env_path).get("PINECONE_API_KEY")
assert stored == key, "verification failed"
print(f"Key stored in local .env (verified by equality; never displayed). Length: {len(key)}")
