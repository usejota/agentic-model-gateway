"""Admin config source loading and source precedence."""

import os
from io import StringIO
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values

from free_claude_code.config.env_files import (
    explicit_env_path as configured_explicit_env_path,
)
from free_claude_code.config.env_files import (
    repo_env_path as configured_repo_env_path,
)
from free_claude_code.config.env_template import load_env_template_or_empty
from free_claude_code.config.paths import managed_env_path

from .manifest import FIELDS

SourceType = Literal[
    "default",
    "template",
    "repo_env",
    "managed_env",
    "explicit_env_file",
    "process",
]


def repo_env_path() -> Path:
    """Return the repo-local env path."""

    return configured_repo_env_path()


def explicit_env_path() -> Path | None:
    """Return the explicit FCC_ENV_FILE path, when configured."""

    return configured_explicit_env_path(os.environ)


def configured_env_files() -> tuple[tuple[SourceType, Path], ...]:
    """Return dotenv files in low-to-high precedence order.

    Mirrors :func:`~free_claude_code.config.env_files.settings_env_files`: the
    managed env is highest precedence. When ``FCC_ENV_FILE`` resolves to the
    same file as the managed env, it is listed once as ``managed_env`` (not the
    locked ``explicit_env_file``) so admin edits to that file are not treated as
    read-only.
    """

    managed = managed_env_path()
    entries: list[tuple[SourceType, Path]] = [("repo_env", repo_env_path())]
    explicit = explicit_env_path()
    if explicit is not None and not _same_path(explicit, managed):
        entries.append(("explicit_env_file", explicit))
    entries.append(("managed_env", managed))
    return tuple(entries)


def _same_path(a: Path, b: Path) -> bool:
    """Return whether two paths point at the same file (best-effort)."""

    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


def dotenv_values_from_text(text: str) -> dict[str, str]:
    """Parse dotenv text into string values."""

    values = dotenv_values(stream=StringIO(text))
    return {key: "" if value is None else value for key, value in values.items()}


def template_values() -> dict[str, str]:
    """Return .env.example values plus manifest defaults for newer fields."""

    values = dotenv_values_from_text(load_env_template_or_empty())
    for field in FIELDS:
        values.setdefault(field.key, field.default)
    return values


def dotenv_values_from_file(path: Path) -> dict[str, str]:
    """Return dotenv values from a file, or an empty mapping when absent."""

    if not path.is_file():
        return {}
    values = dotenv_values(path)
    return {key: "" if value is None else value for key, value in values.items()}


def is_locked_source(source: SourceType) -> bool:
    """Return whether an admin value source must not be overwritten.

    Only ``process`` (a live shell/systemd env var) is truly immutable from the
    admin UI. ``explicit_env_file`` is overridable because the managed env has
    higher runtime precedence, so an admin write to the managed env still wins.
    """

    return source == "process"
