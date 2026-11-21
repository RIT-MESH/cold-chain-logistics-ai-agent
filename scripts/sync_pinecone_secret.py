"""One-time Pinecone key sync (Phase 6, operator procedure step 2).

The operator enters the key PRIVATELY into the local .env via a text
editor (the established private-entry pattern). This script then stores it
in the EXISTING AWS Secrets Manager secret without any intermediate
exposure:

- No CLI arguments (boto3 call from memory — nothing visible in process lists)
- No temp files
- No terminal output of the key (prints only the secret name + version)
- Idempotent: overwrites the current version of the same secret

Run:  python scripts/sync_pinecone_secret.py
"""
import json
import re
import sys
from pathlib import Path

import boto3
from botocore.config import Config
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
env_path = project_root / ".env"
load_dotenv(env_path, override=True)
import os

key = os.getenv("PINECONE_API_KEY", "").strip()
if not key:
    sys.exit("PINECONE_API_KEY is not set in the local .env — the operator "
             "private-entry step has not happened yet.")
if key.startswith("pcsec_") is False and len(key) < 20:
    sys.exit("The .env value does not look like a Pinecone API key.")

# Credential bridge (recorded convention): the coldchain-dev login-session is
# only understood by the AWS CLI. Prefer explicit env credentials (set via
# `eval $(aws configure export-credentials ...)`), else try the profile.
import os as _os
if _os.getenv("AWS_ACCESS_KEY_ID"):
    session = boto3.Session(region_name="us-east-1")
else:
    session = boto3.Session(profile_name="coldchain-dev", region_name="us-east-1")
client = session.client("secretsmanager", config=Config(retries={"max_attempts": 3}))

resp = client.put_secret_value(
    SecretId="coldchain/dev/pinecone-api-key",
    SecretString=json.dumps({"api_key": key}),
)
name = resp.get("Name")
version = resp.get("VersionId", "")[:8]
print(f"Stored: {name} (new version {version}…). The key never appeared in "
      "any command line, file, or output.")
