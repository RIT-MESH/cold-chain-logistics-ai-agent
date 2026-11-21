"""REAL-logout guardrails (2026-11-16): the Streamlit sidebar logout must
chain oauth2-proxy sign_out (cookie invalidation) -> Cognito hosted logout
(pool session termination) -> /oauth2/start landing. A cosmetic navigate-
away button is explicitly forbidden by the operator directive.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = (ROOT / "src" / "ui.py").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker" / "compose.yml").read_text(encoding="utf-8")
WEB = (ROOT / "infra" / "modules" / "web" / "main.tf").read_text(encoding="utf-8")

CF = "d36yix30egjffn.cloudfront.net"


def test_sidebar_logout_button_targets_sign_out_chain():
    # the button routes through oauth2-proxy sign_out with a Cognito rd=
    assert "/oauth2/sign_out?rd=" in UI
    assert "amazoncognito.com/logout?client_id=" in UI
    assert "logout_uri=" in UI  # Cognito requires the registered landing
    assert "Logout" in UI
    # built from APP_PUBLIC_URL, not a hardcoded absolute hostname
    assert "APP_PUBLIC_URL" in UI


def test_logout_button_is_a_native_widget_not_injected_html():
    # 2026-11-17 operator finding: the previous unsafe_allow_html anchor
    # never rendered in the deployed browser — the logout control MUST be
    # a first-class st.link_button so it is visible on every page.
    assert "st.sidebar.link_button('🚪 Logout'" in UI
    assert "unsafe_allow_html=True" not in UI.split("st.title(page)")[0]


def test_logout_landing_is_the_login_entry():
    # after Cognito logout the user lands on /oauth2/start (login screen),
    # not on a page that silently re-authenticates
    assert "/oauth2/start" in UI


def test_compose_carries_logout_config():
    stream = re.search(r"(?ms)^  streamlit:$\n(.*?)(?=^  \w[\w-]*:$|\Z)", COMPOSE).group(1)
    assert f'APP_PUBLIC_URL: "https://{CF}"' in stream
    oauth = re.search(r"(?ms)^  oauth2-proxy:$\n(.*?)(?=^  \w[\w-]*:$|\Z)", COMPOSE).group(1)
    # whitelist via the COMMAND FLAG form — the env-var form of this slice
    # option was not honored live (sign_out rd= redirects were rejected
    # with "domain / port not in whitelist" even with the env set)
    assert "--whitelist-domain=coldchain-dev.auth.us-east-1.amazoncognito.com" in oauth


def test_cognito_allowed_logout_urls_include_login_entry():
    # Cognito validates logout_uri EXACTLY — the /oauth2/start landing
    # must be registered (live-found pattern: bare domain alone 400s)
    m = re.search(r"logout_urls = \[(.*?)\]", WEB, re.S)
    assert m, "logout_urls block not found"
    body = m.group(1)
    assert '"https://${var.public_fqdn}"' in body
    assert '"https://${var.public_fqdn}/oauth2/start"' in body
