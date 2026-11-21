#!/bin/bash
# fetch-secrets.sh — runtime secret injection (blueprint 01 §5.3, C8/ADR-5).
# Runs ON the host with the instance profile; writes the compose env file to
# the tmpfs /run/coldchain (0600, wiped on reboot; docker never receives
# secrets as build args, so nothing lands in image layers).
#
# Usage: fetch-secrets.sh <RDS_ENDPOINT> [ENVIRONMENT=dev]
set -euo pipefail
RDS_ENDPOINT="$1"
ENVIRONMENT="${2:-dev}"
PUBLIC_ROOT="${3:-}"
RUN_DIR=/run/coldchain
ENV_FILE="$RUN_DIR/app.env"

mkdir -p "$RUN_DIR"
mountpoint -q "$RUN_DIR" || mount -t tmpfs -o size=1m,mode=0700 tmpfs "$RUN_DIR"
install -m 0600 /dev/null "$ENV_FILE"

fetch() { aws secretsmanager get-secret-value --secret-id "coldchain/$ENVIRONMENT/$1" --query SecretString --output text; }

# External-provider + application secrets (values seeded out-of-band).
NVIDIA_JSON=$(fetch nvidia-api-key)
FDE_JSON=$(fetch fde-api-key)
PINECONE_JSON=$(fetch pinecone-api-key) || PINECONE_JSON='{"api_key":""}' # SOP search stays unconfigured until Phase 6

# SQL identity secrets ({username, password} pairs seeded by the Phase 2 pipeline).
AGENT_JSON=$(fetch sql-agent-ro)
UI_JSON=$(fetch sql-ui-ro)
GRAFANA_JSON=$(fetch sql-grafana-ro)
INCIDENT_JSON=$(fetch sql-incident-writer)

emit() { printf '%s=%q\n' "$1" "$2" >> "$ENV_FILE"; }

{
  echo "# GENERATED at $(date -u +%FT%TZ) by fetch-secrets.sh — tmpfs, 0600, never on disk"
} > "$ENV_FILE"
emit LLM_PROVIDER NVIDIA_NIM
emit NVIDIA_API_KEY "$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['api_key'])" "$NVIDIA_JSON")"
emit NVIDIA_MODEL z-ai/glm-5.3-flash
emit NVIDIA_BASE_URL https://integrate.api.nvidia.com/v1
# Per-turn timeout: 180 s default was sized from 2026-11-03 deepseek turns
# (47-79 s); glm-5.3-flash qualification measured single turns up to
# ~205 s — the first acceptance attempt on the host timed out at 180 s
# (live-found). 300 s with the existing 2 retries covers observed variance.
emit NVIDIA_REQUEST_TIMEOUT_SECONDS 300
emit FDE_API_KEY "$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['api_key'])" "$FDE_JSON")"
emit PINECONE_API_KEY "$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('api_key',''))" "$PINECONE_JSON")"
emit UNIFIED_DATA_BACKEND mssql
emit SQL_SERVER_HOST "$RDS_ENDPOINT"
emit SQL_SERVER_PORT 1433
emit SQL_DATABASE ColdChainLogistics
emit SQL_AGENT_USER "$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['username'])" "$AGENT_JSON")"
emit SQL_AGENT_PASSWORD "$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['password'])" "$AGENT_JSON")"
emit SQL_UI_USER "$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['username'])" "$UI_JSON")"
emit SQL_UI_PASSWORD "$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['password'])" "$UI_JSON")"
emit SQL_GRAFANA_USER "$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['username'])" "$GRAFANA_JSON")"
emit SQL_GRAFANA_PASSWORD "$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['password'])" "$GRAFANA_JSON")"
emit GRAFANA_ADMIN_USER admin
emit GRAFANA_ADMIN_PASSWORD "$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)"
emit SQL_INCIDENT_WRITER_USER "$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['username'])" "$INCIDENT_JSON")"
emit SQL_INCIDENT_WRITER_PASSWORD "$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['password'])" "$INCIDENT_JSON")"
emit SQL_ENCRYPT yes
emit SQL_TRUST_SERVER_CERTIFICATE no
# Phase 4 event plane (2026-11-17): resolve the Terraform-managed queue/DLQ
# URLs and topic ARN so the worker/reconcile never create AWS resources at
# runtime. Tolerant of pre-apply absence: empty values fall back to
# name-based resolution (Floci) or a fail-closed startup check (AWS worker).
# sqs:GetQueueUrl + sts:GetCallerIdentity (no-permission action) only.
QUEUE_URL=$(aws sqs get-queue-url --queue-name "coldchain-$ENVIRONMENT-telemetry" --query QueueUrl --output text 2>/dev/null || true)
DLQ_URL=$(aws sqs get-queue-url --queue-name "coldchain-$ENVIRONMENT-telemetry-dlq" --query QueueUrl --output text 2>/dev/null || true)
AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)
AWS_REGION_EFFECTIVE=$(aws configure get region 2>/dev/null || echo us-east-1)
emit TELEMETRY_QUEUE_URL "$QUEUE_URL"
emit TELEMETRY_DLQ_URL "$DLQ_URL"
emit TELEMETRY_ALERT_TOPIC_ARN "arn:aws:sns:${AWS_REGION_EFFECTIVE}:${AWS_ACCOUNT}:coldchain-$ENVIRONMENT-alerts"
emit TELEMETRY_PERSIST_SQL true
emit TELEMETRY_VALIDATE_LINKAGE true
# LibreChat server secrets (required: "JwtStrategy requires a secret or key"
# — crash-looped without them, live-found 2026-11-15). Generated per run:
# sessions reset on each fetch (acceptable for the temp deployment).
# Phase 3b: public root for Grafana sub-path links (empty until the
# CloudFront domain exists; set on the second roll).
emit GRAFANA_ROOT_URL "https://$PUBLIC_ROOT"
# Phase 3b Option B: oauth2-proxy OIDC gate (Streamlit path). The Cognito
# client secret is NOT retrievable via API after creation — created ONCE
# via CLI and seeded here (out-of-band, never in Terraform state).
# Emitted under their OAUTH2_PROXY_* names: compose ${VAR} interpolation
# cannot read env_file values (live-found crash loop 2026-11-15), so the
# container must receive them directly via env_file.
if COGNITO_JSON=$(fetch cognito-client 2>/dev/null); then
  emit OAUTH2_PROXY_CLIENT_ID "$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['client_id'])" "$COGNITO_JSON")"
  emit OAUTH2_PROXY_CLIENT_SECRET "$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['client_secret'])" "$COGNITO_JSON")"
else
  echo "WARNING: coldchain/dev/cognito-client not seeded yet — oauth2-proxy will not start."
fi
emit OAUTH2_PROXY_COOKIE_SECRET "$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)"
emit OAUTH2_PROXY_REDIRECT_URL "https://$PUBLIC_ROOT/oauth2/callback"
emit OAUTH2_PROXY_COOKIE_DOMAINS "$PUBLIC_ROOT"
emit JWT_SECRET "$(openssl rand -hex 32)"
# LIVE-FOUND 2026-11-16: LibreChat (recent versions) requires a separate
# JWT_REFRESH_SECRET for refresh-token sessions — without it, login fails
# with "generateRefreshToken: secretOrPrivateKey must have a value".
emit JWT_REFRESH_SECRET "$(openssl rand -hex 32)"
emit CREDS_KEY "$(openssl rand -hex 32)"
emit CREDS_IV "$(openssl rand -hex 16)"

chmod 0600 "$ENV_FILE"
echo "secret env written to tmpfs $ENV_FILE ($(wc -l < "$ENV_FILE") lines; values never printed)"
