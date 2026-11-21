"""SQS worker with idempotent SQL persistence, DLQ routing, and an
append-only SNS attempt ledger (blueprint 03 §B1-B5).

Two runtime modes, selected by environment:
- Local (Floci emulator): queue/topic resolved BY NAME, optional JSONL
  journal, SQL persistence opt-in — unchanged Phase 0 behavior.
- AWS (Phase 4): queue URL / DLQ URL / topic ARN injected by fetch-secrets
  (TELEMETRY_QUEUE_URL, TELEMETRY_DLQ_URL, TELEMETRY_ALERT_TOPIC_ARN), SQL
  persistence required, ingest-time linkage validation (C4) with controlled
  DLQ rejection for bad shipment references, and one attempt row per SNS
  publish outcome (Class B failure fix, R3-2).

At-least-once delivery is assumed everywhere (SQS makes no exactly-once
promise): EventId dedup in SQL is the idempotency anchor.
"""
import json
import logging
import os
import time
from pathlib import Path
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError
from src.telemetry.common import client
from src.telemetry.events import classify, validate
from src.telemetry.store import check_shipment_exists, make_engine, record_attempt, save_event

LOG = logging.getLogger(__name__)


def log_json(event_name, **fields):
    """Structured single-line log: correlation-friendly, no secret values."""
    LOG.info("%s %s", event_name, json.dumps(fields, default=str))


def sns_error_class(exc):
    """Short error class for the attempt ledger — never raw payloads (B5)."""
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "") or type(exc).__name__
    mapping = {"Throttling": "sns_throttled", "ThrottledException": "sns_throttled",
               "InvalidParameter": "sns_invalid_param", "ParameterValueInvalid": "sns_invalid_param",
               "AccessDenied": "sns_access_denied", "AccessDeniedException": "sns_access_denied",
               "AuthorizationError": "sns_access_denied"}
    return mapping.get(code, "sns_error")


def reject_to_dlq(sqs, dlq_url, msg, reason):
    """Controlled failure handling: poison/bad-ref messages go straight to the
    DLQ with a FailureReason attribute (no maxReceiveCount churn), then the
    source message is deleted. Caller deletes the source copy on success."""
    sqs.send_message(QueueUrl=dlq_url, MessageBody=msg.get("Body", ""),
                     MessageAttributes={"FailureReason": {"DataType": "String", "StringValue": reason}})
    sqs.delete_message(QueueUrl=msg.get("SourceQueueUrl", ""), ReceiptHandle=msg["ReceiptHandle"])
    log_json("event_dlq_routed", failure_reason=reason, receipt=msg["ReceiptHandle"][:12])


def payloads_from_message(s3, body, approved_prefix):
    """Extract telemetry event payloads from an SQS message body.

    Two shapes are supported (directive §4 "S3 event → SQS"):
    - a direct event JSON object (publisher send; also the local Floci path), or
    - an S3 ObjectCreated notification envelope {"Records":[...]} — each record
      resolves to the object, which must live under the APPROVED input
      prefix; keys outside the prefix are configuration errors, rejected.
    At-least-once delivery means both paths can carry the SAME event —
    EventId dedup at persist time is the idempotency anchor (R5-2).
    """
    if not isinstance(body, dict) or "Records" not in body:
        return [body]
    payloads = []
    for record in body["Records"]:
        s3info = (record or {}).get("s3") or {}
        bucket = s3info.get("bucket", {}).get("name")
        key = s3info.get("object", {}).get("key")
        if not bucket or not key:
            raise ValueError("s3 record missing bucket/key")
        if not key.startswith(approved_prefix):
            raise ValueError(f"s3 key outside approved prefix: {key}")
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        payloads.append(json.loads(raw.decode("utf-8")))
    return payloads


def process_batch(sqs, sns, queue_url, topic_arn, *, journal=None, engine=None, threshold=4.0,
                  dlq_url=None, validate_linkage=False, worker_id="worker", s3=None,
                  approved_prefix="synthetic/"):
    response = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=2)
    done = 0
    for msg in response.get("Messages", []):
        correlation = msg.get("MessageId", "")[:12]
        # Stage 1 — parse and validate EVERYTHING before touching the
        # database: a message is either fully processable or fully rejected
        # (no partial commits of a batched message; idempotency still covers
        # any redelivery overlap).
        try:
            body = json.loads(msg["Body"])
            payloads = payloads_from_message(s3, body, approved_prefix)
            for payload in payloads:
                validate(payload)
                correlation = payload.get("event_id", correlation)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            # Class A: malformed payload / S3 envelope. DLQ immediately when
            # configured (never silently accepted); otherwise leave for redelivery.
            log_json("event_invalid_payload", correlation=correlation, error=str(exc)[:120])
            if dlq_url:
                reject_to_dlq(sqs, dlq_url, {**msg, "SourceQueueUrl": queue_url}, "invalid_payload")
                done += 1
            continue
        try:
            if validate_linkage and engine is not None:
                # C4: linkage validated at ingest time; bad ref → DLQ labeled
                # bad_shipment_ref, never silently accepted (blueprint 03 §B1).
                bad_refs = [p["shipment_id"] for p in payloads
                            if not check_shipment_exists(engine, p["shipment_id"])]
                if bad_refs:
                    log_json("event_bad_shipment_ref", correlation=correlation,
                             shipment_ids=bad_refs[:3])
                    if dlq_url:
                        reject_to_dlq(sqs, dlq_url, {**msg, "SourceQueueUrl": queue_url},
                                      "bad_shipment_ref")
                        done += 1
                    continue  # no DLQ configured → left for redelivery → redrive
            # Stage 2 — persist + publish.
            for payload in payloads:
                incident = classify(payload, threshold)
                if engine is not None:
                    created = save_event(engine, payload, incident)
                else:
                    created = True
                    if incident is not None and journal is not None:
                        with Path(journal).open("a", encoding="utf-8") as file:
                            file.write(json.dumps(incident) + "\n")
                if created and incident is not None:
                    _publish_with_ledger(sns, topic_arn, incident, engine=engine, correlation=payload.get("event_id", correlation))
                log_json("event_processed", correlation=payload.get("event_id", correlation),
                         worker=worker_id, incident=incident["incident_id"] if incident else None,
                         new_event=created)
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
            done += 1
        except (ValueError, TypeError, KeyError, OSError, BotoCoreError, ClientError) as exc:
            # Class A processing failure: message NOT deleted → SQS redelivery
            # → maxReceiveCount → DLQ via the queue's redrive policy.
            LOG.exception("Message left in queue for retry: %s", exc)
    return done


def _publish_with_ledger(sns, topic_arn, incident, *, engine=None, correlation=""):
    """Publish the notification and append one attempt row per outcome (B3).
    A publish failure AFTER the incident row committed (Class B) is recorded
    and left to the reconcile step — the SQS message is still acknowledged,
    because redelivery cannot fix a post-commit notification failure."""
    payload = json.dumps({"incident_id": incident["incident_id"],
                          "event_ref": correlation,
                          "shipment_id": incident["shipment_id"],
                          "observed_at_utc": incident["timestamp_utc"],
                          "temperature_c": incident["temperature_c"],
                          "threshold_c": incident["threshold_c"],
                          "severity": incident.get("severity"),
                          "provenance": incident["source"],
                          "summary": (f"Temperature excursion: observed {incident['temperature_c']} C "
                                      f"above threshold {incident['threshold_c']} C on shipment "
                                      f"{incident['shipment_id']} at {incident['timestamp_utc']} "
                                      f"(provenance {incident['source']}). Human review required.")})
    try:
        result = sns.publish(TopicArn=topic_arn,
                             Subject=f"Cold-chain incident {incident['incident_id']}"[:100],
                             Message=payload)
        if engine is not None:
            record_attempt(engine, incident["incident_id"], "published_acked",
                           sns_message_id=result.get("MessageId"))
        log_json("notification_acked", correlation=correlation, incident=incident["incident_id"])
    except (BotoCoreError, ClientError, OSError) as exc:
        if engine is not None:
            record_attempt(engine, incident["incident_id"], "publish_failed",
                           error_code=sns_error_class(exc))
        log_json("notification_failed", correlation=correlation, incident=incident["incident_id"],
                 error_class=sns_error_class(exc))


def main():
    logging.basicConfig(level=logging.INFO)
    sqs, sns, s3_client = client("sqs"), client("sns"), client("s3")
    queue_name = os.getenv("TELEMETRY_QUEUE_NAME", "coldchain-telemetry")
    topic_name = os.getenv("TELEMETRY_ALERT_TOPIC_NAME", "coldchain-alerts")
    journal = os.getenv("TELEMETRY_INCIDENT_OUTPUT", "./data/cache/floci_incidents.jsonl")
    Path(journal).parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine() if os.getenv("TELEMETRY_PERSIST_SQL", "false").lower() == "true" else None
    dlq_url = os.getenv("TELEMETRY_DLQ_URL") or None
    # AWS mode resolves the queue by full URL (injected by fetch-secrets) and
    # uses the Terraform-managed topic ARN — the worker never creates AWS
    # resources at runtime; Floci mode keeps name-based create/get.
    queue = os.getenv("TELEMETRY_QUEUE_URL") or sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
    topic = os.getenv("TELEMETRY_ALERT_TOPIC_ARN") or sns.create_topic(Name=topic_name)["TopicArn"]
    validate_linkage = os.getenv("TELEMETRY_VALIDATE_LINKAGE", "true" if engine else "false").lower() == "true"
    worker_id = os.getenv("HOSTNAME", "worker")
    log_json("worker_started", worker=worker_id, queue=queue.rsplit("/", 1)[-1], topic_arn=topic.rsplit(":", 1)[-1],
             sql_persisted=engine is not None, linkage_validation=validate_linkage)
    while True:
        try:
            count = process_batch(sqs, sns, queue, topic, journal=journal, engine=engine,
                                  threshold=float(os.getenv("TELEMETRY_THRESHOLD_C", "4.0")),
                                  dlq_url=dlq_url, validate_linkage=validate_linkage, worker_id=worker_id,
                                  s3=s3_client, approved_prefix=os.getenv("TELEMETRY_S3_PREFIX", "synthetic/"))
            if count:
                LOG.info("Processed %d messages", count)
        except (EndpointConnectionError, ClientError, BotoCoreError, OSError) as exc:
            LOG.warning("Event plane unavailable: %s", exc)
            time.sleep(3)


if __name__ == "__main__":
    main()
