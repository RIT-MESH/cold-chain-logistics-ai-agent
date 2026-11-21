output "instance_id" {
  value = aws_instance.app_host.id
}

output "instance_profile_role_name" {
  value = aws_iam_role.app_host.name
}

output "private_ip" {
  description = "Host private IP — ALB target (web module)."
  value       = aws_instance.app_host.private_ip
}
