"""Daily AWS Cost Explorer billing snapshot (operator directive 2026-11-22).

Runs ON THE APP HOST via a systemd timer (docker/grafana/
coldchain-billing-refresh.timer: once daily 09:30 UTC, Persistent=true so a
run missed during an EC2 stop fires shortly after the next boot — the host
has NO cron, live-found 2026-11-22: no crond, no /etc/cron.d). Cost Explorer
charges per API request, so the scheduler is the ONLY thing that ever
queries CE; Grafana refreshes never do.

Pipeline:
  1. Query GetCostAndUsage for the CURRENT billing month via the host AWS CLI
     (instance profile; the least-privilege ce:GetCostAndUsage grant for the
     app-host role is a SEPARATE, approval-gated change — until it lands,
     this job records the AccessDenied refresh status and the Grafana panel
     shows the stale-data warning with the last successful figures).
  2. Bounded retries (2 attempts) and pagination (<= 10 pages).
  3. Write integrations/grafana/billing_snapshot.json ATOMICALLY (mkstemp in
     the same directory + os.replace; mode 0600) — a partial write can never
     corrupt the displayed data. On failure the last GOOD data is kept and
     only the refresh status fields are updated.
  4. Re-run scripts/generate_grafana_dashboards.py so the Grafana panel
     markdown picks up the new snapshot; Grafana's file provisioning
     re-scans the dashboards directory and reloads changed dashboards
     without a container restart.

No long-lived credentials anywhere: the host CLI uses the instance role.
The snapshot contains only cost figures — no secrets, never published
outside the authenticated Grafana UI (not in the Hugging Face portfolio).

Stdlib only — safe for the host python3.
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "integrations" / "grafana" / "billing_snapshot.json"
GENERATOR = ROOT / "scripts" / "generate_grafana_dashboards.py"
LOG = Path("/var/log/coldchain-billing-refresh.log")

ACCOUNT_FALLBACK = "123456789012"
REGION = "us-east-1"
METRIC = "UnblendedCost"
MAX_ATTEMPTS = 2
MAX_PAGES = 10


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def ce_call(start, end, next_token=None):
    cmd = ["aws", "ce", "get-cost-and-usage", "--region", REGION,
           "--time-period", f"Start={start},End={end}",
           "--granularity", "MONTHLY", "--metrics", METRIC,
           "--group-by", "Type=DIMENSION,Key=SERVICE", "--output", "json"]
    if next_token:
        cmd += ["--next-page-token", next_token]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        detail = " ".join((r.stderr or r.stdout).split())[:200]
        raise RuntimeError(detail)
    return json.loads(r.stdout)


def collect():
    """Current-month CE collection: returns (services, total, estimated,
    api_requests) or raises."""
    now = datetime.now(timezone.utc)
    start = now.strftime("%Y-%m-01")
    # Cost Explorer End is EXCLUSIVE: tomorrow covers all of today.
    end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            data = ce_call(start, end)
            api_requests = 1
            services = {}
            estimated = True
            pages = 1
            while True:
                for res in data.get("ResultsByTime", []):
                    if res.get("Estimated") is False:
                        estimated = False
                    # LIVE-FOUND 2026-11-22: with GROUP BY, CE returns an
                    # EMPTY Total{} — the account total is the sum of the
                    # per-service groups.
                    for g in res.get("Groups", []):
                        key = g["Keys"][0]
                        services[key] = services.get(key, 0.0) + round(
                            float(g["Metrics"][METRIC]["Amount"]), 8)
                token = data.get("NextPageToken")
                if not token or pages >= MAX_PAGES:
                    break
                data = ce_call(start, end, token)
                api_requests += 1
                pages += 1
            total = round(sum(services.values()), 4)
            svc_list = sorted(
                ({"service": k, "usd": round(v, 4)} for k, v in services.items()),
                key=lambda s: -s["usd"])
            return start, end, svc_list, round(total, 4), estimated, api_requests, attempt
        except Exception as exc:  # noqa: BLE001 - bounded retry
            last_err = exc
            log(f"attempt {attempt} failed: {exc}")
    raise last_err


def write_atomic(payload):
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=SNAPSHOT.parent, prefix=".billing-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, SNAPSHOT)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def main():
    existing = None
    try:
        existing = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        account = subprocess.run(
            ["aws", "sts", "get-caller-identity", "--region", REGION,
             "--query", "Account", "--output", "text"],
            capture_output=True, text=True, timeout=30)
        account_id = account.stdout.strip() if account.returncode == 0 else ACCOUNT_FALLBACK
    except Exception:  # noqa: BLE001
        account_id = ACCOUNT_FALLBACK

    try:
        start, end, svc_list, total, estimated, api_requests, attempts = collect()
        payload = {
            "account_id": account_id,
            "billing_period": {"start": start, "end": end},
            "retrieved_at_utc": now_iso,
            "metric": METRIC,
            "granularity": "MONTHLY",
            "group_by": "SERVICE",
            "currency": "USD",
            "total_mtd": total,
            "estimated": estimated,
            "services": svc_list,
            "api_requests": api_requests,
            "refresh": {"status": "ok", "error_class": None, "error_detail": None,
                        "attempts": attempts},
        }
        write_atomic(payload)
        log(f"snapshot written: MTD ${total:.2f}, {len(svc_list)} services, "
            f"{api_requests} CE request(s)")
    except Exception as exc:  # noqa: BLE001 - keep last-good data, record failure
        payload = dict(existing or {})
        payload["refresh"] = {"status": "error",
                              "error_class": type(exc).__name__,
                              "error_detail": str(exc)[:200],
                              "attempts": MAX_ATTEMPTS}
        payload.setdefault("account_id", account_id)
        if payload:
            write_atomic(payload)
        log(f"refresh FAILED; last-good data retained with error status: {exc}")

    # Regenerate the dashboards so the panel reflects the new snapshot.
    r = subprocess.run([sys.executable, str(GENERATOR)], capture_output=True, text=True)
    if r.returncode != 0:
        log(f"generator failed: {r.stderr[-300:]}")
    else:
        log("dashboards regenerated from snapshot")


if __name__ == "__main__":
    main()
