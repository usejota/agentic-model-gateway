"""Tests for context-window resolution (manual override > auto-discovered > default)."""

from config.provider_catalog import (
    DEFAULT_CONTEXT_WINDOW,
    PROVIDER_MODEL_CONTEXT_OVERRIDES,
    resolve_context_window,
)


def test_resolve_falls_back_to_default_when_unknown():
    assert (
        resolve_context_window("open_router", "unknown/model") == DEFAULT_CONTEXT_WINDOW
    )


def test_resolve_uses_manual_override():
    # Seeded in the catalog.
    assert ("deepseek", "deepseek-v4-pro") in PROVIDER_MODEL_CONTEXT_OVERRIDES
    assert resolve_context_window("deepseek", "deepseek-v4-pro") == 1_000_000


def test_resolve_uses_auto_lookup_when_no_override():
    def auto_lookup(provider_id: str, model: str) -> int | None:
        return 256_000 if (provider_id, model) == ("open_router", "grok") else None

    assert resolve_context_window("open_router", "grok", auto_lookup=auto_lookup) == (
        256_000
    )


def test_manual_override_wins_over_auto_lookup():
    def auto_lookup(provider_id: str, model: str) -> int | None:
        return 128_000  # would lose to the manual 1M override

    assert (
        resolve_context_window("deepseek", "deepseek-v4-pro", auto_lookup=auto_lookup)
        == 1_000_000
    )


def test_auto_lookup_zero_or_none_falls_back_to_default():
    assert (
        resolve_context_window("open_router", "x", auto_lookup=lambda _p, _m: None)
        == DEFAULT_CONTEXT_WINDOW
    )
    assert (
        resolve_context_window("open_router", "x", auto_lookup=lambda _p, _m: 0)
        == DEFAULT_CONTEXT_WINDOW
    )
