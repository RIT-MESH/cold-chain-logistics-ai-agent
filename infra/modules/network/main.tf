# Network module — "SG-isolated public-edge app tier + fully private data
# tier" (blueprint 01 §3, ADR-13). The EXACT security-group and route rules
# of 01 §3.1 (correction R3-4) are asserted by tests/test_infra_phase1.py
# and verified against a live account at Phase 2/3 acceptance
# (describe-security-groups + external port scan).
#
# Phase 1 creates the FOUNDATION ONLY: no EC2 instance, no RDS instance, no
# ALB exist yet (09 Phase 1: "no EC2 instance exists in this phase"). The
# SGs are created empty now so the Phase 2 host inherits the exact rule set
# the day it launches, and so the effective inbound surface of the host is
# empty until the ALB appears in Phase 3.
#
# No NAT gateway, no VPC endpoints in this posture (ADR-13 documented
# alternative: private app tier + NAT + endpoints at +$40-60/mo).

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name_prefix = "coldchain-${var.environment}"
  azs         = slice(data.aws_availability_zones.available.names, 0, 2)
  common_tags = {
    Project     = "coldchain"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ------------------------------------------------------------- VPC + IGW

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.common_tags, { Name = "${local.name_prefix}-vpc" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.common_tags, { Name = "${local.name_prefix}-igw" })
}

# ------------------------------------------------------------- Subnets

# Public subnets: ALB (Phase 3) + app host (Phase 2). A public IPv4 is
# assigned to instances here SOLELY so they can initiate outbound HTTPS via
# the IGW without a NAT gateway (ADR-13) — SGs default-deny inbound.
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.this.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index) # 10.0.0.0/24, 10.0.1.0/24
  map_public_ip_on_launch = true
  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-public-${count.index + 1}"
    Tier = "public"
  })
}

# Data subnets: RDS (Phase 2). NO route to the IGW, instances never receive
# a public IP, publicly_accessible=false is enforced again on the RDS
# module in Phase 2 (defense in depth).
resource "aws_subnet" "data" {
  count                   = 2
  vpc_id                  = aws_vpc.this.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 16 + count.index) # 10.0.16.0/24, 10.0.17.0/24
  map_public_ip_on_launch = false
  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-data-${count.index + 1}"
    Tier = "data"
  })
}

# ------------------------------------------------------------- Route tables

# Public: local + 0.0.0.0/0 -> IGW (the ONLY internet route in this VPC).
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.common_tags, { Name = "${local.name_prefix}-rt-public" })
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Data: NO routes are declared — the implicit VPC local route is all RDS
# gets. There is no aws_route with an IGW target on this table (asserted by
# tests); the internet is structurally unreachable from the data tier.
resource "aws_route_table" "data" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.common_tags, { Name = "${local.name_prefix}-rt-data" })
}

resource "aws_route_table_association" "data" {
  count          = 2
  subnet_id      = aws_subnet.data[count.index].id
  route_table_id = aws_route_table.data.id
}

# ------------------------------------------------------------- Security groups (01 §3.1, R3-4)

# sg-alb — public HTTPS edge (Phase 3 attaches the ALB to this SG).
# Inbound : TCP 443 from 0.0.0.0/0; TCP 80 from 0.0.0.0/0 (redirect only).
# Outbound: app container ports -> sg-app ONLY (separate rule resources
#           below — inline SG-to-SG egress would create a dependency cycle
#           alb->app->alb, since sg-app's ingress references this SG).
resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-sg-alb"
  description = "ALB edge - 443/80 from anywhere in, app ports out to sg-app (01 s3.1)"
  vpc_id      = aws_vpc.this.id

  # LIVE-FOUND 2026-11-15: ALB DNS names carry AAAA records and CloudFront
  # (IPv6-enabled) connects over IPv6 — without ::/0 rules the SG silently
  # dropped every CF origin request (502 with zero ALB arrivals; the cause
  # was misdiagnosed twice as origin-cert trust before this was measured).
  # Guard-header + Cognito rules are unchanged; IPv6 here is the §3.1
  # "443/80 from anywhere" intent extended to the IPv6 Internet.
  ingress {
    description      = "HTTPS from anywhere (v4+v6)"
    from_port        = 443
    to_port          = 443
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  ingress {
    description      = "HTTP from anywhere (v4+v6)"
    from_port        = 80
    to_port          = 80
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-sg-alb" })
}

# sg-app — the EC2 host (provisioned Phase 2; empty inbound until the ALB
# exists in Phase 3 — SSM admin is outbound-initiated, so the host has zero
# reachable inbound ports at Phase 2 acceptance).
# Inbound : ONLY the ALB target ports (8501/3000/3080/8000/8080/4180) from
#           sg-alb. No SSH rule of any kind
#           exists anywhere in this module (no key pair is ever created).
# Outbound: 443+80 to 0.0.0.0/0 (NVIDIA NIM, Pinecone, Open-Meteo,
#           registries, apt, AWS APIs via IGW — R6-1 endpoints); 1433 -> sg-db
#           (separate rule resource below — breaks the app<->db cycle).
resource "aws_security_group" "app" {
  name        = "${local.name_prefix}-sg-app"
  description = "App host - inbound ONLY from sg-alb, no SSH; out 443/80 to internet + 1433 to sg-db (01 s3.1)"
  vpc_id      = aws_vpc.this.id

  dynamic "ingress" {
    for_each = [8501, 3000, 3080, 8000, 8080, 4180] # 4180 = oauth2-proxy gate (Phase 3b)
    content {
      description     = "app port ${ingress.value} from the ALB SG only"
      from_port       = ingress.value
      to_port         = ingress.value
      protocol        = "tcp"
      security_groups = [aws_security_group.alb.id]
    }
  }

  # NOTE: sg-app's egress is managed ENTIRELY by separate
  # aws_vpc_security_group_egress_rule resources (below) — mixing inline
  # egress with separate rules on the same SG makes the provider treat the
  # separate rules as inline drift and revoke them (found live 2026-11-15).
  # An SG with no inline egress blocks keeps whatever rules its separate
  # rule resources create; the creation-time allow-all default only applies
  # to SGs with no egress rules at all.

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-sg-app" })
}

# sg-db — RDS (provisioned Phase 2).
# Inbound : ONLY TCP 1433 from sg-app.
# Outbound: the 01 §3.1 table says "none required (stateful return)". AWS
#           SGs are stateful, so return traffic needs no egress rule — but
#           a freshly created SG defaults to ALLOW-ALL egress, and
#           declaring ANY inline egress replaces that default. The single
#           minimal rule below exists to kill the default allow-all
#           (1433 back to the app tier only) — flagged for the Phase 2
#           acceptance reviewer as the one deliberate deviation from the
#           §3.1 table, strictly narrower than the AWS default.
resource "aws_security_group" "db" {
  name        = "${local.name_prefix}-sg-db"
  description = "RDS - inbound ONLY 1433 from sg-app; no internet route in its subnet (01 s3.1)"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "SQL from the app host SG only"
    from_port       = 1433
    to_port         = 1433
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  egress {
    description     = "stateful return path to the app tier only - RDS initiates nothing"
    from_port       = 1433
    to_port         = 1433
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-sg-db" })
}

# ------------------------------------------------- SG-to-SG rules (cycle breakers)

# ALB -> app container ports, to sg-app ONLY (01 §3.1 sg-alb outbound).
resource "aws_vpc_security_group_egress_rule" "alb_to_app" {
  # streamlit / grafana / librechat / gateway / nginx sidecar / oauth2-proxy
  # (for_each sets take strings; 4180 = oauth2-proxy gate, Phase 3b Option B)
  for_each = toset(["8501", "3000", "3080", "8000", "8080", "4180"])

  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.app.id
  description                  = "app port ${each.value} to the app host SG only"
  ip_protocol                  = "tcp"
  from_port                    = tonumber(each.value)
  to_port                      = tonumber(each.value)

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-sg-alb-egress-${each.value}" })
}

# App -> internet 443/80 (01 §3.1 sg-app outbound; NVIDIA NIM, Pinecone,
# Open-Meteo, registries, apt, AWS APIs via IGW — no NAT, ADR-13).
resource "aws_vpc_security_group_egress_rule" "app_to_internet" {
  for_each = toset(["443", "80"])

  security_group_id = aws_security_group.app.id
  cidr_ipv4         = "0.0.0.0/0"
  description       = "port ${each.value} outbound via IGW (NVIDIA NIM, Pinecone, Open-Meteo, registries, apt, AWS APIs)"
  ip_protocol       = "tcp"
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-sg-app-egress-${each.value}" })
}

# App -> RDS 1433, to sg-db ONLY (01 §3.1 sg-app outbound).
resource "aws_vpc_security_group_egress_rule" "app_to_db_sql" {
  security_group_id            = aws_security_group.app.id
  referenced_security_group_id = aws_security_group.db.id
  description                  = "SQL to the RDS SG only"
  ip_protocol                  = "tcp"
  from_port                    = 1433
  to_port                      = 1433

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-sg-app-egress-1433" })
}

