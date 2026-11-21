# Events module — Phase 4 event plane (blueprint 03 §B3, operator Phase 4
# directive 2026-11-17). Creates ONLY the coldchain-dev-scoped event
# resources; no EC2/RDS/CloudFront/Cognito interaction, no apply run yet.
#
#   S3 coldchain-dev-telemetry (SSE, versioned)
#     └─ ObjectCreated:Put prefix synthetic/ → SQS coldchain-dev-telemetry
#          └─ redrive (maxReceiveCount=5) → SQS coldchain-dev-telemetry-dlq
#   SNS coldchain-dev-alerts (Standard topic — R5-1; NO subscriptions:
#     the notification recipient is NOT confirmed; subscribing anyone is an
#     operator authorization gate, directive §7)
#
# Alarms (R3-8: every phase ships its own observability): DLQ depth, queue
# oldest-message age, SNS publish failures, and the two reconcile custom
# metrics emitted by src/telemetry/reconcile.py (ColdChain/Phase4). Alarms
# have NO actions yet — there is no confirmed recipient; they are visible in
# the CloudWatch console and cost nothing while green.
#
# Delivery semantics are at-least-once END TO END by design (R5-2): SQS is
# at-least-once, SNS Standard is at-least-once, and the publisher may deliver
# the same event both directly and via the S3 notification path — the
# worker's EventId dedup (dbo.TelemetryEvents PK + NOT EXISTS transaction)
# is the idempotency anchor, not the transport.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  common_tags = {
    Project     = "coldchain"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# --- SQS --------------------------------------------------------------------

# Dead-letter queue: poison messages (invalid payload, bad shipment ref are
# ALSO written here directly by the worker with a FailureReason attribute;
# unhandled Class A failures land here via redrive after maxReceiveCount).
resource "aws_sqs_queue" "telemetry_dlq" {
  name                       = "coldchain-${var.environment}-telemetry-dlq"
  message_retention_seconds  = var.dlq_retention_seconds
  visibility_timeout_seconds = var.visibility_timeout_seconds
  tags                       = merge(local.common_tags, { Name = "coldchain-${var.environment}-telemetry-dlq" })
}

# Main telemetry queue. Visibility 120 s covers a DB write + SNS publish with
# margin; redrive after 5 receives (blueprint 03 §B3).
resource "aws_sqs_queue" "telemetry" {
  name                       = "coldchain-${var.environment}-telemetry"
  visibility_timeout_seconds = var.visibility_timeout_seconds
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.telemetry_dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
  tags = merge(local.common_tags, { Name = "coldchain-${var.environment}-telemetry" })
}

# S3 → SQS: only this bucket's service principal may SendMessage, proven by
# SourceArn (no wildcard principals).
resource "aws_sqs_queue_policy" "s3_events" {
  queue_url = aws_sqs_queue.telemetry.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowS3EventPublish"
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.telemetry.arn
      Condition = { ArnLike = { "aws:SourceArn" = aws_s3_bucket.telemetry.arn } }
    }]
  })
}

# --- S3 ---------------------------------------------------------------------

resource "aws_s3_bucket" "telemetry" {
  bucket = "coldchain-${var.environment}-telemetry"
  tags   = merge(local.common_tags, { Name = "coldchain-${var.environment}-telemetry" })
}

resource "aws_s3_bucket_versioning" "telemetry" {
  bucket = aws_s3_bucket.telemetry.id
  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-S3 (AES256) for the synthetic event archive — same posture as the
# state bucket (C2's SSE-KMS CMK remains a pending account-level upgrade;
# nothing here blocks moving to KMS later).
resource "aws_s3_bucket_server_side_encryption_configuration" "telemetry" {
  bucket = aws_s3_bucket.telemetry.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "telemetry" {
  bucket                  = aws_s3_bucket.telemetry.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Object-created notifications for the APPROVED input prefix only
# (directive §4: "approved event input prefixes"). Keys outside
# synthetic/ generate no notifications at all.
resource "aws_s3_bucket_notification" "telemetry" {
  bucket = aws_s3_bucket.telemetry.id
  queue {
    queue_arn     = aws_sqs_queue.telemetry.arn
    events        = ["s3:ObjectCreated:Put"]
    filter_prefix = var.input_prefix
  }
  depends_on = [aws_sqs_queue_policy.s3_events] # the queue policy must exist first
}

# --- SNS --------------------------------------------------------------------

# Standard topic by design (R5-1: FIFO topics cannot take email
# subscriptions; the pipeline stays Standard with dedup at the consumer).
# NO aws_sns_topic_subscription here, ever, without explicit operator
# authorization — the recipient is unconfirmed (directive §7).
resource "aws_sns_topic" "alerts" {
  name = "coldchain-${var.environment}-alerts"
  tags = merge(local.common_tags, { Name = "coldchain-${var.environment}-alerts" })
}

# --- Alarms -----------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  alarm_name          = "coldchain-${var.environment}-event-dlq-depth"
  alarm_description   = "Dead-letter queue holds messages — poison payloads, bad shipment refs, or repeated processing failures need review."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.telemetry_dlq.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  tags                = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "queue_oldest_age" {
  alarm_name          = "coldchain-${var.environment}-queue-oldest-age"
  alarm_description   = "Oldest telemetry message older than 5 minutes — worker down or unable to keep up."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = aws_sqs_queue.telemetry.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 300
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  tags                = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "sns_failures" {
  alarm_name          = "coldchain-${var.environment}-sns-publish-failures"
  alarm_description   = "SNS notification failures observed on the alerts topic (R3-8 delivery-failure signal; email delivery itself is only observable at topic level — B4)."
  namespace           = "AWS/SNS"
  metric_name         = "NumberOfNotificationsFailed"
  dimensions          = { TopicName = aws_sns_topic.alerts.name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  tags                = local.common_tags
}

# Reconcile custom metrics (emitted by src/telemetry/reconcile.py).
# IncidentsPendingNotification already encodes "unnotified > 30 min".
resource "aws_cloudwatch_metric_alarm" "reconcile_pending" {
  alarm_name          = "coldchain-${var.environment}-reconcile-pending-age"
  alarm_description   = "Incidents unnotified for more than 30 minutes after commit (Class B failures not yet republished, or publish retries still failing)."
  namespace           = "ColdChain/Phase4"
  metric_name         = "IncidentsPendingNotification"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  tags                = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "reconcile_exhausted" {
  alarm_name          = "coldchain-${var.environment}-reconcile-exhausted"
  alarm_description   = "Notification retries exhausted (NOTIFY_MAX_ATTEMPTS reached without SNS ack) — fail-closed silence, manual review required (blueprint 03 §B5)."
  namespace           = "ColdChain/Phase4"
  metric_name         = "IncidentsNotifyExhausted"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  tags                = local.common_tags
}
