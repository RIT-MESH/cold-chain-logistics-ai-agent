# Cold-Chain dev environment — Phase 1 Terraform foundation
# (blueprint 09 Phase 1): network, secrets containers, log groups, OIDC
# roles, billing alarm. NO EC2, NO RDS, NO ALB, NO application containers
# in this phase. terraform apply is a separate, operator-approved step.
#
# State: S3 backend configured at init via -backend-config (see
# infra/backend.hcl.example) — use_lockfile = true, separate key
# env/dev/terraform.tfstate, dev/prod IAM boundaries in modules/oidc (C2).

terraform {
  required_version = ">= 1.10" # S3 native state locking (use_lockfile, ADR-14); CI pins 1.16.3

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {}
}

provider "aws" {
  region = var.aws_region
  # Safety pin (operator-approved region/account, 2026-11-15): plan/apply
  # can only run against the project account 123456789012 — never a
  # different account by accident. Locally: set AWS_PROFILE=coldchain-dev;
  # CI uses the OIDC role via env credentials (profile stays unset there).
  allowed_account_ids = ["123456789012"]
  # No provider-level default_tags: every resource sets explicit tags
  # except IAM OIDC objects (operator policy grants no IAM tagging -
  # found live 2026-11-15). Teardown inventories by name prefix + URL.
}

# CloudWatch billing metrics (AWS/Billing) are published ONLY in us-east-1;
# this provider alias keeps the R3-8 billing alarm reading them regardless
# of the deployment region chosen for U-2.
provider "aws" {
  alias  = "billing"
  region = "us-east-1"
}

module "network" {
  source      = "../../modules/network"
  environment = "dev"
}

module "secrets" {
  source      = "../../modules/secrets"
  environment = "dev"
}

module "observability" {
  source      = "../../modules/observability"
  environment = "dev"
}

module "oidc" {
  source           = "../../modules/oidc"
  environment      = "dev"
  github_org       = var.github_org
  github_repo      = var.github_repo
  state_bucket     = var.tf_state_bucket
  state_key_prefix = "env/dev"
}

# ---------------------------------------------------------------------------
# U-4 domain/DNS placeholders (ADR-15) — NO DNS resources are created in
# Phase 1. These locals make the planned browser hostnames visible in every
# `terraform plan` so the Phase 3 wiring (Route 53 zone, ACM SANs, ALB host
# rules, Cognito callback, CSP origins) starts from a single source of
# truth. Replace the apex_domain placeholder in dev.tfvars when chosen.
# ---------------------------------------------------------------------------
# PHASE 2 (R3-1/ADR-16): the EC2 host is provisioned FIRST, then RDS. All
# schema/view/grant/import steps execute ON this host via SSM (C7) — never
# from CI runners. No application containers yet (Phase 3).

module "compute" {
  source           = "../../modules/compute"
  environment      = "dev"
  instance_type    = var.app_instance_type
  subnet_id        = module.network.public_subnet_ids[0]
  sg_app_id        = module.network.sg_app_id
  root_volume_size = var.app_root_volume_gb
  secret_arns      = concat(values(module.secrets.secret_arns), [module.rds.master_secret_arn]) # exact ARNs only (01 §6)
  event_dlq_arn    = module.events.dlq_queue_arn                                                # Phase 4: worker's ONLY sqs:SendMessage target
}

# PHASE 4 (operator directive 2026-11-17): event plane — S3 telemetry
# archive + ObjectCreated→SQS notification, SQS + DLQ redrive, SNS alerts
# topic (NO subscriptions — recipient unconfirmed, operator gate), and the
# queue/notification alarms (R3-8). No apply run yet: deployment is a
# separate operator approval (directive §11).
module "events" {
  source      = "../../modules/events"
  environment = "dev"
}

module "rds" {
  source            = "../../modules/rds"
  environment       = "dev"
  data_subnet_ids   = module.network.data_subnet_ids
  sg_db_id          = module.network.sg_db_id
  instance_class    = var.rds_instance_class
  engine_version    = var.rds_engine_version
  allocated_storage = var.rds_storage_gb
}

# ---------------------------------------------------------------------------
# PHASE 3b: public web tier on the CloudFront default hostname (U-4 decision
# 2026-11-15 — no custom domain; ritmesh.com untouched). TWO-STEP apply:
# apply #1 creates the distribution (placeholder OAuth URLs); apply #2 sets
# the real cloudfront.net host in public_fqdn/cf_host_header (proposal §3.2).

module "web" {
  source            = "../../modules/web"
  environment       = "dev"
  public_subnet_ids = module.network.public_subnet_ids
  vpc_id            = module.network.vpc_id
  sg_alb_id         = module.network.sg_alb_id
  target_private_ip = module.compute.private_ip
  public_fqdn       = var.public_fqdn
  cf_host_header    = var.cf_host_header
}

locals {
  app_fqdn     = "app.${var.apex_domain}"     # Streamlit — Cognito-gated (01 §7.1a)
  chat_fqdn    = "chat.${var.apex_domain}"    # LibreChat (own login)
  grafana_fqdn = "grafana.${var.apex_domain}" # Grafana (own login)
  # api (FastAPI gateway) is NOT publicly routed (compose-network only, 01 §7.1)
}

# ---------------------------------------------------------------------------
# Billing alarm — deployed FIRST, at 120% of the 07 dev-tier estimate
# (R3-8 / lever 1). Mode A (24/7) ≈ $135-140/mo → threshold $170/mo.
# Adjust at approval if U-10 resolves to dev-hours mode (07 §6).
# Phase 4 wires alarm_actions to the ops SNS topic; until then the alarm is
# visible/firing in the console (acceptance: synthetic threshold breach).
resource "aws_cloudwatch_metric_alarm" "billing" {
  provider          = aws.billing
  alarm_name        = "coldchain-dev-billing-120pct"
  alarm_description = "Dev environment bill exceeds 120% of the approved 07 estimate (Mode A ~$140/mo) - R3-8"

  namespace   = "AWS/Billing"
  metric_name = "EstimatedCharges"
  dimensions = {
    Currency = "USD"
  }

  comparison_operator = "GreaterThanThreshold"
  threshold           = var.billing_alarm_threshold_usd
  evaluation_periods  = 1
  period              = 21600 # 6h — EstimatedCharges publishes ~8x/day
  statistic           = "Maximum"
  treat_missing_data  = "notBreaching"

  tags = {
    Project     = "coldchain"
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

# ---------------------------------------------------------------------------
# Root outputs (Phase 3b)

output "cloudfront_url" {
  description = "THE public HTTPS entry point (U-4: AWS-generated hostname)."
  value       = module.web.cloudfront_url
}

output "cloudfront_domain" {
  value = module.web.cloudfront_domain
}

output "cognito_pool_id" {
  value = module.web.cognito_pool_id
}

output "cognito_auth_domain" {
  value = module.web.cognito_auth_domain
}

output "origin_secret" {
  sensitive = true
  value     = module.web.origin_secret
}
