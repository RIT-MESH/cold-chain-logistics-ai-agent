variable "environment" {
  type = string
}

variable "data_subnet_ids" {
  description = "The two no-IGW data subnets (network module output)."
  type        = list(string)
}

variable "sg_db_id" {
  description = "sg-db — inbound 1433 from sg-app only (01 §3.1)."
  type        = string
}

variable "instance_class" {
  description = "Express tier: db.t3.small (07 §3). Web edition excluded (ADR-3)."
  type        = string
  default     = "db.t3.small"
}

variable "engine_version" {
  description = "SQL Server Express version — pin in tfvars to a currently-servable Express version."
  type        = string
}

variable "allocated_storage" {
  description = "gp3 storage in GB (07 §3: 20 GB)."
  type        = number
  default     = 20
}

variable "master_username" {
  description = "Master user name (value only; the PASSWORD is RDS-managed in Secrets Manager, never in state)."
  type        = string
  default     = "cc_admin"
}
