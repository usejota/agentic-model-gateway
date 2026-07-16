"""Tests for admin env-file source classification and locking.

Regression coverage for the managed-env admin fix: when ``FCC_ENV_FILE`` points
at the same file as the managed env, its fields must classify as ``managed_env``
(unlocked), not the locked ``explicit_env_file`` — otherwise admin UI edits are
silently ignored on reload.
"""

from pathlib import Path

import pytest

from free_claude_code.config.admin.sources import (
    configured_env_files,
    is_locked_source,
)
from free_claude_code.config.paths import managed_env_path


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FCC_ENV_FILE", raising=False)
    (tmp_path / ".fcc").mkdir(parents=True, exist_ok=True)


def test_only_process_source_is_locked():
    assert is_locked_source("process") is True
    assert is_locked_source("explicit_env_file") is False
    assert is_locked_source("managed_env") is False
    assert is_locked_source("repo_env") is False


def test_managed_env_is_highest_precedence_and_last():
    sources = [name for name, _ in configured_env_files()]
    assert sources[-1] == "managed_env"


def test_explicit_env_file_deduped_when_equal_to_managed(monkeypatch):
    managed = managed_env_path()
    monkeypatch.setenv("FCC_ENV_FILE", str(managed))

    entries = configured_env_files()
    names = [name for name, _ in entries]
    assert "explicit_env_file" not in names
    assert names.count("managed_env") == 1
    # The managed path is still present exactly once, as managed_env.
    managed_paths = [p for name, p in entries if name == "managed_env"]
    assert len(managed_paths) == 1


def test_separate_explicit_env_file_still_listed(monkeypatch, tmp_path):
    explicit = tmp_path / "external.env"
    explicit.write_text("MODEL=open_router/foo/bar\n")
    monkeypatch.setenv("FCC_ENV_FILE", str(explicit))

    entries = configured_env_files()
    names = [name for name, _ in entries]
    assert "explicit_env_file" in names
    # Managed env stays last (highest precedence) so admin writes win.
    assert names[-1] == "managed_env"
    explicit_paths = [Path(p) for name, p in entries if name == "explicit_env_file"]
    assert explicit_paths == [explicit]
