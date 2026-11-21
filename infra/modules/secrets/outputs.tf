output "secret_arns" {
  description = "ARNs of the empty containers — instance profile gets read on these exact ARNs at Phase 2+ (01 §5.3)."
  value       = { for name, secret in aws_secretsmanager_secret.this : name => secret.arn }
}

output "secret_ids" {
  value = { for name, secret in aws_secretsmanager_secret.this : name => secret.id }
}
