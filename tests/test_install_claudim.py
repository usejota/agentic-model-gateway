"""Tests for scripts/install-claudim.sh — renameability, baked URL, name validation.

Runs the installer in a tmpdir with ``file://`` source overrides so no network is
needed and the real ``~/.local/bin`` and ``~/.claude`` are never touched. Pairs
with the static launcher-renameability checks on ``deploy/claudim``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "scripts" / "install-claudim.sh"
LAUNCHER = REPO_ROOT / "deploy" / "claudim"


def _file_url(p: Path) -> str:
    return f"file://{p}"


def _installer_env(bin_dir: Path, home: Path, **extra: str) -> dict[str, str]:
    """Env to run the installer offline into a throwaway bin/home.

    Every source the installer fetches (launcher, renderer, hook, both skills) is
    pointed at the local repo file via file://, so curl/wget never hits the
    network. CLAUDIM_BIN_DIR/HOME isolate the install footprint.
    """
    env = {
        **os.environ,
        "HOME": str(home),
        "CLAUDIM_BIN_DIR": str(bin_dir),
        "CLAUDIM_SRC": _file_url(LAUNCHER),
        "CLAUDIM_RENDER_SRC": _file_url(REPO_ROOT / "deploy" / "claudim-render.py"),
        "CLAUDIM_HOOK_SRC": _file_url(REPO_ROOT / "deploy" / "claudim-enforce-hook.py"),
        "CLAUDIM_SKILL_SRC": _file_url(
            REPO_ROOT / ".claude" / "skills" / "claudim-delegate" / "SKILL.md"
        ),
        "CLAUDIM_PANEL_SKILL_SRC": _file_url(
            REPO_ROOT / ".claude" / "skills" / "claudim-panel" / "SKILL.md"
        ),
    }
    env.update(extra)
    return env


def _run_installer(
    bin_dir: Path, home: Path, **extra: str
) -> subprocess.CompletedProcess[str]:
    bin_dir.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["sh", str(INSTALLER)],
        env=_installer_env(bin_dir, home, **extra),
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Install artifacts + renameability
# ---------------------------------------------------------------------------


def test_install_renamed_name_creates_all_artifacts(tmp_path: Path) -> None:
    """CLAUDIM_NAME=buxexa installs binary + renderer + hook + both skills under
    that name (no `claudim`-named artifacts)."""
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"
    proc = _run_installer(bin_dir, home, CLAUDIM_NAME="buxexa")
    assert proc.returncode == 0, proc.stderr
    assert (bin_dir / "buxexa").exists()
    assert (bin_dir / "buxexa-render.py").exists()
    assert (bin_dir / "buxexa-enforce-hook.py").exists()
    assert (home / ".claude" / "skills" / "buxexa-delegate" / "SKILL.md").exists()
    assert (home / ".claude" / "skills" / "buxexa-panel" / "SKILL.md").exists()
    # No leftover `claudim`-named artifacts.
    assert not (bin_dir / "claudim").exists()
    assert not (home / ".claude" / "skills" / "claudim-delegate").exists()


def test_install_renamed_skill_has_no_literal_claudim(tmp_path: Path) -> None:
    """A renamed skill references the installed name, never the lowercase literal
    `claudim`. The sed template rewrites every lowercase `claudim` token to NAME;
    uppercase `CLAUDIM_` env-var references are case-sensitive and survive."""
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"
    proc = _run_installer(bin_dir, home, CLAUDIM_NAME="buxexa")
    assert proc.returncode == 0, proc.stderr
    skill = (home / ".claude" / "skills" / "buxexa-delegate" / "SKILL.md").read_text()
    assert "claudim" not in skill  # no lowercase literal survives templating


def test_install_default_name_uses_claudim(tmp_path: Path) -> None:
    """Default install (no CLAUDIM_NAME) still installs under `claudim`."""
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"
    proc = _run_installer(bin_dir, home)
    assert proc.returncode == 0, proc.stderr
    assert (bin_dir / "claudim").exists()
    assert (home / ".claude" / "skills" / "claudim-delegate" / "SKILL.md").exists()


# ---------------------------------------------------------------------------
# Baked gateway URL (CLAUDIM_DEFAULT_BASE_URL → CLAUDIM_BAKED_BASE_URL)
# ---------------------------------------------------------------------------


def test_install_bakes_default_base_url(tmp_path: Path) -> None:
    """CLAUDIM_DEFAULT_BASE_URL is stamped into the installed binary's
    CLAUDIM_BAKED_BASE_URL so a local-test wrapper points at its own gateway."""
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"
    proc = _run_installer(
        bin_dir,
        home,
        CLAUDIM_NAME="loclaudim",
        CLAUDIM_DEFAULT_BASE_URL="http://localhost:8082",
    )
    assert proc.returncode == 0, proc.stderr
    text = (bin_dir / "loclaudim").read_text()
    assert 'CLAUDIM_BAKED_BASE_URL="http://localhost:8082"' in text


def test_install_default_has_empty_baked_url(tmp_path: Path) -> None:
    """Default install leaves CLAUDIM_BAKED_BASE_URL empty (no bake)."""
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"
    proc = _run_installer(bin_dir, home, CLAUDIM_NAME="claudim")
    assert proc.returncode == 0, proc.stderr
    text = (bin_dir / "claudim").read_text()
    assert 'CLAUDIM_BAKED_BASE_URL=""' in text


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_name", ["a/b", "my.tool", "a b", "a&b", "a$b"])
def test_install_rejects_invalid_name(tmp_path: Path, bad_name: str) -> None:
    """Invalid CLAUDIM_NAME values (sed metacharacters / bad filename chars) are
    rejected with a clear error and non-zero exit before any download."""
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"
    proc = _run_installer(bin_dir, home, CLAUDIM_NAME=bad_name)
    assert proc.returncode != 0
    assert "CLAUDIM_NAME must match [A-Za-z0-9_-]+" in proc.stderr


# ---------------------------------------------------------------------------
# Launcher renameability (static checks on deploy/claudim)
# ---------------------------------------------------------------------------


def test_launcher_derives_name_from_argv0() -> None:
    """The launcher derives its name from $0, not a hardcoded literal; the
    allowlist path follows $SELF (V1 regression guard — no literal path)."""
    text = LAUNCHER.read_text()
    assert 'SELF="${0##*/}"' in text
    assert "~/.claude/claudim-allowlist.json" not in text
    assert "${SELF}-allowlist.json" in text


def test_launcher_syntax_ok() -> None:
    """bash -n: the launcher parses cleanly."""
    proc = subprocess.run(["bash", "-n", str(LAUNCHER)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_launcher_renamed_copy_syntax_ok(tmp_path: Path) -> None:
    """A renamed copy of the launcher still parses — name is not hardcoded."""
    dest = tmp_path / "buxexa"
    dest.write_text(LAUNCHER.read_text())
    proc = subprocess.run(["bash", "-n", str(dest)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
