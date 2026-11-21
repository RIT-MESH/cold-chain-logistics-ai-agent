output "oidc_provider_arn" {
  value = aws_iam_openid_connect_provider.github.arn
}

output "role_arns" {
  description = "The four separated OIDC roles (C6/ADR-6) — wire into GitHub repo variables."
  value = {
    build   = aws_iam_role.build.arn
    plan    = aws_iam_role.plan.arn
    apply   = aws_iam_role.apply.arn
    release = aws_iam_role.release.arn
  }
}
