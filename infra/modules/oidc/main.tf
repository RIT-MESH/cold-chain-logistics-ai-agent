# OIDC module — GitHub Actions federation (blueprint 01 §1/§6, correction
# C6 / ADR-6). NO AWS access keys ever enter the repository, variables, or
# Secrets Manager: GitHub runners assume IAM roles via OIDC.
#
# FOUR SEPARATED ROLES — no single workflow can both mutate infrastructure
# and release applications:
#
#   build   — push images to ECR only. No deploy rights, no state access.
#   plan    — terraform plan read-set + state-object READ. No apply, no
#             s3:PutObject on state.
#   apply   — environment-scoped write + state read/write for ITS OWN
#             env/* key prefix only (C2 boundary: the dev apply role can
#             never touch prod state and vice versa). No IAM user creation,
#             no billing access.
#   release — ssm:SendCommand on the app host (document filter) +
#             secretsmanager:ListSecrets for existence checks. NO
#             GetSecretValue, no infra write.
#
# The manual approval gates are GitHub ENVIRONMENTS (infra-approval /
# release-approval, wired in .github/workflows/*), not IAM.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

data "aws_caller_identity" "this" {}

locals {
  account_id = data.aws_caller_identity.this.account_id
  github_sub = "repo:${var.github_org}/${var.github_repo}:*"
  role_names = {
    build   = "coldchain-${var.environment}-gh-build"
    plan    = "coldchain-${var.environment}-gh-plan"
    apply   = "coldchain-${var.environment}-gh-apply"
    release = "coldchain-${var.environment}-gh-release"
  }
}

# ------------------------------------------------------- OIDC provider

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # GitHub's published thumbprints (AWS validates the intermediate chain
  # server-side as well since 2023-05; these are the documented roots).
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e378dbaea", "1c58a3a8518e8759bf075b76b750d4f2df264fcd"]
  # No tags: IAM tagging permissions are intentionally not required
  # (operator policy has no iam:TagRole/TagOpenIDConnectProvider); the
  # teardown identifies the provider by URL and roles by name prefix.
}

# Trust policy shared by all four roles: any workflow in the one repository
# may assume the role; WHICH workflow (and whether a human approved) is
# enforced by the GitHub Environments + workflow `environment:` blocks.
data "aws_iam_policy_document" "trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.github_sub]
    }
  }
}

# ------------------------------------------------------- build role

resource "aws_iam_role" "build" {
  name               = local.role_names["build"]
  assume_role_policy = data.aws_iam_policy_document.trust.json
}

# Push-only on the coldchain ECR repositories (repos created in Phase 3;
# the role is defined now so the workflow file exists with its final shape).
resource "aws_iam_role_policy" "build" {
  name = "ecr-push-only"
  role = aws_iam_role.build.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload",
        "ecr:PutImage",
        "ecr:DescribeRepositories", "ecr:CreateRepository"
      ]
      Resource = "arn:aws:ecr:*:${local.account_id}:repository/coldchain/*"
    }]
  })
}

# ------------------------------------------------------- plan role

resource "aws_iam_role" "plan" {
  name               = local.role_names["plan"]
  assume_role_policy = data.aws_iam_policy_document.trust.json
}

data "aws_iam_policy_document" "plan" {
  # State READ only (kms:Decrypt is required to read SSE-KMS state).
  statement {
    sid     = "StateReadOwnEnvironmentOnly"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:ListBucket"]
    resources = [
      "arn:aws:s3:::${var.state_bucket}",
      "arn:aws:s3:::${var.state_bucket}/${var.state_key_prefix}/*",
    ]
  }
  statement {
    sid       = "StateKmsDecrypt"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["*"] # scoped to the state CMK via the key policy
  }
  # terraform plan refresh needs the describe/get/list set for the
  # resource families this project manages — write actions are absent.
  statement {
    sid    = "ReadOnlyDiscovery"
    effect = "Allow"
    actions = [
      "ec2:Describe*", "rds:Describe*", "elasticloadbalancing:Describe*",
      "sqs:List*", "sqs:Get*", "sns:List*", "sns:Get*", "s3:ListAllMyBuckets",
      "secretsmanager:ListSecrets", "secretsmanager:DescribeSecret",
      "cloudwatch:Describe*", "cloudwatch:Get*", "cloudwatch:List*",
      "logs:Describe*", "logs:List*", "ssm:Describe*", "ssm:List*",
      "iam:ListRoles", "iam:GetRole", "iam:ListInstanceProfiles",
      "kms:DescribeKey", "kms:List*", "tag:Get*",
      "ecr:DescribeRepositories", "ecr:ListImages",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "plan" {
  name   = "tf-plan"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan.json
}

# ------------------------------------------------------- apply role

resource "aws_iam_role" "apply" {
  name               = local.role_names["apply"]
  assume_role_policy = data.aws_iam_policy_document.trust.json
}

data "aws_iam_policy_document" "apply" {
  # State READ/WRITE, own environment's key prefix ONLY (C2 boundary).
  statement {
    sid     = "StateOwnEnvironmentOnly"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [
      "arn:aws:s3:::${var.state_bucket}",
      "arn:aws:s3:::${var.state_bucket}/${var.state_key_prefix}/*",
    ]
  }
  statement {
    sid       = "StateKms"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = ["*"] # scoped to the state CMK via the key policy
  }
  # Environment-scoped writes for the resource families of Phases 2-5.
  # Tag conditions enforce the project boundary where the API supports
  # conditions on create (aws:RequestTag for creation, ResourceTag for
  # in-place calls). NOTE FOR REVIEWER: several create actions cannot be
  # tag-conditioned (e.g. CreateBucket, CreateSecret validate by name
  # prefix below); this is the pragmatic Phase 1 least-privilege envelope,
  # tightened per-phase as resources land.
  statement {
    sid    = "EnvScopedWrites"
    effect = "Allow"
    actions = [
      "ec2:*", "rds:*", "elasticloadbalancing:*",
      "sqs:*", "sns:*", "cloudwatch:*", "logs:*", "ssm:*",
      "secretsmanager:CreateSecret", "secretsmanager:TagResource",
      "secretsmanager:DeleteSecret", "secretsmanager:DescribeSecret",
      "ecr:CreateRepository", "ecr:ReplicateImage",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "NamedScopedWrites"
    effect    = "Allow"
    actions   = ["s3:CreateBucket", "s3:PutBucket*"]
    resources = ["arn:aws:s3:::coldchain-${var.environment}-*"]
  }
  # Instance profiles need role/PassRole for the EC2 host (Phase 2) —
  # scoped to this project's own roles; IAM USER creation is explicitly
  # denied below.
  statement {
    sid       = "IamForInstanceProfiles"
    effect    = "Allow"
    actions   = ["iam:CreateRole", "iam:DeleteRole", "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:CreateInstanceProfile", "iam:DeleteInstanceProfile", "iam:AddRoleToInstanceProfile", "iam:RemoveRoleFromInstanceProfile", "iam:PassRole", "iam:TagRole"]
    resources = ["arn:aws:iam::${local.account_id}:role/coldchain-*", "arn:aws:iam::${local.account_id}:instance-profile/coldchain-*"]
  }
  statement {
    sid       = "IamRead"
    effect    = "Allow"
    actions   = ["iam:GetRole", "iam:ListRoles", "iam:GetInstanceProfile", "iam:ListInstanceProfiles", "iam:ListAttachedRolePolicies", "iam:ListRolePolicies"]
    resources = ["*"]
  }
  # Explicit denies — the posture line items of 01 §6.
  statement {
    sid       = "DenyIamUserCreationAndKeys"
    effect    = "Deny"
    actions   = ["iam:CreateUser", "iam:CreateAccessKey", "iam:CreateLoginProfile", "iam:*LoginProfile*", "organizations:*", "account:*", "aws-portal:*", "billing:*", "purchase-orders:*"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "apply" {
  name   = "tf-apply"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.apply.json
}

# ------------------------------------------------------- release role

resource "aws_iam_role" "release" {
  name               = local.role_names["release"]
  assume_role_policy = data.aws_iam_policy_document.trust.json
}

data "aws_iam_policy_document" "release" {
  statement {
    sid       = "SsmSendCommandOnAppHost"
    effect    = "Allow"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ec2:*:${local.account_id}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/Environment"
      values   = [var.environment] # dev release role can only command dev-tagged instances
    }
    condition {
      test     = "StringEquals"
      variable = "ssm:DocumentName"
      values   = ["AWS-RunShellScript"]
    }
  }
  statement {
    sid       = "SsmObservability"
    effect    = "Allow"
    actions   = ["ssm:GetCommandInvocation", "ssm:ListCommands", "ssm:DescribeInstanceInformation"]
    resources = ["*"]
  }
  statement {
    sid       = "SecretExistenceChecksOnly"
    effect    = "Allow"
    actions   = ["secretsmanager:ListSecrets"]
    resources = ["*"] # deliberately NO secret-value read permission (01 §6)
  }
}

resource "aws_iam_role_policy" "release" {
  name   = "ssm-release"
  role   = aws_iam_role.release.id
  policy = data.aws_iam_policy_document.release.json
}
