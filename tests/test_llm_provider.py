"""Offline configuration checks without spending NVIDIA NIM credits."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_nim_is_only_provider_and_no_implicit_fallback():
    # Explicit utf-8 decoding (Phase 0 fix G-HY-1): src/orchestrator.py
    # contains non-ASCII glyphs and the Windows default codec (cp1252)
    # crashed this test, which passed on the Linux CI runner.
    source = (ROOT / "src/orchestrator.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert '"NVIDIA_NIM"' in source
    assert 'NVIDIA_API_KEY is required' in source
    assert 'https://integrate.api.nvidia.com/v1' in source
    assert 'Unsupported LLM_PROVIDER' in source
    # Removed providers must not be resurrected (2026-11-08 decision, R6-1).
    assert '"OLLAMA_CLOUD"' not in source
    assert '"OLLAMA_LOCAL"' not in source
    assert '"DEEPSEEK"' not in source

def test_nim_secret_never_in_librechat_configuration():
    assert "NVIDIA_API_KEY" not in (ROOT / "integrations/librechat/librechat.yaml").read_text(encoding="utf-8")
    assert 'cold-chain-agent' in (ROOT / "integrations/librechat/librechat.yaml").read_text(encoding="utf-8")
