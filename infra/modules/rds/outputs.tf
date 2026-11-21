output "db_instance_id" {
  value = aws_db_instance.this.id
}

output "endpoint" {
  description = "SQL Server endpoint for the app host (SSM port-forward for admin, 01 §3)."
  value       = aws_db_instance.this.endpoint
}

output "master_secret_arn" {
  description = "RDS-managed master credential secret (C8 — read via instance profile / admin SSM session)."
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}
