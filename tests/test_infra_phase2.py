"""Phase 2 infra guardrail tests (blueprint 09 Phase 2; ADR-1/13/16/17,
C7/C8; 01 §3/§5/§6/§9). Structural tests — run everywhere, no AWS
credentials needed. Live verification (SSM reachability, port scan,
grants, import counts) happens post-apply on the real host.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- compute


class TestComputeModule:
    def test_no_ssh_key_anywhere(self):
        # 01 §3.1: no SSH key_pair ATTRIBUTE (comments may mention the rule).
        text = _read(INFRA / "modules" / "compute" / "main.tf")
        assert re.search(r"key_name\s*=", text) is None

    def test_user_data_has_no_credentials(self):
        # C8/ADR-5: user data installs Docker only — zero secrets.
        text = _read(INFRA / "modules" / "compute" / "main.tf")
        user_data = text.split("user_data = <<-EOT")[1].split("EOT")[0]
        for forbidden in ("KEY=", "nvapi", "SECRET", "PASSWORD", "token"):
            assert forbidden.lower() not in user_data.lower()

    def test_instance_profile_reads_only_exact_secret_arns(self):
        text = _read(INFRA / "modules" / "compute" / "main.tf")
        block = text.split('"ReadOnlyExactSecretArns"')[1].split("}")[0]
        assert re.search(r'Resource\s+=\s+var\.secret_arns', block)
        # GetSecretValue itself NEVER carries a wildcard resource
        assert '"*"' not in block
        # Wildcard resources are permitted ONLY in the two documented
        # read-only statements whose actions cannot be resource-scoped:
        # CloudWatchMetricsReadOnly (Phase 3 Dashboard D) and
        # CloudWatchLogsReadOnlyQueryResults (Phase 5 audit panels —
        # query-id-addressed + DescribeLogGroups only accept "*").
        # Every other policy statement must stay exact or pattern-scoped.
        parts = re.split(r'Sid\s*=\s*"([^"]+)"', text)
        allowed = {
            "CloudWatchMetricsReadOnly",          # Phase 3 Dashboard D (metrics read — only "*" scope)
            "CloudWatchLogsReadOnlyQueryResults", # Phase 5 audit panels (query-id/list actions — only "*" scope)
            "CloudWatchPutPhase4Metrics",         # Phase 4 reconcile metrics (PutMetricData — only "*"; namespace-CONDITIONED)
        }
        for i in range(1, len(parts) - 1, 2):
            sid, body = parts[i], parts[i + 1]
            if re.search(r'Resource\s*=\s*"?\[?"?\*"?]?\s*$', body, re.M):
                assert sid in allowed, f"wildcard resource in unexpected statement: {sid}"

    def test_sg_and_subnet_wiring(self):
        text = _read(INFRA / "modules" / "compute" / "main.tf")
        assert re.search(r'vpc_security_group_ids\s+=\s+\[var\.sg_app_id\]', text)
        assert re.search(r'subnet_id\s+=\s+var\.subnet_id', text)

    def test_imdsv2_required_and_encrypted_root(self):
        text = _read(INFRA / "modules" / "compute" / "main.tf")
        assert re.search(r'http_tokens\s+=\s+"required"', text)
        assert re.search(r'encrypted\s+=\s+true', text)

    def test_ssm_managed_without_ssh(self):
        text = _read(INFRA / "modules" / "compute" / "main.tf")
        assert "AmazonSSMManagedInstanceCore" in text

    def test_t3_large_is_called_a_candidate(self):
        text = _read(INFRA / "modules" / "compute" / "variables.tf")
        assert "CANDIDATE" in text  # R4-4: capacity is a load-test result, not an assumption


# ---------------------------------------------------------------- rds


class TestRdsModule:
    def test_express_engine_and_dev_class(self):
        text = _read(INFRA / "modules" / "rds" / "main.tf")
        assert '"sqlserver-ex"' in text  # ADR-3: Express (Web is excluded)
        assert "Web" not in text.replace("Web edition is PERMANENTLY EXCLUDED", "")
        assert re.search(r'multi_az\s+=\s+false', text)  # Express is single-AZ

    def test_not_publicly_accessible(self):
        text = _read(INFRA / "modules" / "rds" / "main.tf")
        assert re.search(r'publicly_accessible\s+=\s+false', text)  # TDD §4.2
        assert "data_subnet_ids" in text  # no-IGW subnets only

    def test_no_password_in_terraform_state(self):
        # C8/ADR-5: RDS-managed master secret — the password never enters state.
        text = _read(INFRA / "modules" / "rds" / "main.tf")
        assert re.search(r'manage_master_user_password\s+=\s+true', text)
        # no standalone password ATTRIBUTE anywhere (comments may discuss it)
        assert re.search(r"\n\s*password\s*=", text) is None

    def test_storage_and_snapshot_posture(self):
        text = _read(INFRA / "modules" / "rds" / "main.tf")
        assert re.search(r'storage_type\s+=\s+"gp3"', text)
        assert re.search(r'storage_encrypted\s+=\s+true', text)
        assert re.search(r'skip_final_snapshot\s+=\s+false', text)  # kept; teardown deletes deliberately

    def test_sg_db_wiring(self):
        text = _read(INFRA / "modules" / "rds" / "main.tf")
        assert re.search(r'vpc_security_group_ids\s+=\s+\[var\.sg_db_id\]', text)


# ---------------------------------------------------------------- environment wiring


class TestPhase2Wiring:
    def test_dev_wires_compute_and_rds(self):
        text = _read(INFRA / "environments" / "dev" / "main.tf")
        assert 'module "compute"' in text
        assert 'module "rds"' in text
        assert "values(module.secrets.secret_arns)" in text  # exact ARNs, 01 §6

    def test_dev_phase2_defaults(self):
        text = _read(INFRA / "environments" / "dev" / "variables.tf")
        assert 'default     = "t3.large"' in text
        assert 'default     = "db.t3.small"' in text
        assert 'default     = 20' in text  # 07 §3 storage line
