"""Phase 4 event-processing tests (operator directive §10, 2026-11-17).

Offline only: AWS/SNS/SQS interactions are stubbed; SQL statements are
asserted against fake engines (no local SQL Server exists — the live
migration + E2E test are post-approval items, per directive §11).
"""
import io
import json
from unittest.mock import MagicMock, patch

import src.telemetry.reconcile as reconcile_mod
from src.telemetry.events import classify, severity
from src.telemetry.worker import (process_batch, payloads_from_message, reject_to_dlq,
                                  sns_error_class)
from src.telemetry.store import record_attempt

BASE = {"event_id": "e1", "shipment_id": "UC-003241", "timestamp_utc": "2026-11-17T10:00:00Z",
        "temperature_c": 6.2, "source": "synthetic"}


# ---------------------------------------------------------------- stubs
class StubSqs:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent_dlq = []
        self.deleted = []

    def receive_message(self, **kw):
        return {"Messages": self.messages} if self.messages else {}

    def send_message(self, **kw):
        self.sent_dlq.append(kw)

    def delete_message(self, **kw):
        self.deleted.append(kw)


class StubSns:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def publish(self, **kw):
        if self.fail:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "Throttling", "Message": "slow down"}}, "Publish")
        self.sent.append(kw)
        return {"MessageId": "sns-msg-1"}


class FakeResult:
    def __init__(self, first=None, scalar=None, rows=None):
        self._first, self._scalar, self._rows = first, scalar, rows

    def first(self):
        return self._first

    def scalar(self):
        return self._scalar

    def __iter__(self):
        return iter(self._rows or [])


class FakeConnection:
    """Records executed SQL text + params; scriptable outcomes."""

    def __init__(self, exists=True, fail=False, pending_rows=None, lock=0, counts=None, dup=False):
        self.statements = []
        self.exists, self.fail, self.dup = exists, fail, dup
        self.pending_rows, self.lock = pending_rows, lock
        self.counts = counts or {}

    def execute(self, stmt, params=None):
        text_sql = str(stmt)
        self.statements.append((text_sql, params))
        if self.fail:
            raise OSError("db down")
        upper = text_sql.strip().upper()
        if "UNIFIED.SHIPMENTS" in upper:                          # linkage check
            return FakeResult(first=("ok",) if self.exists else None)
        if "SP_GETAPPLOCK" in upper:                             # reconcile lock
            return FakeResult(scalar=self.lock)
        if upper.startswith("INSERT"):
            if "TELEMETRYEVENTS" in upper and "OUTPUT" in upper:   # save_event dedup guard
                return FakeResult(first=None if self.dup else ("e1",))
            return FakeResult(first=(1,))                         # incident/attempt insert
        if upper.startswith("SELECT COUNT"):
            return FakeResult(scalar=self.counts.get("count", 0))
        if upper.startswith("SELECT"):
            rows = []
            for r in (self.pending_rows or []):
                m = MagicMock(); m._mapping = r; rows.append(m)
            return FakeResult(rows=rows)
        return FakeResult(first=("ok",))


class Engine:
    def __init__(self, conn):
        self.conn = conn

    def begin(self):
        return _Tx(self.conn)

    def connect(self):
        return _ConnCtx(self.conn)


class _ConnCtx:
    """Connection context: `with engine.connect() as conn` then
    `with conn.begin():` (the reconcile lock pattern)."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def begin(self):
        return _Tx(self.conn)

    def execute(self, stmt, params=None):
        return self.conn.execute(stmt, params)


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *a):
        return False


def incident_from(event, threshold=4.0):
    return classify(event, threshold)


# ---------------------------------------------------------------- §10.3 + §10.1
def test_excursion_incident_generation_with_derived_severity():
    inc = incident_from(BASE)
    assert inc["severity"] == "medium"         # 6.2 - 4.0 = 2.2 -> medium bucket
    assert severity(0.1) == "low" and severity(2.5) == "medium" and severity(9.9) == "high"
    assert severity(-1) is None
    assert incident_from({**BASE, "temperature_c": 4.0}) is None


def test_valid_event_ingestion_sql_mode_records_attempt():
    sqs, sns = StubSqs([{"MessageId": "m1", "Body": json.dumps(BASE), "ReceiptHandle": "r1"}]), StubSns()
    conn = FakeConnection(exists=True)
    engine = Engine(conn)
    done = process_batch(sqs, sns, "q", "arn", engine=engine, validate_linkage=True, dlq_url="dlq")
    assert done == 1 and len(sqs.deleted) == 1 and len(sns.sent) == 1
    # Event + incident inserted, linkage checked, attempt row appended.
    joined = " ".join(s for s, _ in conn.statements)
    assert "unified.shipments" in joined and "IncidentNotificationAttempts" in joined
    assert "TelemetryEvents" in joined and "TelemetryIncidents" in joined


# ---------------------------------------------------------------- §10.2 + §10.6
def test_invalid_payload_routed_to_dlq_and_acked():
    bad = {"event_id": "x", "source": "bogus"}
    sqs, sns = StubSqs([{"MessageId": "m1", "Body": json.dumps(bad), "ReceiptHandle": "r1"}]), StubSns()
    conn = FakeConnection()
    done = process_batch(sqs, sns, "q", "arn", engine=Engine(conn), dlq_url="dlq")
    assert done == 1
    assert sqs.sent_dlq[0]["MessageAttributes"]["FailureReason"]["StringValue"] == "invalid_payload"
    assert len(sqs.deleted) == 1 and not sns.sent          # source acked AFTER DLQ write
    assert not conn.statements                             # nothing touched the DB


def test_poison_bad_shipment_ref_routed_to_dlq():
    sqs, sns = StubSqs([{"MessageId": "m1", "Body": json.dumps(BASE), "ReceiptHandle": "r1"}]), StubSns()
    conn = FakeConnection(exists=False)                    # linkage check fails
    done = process_batch(sqs, sns, "q", "arn", engine=Engine(conn), validate_linkage=True, dlq_url="dlq")
    assert done == 1
    assert sqs.sent_dlq[0]["MessageAttributes"]["FailureReason"]["StringValue"] == "bad_shipment_ref"
    assert not sns.sent and "TelemetryEvents" not in " ".join(s for s, _ in conn.statements)


# ---------------------------------------------------------------- §10.5 + §10.12
def test_worker_retry_behavior_and_recovery():
    # DB fails mid-processing: message NOT deleted (redelivery), no ack.
    sqs = StubSqs([{"MessageId": "m1", "Body": json.dumps(BASE), "ReceiptHandle": "r1"}])
    conn = FakeConnection(exists=True, fail=True)
    assert process_batch(sqs, StubSns(), "q", "arn", engine=Engine(conn), validate_linkage=True) == 0
    assert not sqs.deleted
    # "Restart" (fresh call) with a recovered DB: the same message processes.
    conn2 = FakeConnection(exists=True)
    assert process_batch(sqs, StubSns(), "q", "arn", engine=Engine(conn2), validate_linkage=True) == 1
    assert len(sqs.deleted) == 1


# ---------------------------------------------------------------- §10.4
def test_duplicate_event_no_second_publish_or_incident():
    # Redelivery (at-least-once): the dedup guard reports the event already
    # persisted -> no new incident insert, no second publish, message acked.
    sqs = StubSqs([{"MessageId": "m1", "Body": json.dumps(BASE), "ReceiptHandle": "r1"}])
    conn = FakeConnection(exists=True, dup=True)
    sns = StubSns()
    assert process_batch(sqs, sns, "q", "arn", engine=Engine(conn), validate_linkage=True) == 1
    assert not sns.sent and len(sqs.deleted) == 1
    assert not any("TelemetryIncidents" in s for s, _ in conn.statements)


# ---------------------------------------------------------------- S3 event source
def test_s3_envelope_extraction_and_prefix_gate():
    class S3Stub:
        def get_object(self, **kw):
            return {"Body": io.BytesIO(json.dumps({**BASE, "event_id": "e2"}).encode())}

    env = {"Records": [{"eventName": "ObjectCreated:Put", "s3": {"bucket": {"name": "coldchain-dev-telemetry"},
                        "object": {"key": "synthetic/e2.json"}}}]}
    [event] = payloads_from_message(S3Stub(), env, "synthetic/")
    assert event["event_id"] == "e2"
    # Keys outside the approved prefix are rejected (never processed).
    import pytest
    with pytest.raises(ValueError):
        payloads_from_message(S3Stub(), {"Records": [{"s3": {"bucket": {"name": "b"},
                                                             "object": {"key": "real/e2.json"}}}]}, "synthetic/")


# ---------------------------------------------------------------- §10.10
def test_sns_notification_generation_content():
    sqs, sns = StubSqs([{"MessageId": "m1", "Body": json.dumps(BASE), "ReceiptHandle": "r1"}]), StubSns()
    conn = FakeConnection(exists=True)
    process_batch(sqs, sns, "q", "arn", engine=Engine(conn), validate_linkage=True)
    msg = json.loads(sns.sent[0]["Message"])
    for key in ("incident_id", "shipment_id", "observed_at_utc", "temperature_c",
                "threshold_c", "severity", "provenance", "summary"):
        assert key in msg
    assert BASE["shipment_id"] in msg["summary"]
    assert "nvapi" not in json.dumps(msg) and "password" not in json.dumps(msg).lower()


def test_publish_failure_records_attempt_and_acks_message():
    sqs, sns = StubSqs([{"MessageId": "m1", "Body": json.dumps(BASE), "ReceiptHandle": "r1"}]), StubSns(fail=True)
    conn = FakeConnection(exists=True)
    done = process_batch(sqs, sns, "q", "arn", engine=Engine(conn), validate_linkage=True)
    assert done == 1 and len(sqs.deleted) == 1          # Class B: incident committed, message acked
    attempt_sql = [(s, p) for s, p in conn.statements if "IncidentNotificationAttempts" in s]
    assert attempt_sql and attempt_sql[0][1]["result"] == "publish_failed"
    assert attempt_sql[0][1]["err"] == "sns_throttled"


def test_attempt_ledger_insert_only_shape():
    conn = FakeConnection()
    record_attempt(Engine(conn), "temp:e1", "published_acked", sns_message_id="mid-1")
    sql, params = conn.statements[0]
    assert sql.strip().upper().startswith("INSERT INTO DBO.INCIDENTNOTIFICATIONATTEMPTS")
    assert params["mid"] == "mid-1"
    import pytest
    with pytest.raises(ValueError):
        record_attempt(Engine(conn), "temp:e1", "status_update")   # only the two append outcomes


def test_sns_error_classes_are_short_never_raw():
    from botocore.exceptions import ClientError
    assert sns_error_class(ClientError({"Error": {"Code": "Throttling"}}, "x")) == "sns_throttled"
    assert sns_error_class(ClientError({"Error": {"Code": "AccessDenied"}}, "x")) == "sns_access_denied"
    assert sns_error_class(RuntimeError("traceback with secrets nvapi-xxx")) == "sns_error"


# ---------------------------------------------------------------- §10.7
def test_reconciliation_republishes_pending_and_records_attempts(monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")   # skip real CW metrics
    pending = {"IncidentId": "temp:e9", "EventId": "e9", "ShipmentId": "UC-003241",
               "ObservedAtUtc": "2026-11-17 09:00", "TemperatureC": 7.1, "ThresholdC": 4.0,
               "Provenance": "synthetic", "AttemptCount": 1}
    conn = FakeConnection(exists=True, pending_rows=[pending], lock=0, counts={"count": 1})
    engine = Engine(conn)
    published = []

    def fake_publish(sns, topic_arn, incident, *, engine=None, correlation=""):
        published.append(incident["incident_id"])
        record_attempt(engine, incident["incident_id"], "published_acked", sns_message_id="re-1")

    monkeypatch.setattr(reconcile_mod, "_publish_with_ledger", fake_publish)
    republished = reconcile_mod.reconcile_once(engine, StubSns(), "arn")
    assert republished == 1 and published == ["temp:e9"]
    assert any("IncidentNotificationAttempts" in s for s, _ in conn.statements)


def test_reconcile_skips_when_lock_held(monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
    conn = FakeConnection(lock=-1)                                   # sp_getapplock timed out
    assert reconcile_mod.reconcile_once(Engine(conn), StubSns(), "arn") == 0


# ---------------------------------------------------------------- §10.9 source assertions
SQL_SCRIPTS = ("scripts/phase4_event_processing.sql",)


def _read(path):
    from pathlib import Path
    return Path(path).read_text(encoding="utf-8")


def test_sql_grants_remain_insert_only_and_narrow():
    sql = _read("scripts/phase4_event_processing.sql")
    assert "GRANT SELECT, INSERT ON dbo.IncidentNotificationAttempts TO USR_INCIDENT_WRITER" in sql
    # Column-level linkage grant: shipment_id ONLY, no broader shipments read.
    assert "GRANT SELECT ON unified.shipments (shipment_id) TO USR_INCIDENT_WRITER" in sql
    low = sql.lower()
    for forbidden in ("grant update", "grant delete", "grant alter", "grant control"):
        assert forbidden not in low, forbidden
    assert "published_acked" in low and "publish_failed" in low
    # Derived notification state is a view, never a mutable status column.
    assert "VW_INCIDENT_NOTIFICATIONS" in sql and "VW_EVENT_LINK_VALIDATION" in sql


def test_append_only_ledger_has_no_update_path_in_code():
    import src.telemetry.store as store_mod
    import inspect
    for fn in (store_mod.record_attempt, store_mod.save_event):
        assert "UPDATE" not in inspect.getsource(fn).upper()
        assert "DELETE" not in inspect.getsource(fn).upper()


def test_compose_phase4_services_and_env():
    compose = _read("docker/compose.yml")
    assert 'profiles: ["phase4"]' in compose
    assert "python -m src.telemetry.reconcile --loop" in compose
    assert "TELEMETRY_PERSIST_SQL true" in _read("docker/fetch-secrets.sh")
    assert "TELEMETRY_QUEUE_URL" in _read("docker/fetch-secrets.sh")


def test_terraform_events_module_source_assertions():
    events = _read("infra/modules/events/main.tf")
    assert "maxReceiveCount" in events and "filter_prefix" in events
    low = events.lower()
    assert 'resource "aws_sns_topic_subscription"' not in low  # NO recipient until operator authorizes
    assert "s3:ObjectCreated:Put" in events
    assert "coldchain-${var.environment}-telemetry" in events
    compute = _read("infra/modules/compute/main.tf")
    # DLQ SendMessage is scoped to the EXACT ARN — main queue not writable.
    assert "var.event_dlq_arn" in compute
    assert 'StringEquals = { "cloudwatch:namespace" = "ColdChain/Phase4" }' in compute


# ---------------------------------------------------------------- §10.11
def test_dashboard_shows_functional_viewer_and_honest_labels():
    ui = _read("src/ui.py")
    assert "VW_INCIDENT_NOTIFICATIONS" in ui          # derived notification state surfaced
    assert "VW_EVENT_LINK_VALIDATION" in ui           # C4 audit backstop surfaced
    assert "TELEMETRY_DLQ_URL" in ui                  # DLQ health tile (best-effort)
    assert "TOPIC accepted" in ui                     # SNS-acked != delivered (B4)
    assert "not real operational incidents" in ui     # synthetic labeling preserved


def test_grafana_incident_dashboard_gains_phase4_panels():
    dash = _read("integrations/grafana/dashboards/telemetry-incidents.json")
    assert "VW_INCIDENT_NOTIFICATIONS" in dash        # derived notification state panel
    assert "VW_EVENT_LINK_VALIDATION" in dash         # linkage audit stat
    assert "AttemptCount" in dash and "NotificationState" in dash
    assert "SNS-acked != delivered" in dash           # B4 honesty label
    # every panel QUERY stays on the approved read-only views (descriptive
    # prose may legitimately mention the base tables it does NOT query)
    import json as _json
    obj = _json.loads(dash)
    raws = [t["rawSql"] for p in obj["panels"] for t in p.get("targets", [])]
    assert raws and all("unified." not in r and "dbo." not in r for r in raws)
