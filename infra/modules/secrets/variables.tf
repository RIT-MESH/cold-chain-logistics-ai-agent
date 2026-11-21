variable "environment" {
  description = "Environment name (dev/prod)."
  type        = string
}

variable "secret_names" {
  description = <<-EOT
    Container names. Phase 1 set (matches the ~9-10 secrets of 07 §3 and the
    identities of 05 §3 / ADR-4): LLM gateway key, gateway bearer key,
    SOP-store key, RDS master, four per-consumer view-only SQL identities
    plus the temporary import admin.
  EOT
  type        = list(string)
  default = [
    "nvidia-api-key",      # NVIDIA NIM (R6-1) — external provider, no AWS rotation
    "fde-api-key",         # gateway bearer key (api/main.py require_key)
    "pinecone-api-key",    # SOP vector store — external provider
    "sql-master",          # RDS master credential (RDS-managed rotation)
    "sql-agent-ro",        # USR_FDE_RO      — agent identity, views only
    "sql-ui-ro",           # USR_COLDCHAIN_UI_RO — Streamlit identity, views only
    "sql-grafana-ro",      # USR_GRAFANA_RO  — Grafana identity, views only
    "sql-incident-writer", # INSERT-only incident writer (ADR-7)
    "sql-audit-writer",    # INSERT-only audit writer (ADR-10)
    "sql-import-admin",    # temporary Phase 2 import/grants admin, revoked after (ADR-4)
    "cognito-client",      # Phase 3b Option B: oauth2-proxy OIDC client credentials
    # (the Cognito client secret is NOT retrievable via IaC/API after
    # creation — stored here out-of-band, keeping it OUT of Terraform
    # state entirely; even better than the R4-1 baseline)
  ]
}
