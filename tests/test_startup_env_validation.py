import pytest

from src.ui.app import _validate_required_env_vars


def test_validate_required_env_vars_raises_on_missing(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)

    with pytest.raises(RuntimeError) as exc:
        _validate_required_env_vars()

    message = str(exc.value)
    assert "Cannot start KensaraAI SEO Agent" in message
    assert "GROQ_API_KEY" in message
    assert "NVIDIA_API_KEY" in message
    assert "TAVILY_API_KEY" in message
    assert "SERPER_API_KEY" in message


def test_validate_required_env_vars_passes_when_present(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setenv("NVIDIA_API_KEY", "x")
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    monkeypatch.setenv("SERPER_API_KEY", "x")

    _validate_required_env_vars()
