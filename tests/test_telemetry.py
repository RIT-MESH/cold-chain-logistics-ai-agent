import json
from src.telemetry.events import validate, classify
from src.telemetry.worker import process_batch
import pytest

BASE = {"event_id": "e1", "shipment_id": "DEMO-1", "timestamp_utc": "2026-10-27T10:00:00Z", "temperature_c": 6.2, "source": "synthetic"}


def test_classify_above():
    assert classify(BASE)["status"] == "OPEN"
    assert classify({**BASE, "temperature_c": 4.0}) is None


def test_invalid_events():
    with pytest.raises(ValueError):
        validate({**BASE, "source": "unknown"})
    with pytest.raises(ValueError):
        validate({**BASE, "timestamp_utc": "yesterday"})
    with pytest.raises(ValueError):
        validate({**BASE, "temperature_c": float("nan")})


class StubSqs:
    def __init__(self, payload):
        self.payload = payload
        self.deleted = []
    def receive_message(self, **kw):
        return {"Messages": [{"Body": json.dumps(self.payload), "ReceiptHandle": "r1"}]}
    def delete_message(self, **kw):
        self.deleted.append(kw)

class StubSns:
    def __init__(self):
        self.sent = []
    def publish(self, **kw):
        self.sent.append(kw)


def test_worker_processes_alert(tmp_path):
    sqs, sns = StubSqs(BASE), StubSns()
    count = process_batch(sqs, sns, "queue", "topic", journal=tmp_path/"journal.jsonl")
    assert count == 1 and len(sns.sent) == 1 and len(sqs.deleted) == 1
    incident = json.loads((tmp_path/"journal.jsonl").read_text().strip())
    assert incident["source"] == "synthetic"
