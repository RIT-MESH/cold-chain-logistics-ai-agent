output "log_group_arns" {
  value = { for name, lg in aws_cloudwatch_log_group.app : name => lg.arn }
}

output "gateway_log_group_name" {
  description = "Where the C12 token_usage line ships (Phase 3 wiring; ColdChain/NimTokens at Phase 3, R6-1)."
  value       = "/coldchain/${var.environment}/gateway"
}
