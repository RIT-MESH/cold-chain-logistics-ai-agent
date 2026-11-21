variable "environment" {
  type = string
}

variable "log_group_names" {
  description = "Application log groups (gateway token_usage line lands in `gateway`, C12)."
  type        = list(string)
  default     = ["gateway", "worker", "streamlit", "system"]
}

variable "retention_days" {
  description = "Log retention (ADR-10 90-day purge philosophy)."
  type        = number
  default     = 90
}
