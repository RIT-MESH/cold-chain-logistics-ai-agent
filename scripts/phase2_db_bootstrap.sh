#!/bin/bash
# phase2_db_bootstrap.sh — runs ON the EC2 app host via SSM RunCommand (C7/
# ADR-16). Executes the complete Phase 2 database deployment:
#   deps -> files -> master secret -> EMPTY database -> identities ->
#   import (canonical script applies schema + loads CSVs) ->
#   views + telemetry -> EXECUTED grants (ADR-4) -> verify_grants.sql ->
#   manifest count verification -> live view query as USR_FDE_RO ->
#   temp import admin dropped (ADR-4).
#
# Ordering notes (found live, retries 1-4 documented in AUDIT_LOG):
#   - The canonical import script applies sql_server_schema.sql ITSELF
#     statement-by-statement (satisfying the CREATE VIEW batch rule) — never
#     pre-apply the schema (error 2714).
#   - Views/telemetry must come AFTER the import (they select from the
#     unified tables); grants must come AFTER views (they grant ON them).
#   - Identities (logins/users) have no object dependencies and must come
#     BEFORE the import (the temp import admin performs the import).
#
# Secrets handling (C8/ADR-5): the master password is fetched at runtime via
# the instance profile; ALL generated passwords live only in process memory
# until they are seeded into the coldchain/dev/sql-* containers via
# PutSecretValue, and are cleared before exit. Nothing is printed or logged.
#
# Usage: phase2_db_bootstrap.sh <PRESIGNED_BUNDLE_URL> <RDS_ENDPOINT> <MASTER_SECRET_ARN>
set -euo pipefail

PRESIGNED_BUNDLE_URL="$1"; RDS_ENDPOINT="$2"; MASTER_SECRET_ARN="$3"
ENVIRONMENT="dev"
WORK=/opt/coldchain
LOG="$WORK/phase2_db.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== phase2_db_bootstrap started $(date -u +%FT%TZ) ==="

declare -A PW

cleanup_secrets() {
  for u in "${!PW[@]}"; do PW[$u]=""; done
  unset MASTER_PASS MASTER_USER MASTER_CS IMPORT_CS AGENT_CS VERIFY_CS
}
trap cleanup_secrets EXIT

# ---------------------------------------------------------------- 1. deps
echo "--- [1/9] dependencies ---"
# AL2023 ships curl-minimal (conflicts with the full curl package) — do NOT
# install curl. aws-cli name varies; fall back to pip if dnf lacks it.
dnf install -y tar openssl python3-pip unixODBC >/dev/null
dnf install -y aws-cli >/dev/null 2>&1 || python3 -m pip install --quiet awscli
curl -sSL https://packages.microsoft.com/config/rhel/9/prod.repo -o /etc/yum.repos.d/msprod.repo
ACCEPT_EULA=Y dnf install -y msodbcsql18 mssql-tools18 >/dev/null
python3 -m pip install --quiet pyodbc
export PATH="$PATH:/opt/mssql-tools18/bin"
sqlcmd -? >/dev/null

# ---------------------------------------------------------------- 2. files
echo "--- [2/9] bundle ---"
mkdir -p "$WORK"
curl -sSL --fail "$PRESIGNED_BUNDLE_URL" -o "$WORK/bundle.tar.gz"
tar -xzf "$WORK/bundle.tar.gz" -C "$WORK"
ls "$WORK/scripts" >/dev/null && ls "$WORK/data/unified" >/dev/null

# ---------------------------------------------------------------- 3. master secret
echo "--- [3/9] RDS master secret (instance profile, memory only) ---"
MASTER_JSON=$(aws secretsmanager get-secret-value --secret-id "$MASTER_SECRET_ARN" --query SecretString --output text)
MASTER_USER=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['username'])" "$MASTER_JSON")
MASTER_PASS=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['password'])" "$MASTER_JSON")
unset MASTER_JSON
SQLCMD_BASE=(sqlcmd -S "$RDS_ENDPOINT" -U "$MASTER_USER" -P "$MASTER_PASS" -C -b -I)

# ---------------------------------------------------------------- 4. empty database
echo "--- [4/9] empty database (data-safety guarded) ---"
# If the DB exists with unified tables, only proceed on a fresh drop when it
# holds NO rows (a previous partial run); abort if any data exists.
HAS_DATA=$("${SQLCMD_BASE[@]}" -d ColdChainLogistics -Q "SET NOCOUNT ON; IF OBJECT_ID('unified.shipments','U') IS NOT NULL AND EXISTS(SELECT 1 FROM unified.shipments) SELECT 1 ELSE SELECT 0" -W 2>/dev/null | tr -d '[:space:]' || echo 0)
if [ "$HAS_DATA" = "1" ]; then
  echo "ABORT: unified.shipments already holds data — refusing to drop the database."
  exit 1
fi
"${SQLCMD_BASE[@]}" -Q "IF DB_ID(N'ColdChainLogistics') IS NOT NULL DROP DATABASE ColdChainLogistics;"
"${SQLCMD_BASE[@]}" -Q "IF DB_ID(N'ColdChainLogistics') IS NULL CREATE DATABASE ColdChainLogistics;"

# ---------------------------------------------------------------- 5. identities
echo "--- [5/9] identities with generated passwords (never printed) ---"
genpw() { openssl rand -base64 32 | tr -d '/+=' | head -c 40; }
for u in FDE_RO COLDCHAIN_UI_RO GRAFANA_RO INCIDENT_WRITER AUDIT_WRITER IMPORT_ADMIN; do
  PW[$u]="$(genpw)"
done
IDENT_SQL="$WORK/scripts/_identities.generated.sql"
{
  echo "USE master;"
  # CREATE-if-missing then ALWAYS ALTER: DROP DATABASE removes users but
  # NOT server-level logins — without the ALTER, a retry would generate new
  # passwords against stale logins (found live: Login failed 18456, retry 5).
  for u in FDE_RO COLDCHAIN_UI_RO GRAFANA_RO INCIDENT_WRITER AUDIT_WRITER IMPORT_ADMIN; do
    echo "IF SUSER_ID(N'USR_$u') IS NULL CREATE LOGIN [USR_$u] WITH PASSWORD=N'${PW[$u]}';"
    echo "ALTER LOGIN [USR_$u] WITH PASSWORD=N'${PW[$u]}';"
  done
  echo "USE ColdChainLogistics;"
  for u in FDE_RO COLDCHAIN_UI_RO GRAFANA_RO INCIDENT_WRITER AUDIT_WRITER IMPORT_ADMIN; do
    echo "IF USER_ID(N'USR_$u') IS NULL CREATE USER [USR_$u] FOR LOGIN [USR_$u];"
  done
  echo "ALTER ROLE db_owner ADD MEMBER USR_IMPORT_ADMIN;" # temp; dropped in step 9 (ADR-4)
} > "$IDENT_SQL"
"${SQLCMD_BASE[@]}" -i "$IDENT_SQL"
rm -f "$IDENT_SQL" # contained generated passwords — remove immediately

seed() { # seed <container-suffix> <identity-suffix>
  python3 -c "import json,sys; print(json.dumps({'username': 'USR_' + sys.argv[1], 'password': sys.argv[2]}))" "$2" "${PW[$2]}" \
    | aws secretsmanager put-secret-value --secret-id "coldchain/$ENVIRONMENT/$1" --secret-string file:///dev/stdin >/dev/null
}
seed sql-agent-ro        FDE_RO
seed sql-ui-ro           COLDCHAIN_UI_RO
seed sql-grafana-ro      GRAFANA_RO
seed sql-incident-writer INCIDENT_WRITER
seed sql-audit-writer    AUDIT_WRITER

# ---------------------------------------------------------------- 6. import (canonical script applies the schema itself, then loads CSVs)
echo "--- [6/9] dataset import as temp import admin ---"
IMPORT_CS="DRIVER={ODBC Driver 18 for SQL Server};SERVER=$RDS_ENDPOINT;DATABASE=ColdChainLogistics;UID=USR_IMPORT_ADMIN;PWD=${PW[IMPORT_ADMIN]};Encrypt=yes;TrustServerCertificate=yes"
SQLSERVER_CONNECTION_STRING="$IMPORT_CS" python3 "$WORK/scripts/import_unified.py"

# ---------------------------------------------------------------- 7. views + telemetry (AFTER tables exist)
echo "--- [7/9] FDE_VIEWS + telemetry objects ---"
"${SQLCMD_BASE[@]}" -d ColdChainLogistics -i "$WORK/scripts/setup_unified_views.sql"
"${SQLCMD_BASE[@]}" -d ColdChainLogistics -i "$WORK/scripts/setup_telemetry.sql"

# ---------------------------------------------------------------- 8. EXECUTED grants + verify
echo "--- [8/9] EXECUTED grants (ADR-4) + verify_grants.sql ---"
"${SQLCMD_BASE[@]}" -d ColdChainLogistics -i "$WORK/scripts/phase2_executed_grants.sql"
"${SQLCMD_BASE[@]}" -d ColdChainLogistics -i "$WORK/scripts/verify_grants.sql" -o "$WORK/verify_grants_output.txt" || true
sed -n '1,200p' "$WORK/verify_grants_output.txt"

echo "--- [8b] count verification vs DATASET_MANIFEST.json ---"
VERIFY_CS="DRIVER={ODBC Driver 18 for SQL Server};SERVER=$RDS_ENDPOINT;DATABASE=ColdChainLogistics;UID=$MASTER_USER;PWD=$MASTER_PASS;Encrypt=yes;TrustServerCertificate=yes"
VERIFY_CONNECTION_STRING="$VERIFY_CS" python3 - <<'PYEOF'
import json, os, pyodbc
manifest = json.load(open('/opt/coldchain/data/unified/DATASET_MANIFEST.json'))
conn = pyodbc.connect(os.environ['VERIFY_CONNECTION_STRING'], timeout=30)
ok = True
for csv_name, expected in manifest['counts'].items():
    table = csv_name.replace('.csv', '')
    got = conn.execute(f"SELECT COUNT(*) FROM [unified].[{table}]").fetchone()[0]
    status = "OK" if got == expected else "MISMATCH"
    if got != expected:
        ok = False
    print(f"  [unified].{table}: manifest={expected} imported={got} -> {status}")
print("IMPORT VERIFICATION:", "PASS" if ok else "FAIL")
PYEOF

# ---------------------------------------------------------------- 9. live guard-pattern query + admin drop
echo "--- [9/9] live view query as USR_FDE_RO (guard-validated pattern) + drop temp admin ---"
AGENT_CS="DRIVER={ODBC Driver 18 for SQL Server};SERVER=$RDS_ENDPOINT;DATABASE=ColdChainLogistics;UID=USR_FDE_RO;PWD=${PW[FDE_RO]};Encrypt=yes;TrustServerCertificate=yes"
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
"${SQLCMD_BASE[@]}" -Q "IF SUSER_ID(N'USR_IMPORT_ADMIN') IS NOT NULL BEGIN DROP USER [USR_IMPORT_ADMIN]; DROP LOGIN [USR_IMPORT_ADMIN]; END" -d ColdChainLogistics
echo "  temp import admin dropped (ADR-4)"

rm -f "$WORK/bundle.tar.gz"
echo "=== phase2_db_bootstrap COMPLETED $(date -u +%FT%TZ) ==="
