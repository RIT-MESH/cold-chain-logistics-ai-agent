"""Phase 1 infra guardrail tests (blueprint 09 Phase 1; SG rules of 01
§3.1 / R3-4; state rules of C2/ADR-14; secrets rules of C8/ADR-5; pipeline
separation of C6/ADR-6).

These are STRUCTURAL tests against the staged Terraform sources — they run
everywhere (no AWS credentials, no terraform binary needed, matching the
deploy.yml CI install set). Live verification (describe-security-groups,
external port scan, parallel-plan lock conflict) happens at Phase 2/3
acceptance against the real account.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
WORKFLOWS = ROOT / ".github" / "workflows"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- backend


class TestBackend:
    def test_use_lockfile_true_no_dynamodb(self):
        text = _read(INFRA / "backend.hcl.example")
        assert "use_lockfile = true" in text
        # DynamoDB lock tables are deprecated (ADR-14): no backend key for one
        assert "dynamodb_table" not in text

    def test_separate_state_keys_per_environment(self):
        text = _read(INFRA / "backend.hcl.example")
        assert "env/dev/terraform.tfstate" in text
        assert "env/prod" in text

    def test_bootstrap_script_is_operator_run_only(self):
        # The bootstrap script exists but nothing in infra/ auto-creates the
        # state bucket (Terraform cannot manage its own backend bucket).
        script = ROOT / "scripts" / "bootstrap_tfstate.ps1"
        assert script.exists()
        assert "cc-tfstate-" in _read(script)
        for env in ("dev", "prod"):
            assert "aws_s3_bucket" not in _read(
                INFRA / "environments" / env / "main.tf"
            )


# ---------------------------------------------------------------- network (01 §3.1)


class TestNetworkSecurityGroups:
    def test_sg_alb_inbound_443_80_from_anywhere(self):
        text = _read(INFRA / "modules" / "network" / "main.tf")
        alb_block = text.split('resource "aws_security_group" "alb"')[1].split(
            "# sg-app"
        )[0]
        # LIVE-FOUND 2026-11-15: CloudFront (IPv6-enabled) reaches the ALB
        # origin over IPv6 — ::/0 ingress is REQUIRED alongside 0.0.0.0/0
        # (without it the SG silently dropped every CF origin request).
        assert re.search(r"from_port\s*=\s*443", alb_block)
        assert re.search(r"from_port\s*=\s*80", alb_block)
        assert alb_block.count("0.0.0.0/0") == 2
        assert alb_block.count('ipv6_cidr_blocks = ["::/0"]') == 2

    def test_sg_alb_egress_only_to_app_ports(self):
        text = _read(INFRA / "modules" / "network" / "main.tf")
        alb_rule = text.split('resource "aws_vpc_security_group_egress_rule" "alb_to_app"')[1].split(
            "# App -> internet"
        )[0]
        # Phase 3b: + nginx sidecar 8080, + oauth2-proxy gate 4180
        assert 'toset(["8501", "3000", "3080", "8000", "8080", "4180"])' in alb_rule
        assert "referenced_security_group_id = aws_security_group.app.id" in alb_rule
        assert "0.0.0.0/0" not in alb_rule

    def test_sg_app_inbound_only_from_alb_no_ssh(self):
        text = _read(INFRA / "modules" / "network" / "main.tf")
        app_block = text.split('resource "aws_security_group" "app"')[1].split(
            'resource "aws_security_group" "db"'
        )[0]
        assert "[8501, 3000, 3080, 8000, 8080, 4180]" in app_block  # + nginx sidecar + oauth2-proxy (Phase 3b)
        assert "security_groups = [aws_security_group.alb.id]" in app_block
        # No SSH rule exists anywhere in the module (01 §3.1, R3-4):
        assert "22" not in text.replace("1022", "")

    def test_sg_app_egress_fully_separate_rules(self):
        # 01 §3.1 sg-app outbound = 443+80 -> internet, 1433 -> sg-db. All
        # three live as SEPARATE aws_vpc_security_group_egress_rule resources
        # (mixing inline egress with separate rules on one SG makes the
        # provider revoke the separate rules as drift — found live
        # 2026-11-15). The app SG resource itself has NO inline egress.
        text = _read(INFRA / "modules" / "network" / "main.tf")
        app_resource = text.split('resource "aws_security_group" "app"')[1].split(
            "# sg-db"
        )[0]
        assert "egress {" not in app_resource  # fully separate (no inline egress)
        internet_rule = text.split('resource "aws_vpc_security_group_egress_rule" "app_to_internet"')[1].split(
            "# App -> RDS"
        )[0]
        assert 'toset(["443", "80"])' in internet_rule
        assert 'cidr_ipv4         = "0.0.0.0/0"' in internet_rule
        sql_rule = text.split('resource "aws_vpc_security_group_egress_rule" "app_to_db_sql"')[1]
        assert "from_port                    = 1433" in sql_rule
        assert "referenced_security_group_id = aws_security_group.db.id" in sql_rule

    def test_sg_db_inbound_only_1433_from_app(self):
        text = _read(INFRA / "modules" / "network" / "main.tf")
        db_block = text.split('resource "aws_security_group" "db"')[1].split(
            "# ALB -> app container ports"
        )[0]
        assert "from_port       = 1433" in db_block
        assert "security_groups = [aws_security_group.app.id]" in db_block
        assert "0.0.0.0/0" not in db_block  # no internet rule on the DB SG

    def test_data_route_table_has_no_internet_route(self):
        text = _read(INFRA / "modules" / "network" / "main.tf")
        # exactly ONE aws_route resource in the module — the public IGW route
        assert text.count('resource "aws_route"') == 1
        assert 'resource "aws_route" "public_internet"' in text
        # data subnets never map_public_ip_on_launch
        assert "map_public_ip_on_launch = false" in text

    def test_no_nat_gateway_and_no_vpc_endpoints(self):
        text = _read(INFRA / "modules" / "network" / "main.tf")
        assert "aws_nat_gateway" not in text
        assert "aws_vpc_endpoint" not in text  # ADR-13 chosen posture

    def test_explicit_deny_by_default_egress(self):
        # 01 §3.1 egress posture: NO allow-all egress rule exists anywhere in
        # the module. Internet egress (0.0.0.0/0) appears ONLY in sg-app's
        # two separate 443/80 rules; sg-db achieves deny-by-default via its
        # single inline rule (inline egress presence removes the AWS default
        # allow-all; separate rule resources would NOT — reviewed 2026-11-15).
        text = _read(INFRA / "modules" / "network" / "main.tf")
        assert 'protocol    = "-1"' not in text  # no any-protocol inline rules
        assert 'ip_protocol                  = "-1"' not in text
        assert 'ip_protocol       = "-1"' not in text
        # exactly FOUR internet-wide rule assignments in the whole module:
        # 1 public route + 2 ALB ingress (["..."] form) + 1 sg-app egress
        # rule for 443/80 ("..." form, for_each). Nothing wider exists.
        assert text.count('= "0.0.0.0/0"') + text.count('["0.0.0.0/0"]') == 4
        db_block = text.split('resource "aws_security_group" "db"')[1].split(
            "# ALB -> app container ports"
        )[0]
        assert db_block.count("0.0.0.0/0") == 0
        alb_resource = text.split('resource "aws_security_group" "alb"')[1].split(
            "# sg-app"
        )[0]
        assert alb_resource.count('["0.0.0.0/0"]') == 2  # inbound only, per §3.1


# ---------------------------------------------------------------- secrets (C8)


class TestSecrets:
    def test_containers_only_no_values_in_state(self):
        text = _read(INFRA / "modules" / "secrets" / "main.tf")
        assert 'resource "aws_secretsmanager_secret_version"' not in text  # (the doc comment explains the rule; no such RESOURCE exists)
        assert "secret_string =" not in text  # no value assignment anywhere (CLI seeding is out-of-band)
        assert 'resource "aws_secretsmanager_secret" "this"' in text

    def test_phase1_container_set_matches_design(self):
        text = _read(INFRA / "modules" / "secrets" / "variables.tf")
        for name in (
            "nvidia-api-key",
            "fde-api-key",
            "pinecone-api-key",
            "sql-master",
            "sql-agent-ro",
            "sql-ui-ro",
            "sql-grafana-ro",
            "sql-incident-writer",
            "sql-audit-writer",
            "sql-import-admin",
        ):
            assert f'"{name}"' in text


# ---------------------------------------------------------------- oidc (C6)


class TestOidc:
    def test_four_separated_roles_exist(self):
        text = _read(INFRA / "modules" / "oidc" / "main.tf")
        for role in ("build", "plan", "apply", "release"):
            assert f'resource "aws_iam_role" "{role}"' in text

    def test_plan_role_cannot_write_state(self):
        text = _read(INFRA / "modules" / "oidc" / "main.tf")
        plan_block = text.split('data "aws_iam_policy_document" "plan"')[1].split(
            'resource "aws_iam_role_policy" "plan"'
        )[0]
        assert "s3:PutObject" not in plan_block
        assert "s3:DeleteObject" not in plan_block

    def test_apply_role_scoped_to_own_state_prefix(self):
        text = _read(INFRA / "modules" / "oidc" / "main.tf")
        apply_block = text.split('data "aws_iam_policy_document" "apply"')[1].split(
            'resource "aws_iam_role_policy" "apply"'
        )[0]
        assert "${var.state_key_prefix}/*" in apply_block

    def test_apply_role_denies_iam_user_creation_and_billing(self):
        text = _read(INFRA / "modules" / "oidc" / "main.tf")
        assert '"iam:CreateUser"' in text
        assert '"iam:CreateAccessKey"' in text
        deny_block = text.split("DenyIamUserCreationAndKeys")[1][:600]
        assert '= "Deny"' in deny_block  # aws_iam_policy_document DSL

    def test_release_role_has_no_getsecretvalue(self):
        text = _read(INFRA / "modules" / "oidc" / "main.tf")
        release_block = text.split('data "aws_iam_policy_document" "release"')[1]
        assert "GetSecretValue" not in release_block
        assert "secretsmanager:ListSecrets" in release_block

    def test_build_role_is_ecr_push_only(self):
        text = _read(INFRA / "modules" / "oidc" / "main.tf")
        build_block = text.split('resource "aws_iam_role_policy" "build"')[1].split(
            "------------------------------------------------------- plan role"
        )[0]
        assert "ecr:PutImage" in build_block
        for forbidden in ("rds:", "ec2:", "ssm:SendCommand", "s3:PutObject"):
            assert forbidden not in build_block

    def test_oidc_no_static_aws_keys(self):
        for tf in (INFRA / "modules" / "oidc").glob("*.tf"):
            assert "aws_access_key" not in _read(tf).lower()


# ---------------------------------------------------------------- environments


class TestEnvironments:
    def test_dev_and_prod_separate_state_keys(self):
        dev = _read(INFRA / "environments" / "dev" / "main.tf")
        prod = _read(INFRA / "environments" / "prod" / "main.tf")
        assert 'state_key_prefix = "env/dev"' in dev
        assert 'state_key_prefix = "env/prod"' in prod

    def test_required_version_supports_use_lockfile(self):
        for env in ("dev", "prod"):
            assert ">= 1.10" in _read(INFRA / "environments" / env / "main.tf")

    def test_billing_alarm_120pct_deployed_in_phase1(self):
        for env in ("dev", "prod"):
            text = _read(INFRA / "environments" / env / "main.tf")
            assert "aws_cloudwatch_metric_alarm" in text
            assert "AWS/Billing" in text
            assert "provider          = aws.billing" in text or "provider = aws.billing" in text

    def test_billing_alarm_thresholds(self):
        dev = _read(INFRA / "environments" / "dev" / "dev.tfvars.example")
        prod = _read(INFRA / "environments" / "prod" / "prod.tfvars.example")
        assert "billing_alarm_threshold_usd = 10" in dev  # temporary 24-48 h window (approved 2026-11-15); restore 170 for standing
        assert "billing_alarm_threshold_usd = 650" in prod  # 120% of ~$540 (07 §4)

    def test_no_compute_or_data_resources_in_phase1(self):
        # 09 Phase 1 acceptance: no EC2 instance exists in this phase.
        for env in ("dev", "prod"):
            for tf in (INFRA / "environments" / env).glob("*.tf"):
                text = _read(tf)
                for resource in ("aws_instance", "aws_db_instance", "aws_lb", "aws_ecs"):
                    assert resource not in text

    def test_account_safety_lock_and_real_state_bucket(self):
        # Operator-verified account (STS 2026-11-15): plan/apply can only run
        # against 123456789012; tfvars reference the deterministic bucket name.
        for env in ("dev", "prod"):
            text = _read(INFRA / "environments" / env / "main.tf")
            assert 'allowed_account_ids = ["123456789012"]' in text
        for tfvars in ("dev/dev.tfvars.example", "prod/prod.tfvars.example"):
            assert "cc-tfstate-123456789012" in _read(INFRA / "environments" / tfvars)


# ---------------------------------------------------------------- domain (U-4)


class TestDomainPlaceholders:
    def test_placeholder_apex_domain_in_both_tfvars(self):
        dev = _read(INFRA / "environments" / "dev" / "dev.tfvars.example")
        prod = _read(INFRA / "environments" / "prod" / "prod.tfvars.example")
        assert 'apex_domain = "CHANGE-ME.example"' in dev
        assert 'apex_domain = "CHANGE-ME.example"' in prod

    def test_fqdn_locals_and_cognito_callback_present(self):
        for env in ("dev", "prod"):
            text = _read(INFRA / "environments" / env / "main.tf")
            assert 'app_fqdn     = "app.${var.apex_domain}"' in text
            assert 'chat_fqdn    = "chat.${var.apex_domain}"' in text
            assert 'grafana_fqdn = "grafana.${var.apex_domain}"' in text
            outputs = _read(INFRA / "environments" / env / "outputs.tf")
            assert "/oauth2/idpresponse" in outputs  # 01 §7.1a callback

    def test_no_dns_resources_created_in_phase1(self):
        # Phase 1 prepares placeholders only — the Route 53 zone, ACM cert and
        # ALB are Phase 3 deliverables (09).
        for env in ("dev", "prod"):
            for tf in (INFRA / "environments" / env).glob("*.tf"):
                assert "aws_route53" not in _read(tf)
                assert "aws_acm_certificate" not in _read(tf)


# ---------------------------------------------------------------- workflows (C6)


class TestWorkflows:
    def test_four_separated_pipelines(self):
        for name in ("deploy.yml", "build-images.yml", "infra.yml", "release.yml"):
            assert (WORKFLOWS / name).exists()

    def test_infra_apply_gated_by_approval_environment(self):
        text = _read(WORKFLOWS / "infra.yml")
        assert "environment: infra-approval" in text
        assert "workflow_dispatch" in text
        assert "terraform apply" in text

    def test_infra_terraform_version_pinned(self):
        text = _read(WORKFLOWS / "infra.yml")
        assert 'TF_VERSION: "1.15.8"' in text  # matches operator local env; >= 1.10 for use_lockfile (ADR-14)

    def test_release_gated_and_ssm_only(self):
        text = _read(WORKFLOWS / "release.yml")
        assert "environment: release-approval" in text
        assert "aws ssm send-command" in text
        assert "terraform" not in text  # releases never mutate infra

    def test_build_images_push_only_sha_tags(self):
        text = _read(WORKFLOWS / "build-images.yml")
        assert "sha-${{ github.sha }}" in text
        assert "terraform" not in text
        assert "docker compose up" not in text  # no deploy rights
