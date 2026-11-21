#!/bin/bash
# phase3_rollout.sh — Phase 3a application rollout ON the EC2 host (SSM, C7).
# Extract assumed done (bundle at /opt/coldchain). Sequence:
#   secrets to tmpfs -> compose build -> up -d -> health checks.
# Usage: phase3_rollout.sh <RDS_ENDPOINT>
set -euo pipefail
RDS_ENDPOINT="$1"
WORK=/opt/coldchain
LOG="$WORK/phase3_rollout.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== phase3_rollout started $(date -u +%FT%TZ) ==="

command -v docker >/dev/null || { echo "docker not installed"; exit 1; }
systemctl is-active --quiet docker || systemctl start docker
# AL2023 ships the compose plugin as a separate package — install it if the
# daemon CLI lacks `docker compose` (retry 2 failed exactly here, exit 125).
docker compose version >/dev/null 2>&1 || {
  echo "installing docker compose plugin"
  dnf install -y docker-compose-plugin >/dev/null 2>&1 || {
    mkdir -p /usr/local/lib/docker/cli-plugins
    curl -sSL --fail https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
      -o /usr/local/lib/docker/cli-plugins/docker-compose
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
  }
  docker compose version
}
# Compose v5 requires buildx >= 0.17.0 — AL2023 ships older (retry 3 failed
# exactly here; retry 4 died because this version-check pipeline trips
# pipefail when buildx is absent — hence the || true guards).
NEED_BUILDX=1
INSTALLED_BUILDX=$(docker buildx version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
if [ -n "$INSTALLED_BUILDX" ]; then
  echo "installed buildx: v$INSTALLED_BUILDX"
  MINOR=$(echo "$INSTALLED_BUILDX" | cut -d. -f2)
  if [ "${MINOR:-0}" -ge 17 ] 2>/dev/null; then NEED_BUILDX=0; fi
fi
if [ "$NEED_BUILDX" = "1" ]; then
  echo "installing/upgrading docker buildx plugin"
  mkdir -p /usr/local/lib/docker/cli-plugins
  BUILDX_VER=$(curl -s https://api.github.com/repos/docker/buildx/releases/latest \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null || echo "v0.17.0")
  curl -sSL --fail "https://github.com/docker/buildx/releases/download/${BUILDX_VER}/buildx-${BUILDX_VER}.linux-amd64" \
    -o /usr/local/lib/docker/cli-plugins/docker-buildx
  chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx
  docker buildx version
fi

echo "--- [1/4] runtime secrets -> tmpfs (instance profile) ---"
bash "$WORK/docker/fetch-secrets.sh" "$RDS_ENDPOINT" dev

echo "--- [2/4] compose build (gateway + streamlit images) ---"
docker compose -f "$WORK/docker/compose.yml" --project-directory "$WORK" build fde-api streamlit

echo "--- [3/4] up -d (5 containers; worker gated to Phase 4) ---"
docker compose -f "$WORK/docker/compose.yml" --project-directory "$WORK" up -d
sleep 20

echo "--- [4/4] health checks ---"
docker compose -f "$WORK/docker/compose.yml" --project-directory "$WORK" ps
echo "gateway /health:"
curl -sf http://127.0.0.1:8000/health && echo " <- OK"
echo "streamlit responds:"
curl -sf -o /dev/null -w "%{http_code}" http://127.0.0.1:8501/ && echo " <- streamlit OK"
echo "grafana responds:"
curl -sf -o /dev/null -w "%{http_code}" http://127.0.0.1:3000/api/health && echo " <- grafana OK"
echo "memory limits in effect:"
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"
echo "no-leak grep (docker history + env):"
if docker history $(docker compose -f "$WORK/docker/compose.yml" --project-directory "$WORK" images -q fde-api) 2>/dev/null | grep -iE "nvapi|api_key|password"; then
  echo "LEAK SUSPECTED"; exit 1
else
  echo "  no key material in gateway image layers"
fi
echo "=== phase3_rollout COMPLETED $(date -u +%FT%TZ) ==="
