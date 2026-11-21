"""Idempotent S3/SQS/SNS resource bootstrap for local Floci or explicitly configured AWS."""
import os
from src.telemetry.common import client


def bootstrap():
    if not os.getenv("AWS_ENDPOINT_URL") and os.getenv("ALLOW_REAL_AWS_BOOTSTRAP") != "yes":
        raise RuntimeError("Set AWS_ENDPOINT_URL for Floci; real AWS resource creation requires explicit opt-in")
    s3, sqs, sns = (client(x) for x in ("s3", "sqs", "sns"))
    bucket = os.getenv("TELEMETRY_BUCKET", "coldchain-local-telemetry")
    region = os.getenv("AWS_REGION", "us-east-1")
    existing = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
    if bucket not in existing:
        args = {"Bucket": bucket}
        if region != "us-east-1":
            args["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**args)
    queue = sqs.create_queue(QueueName=os.getenv("TELEMETRY_QUEUE_NAME", "coldchain-telemetry"))["QueueUrl"]
    topic = sns.create_topic(Name=os.getenv("TELEMETRY_ALERT_TOPIC_NAME", "coldchain-alerts"))["TopicArn"]
    print(f"S3 bucket: {bucket}\nSQS queue: {queue}\nSNS topic: {topic}")
    return bucket, queue, topic


if __name__ == "__main__":
    bootstrap()
