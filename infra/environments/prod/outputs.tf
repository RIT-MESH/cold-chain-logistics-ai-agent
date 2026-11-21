# U-4 placeholders — visible in every plan; nothing is provisioned.
output "app_fqdn" {
  description = "Streamlit hostname (Cognito-gated at the ALB, Phase 3)."
  value       = local.app_fqdn
}

output "chat_fqdn" {
  description = "LibreChat hostname (own login, Phase 3)."
  value       = local.chat_fqdn
}

output "grafana_fqdn" {
  description = "Grafana hostname (own login, Phase 3)."
  value       = local.grafana_fqdn
}

output "cognito_callback_url" {
  description = "ALB authenticate-cognito callback (01 §7.1a; app client created Phase 3)."
  value       = "https://${local.app_fqdn}/oauth2/idpresponse"
}

output "acm_san_list" {
  description = "Subject-alternative names for the single ACM certificate (Phase 3; issued in-region)."
  value       = [local.app_fqdn, local.chat_fqdn, local.grafana_fqdn]
}
