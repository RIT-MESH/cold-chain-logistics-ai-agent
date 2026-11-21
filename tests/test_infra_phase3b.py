"""Phase 3b guardrail tests — CloudFront default-hostname web tier (U-4
decision 2026-11-15). Enforces: no custom domain / ritmesh.com untouched;
CloudFront default cert; ALB bypass guard; Cognito R4-1/R4-2 posture;
nginx sub-path routing; container port posture.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
DOCKER = ROOT / "docker"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestNoCustomDomain:
    def test_ritmesh_untouched_everywhere(self):
        # U-4: ritmesh.com must not be USED by any infra/docker file. Comments
        # documenting the guarantee are fine — check non-comment content only.
        for p in list(INFRA.rglob("*.tf")) + list(DOCKER.glob("*")) + [ROOT / "scripts"]:
            for f in (p.rglob("*") if p.is_dir() else [p]):
                if not f.is_file() or f.suffix in (".pyc", ".zip") or "__pycache__" in str(f):
                    continue  # binaries / caches are not source
                code = "\n".join(
                    l for l in _read(f).splitlines()
                    if not l.lstrip().startswith(("#", "--", "//"))
                )
                assert "ritmesh" not in code.lower(), f"ritmesh referenced in {f}"

    def test_cloudfront_default_certificate_no_aliases(self):
        text = _read(INFRA / "modules" / "web" / "main.tf")
        assert "cloudfront_default_certificate = true" in text
        assert "aliases" not in text.replace("aliases involved", "")  # no CNAME/SAN anywhere


class TestBypassProtection:
    def test_alb_default_action_403(self):
        text = _read(INFRA / "modules" / "web" / "main.tf")
        m = re.search(r'default_action \{\s*type\s*=\s*"fixed-response"', text)
        assert m
        seg = text[m.start():m.start() + 400]
        assert '"403"' in seg

    def test_every_listener_rule_requires_origin_secret(self):
        text = _read(INFRA / "modules" / "web" / "main.tf")
        rules = re.findall(r'aws_lb_listener_rule" "\w+"(.*?)(?=resource "aws_lb_listener_rule"|\Z)', text, re.S)
        assert len(rules) == 3
        for r in rules:
            assert 'http_header_name = "x-cf-origin-secret"' in r

    def test_cloudfront_sends_guard_header_option_b_http_origin(self):
        text = _read(INFRA / "modules" / "web" / "main.tf")
        assert 'name  = "x-cf-origin-secret"' in text
        # OPTION B (operator decision 2026-11-15): CF->ALB over plain HTTP —
        # CloudFront refuses self-signed origin certs (502, live-proven);
        # the viewer leg stays HTTPS on the AWS-managed default certificate.
        assert 'origin_protocol_policy   = "http-only"' in text
        assert 'X-Forwarded-Host' in text  # carries the public hostname to oauth2-proxy

    def test_gateway_never_publicly_routed(self):
        compose = _read(DOCKER / "compose.yml")

        def service(name):
            # Exact service block (two-space indent) — service ORDER must
            # not leak neighboring services into the section (live-found:
            # oauth2-proxy sits between grafana and nginx).
            m = re.search(r"(?ms)^  %s:$\n(.*?)(?=^  \w[\w-]*:$|\Z)" % re.escape(name), compose)
            assert m, f"service {name} not found"
            return m.group(1)

        fde_api = service("fde-api")
        assert "127.0.0.1:8000" in fde_api  # loopback + compose-network only (01 §7.1)
        librechat = service("librechat")
        grafana = service("grafana")
        assert "3080:3080" not in librechat and "ports:" not in librechat  # sidecar-only
        assert "3000:3000" not in grafana and "ports:" not in grafana  # sidecar-only


class TestCognitoPosture:
    def test_r4_1_secret_and_code_grant_and_no_self_signup(self):
        text = _read(INFRA / "modules" / "web" / "main.tf")
        assert re.search(r"generate_secret\s*=\s*true", text)  # R4-1
        assert re.search(r'allowed_oauth_flows\s+=\s+\["code"\]', text)
        assert "allow_admin_create_user_only = true" in text  # 01 §7.1a

    def test_callback_on_cloudfront_host_and_fail_closed(self):
        text = _read(INFRA / "modules" / "web" / "main.tf")
        # OPTION B: the Cognito redirect belongs to oauth2-proxy
        # (/oauth2/callback) — the ALB authenticate-cognito path
        # (/oauth2/idpresponse) was removed with the HTTPS listener.
        assert "https://${var.public_fqdn}/oauth2/callback" in text
        # no callback/default_redirect CONFIG line may reference the old
        # ALB-auth path (comments documenting the removal are fine)
        assert not re.search(r'^(callback_urls|default_redirect_uri).*idpresponse', text, re.M)
        assert re.search(r"minimum_length\s*=\s*12", text)
        # Fail-closed at the edge now lives in oauth2-proxy (compose),
        # behind the ALB origin-secret guard + 403 default action.
        compose = _read(DOCKER / "compose.yml")
        assert 'OAUTH2_PROXY_COOKIE_SECURE: "true"' in compose
        assert 'OAUTH2_PROXY_UPSTREAMS: "http://streamlit:8501"' in compose


class TestNginxRouting:
    def test_chat_prefix_stripped_grafana_passthrough(self):
        text = _read(DOCKER / "nginx.conf")
        assert re.search(r"location /chat/ \{\s*proxy_pass http://librechat:3080/", text)  # trailing slash = strip
        assert re.search(r"location /grafana/ \{\s*proxy_pass http://grafana:3000;", text)  # no trailing slash = passthrough
        assert "proxy_set_header Upgrade" in text  # websockets for LibreChat

    def test_sg_app_allows_only_sg_alb_including_sidecar(self):
        text = _read(INFRA / "modules" / "network" / "main.tf")
        assert "[8501, 3000, 3080, 8000, 8080, 4180]" in text  # incl. nginx sidecar + oauth2-proxy gate
        assert 'toset(["8501", "3000", "3080", "8000", "8080", "4180"])' in text
        assert 'security_groups = [aws_security_group.alb.id]' in text
