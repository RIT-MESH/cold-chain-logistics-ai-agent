variable "environment" {
  type = string
}

variable "github_org" {
  description = "GitHub organization/user owning the repository (OIDC sub condition)."
  type        = string
}

variable "github_repo" {
  description = "Repository name (OIDC sub condition)."
  type        = string
}

variable "state_bucket" {
  description = "Terraform state bucket name (from scripts/bootstrap_tfstate.ps1)."
  type        = string
}

variable "state_key_prefix" {
  description = "This environment's state key prefix, e.g. env/dev — the C2 IAM boundary (own prefix only)."
  type        = string
}
