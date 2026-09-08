"""Task 4: Model specialization test — verify tier→model mapping when specialization is enabled."""
from __future__ import annotations

from unittest.mock import patch

from config import Settings


def _make_settings(**overrides) -> Settings:
    """Create a Settings instance with hermetic defaults for testing."""
    defaults = {
        "gemini_api_key": "test-key",
        "enable_tier_model_specialization": True,
        "specialized_model_lite": "gemini-2.0-flash-lite",
        "specialized_model_core": "gemini-2.5-flash",
        "specialized_model_apex": "gemini-2.5-pro",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_specialization_enabled_free_tier():
    """Free tier should use gemini-2.0-flash-lite when specialization is on."""
    settings = _make_settings()
    with patch("llm.provider.get_settings", return_value=settings):
        from llm.provider import get_llm_provider
        provider = get_llm_provider("main", "Free")
        assert provider.models[0] == "gemini-2.0-flash-lite"


def test_specialization_enabled_pro_tier():
    """Pro tier should use gemini-2.5-flash when specialization is on."""
    settings = _make_settings()
    with patch("llm.provider.get_settings", return_value=settings):
        from llm.provider import get_llm_provider
        provider = get_llm_provider("main", "Pro")
        assert provider.models[0] == "gemini-2.5-flash"


def test_specialization_enabled_max_tier():
    """Max tier should use gemini-2.5-pro when specialization is on."""
    settings = _make_settings()
    with patch("llm.provider.get_settings", return_value=settings):
        from llm.provider import get_llm_provider
        provider = get_llm_provider("main", "Max")
        assert provider.models[0] == "gemini-2.5-pro"


def test_specialization_disabled_uses_uniform_model():
    """When specialization is off, all tiers should use the default gemini model."""
    settings = _make_settings(enable_tier_model_specialization=False)
    with patch("llm.provider.get_settings", return_value=settings):
        from llm.provider import get_llm_provider
        expected_by_tier = {
            "Free": settings.gemini_model_lite,
            "Pro": settings.gemini_model_core,
            "Max": settings.gemini_model_apex,
        }
        for tier, expected in expected_by_tier.items():
            provider = get_llm_provider("main", tier)
            assert provider.models[0] == expected


def test_specialization_pricing_entries_present():
    """All three specialized models must have pricing entries."""
    settings = _make_settings()
    for model in ("gemini-2.0-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"):
        assert model in settings.llm_price_input_per_m, f"{model} missing from input price table"
        assert model in settings.llm_price_output_per_m, f"{model} missing from output price table"
        assert model in settings.llm_price_thinking_per_m, f"{model} missing from thinking price table"


def test_thinking_budget_low_is_1024():
    """thinking_budget_low should be 1024 for Lite TTFT < 350ms."""
    settings = _make_settings()
    assert settings.thinking_budget_low == 1024


def test_enable_tier_model_specialization_default_is_true():
    """Specialization should be enabled by default."""
    settings = Settings(gemini_api_key="test-key")
    assert settings.enable_tier_model_specialization is True
