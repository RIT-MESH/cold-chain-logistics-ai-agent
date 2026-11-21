variable "environment" {
  description = "Deployment environment (dev/prod) — names are coldchain-<environment>-*"
  type        = string
}

variable "visibility_timeout_seconds" {
  description = "SQS visibility timeout — must cover a DB write + SNS publish with margin"
  type        = number
  default     = 120
}

variable "max_receive_count" {
  description = "Redrive to the DLQ after this many receives (blueprint 03 §B3 default 5)"
  type        = number
  default     = 5
}

variable "dlq_retention_seconds" {
  description = "Poison-message retention (14 days default)"
  type        = number
  default     = 1209600
}

variable "input_prefix" {
  description = "Approved S3 event input prefix (provenance boundary)"
  type        = string
  default     = "synthetic/"
}
