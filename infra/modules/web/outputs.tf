output "cloudfront_domain" {
  description = "The generated *.cloudfront.net URL — the public entry point (U-4)."
  value       = aws_cloudfront_distribution.web.domain_name
}

output "cloudfront_url" {
  value = "https://${aws_cloudfront_distribution.web.domain_name}"
}

output "cognito_pool_id" {
  value = aws_cognito_user_pool.operators.id
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.alb.id
}

output "cognito_auth_domain" {
  value = "${aws_cognito_user_pool_domain.main.domain}.auth.${data.aws_region.current.name}.amazoncognito.com"
}

output "alb_dns_name" {
  value = aws_lb.public.dns_name
}

output "origin_secret" {
  sensitive = true
  value     = random_password.origin_secret.result
}
