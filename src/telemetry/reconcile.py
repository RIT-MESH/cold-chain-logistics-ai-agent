"""Scheduled notification reconciliation (blueprint 03 §B3/B5, R3-2/R4-3).

Republishes incidents whose SNS notification was lost in the post-commit
window (Class B failures): an incident with no `published_acked` attempt
row, past its exponential-backoff eligibility point and below
NOTIFY_MAX_ATTEMPTS, is re-published and another attempt row is appended.

Concurrency: single-scheduler by construction (one host, one loop), plus a
SQL advisory lock (sp_getapplock, 0 s timeout) so a manually-triggered
second run exits immediately instead of double-publishing. Residual risk is
acknowledged in the blueprint: SNS Standard is at-least-once and the
ack-to-insert crash window can still produce duplicate notifications —
database-only reconciliation cannot close that (R5-2).

Run standalone (`--once`, e.g. from cron/SSM) or as a loop
(`--loop`, the compose reconcile service). Custom CloudWatch metrics
(ColdChain/Phase4 namespace) feed the pending-age and exhausted alarms;
they are emitted only against real AWS (no endpoint override).

No UPDATE/DELETE anywhere: re-publish + INSERT attempt rows only.
"""
import argparse
import logging
import os
import time
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError

from src.telemetry.common import client
from src.telemetry.store import (acquire_reconcile_lock, alarm_pending_count, exhausted_notifications,
                                 make_engine, pending_notifications, record_attempt)
from src.telemetry.worker import _publish_with_ledger, log_json, sns_error_class

LOG = logging.getLogger(__name__)

METRIC_NAMESPACE = "ColdChain/Phase4"


def emit_metrics(pending_alarm, exhausted):
    """Custom CW metrics for the reconcile alarms — real AWS only."""
    if os.getenv("AWS_ENDPOINT_URL"):
        return  # emulator has no CloudWatch; skip silently
    cw = client("cloudwatch")
    cw.put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=[
        {"MetricName": "IncidentsPendingNotification", "Value": pending_alarm, "Unit": "Count"},
        {"MetricName": "IncidentsNotifyExhausted", "Value": exhausted, "Unit": "Count"},
    ])


def reconcile_once(engine, sns, topic_arn):
    """One bounded reconcile pass under the advisory lock. Returns the
    number of re-publish attempts made this pass."""
    republished = 0
    with engine.connect() as lock_conn:
        with lock_conn.begin():
            if not acquire_reconcile_lock(lock_conn):
                log_json("reconcile_skipped_locked")
                return 0
            pending = pending_notifications(
                engine,
                base_interval_minutes=int(os.getenv("NOTIFY_BASE_INTERVAL_MINUTES", "15")),
                max_attempts=int(os.getenv("NOTIFY_MAX_ATTEMPTS", "5")),
                window_hours=int(os.getenv("NOTIFY_ATTEMPT_WINDOW_H", "24")))
            for row in pending:
                incident = {"incident_id": row["IncidentId"], "shipment_id": row["ShipmentId"],
                            "timestamp_utc": str(row["ObservedAtUtc"]),
                            "temperature_c": row["TemperatureC"], "threshold_c": row["ThresholdC"],
                            "source": row["Provenance"]}
                try:
                    _publish_with_ledger(sns, topic_arn, incident, engine=engine,
                                         correlation=row["EventId"])
                    republished += 1
                except (BotoCoreError, ClientError, OSError) as exc:
                    log_json("reconcile_attempt_failed", incident=row["IncidentId"],
                             error_class=sns_error_class(exc))
    return republished


def main():
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true", help="single pass (cron/SSM)")
    group.add_argument("--loop", action="store_true", help="run every RECONCILE_INTERVAL_SECONDS")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO)
    engine = make_engine()  # reconcile is SQL-backed only; no journal mode
    sns = client("sns")
    topic_arn = os.getenv("TELEMETRY_ALERT_TOPIC_ARN")
    if not topic_arn:
        raise SystemExit("TELEMETRY_ALERT_TOPIC_ARN must be set for reconcile (Terraform-managed topic)")
    interval = int(os.getenv("RECONCILE_INTERVAL_SECONDS", "900"))

    def pass_with_metrics():
        republished = reconcile_once(engine, sns, topic_arn)
        pending_alarm = alarm_pending_count(engine,
                                            pending_minutes=int(os.getenv("NOTIFY_PENDING_ALARM_MINUTES", "30")))
        exhausted = exhausted_notifications(
            engine, max_attempts=int(os.getenv("NOTIFY_MAX_ATTEMPTS", "5")),
            window_hours=int(os.getenv("NOTIFY_ATTEMPT_WINDOW_H", "24")))
        try:
            emit_metrics(pending_alarm, exhausted)
        except (BotoCoreError, ClientError, OSError) as exc:
            LOG.warning("Metric emission skipped: %s", exc)
        log_json("reconcile_pass", republished=republished, pending_alarm=pending_alarm,
                 exhausted=exhausted)
        return republished

    if args.once:
        pass_with_metrics()
        return
    while True:
        try:
            pass_with_metrics()
        except (EndpointConnectionError, ClientError, BotoCoreError, OSError) as exc:
            LOG.warning("Reconcile pass failed (will retry next interval): %s", exc)
        time.sleep(interval)


if __name__ == "__main__":
    main()
