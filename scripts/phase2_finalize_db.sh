#!/bin/bash
# phase2_finalize_db.sh — finalizes a Phase 2 database whose import and
# grants already succeeded. Runs ON the host via SSM (C7). Three jobs:
#
# 1. OWNERSHIP CHAINING CONVERGENCE: the canonical import script creates the
#    [unified] objects as the TEMP IMPORT ADMIN, while setup_unified_views
#    creates the FDE_VIEWS views as the master user — different owners break
#    the view->base-table ownership chain that the view-only identities
#    (USR_FDE_RO etc.) depend on (found live: "SELECT permission denied on
#    object 'telemetry', schema 'unified'" for a user WITH SELECT on the
#    view — retry 6). Converge ALL project objects + both schemas to dbo.
# 2. Live guard-pattern verification as USR_FDE_RO (credential fetched from
#    the coldchain/dev/sql-agent-ro container — seeded by the pipeline).
# 3. Drop the temp import admin (ADR-4) — safe AFTER the ownership transfer.
#
# Usage: phase2_finalize_db.sh <RDS_ENDPOINT> <MASTER_SECRET_ARN>
set -euo pipefail

RDS_ENDPOINT="$1"; MASTER_SECRET_ARN="$2"
ENVIRONMENT="dev"
WORK=/opt/coldchain
LOG="$WORK/phase2_finalize.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== phase2_finalize_db started $(date -u +%FT%TZ) ==="

unset MASTER_PASS MASTER_USER AGENT_JSON AGENT_CS || true

# ---------------------------------------------------------------- creds (memory only)
export PATH="$PATH:/opt/mssql-tools18/bin"
MASTER_JSON=$(aws secretsmanager get-secret-value --secret-id "$MASTER_SECRET_ARN" --query SecretString --output text)
MASTER_USER=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['username'])" "$MASTER_JSON")
MASTER_PASS=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['password'])" "$MASTER_JSON")
unset MASTER_JSON
AGENT_JSON=$(aws secretsmanager get-secret-value --secret-id "coldchain/$ENVIRONMENT/sql-agent-ro" --query SecretString --output text)
AGENT_USER=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['username'])" "$AGENT_JSON")
AGENT_PASS=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['password'])" "$AGENT_JSON")
unset AGENT_JSON
SQLCMD_BASE=(sqlcmd -S "$RDS_ENDPOINT" -U "$MASTER_USER" -P "$MASTER_PASS" -C -b -I)

# ---------------------------------------------------------------- 1. ownership -> dbo
echo "--- [1/3] ownership chaining convergence (objects + schemas -> dbo) ---"
OWN_SQL="$WORK/scripts/_ownership.generated.sql"
{
  echo "USE ColdChainLogistics;"
  echo "GO"
  echo "ALTER AUTHORIZATION ON SCHEMA::[unified] TO dbo;"
  echo "GO"
  echo "ALTER AUTHORIZATION ON SCHEMA::[FDE_VIEWS] TO dbo;"
  echo "GO"
  for t in vehicles shipments sensors telemetry custody_handoffs alerts route_events v_shipment_summary v_sensor_metrics; do
    echo "IF OBJECT_ID(N'[unified].[$t]') IS NOT NULL ALTER AUTHORIZATION ON OBJECT::[unified].[$t] TO dbo;"
    echo "GO"
  done
  for v in VW_SHIPMENTS VW_TELEMETRY VW_CUSTODY VW_ALERTS VW_SENSORS VW_VEHICLES VW_ROUTES VW_TELEMETRY_EVENTS VW_TELEMETRY_INCIDENTS; do
    echo "IF OBJECT_ID(N'[FDE_VIEWS].[$v]') IS NOT NULL ALTER AUTHORIZATION ON OBJECT::[FDE_VIEWS].[$v] TO dbo;"
    echo "GO"
  done
} > "$OWN_SQL"
"${SQLCMD_BASE[@]}" -i "$OWN_SQL"
rm -f "$OWN_SQL"
echo "  ownership converged to dbo"

# ---------------------------------------------------------------- 1b. re-EXECUTE grants (AFTER ownership transfer)
# Live-found: the schema-scoped SELECT grants do not survive the schema
# ownership change — re-apply the EXECUTED grants (idempotent) so the
# view-only identities regain FDE_VIEWS access, then verify again.
echo "--- [1b/3] re-EXECUTE grants after ownership convergence ---"
"${SQLCMD_BASE[@]}" -d ColdChainLogistics -i "$WORK/scripts/phase2_executed_grants.sql"
"${SQLCMD_BASE[@]}" -d ColdChainLogistics -i "$WORK/scripts/verify_grants.sql" -o "$WORK/verify_grants_output.txt" || true
sed -n '1,200p' "$WORK/verify_grants_output.txt"

# ---------------------------------------------------------------- 2. live guard-pattern query
echo "--- [2/3] live view query as $AGENT_USER (guard-validated pattern) ---"
AGENT_CS="DRIVER={ODBC Driver 18 for SQL Server};SERVER=$RDS_ENDPOINT;DATABASE=ColdChainLogistics;UID=$AGENT_USER;PWD=$AGENT_PASS;Encrypt=yes;TrustServerCertificate=yes"
AGENT_CS="$AGENT_CS" python3 - <<'PYEOF'
import os, pyodbc
conn = pyodbc.connect(os.environ['AGENT_CS'], timeout=30)
rows = conn.execute("SELECT TOP 5 shipment_id, recorded_at, temperature_c FROM FDE_VIEWS.VW_TELEMETRY ORDER BY recorded_at").fetchall()
print("  live VW_TELEMETRY sample:", [(r[0], str(r[1]), r[2]) for r in rows[:2]], "...", f"({len(rows)} rows)")
denied = False
try:
    conn.execute("SELECT TOP 1 vehicle_id FROM [unified].[vehicles]").fetchone()
except pyodbc.Error:
    denied = True
print("  base-table access correctly DENIED to the agent identity:", denied)
PYEOF

# ---------------------------------------------------------------- 3. drop temp import admin
echo "--- [3/3] drop temp import admin (ADR-4) ---"
"${SQLCMD_BASE[@]}" -Q "IF SUSER_ID(N'USR_IMPORT_ADMIN') IS NOT NULL BEGIN DROP USER [USR_IMPORT_ADMIN]; DROP LOGIN [USR_IMPORT_ADMIN]; END" -d ColdChainLogistics
echo "  temp import admin dropped"

unset MASTER_PASS MASTER_USER AGENT_PASS AGENT_USER AGENT_CS
echo "=== phase2_finalize_db COMPLETED $(date -u +%FT%TZ) ==="
