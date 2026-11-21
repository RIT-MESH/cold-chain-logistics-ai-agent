"""AWS-compatible client construction with explicit local vs AWS selection."""
import os
import boto3


def client(service, *, endpoint=None):
    kw = {"region_name": os.getenv("AWS_REGION", "us-east-1")}
    url = endpoint if endpoint is not None else os.getenv("AWS_ENDPOINT_URL")
    if url:
        kw.update(endpoint_url=url, aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
                  aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"))
    return boto3.client(service, **kw)
