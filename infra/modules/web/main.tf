# Web module — Phase 3b public tier adapted to the AWS-generated CloudFront
# hostname (operator U-4 decision 2026-11-15; full design record:
# docs/evidence/PHASE3B_CLOUDFRONT_PROPOSAL.md). No custom domain exists
# anywhere in this module; ritmesh.com is never referenced.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

data "aws_region" "current" {}

locals {
  name_prefix = "coldchain-${var.environment}"
  common_tags = {
    Project     = "coldchain"
    Environment = var.environment
    ManagedBy   = "terraform"
    Phase       = "phase3b"
  }
}

# ---------------------------------------------------------------- origin guard secret

# Random token CloudFront presents to the ALB; listener rules forward ONLY
# when this header matches — direct-ALB requests get the 403 default.
resource "random_password" "origin_secret" {
  length  = 32
  special = false
}

# ---------------------------------------------------------------- Cognito (01 §7.1a, R4-1/R4-2)

resource "aws_cognito_user_pool" "operators" {
  name = "${local.name_prefix}-operators"

  # R4-2: pool membership IS the authorization for the test environment;
  # no group enforcement is claimed.
  admin_create_user_config {
    allow_admin_create_user_only = true # NO self-signup (01 §7.1a)
  }

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  # MFA optional (recommended ON if operators accept TOTP — 01 §7.1a);
  # left off for the temporary demo to reduce login friction.
  mfa_configuration = "OFF"

  tags = local.common_tags
}

resource "aws_cognito_user_pool_client" "alb" {
  name         = "${local.name_prefix}-alb"
  user_pool_id = aws_cognito_user_pool.operators.id

  generate_secret                      = true     # R4-1: the ALB app client MUST generate a secret
  allowed_oauth_flows                  = ["code"] # authorization code grant ONLY — no implicit
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email"]
  # OPTION B (2026-11-15): the OIDC redirect belongs to oauth2-proxy (the
  # Streamlit gate), NOT the ALB authenticate-cognito path
  # (/oauth2/idpresponse). Cognito enforces exact redirect_uri matching —
  # a wrong path = redirect_mismatch at the hosted-UI authorize step.
  callback_urls = ["https://${var.public_fqdn}/oauth2/callback"] # CloudFront hostname (U-4)
  # logout landing for the REAL logout flow (2026-11-16): after Cognito
  # hosted /logout terminates the pool session, the user lands on
  # /oauth2/start — the login entry — until they authenticate again.
  logout_urls = [
    "https://${var.public_fqdn}",
    "https://${var.public_fqdn}/oauth2/start",
  ]
  default_redirect_uri = "https://${var.public_fqdn}/oauth2/callback"

  explicit_auth_flows = [
    "ALLOW_ADMIN_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_PASSWORD_AUTH",
  ]

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 43200

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "minutes"
  }

  prevent_user_existence_errors = "ENABLED"
}

# AWS-generated hosted-UI domain (no custom domain anywhere — U-4).
resource "aws_cognito_user_pool_domain" "main" {
  domain       = "coldchain-${var.environment}"
  user_pool_id = aws_cognito_user_pool.operators.id
}

# ---------------------------------------------------------------- origin design (Option B)
# OPERATOR DECISION 2026-11-15 (Option B, after live-proven constraints):
# CloudFront refuses self-signed origin certificates (502, zero ALB
# arrivals, while plain TLS/SAN-matched handshakes succeed), and a
# publicly-trusted ALB cert is impossible without owning a domain (U-4).
# Design: CF -> ALB over HTTP:80 (CloudFront's documented ALB-origin
# pattern; no origin TLS). The VIEWER leg stays HTTPS on CloudFront's
# AWS-managed *.cloudfront.net certificate. Streamlit authentication
# moves to an oauth2-proxy container (OIDC against the Cognito pool
# below); LibreChat/Grafana keep their own logins (01 s7.4). The
# origin-secret guard rule still 403s all non-CloudFront traffic.

# ---------------------------------------------------------------- ALB

resource "aws_lb" "public" {
  name               = "${local.name_prefix}-web"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [var.sg_alb_id]
  subnets            = var.public_subnet_ids
  idle_timeout       = 300 # streamlit websockets / NIM-length agent turns

  tags = local.common_tags
}

resource "aws_lb_target_group" "streamlit" {
  name        = "${local.name_prefix}-app"
  port        = 4180 # oauth2-proxy auth gate (upstream = streamlit)
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip" # target is the EC2 host's PRIVATE IP
  # streamlit websockets:
  stickiness {
    type            = "lb_cookie"
    enabled         = true
    cookie_duration = 3600
  }
  health_check {
    path                = "/ping" # oauth2-proxy health endpoint
    port                = "4180"
    matcher             = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
  tags = local.common_tags
}

resource "aws_lb_target_group" "nginx" {
  name        = "${local.name_prefix}-nginx"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"
  health_check {
    path                = "/chat/healthz" # nginx sidecar exposes a probe
    port                = "8080"
    matcher             = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
  tags = local.common_tags
}

resource "aws_lb_target_group_attachment" "streamlit" {
  target_group_arn = aws_lb_target_group.streamlit.arn
  target_id        = var.target_private_ip
  port             = 4180 # oauth2-proxy (gates streamlit; own login stays LibreChat/Grafana)
}

resource "aws_lb_target_group_attachment" "nginx" {
  target_group_arn = aws_lb_target_group.nginx.arn
  target_id        = var.target_private_ip
  port             = 8080
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.public.arn
  port              = 80
  protocol          = "HTTP" # CF->ALB origin leg (viewer leg is HTTPS on CloudFront)

  # DEFAULT: anything unmatched = 403. Requests without the CloudFront
  # origin secret header match NO rule below -> 403 (no ALB bypass).
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "application/json"
      message_body = "{\"error\":\"forbidden\"}"
      status_code  = "403"
    }
  }

  tags = local.common_tags
}

resource "aws_lb_listener_rule" "chat" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10

  condition {
    path_pattern {
      values = ["/chat/*"]
    }
  }
  condition {
    http_header {
      http_header_name = "x-cf-origin-secret"
      values           = [random_password.origin_secret.result]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.nginx.arn
  }
}

resource "aws_lb_listener_rule" "grafana" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 20

  condition {
    path_pattern {
      values = ["/grafana/*"]
    }
  }
  condition {
    http_header {
      http_header_name = "x-cf-origin-secret"
      values           = [random_password.origin_secret.result]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.nginx.arn
  }
}

# The Cognito ALB authenticate-cognito action is REMOVED (Option B): it
# requires an HTTPS listener, which requires a certificate that cannot be
# obtained without a domain. Streamlit auth = oauth2-proxy (OIDC against
# the Cognito pool); its listener rule is the generic guarded app rule.

resource "aws_lb_listener_rule" "app" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 30

  condition {
    http_header {
      http_header_name = "x-cf-origin-secret"
      values           = [random_password.origin_secret.result]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.streamlit.arn
  }
}

# ---------------------------------------------------------------- CloudFront

resource "aws_cloudfront_distribution" "web" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "Cold-chain public web tier (default cert, U-4 decision)"
  price_class     = "PriceClass_100" # NA only — demo users
  http_version    = "http2and3"

  origin {
    domain_name = aws_lb.public.dns_name
    origin_id   = "alb"

    custom_origin_config {
      # OPTION B (operator decision 2026-11-15): CF->ALB over plain HTTP:80.
      # CloudFront refuses self-signed origin certificates (502, live-proven
      # 2026-11-15), and a publicly-trusted ALB cert is impossible without
      # owning a domain (U-4). The VIEWER leg stays HTTPS on the AWS-managed
      # *.cloudfront.net default certificate; this origin leg is NOT
      # encrypted in transit — documented limitation (proposal §3, 01 §7).
      # https_port/origin_ssl_protocols are schema-required but ignored
      # under the http-only policy.
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "http-only"
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60 # CloudFront max; app turns run server-side, the browser exchanges keepalive frames
      origin_keepalive_timeout = 60
    }

    # Guard header — must match the ALB listener rules or the request 403s.
    custom_header {
      name  = "x-cf-origin-secret"
      value = random_password.origin_secret.result
    }

    # Phase 3b two-step apply: X-Forwarded-Host carries the public CF
    # hostname to the ALB so OAuth redirects (oauth2-proxy OIDC flow) and
    # app-generated absolute URLs carry the cloudfront.net domain the
    # browser actually sees (proposal §3.2).
    custom_header {
      name  = "X-Forwarded-Host"
      value = var.cf_host_header == "" ? "placeholder.invalid" : var.cf_host_header
    }
  }

  # Streamlit root (default behavior)
  default_cache_behavior {
    target_origin_id       = "alb"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    # Default behavior (streamlit root): forward-all / cache-none (below).
    # Legacy forwarding (no managed-policy IDs — live-found NoSuchCachePolicy):
    # forward EVERYTHING, cache NOTHING. Cookies = Cognito + app sessions;
    # headers = all viewer headers except the reserved Host; TTLs = 0.
    forwarded_values {
      query_string = true
      cookies {
        forward = "all"
      }
      headers = ["*"]
    }
    min_ttl     = 0
    default_ttl = 0
    max_ttl     = 0
  }

  ordered_cache_behavior {
    target_origin_id       = "alb"
    path_pattern           = "/chat/*"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    # Legacy forwarding (no managed-policy IDs — live-found NoSuchCachePolicy):
    # forward EVERYTHING, cache NOTHING. Cookies = Cognito + app sessions;
    # headers = all viewer headers except the reserved Host; TTLs = 0.
    forwarded_values {
      query_string = true
      cookies {
        forward = "all"
      }
      headers = ["*"]
    }
    min_ttl     = 0
    default_ttl = 0
    max_ttl     = 0
  }

  ordered_cache_behavior {
    target_origin_id       = "alb"
    path_pattern           = "/grafana/*"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    # Legacy forwarding (no managed-policy IDs — live-found NoSuchCachePolicy):
    # forward EVERYTHING, cache NOTHING. Cookies = Cognito + app sessions;
    # headers = all viewer headers except the reserved Host; TTLs = 0.
    forwarded_values {
      query_string = true
      cookies {
        forward = "all"
      }
      headers = ["*"]
    }
    min_ttl     = 0
    default_ttl = 0
    max_ttl     = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # U-4: AWS-managed *.cloudfront.net certificate — no ACM aliases involved.
  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------- Alarms (R3-8 co-deploy)

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${local.name_prefix}-alb-5xx"
  alarm_description   = "ALB 5xx responses (R3-8: controls ship with the service)"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_ELB_5XXCount"
  dimensions          = { LoadBalancer = aws_lb.public.arn_suffix }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  tags                = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "alb_target_latency" {
  alarm_name          = "${local.name_prefix}-alb-target-latency"
  alarm_description   = "ALB target response time (NIM-length agent turns make this coarse by design)"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  dimensions          = { LoadBalancer = aws_lb.public.arn_suffix }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 120
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  tags                = local.common_tags
}
