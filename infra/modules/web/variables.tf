variable "environment" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "vpc_id" {
  type = string
}

variable "sg_alb_id" {
  type = string
}

variable "target_private_ip" {
  description = "EC2 host private IP (ALB targets reach the compose ports on it; sg-app allows only sg-alb)."
  type        = string
}

variable "public_fqdn" {
  description = "The public CloudFront hostname (https://<this>/oauth2/idpresponse). Computed: first apply uses the placeholder, second apply passes the real domain."
  type        = string
}

variable "cf_host_header" {
  description = "CloudFront domain for the X-Forwarded-Host origin header. EMPTY on apply #1, the real dname on apply #2 (proposal §3.2)."
  type        = string
  default     = ""
}
