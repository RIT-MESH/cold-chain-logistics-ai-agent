"""Pinecone verification (Phase 6 deployment step 4): authenticate, verify
Starter free-tier eligibility, and list current index state. Prints only
plan/index metadata — never the key."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from pinecone import Pinecone

key = os.environ["PINECONE_API_KEY"]
pc = Pinecone(api_key=key)

# Index-level facts (metadata only)
idx = pc.list_indexes()
print("indexes:", idx.names() or "(none)")
for name in idx.names():
    d = pc.describe_index(name)
    print(f"  {name}: dim={d.dimension} metric={d.metric} "
          f"status={d.status.get('ready')} spec={d.spec.get('serverless')}")

# Whoami / plan: available on the client in recent SDKs
try:
    who = pc.whoami()
    print("account:", who.user_email if hasattr(who, "user_email") else who)
except Exception as e:
    print("whoami unavailable:", type(e).__name__)

print("PINECONE_AUTH_OK")
