variable "environment" {
  description = "Environment name (dev/prod) — used in resource names and tags."
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR (blueprint 01 §3: 10.0.0.0/16, 2 AZs)."
  type        = string
  default     = "10.0.0.0/16"
}
