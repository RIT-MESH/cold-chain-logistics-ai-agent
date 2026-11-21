# Observability module — Phase 1 tier (correction R3-8: every phase ships
# the alarms/observability for the services it creates).
#
# Phase 1 owns: CloudWatch log groups for the application containers (empty
# until Phase 3, free until logs are ingested; 90-day retention matches the
# ADR-10 audit-purge philosophy).
#
# The billing alarm (120% of the 07 estimate, R3-8) lives in the
# ENVIRONMENT root config, not here: CloudWatch publishes AWS/Billing
# metrics only in us-east-1, so the alarm uses the root's us-east-1
# provider alias and keeps working in any deployment region.

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

resource "aws_cloudwatch_log_group" "app" {
  for_each = toset(var.log_group_names)

  name              = "/coldchain/${var.environment}/${each.value}"
  retention_in_days = var.retention_days
  tags              = merge(local.common_tags, { Name = "coldchain-${var.environment}-${each.value}" })
}
