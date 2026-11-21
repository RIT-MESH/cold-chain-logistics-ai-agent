# RDS module — SQL Server Express, the dev/test tier (ADR-3/correction C3,
# 07 §2-3). Express IS licensed for internal applications; the 10 GB/db cap
# is irrelevant at a ~25 MB dataset; no SQL Agent is needed (scheduling is
# SSM/CloudWatch Events). Web edition is PERMANENTLY EXCLUDED (Microsoft
# licensing); Standard is the documented upgrade path only.
#
# Private by construction: data subnets have NO route to the IGW, the SG is
# sg-db (inbound 1433 from sg-app only), and publicly_accessible=false is
# set here again as defense in depth (TDD §4.2).
#
# Master credential (C8/ADR-5): manage_master_user_password = true — RDS
# itself creates and manages the master password in Secrets Manager, so NO
# password value ever enters Terraform state. The SQL consumer identities
# (USR_FDE_RO etc.) are created by the seeding step and live in the
# coldchain/dev/sql-* containers.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  name_prefix = "coldchain-${var.environment}"
  common_tags = {
    Project     = "coldchain"
    Environment = var.environment
    ManagedBy   = "terraform"
    Phase       = "phase2"
  }
}

resource "aws_db_subnet_group" "this" {
  name       = "${local.name_prefix}-db-subnets"
  subnet_ids = var.data_subnet_ids # no-IGW data subnets (01 §3)

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-db-subnets" })
}

resource "aws_db_instance" "this" {
  identifier     = "${local.name_prefix}-sqlexpress"
  engine         = "sqlserver-ex"
  engine_version = var.engine_version # pinned in tfvars; Express-compatible
  instance_class = var.instance_class # db.t3.small (07 §3)

  allocated_storage = var.allocated_storage
  storage_type      = "gp3"
  storage_encrypted = true # default aws/rds key — no CMK permissions needed

  db_name                     = null # SQL Server: databases created post-boot by sql-init
  username                    = var.master_username
  manage_master_user_password = true # C8: RDS-managed secret — never in Terraform state

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [var.sg_db_id]
  publicly_accessible    = false # hard requirement (TDD §4.2) — also enforced by subnet + SG
  multi_az               = false # Express is single-AZ only anyway

  # Temporary deployment cost control: the dataset is synthetic and
  # re-importable; PITR backups are unnecessary. Final snapshot on destroy
  # is KEPT (teardown deletes it deliberately after evidence capture).
  backup_retention_period   = 0
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.name_prefix}-sqlexpress-final"

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-sqlexpress" })
}
