output "queue_url" {
  value = aws_sqs_queue.telemetry.url
}

output "queue_arn" {
  value = aws_sqs_queue.telemetry.arn
}

output "dlq_queue_url" {
  value = aws_sqs_queue.telemetry_dlq.url
}

output "dlq_queue_arn" {
  value = aws_sqs_queue.telemetry_dlq.arn
}

output "topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "bucket_name" {
  value = aws_s3_bucket.telemetry.id
}

output "bucket_arn" {
  value = aws_s3_bucket.telemetry.arn
}
