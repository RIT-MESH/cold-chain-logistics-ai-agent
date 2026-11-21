"""Behavioral verification that the orchestrator's runtime LLM is NVIDIA NIM
— the ONLY supported provider since the 2026-11-08 operator decision
(blueprint correction R6-1). It hard-fails when the key is missing, locks
the endpoint URL, and rejects every other provider (including the removed
OLLAMA_CLOUD) instead of falling back.

These tests reload src.orchestrator under controlled environments, so they
run offline with a FAKE key and never contact NVIDIA NIM.
"""
import importlib
import os
import sys

import pytest

FAKE_KEY = "fake-key-for-offline-test-only"

RELEVANT_ENV = [
    "LLM_PROVIDER", "Agent_llm", "NVIDIA_API_KEY", "NVIDIA_MODEL", "NVIDIA_BASE_URL",
    # Legacy variables of removed providers are cleared too, so a stale
    # private .env cannot resurrect a deleted code path.
    "OLLAMA_API_KEY", "OLLAMA_CLOUD_MODEL", "OLLAMA_CLOUD_BASE_URL",
]


@pytest.fixture
def clean_env(monkeypatch):
    # Neutralize dotenv so a developer's real .env (which may hold a live
    # NVIDIA_API_KEY) cannot leak into these behavior tests.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    for k in RELEVANT_ENV:
        monkeypatch.delenv(k, raising=False)
    yield monkeypatch
    sys.modules.pop("src.orchestrator", None)  # force fresh import in the next test


def _reload():
    sys.modules.pop("src.orchestrator", None)
    return importlib.import_module("src.orchestrator")


def test_nim_default_with_fake_key_loads_verified_model(clean_env):
    clean_env.setenv("NVIDIA_API_KEY", FAKE_KEY)
    clean_env.setenv("LLM_PROVIDER", "NVIDIA_NIM")
    module = _reload()
    from langchain_openai import ChatOpenAI
    assert isinstance(module.llm, ChatOpenAI)
    model = getattr(module.llm, "model_name", None) or getattr(module.llm, "model", "")
    assert "glm-5.3-flash" in str(model), f"default model should be z-ai/glm-5.3-flash (blueprint P-8), got {model!r}"


def test_nim_is_default_when_provider_env_unset(clean_env):
    """No LLM_PROVIDER at all -> NVIDIA_NIM default (no legacy alias, no local model)."""
    clean_env.setenv("NVIDIA_API_KEY", FAKE_KEY)
    module = _reload()
    from langchain_openai import ChatOpenAI
    assert isinstance(module.llm, ChatOpenAI)


def test_missing_key_hard_fails_no_silent_fallback(clean_env):
    """No NVIDIA_API_KEY -> RuntimeError; there is no other provider to fall back to."""
    try:
        _reload()
    except RuntimeError as exc:
        assert "NVIDIA_API_KEY is required" in str(exc)
    else:
        pytest.fail("expected RuntimeError for missing NVIDIA_API_KEY; a fallback may have occurred")
    finally:
        sys.modules.pop("src.orchestrator", None)


def test_untrusted_base_url_is_rejected(clean_env):
    """Credentials must never be transmitted to an endpoint other than integrate.api.nvidia.com."""
    clean_env.setenv("NVIDIA_API_KEY", FAKE_KEY)
    clean_env.setenv("NVIDIA_BASE_URL", "https://evil.example/v1")
    try:
        _reload()
    except ValueError as exc:
        assert "NVIDIA_BASE_URL" in str(exc)
    else:
        pytest.fail("expected ValueError for untrusted NVIDIA_BASE_URL")
    finally:
        sys.modules.pop("src.orchestrator", None)


def test_removed_ollama_provider_hard_fails(clean_env):
    """The deleted OLLAMA_CLOUD path must hard-fail, not silently switch."""
    clean_env.setenv("NVIDIA_API_KEY", FAKE_KEY)
    clean_env.setenv("LLM_PROVIDER", "OLLAMA_CLOUD")
    try:
        _reload()
    except ValueError as exc:
        assert "Unsupported LLM_PROVIDER" in str(exc)
    else:
        pytest.fail("expected ValueError for removed provider OLLAMA_CLOUD")
    finally:
        sys.modules.pop("src.orchestrator", None)


def test_unsupported_provider_hard_fails(clean_env):
    clean_env.setenv("LLM_PROVIDER", "SOME_OTHER_VENDOR")
    try:
        _reload()
    except ValueError as exc:
        assert "Unsupported LLM_PROVIDER" in str(exc)
    else:
        pytest.fail("expected ValueError for unsupported LLM_PROVIDER")
    finally:
        sys.modules.pop("src.orchestrator", None)


def test_empty_model_rejected(clean_env):
    clean_env.setenv("NVIDIA_API_KEY", FAKE_KEY)
    clean_env.setenv("NVIDIA_MODEL", "  ")
    try:
        _reload()
    except ValueError as exc:
        assert "NVIDIA_MODEL" in str(exc)
    else:
        pytest.fail("expected ValueError for empty NVIDIA_MODEL")
    finally:
        sys.modules.pop("src.orchestrator", None)
