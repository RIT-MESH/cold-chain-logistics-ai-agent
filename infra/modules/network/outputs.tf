output "vpc_id" {
  value = aws_vpc.this.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "data_subnet_ids" {
  value = aws_subnet.data[*].id
}

output "public_route_table_id" {
  value = aws_route_table.public.id
}

output "data_route_table_id" {
  value = aws_route_table.data.id
}

output "sg_alb_id" {
  description = "01 §3.1 sg-alb — attach the Phase 3 ALB here."
  value       = aws_security_group.alb.id
}

output "sg_app_id" {
  description = "01 §3.1 sg-app — attach the Phase 2 EC2 host here."
  value       = aws_security_group.app.id
}

output "sg_db_id" {
  description = "01 §3.1 sg-db — attach the Phase 2 RDS instance here."
  value       = aws_security_group.db.id
}

output "internet_gateway_id" {
  value = aws_internet_gateway.this.id
}
