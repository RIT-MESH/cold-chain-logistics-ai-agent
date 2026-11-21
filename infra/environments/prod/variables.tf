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
