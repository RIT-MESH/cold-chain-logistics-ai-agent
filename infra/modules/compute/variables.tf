variable "environment" {
  type = string
}

variable "ami_id" {
  description = "PINNED AL2023 AMI id (stability over auto-updates — see main.tf note). Currently ami-0129e28f078083cdf (the running host's AMI, 2026-11-15)."
  type        = string
  default     = "ami-0129e28f078083cdf"
}

variable "instance_type" {
  description = "App host class — t3.large is the load-test CANDIDATE (ADR-17/R4-4), not a conclusion."
  type        = string
  default     = "t3.large"
}

variable "subnet_id" {
  description = "Public subnet for the host (IGW egress without NAT, ADR-13)."
  type        = string
}

variable "sg_app_id" {
  description = "sg-app — inbound only from sg-alb (01 §3.1)."
  type        = string
}

variable "root_volume_size" {
  description = "Root EBS gp3 size in GB (07 §3: 50 GB)."
  type        = number
  default     = 50
}

variable "secret_arns" {
  description = "Exact secret ARNs the instance profile may read (01 §5.3 runtime tmpfs fetch)."
  type        = list(string)
}

variable "event_dlq_arn" {
  description = "EXACT ARN of the Phase 4 telemetry dead-letter queue (modules/events) — the worker's only sqs:SendMessage target. Empty string before the events module exists (no DLQ-write statement is useful then; the value is only consumed by the Phase 4 profile which ships after the events apply)."
  type        = string
  default     = ""
}
