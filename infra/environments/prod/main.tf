# Cold-Chain prod(-shaped) environment — Phase 1 Terraform foundation.
# Same Phase 1 scope as dev (network/secrets/log groups/OIDC/billing alarm)
# with its OWN state key (env/prod) and IAM boundary: the dev apply role can
# never read or write prod state (C2). The prod-shaped RDS Standard tier
# (~$432/mo, 07 §4) and any instance sizing are Phase 2+ decisions — this
# config deploys no compute or data services.

terraform {
  required_version = ">= 1.10" # use_lockfile (ADR-14); CI pins 1.16.3

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {} # key = env/prod/terraform.tfstate via -backend-config
}

provider "aws" {
  region = var.aws_region
  # Safety pin: project account only (see dev/main.tf note).
  allowed_account_ids = ["123456789012"]
  # No provider-level default_tags (IAM OIDC objects stay untagged; see dev).
}

provider "aws" {
  alias  = "billing"
  region = "us-east-1"
}

module "network" {
  source      = "../../modules/network"
  environment = "prod"
}

module "secrets" {
  source      = "../../modules/secrets"
  environment = "prod"
}

module "observability" {
  source      = "../../modules/observability"
  environment = "prod"
}

module "oidc" {
  source           = "../../modules/oidc"
  environment      = "prod"
  github_org       = var.github_org
  github_repo      = var.github_repo
  state_bucket     = var.tf_state_bucket
  state_key_prefix = "env/prod"
}

# U-4 domain/DNS placeholders (ADR-15) — NO DNS resources in Phase 1; the
# Phase 3 web tier consumes these locals (see dev/main.tf comment).
locals {
  app_fqdn     = "app.${var.apex_domain}"
  chat_fqdn    = "chat.${var.apex_domain}"
  grafana_fqdn = "grafana.${var.apex_domain}"
}

# R3-8: 120% of the 07 §4 production-shaped estimate (~$540/mo) → $650.
resource "aws_cloudwatch_metric_alarm" "billing" {
  provider          = aws.billing
  alarm_name        = "coldchain-prod-billing-120pct"
  alarm_description = "Prod environment bill exceeds 120% of the approved 07 estimate (~$540/mo) - R3-8"

  namespace   = "AWS/Billing"
  metric_name = "EstimatedCharges"
  dimensions = {
    Currency = "USD"
  }

  comparison_operator = "GreaterThanThreshold"
  threshold           = var.billing_alarm_threshold_usd
  evaluation_periods  = 1
  period              = 21600
  statistic           = "Maximum"
  treat_missing_data  = "notBreaching"

  tags = {
    Project     = "coldchain"
    Environment = "prod"
    ManagedBy   = "terraform"
  }
}
