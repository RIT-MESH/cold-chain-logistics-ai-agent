variable "aws_region" {
  description = "Deployment region (U-2 recommendation: us-east-1)."
  type        = string
}

variable "github_org" {
  type = string
}

variable "github_repo" {
  type = string
}

variable "tf_state_bucket" {
  description = "State bucket from scripts/bootstrap_tfstate.ps1."
  type        = string
}

variable "billing_alarm_threshold_usd" {
  description = "R3-8: 120% of the approved 07 estimate for the chosen operating mode."
  type        = number
}

variable "apex_domain" {
  description = <<-EOT
    U-4 — the one registrable apex domain (ADR-15 same-site cookie rule).
    PLACEHOLDER until the operator chooses a domain; Phase 1 creates NO DNS
    resources — Phase 3 wires these into the Route 53 zone, ACM SANs, ALB
    host rules, Cognito callback and CSP origins.
  EOT
  type        = string
}

# ------------------------------------------------------------------ Phase 2

variable "app_instance_type" {
  description = "EC2 app host class (ADR-17: t3.large is the load-test CANDIDATE, R4-4)."
  type        = string
  default     = "t3.large"
}

variable "app_root_volume_gb" {
  description = "Root EBS gp3 size (07 §3: 50 GB)."
  type        = number
  default     = 50
}

variable "rds_instance_class" {
  description = "RDS SQL Server Express class (ADR-3: db.t3.small dev tier)."
  type        = string
  default     = "db.t3.small"
}

variable "rds_engine_version" {
  description = "Servable sqlserver-ex version (queried live 2026-11-15: SQL Server 2022 latest patch)."
  type        = string
  default     = "16.00.4295.3.v1"
}

variable "rds_storage_gb" {
  description = "RDS gp3 storage (07 §3: 20 GB)."
  type        = number
  default     = 20
}

# ------------------------------------------------------------------ Phase 3b

variable "public_fqdn" {
  description = "CloudFront hostname (no scheme) for Cognito callback/logout. placeholder.invalid on apply #1; the real dXXXXXXXX.cloudfront.net on apply #2."
  type        = string
  default     = "placeholder.invalid"
}

variable "cf_host_header" {
  description = "X-Forwarded-Host origin header value (apply #2 only)."
  type        = string
  default     = ""
}
