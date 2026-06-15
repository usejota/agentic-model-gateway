"""Tests for the optional GCP Secret Manager provider-key source.

These tests run WITHOUT the optional ``gcp`` extra installed; the Secret Manager
client and the ``google.cloud.secretmanager`` import are mocked, so no real GCP
calls are made.
"""

from __future__ import annotations

import builtins
import sys
import types
from typing import Any

import pytest
from pydantic import ValidationError

from config import secret_source
from config.secret_source import SecretManagerError, fetch_secret
from config.settings import Settings


def _module(name: str, **attrs: Any) -> types.ModuleType:
    """Build a fake module, setting attributes via its ``__dict__``.

    Mutating ``vars(module)`` avoids static-typing complaints about assigning
    unknown attributes onto ``ModuleType``.
    """
    module = types.ModuleType(name)
    vars(module).update(attrs)
    return module


def _install_fake_secretmanager(monkeypatch, *, payload: bytes) -> None:
    """Install a fake ``google.cloud.secretmanager`` module returning ``payload``."""

    class _FakeClient:
        def access_secret_version(self, *, name: str):
            response = types.SimpleNamespace()
            response.payload = types.SimpleNamespace(data=payload)
            return response

    fake_module = _module(
        "google.cloud.secretmanager", SecretManagerServiceClient=_FakeClient
    )
    cloud_pkg = _module("google.cloud", secretmanager=fake_module)
    google_pkg = _module("google", cloud=cloud_pkg)

    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_pkg)
    monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", fake_module)


# --------------------------------------------------------------------------- #
# fetch_secret
# --------------------------------------------------------------------------- #
def test_fetch_secret_returns_payload(monkeypatch):
    _install_fake_secretmanager(monkeypatch, payload=b"super-secret-key")
    result = fetch_secret("projects/p/secrets/provider-key/versions/latest")
    assert result == "super-secret-key"


def test_fetch_secret_empty_resource_raises():
    with pytest.raises(SecretManagerError, match="must not be empty"):
        fetch_secret("   ")


def test_fetch_secret_missing_package_raises(monkeypatch):
    """Simulate the gcp extra not being installed."""
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "google.cloud" or name.startswith("google.cloud"):
            raise ImportError("No module named 'google.cloud'")
        return real_import(name, *args, **kwargs)

    # Drop any cached fake modules so the import path is exercised.
    for mod in ("google.cloud.secretmanager", "google.cloud", "google"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(SecretManagerError, match="google-cloud-secret-manager"):
        fetch_secret("projects/p/secrets/k/versions/latest")


def test_fetch_secret_fetch_failure_raises(monkeypatch):
    class _BoomClient:
        def access_secret_version(self, *, name: str):
            raise RuntimeError("permission denied")

    fake_module = _module(
        "google.cloud.secretmanager", SecretManagerServiceClient=_BoomClient
    )
    cloud_pkg = _module("google.cloud", secretmanager=fake_module)
    google_pkg = _module("google", cloud=cloud_pkg)
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_pkg)
    monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", fake_module)

    with pytest.raises(SecretManagerError, match="Failed to fetch secret"):
        fetch_secret("projects/p/secrets/k/versions/latest")


# --------------------------------------------------------------------------- #
# Settings integration
# --------------------------------------------------------------------------- #
def _clear_secret_env(monkeypatch) -> None:
    """Ensure no ambient Secret Manager env leaks into a test."""
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "test_key")
    monkeypatch.delenv("PROVIDER_KEY_SECRET_RESOURCE", raising=False)
    monkeypatch.delenv("PROVIDER_KEY_SECRET_TARGET", raising=False)


def test_settings_feature_off_is_noop(monkeypatch):
    """Default behavior preserved when the resource is unset."""
    _clear_secret_env(monkeypatch)
    called = False

    def _fail(_resource):
        nonlocal called
        called = True
        raise AssertionError("fetch_secret should not be called when feature off")

    monkeypatch.setattr("config.settings.fetch_secret", _fail)
    monkeypatch.setenv("MODEL", "nvidia_nim/test-model")
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "disk-key")

    settings = Settings()
    assert settings.nvidia_nim_api_key == "disk-key"
    assert called is False


def test_settings_feature_on_populates_active_provider_key(monkeypatch):
    """Feature-on resolves the secret into the active provider's key field."""
    _clear_secret_env(monkeypatch)
    monkeypatch.setattr(
        "config.settings.fetch_secret", lambda _resource: "secret-from-gcp"
    )
    monkeypatch.setenv("MODEL", "open_router/some/model")
    monkeypatch.setenv("OPENROUTER_API_KEY", "disk-key")
    monkeypatch.setenv(
        "PROVIDER_KEY_SECRET_RESOURCE",
        "projects/p/secrets/openrouter/versions/latest",
    )

    settings = Settings()
    # Active provider for open_router/* maps to open_router_api_key.
    assert settings.open_router_api_key == "secret-from-gcp"


def test_settings_feature_on_explicit_target(monkeypatch):
    _clear_secret_env(monkeypatch)
    monkeypatch.setattr(
        "config.settings.fetch_secret", lambda _resource: "explicit-secret"
    )
    monkeypatch.setenv("MODEL", "nvidia_nim/test-model")
    monkeypatch.setenv(
        "PROVIDER_KEY_SECRET_RESOURCE", "projects/p/secrets/k/versions/latest"
    )
    monkeypatch.setenv("PROVIDER_KEY_SECRET_TARGET", "gemini_api_key")

    settings = Settings()
    assert settings.gemini_api_key == "explicit-secret"


def test_settings_unknown_target_raises(monkeypatch):
    _clear_secret_env(monkeypatch)
    monkeypatch.setattr("config.settings.fetch_secret", lambda _resource: "x")
    monkeypatch.setenv("MODEL", "nvidia_nim/test-model")
    monkeypatch.setenv(
        "PROVIDER_KEY_SECRET_RESOURCE", "projects/p/secrets/k/versions/latest"
    )
    monkeypatch.setenv("PROVIDER_KEY_SECRET_TARGET", "does_not_exist")
    with pytest.raises(ValidationError, match="not a known"):
        Settings()


def test_settings_empty_secret_raises(monkeypatch):
    _clear_secret_env(monkeypatch)
    monkeypatch.setattr("config.settings.fetch_secret", lambda _resource: "  ")
    monkeypatch.setenv("MODEL", "nvidia_nim/test-model")
    monkeypatch.setenv(
        "PROVIDER_KEY_SECRET_RESOURCE", "projects/p/secrets/k/versions/latest"
    )
    with pytest.raises(SecretManagerError, match="empty value"):
        Settings()


def test_settings_feature_on_missing_package(monkeypatch):
    """End-to-end: feature on, gcp extra absent -> clear error via Settings."""
    _clear_secret_env(monkeypatch)

    def _raise(_resource):
        raise SecretManagerError("google-cloud-secret-manager is required ...")

    monkeypatch.setattr("config.settings.fetch_secret", _raise)
    monkeypatch.setenv("MODEL", "nvidia_nim/test-model")
    monkeypatch.setenv(
        "PROVIDER_KEY_SECRET_RESOURCE", "projects/p/secrets/k/versions/latest"
    )
    with pytest.raises(SecretManagerError, match="google-cloud-secret-manager"):
        Settings()


def test_secret_source_module_has_no_top_level_gcp_import():
    """Lazy import: the module must import without the optional package."""
    assert "google" not in dir(secret_source)
