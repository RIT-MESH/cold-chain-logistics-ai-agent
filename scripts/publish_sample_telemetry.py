"""Publish clearly synthetic events and archive each event in emulated S3."""
import json
import os
import uuid
from datetime import datetime, timezone
from src.telemetry.common import client
from src.telemetry.events import validate


def publish(temp=6.2):
    if not os.getenv("AWS_ENDPOINT_URL") and os.getenv("ALLOW_REAL_AWS_PUBLISH") != "yes":
        raise RuntimeError("Set AWS_ENDPOINT_URL for Floci to avoid accidental publication to AWS")
    event = {"event_id": str(uuid.uuid4()), "shipment_id": os.getenv("DEMO_SHIPMENT_ID", "UC-003241"),
             "timestamp_utc": datetime.now(timezone.utc).isoformat(),
             "temperature_c": float(temp), "source": "synthetic"}
    validate(event)
    bucket = os.getenv("TELEMETRY_BUCKET", "coldchain-local-telemetry")
    queue_name = os.getenv("TELEMETRY_QUEUE_NAME", "coldchain-telemetry")
    payload = json.dumps(event)
    client("s3").put_object(Bucket=bucket, Key=f"synthetic/{event['event_id']}.json",
                            Body=payload.encode(), ContentType="application/json")
    sqs = client("sqs")
    url = sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
    sqs.send_message(QueueUrl=url, MessageBody=payload)
    print(payload)
    return event


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--temperature", type=float, default=6.2)
    publish(ap.parse_args().temperature)
