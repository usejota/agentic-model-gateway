"""Tests for scripts/install-buxexa.sh — renameability, baked URL, name validation.

Runs the installer in a tmpdir with a ``file://`` source override so no network
is needed and the real ``~/.local/bin`` is never touched. Pairs with the static
launcher-renameability + upgrade-hardening checks on ``deploy/buxexa``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "scripts" / "install-buxexa.sh"
LAUNCHER = REPO_ROOT / "deploy" / "buxexa"


def _file_url(p: Path) -> str:
    return f"file://{p}"


def _installer_env(bin_dir: Path, home: Path, **extra: str) -> dict[str, str]:
    """Env to run the installer offline into a throwaway bin/home.

    The launcher source is pointed at the local repo file via file://, so
    curl/wget never hit the network. BUXEXA_BIN_DIR/HOME isolate the footprint.
    """
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("BUXEXA_")},
        "HOME": str(home),
        "BUXEXA_BIN_DIR": str(bin_dir),
        "BUXEXA_SRC": _file_url(LAUNCHER),
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


def test_install_default_name_creates_launcher(tmp_path: Path) -> None:
    """Default install (no BUXEXA_NAME) installs an executable `buxexa`."""
    bin_dir = tmp_path / "bin"
    proc = _run_installer(bin_dir, tmp_path)
    assert proc.returncode == 0, proc.stderr
    launcher = bin_dir / "buxexa"
    assert launcher.exists()
    assert launcher.stat().st_mode & 0o111  # executable


def test_install_renamed_name_creates_launcher(tmp_path: Path) -> None:
    """BUXEXA_NAME=buxexa2 installs the binary under that name (not the default `buxexa`)."""
    bin_dir = tmp_path / "bin"
    proc = _run_installer(bin_dir, tmp_path, BUXEXA_NAME="buxexa2")
    assert proc.returncode == 0, proc.stderr
    assert (bin_dir / "buxexa2").exists()
    # The default name is NOT created — the install name fully parameterizes it.
    assert not (bin_dir / "buxexa").exists()


# ---------------------------------------------------------------------------
# Baked gateway URL (BUXEXA_DEFAULT_BASE_URL → BUXEXA_BAKED_BASE_URL)
# ---------------------------------------------------------------------------


def test_install_bakes_default_base_url(tmp_path: Path) -> None:
    """BUXEXA_DEFAULT_BASE_URL is stamped into the installed binary's
    BUXEXA_BAKED_BASE_URL so a local-test wrapper points at its own gateway."""
    bin_dir = tmp_path / "bin"
    proc = _run_installer(
        bin_dir,
        tmp_path,
        BUXEXA_NAME="lobuxexa",
        BUXEXA_DEFAULT_BASE_URL="http://localhost:8082",
    )
    assert proc.returncode == 0, proc.stderr
    text = (bin_dir / "lobuxexa").read_text(encoding="utf-8")
    assert 'BUXEXA_BAKED_BASE_URL="http://localhost:8082"' in text


def test_install_default_has_empty_baked_url(tmp_path: Path) -> None:
    """Default install leaves BUXEXA_BAKED_BASE_URL empty (no bake)."""
    bin_dir = tmp_path / "bin"
    proc = _run_installer(bin_dir, tmp_path, BUXEXA_NAME="buxexa")
    assert proc.returncode == 0, proc.stderr
    text = (bin_dir / "buxexa").read_text(encoding="utf-8")
    assert 'BUXEXA_BAKED_BASE_URL=""' in text


# ---------------------------------------------------------------------------
# Name validation (rejects characters unsafe as a filename / sed replacement)
# ---------------------------------------------------------------------------


def test_install_rejects_invalid_name(tmp_path: Path) -> None:
    """A NAME with a shell/sed metacharacter is rejected before any download."""
    bin_dir = tmp_path / "bin"
    proc = _run_installer(bin_dir, tmp_path, BUXEXA_NAME="bad/name")
    assert proc.returncode != 0
    assert "BUXEXA_NAME must match [A-Za-z0-9_-]+" in proc.stderr
    # Nothing was installed — the bin dir may not even exist, and certainly no binary.
    assert not (bin_dir / "bad").exists()


# ---------------------------------------------------------------------------
# Static checks on the launcher: renameability + upgrade command-injection fix
# ---------------------------------------------------------------------------


def test_launcher_derives_self_from_argv0() -> None:
    """The launcher is renameable: SELF comes from $0, and user-facing messages
    use ${SELF} — never the hardcoded literal `buxexa`."""
    text = LAUNCHER.read_text(encoding="utf-8")
    assert 'SELF="${0##*/}"' in text
    assert 'say()  { echo "[${SELF}] $*"' in text
    assert 'die()  { echo "[${SELF}] error: $*"' in text


def test_launcher_upgrade_quotes_installer_url() -> None:
    """The upgrade path shell-quotes BUXEXA_INSTALLER with printf %q instead of
    interpolating it into a re-parsed shell string — BUXEXA_INSTALLER is
    user-controlled (env), so unquoted interpolation would allow command
    injection into the upgrade subshell."""
    text = LAUNCHER.read_text(encoding="utf-8")
    assert '_installer_q="$(printf \'%q\' "${INSTALLER}")"' in text
    assert "curl -fsSL ${_installer_q} | sh" in text
    # The old, vulnerable interpolation must be gone.
    assert "curl -fsSL '${INSTALLER}'" not in text


def test_launcher_export_reinstall_name_on_upgrade() -> None:
    """`upgrade` exports BUXEXA_NAME=$SELF so a renamed install reinstalls under
    its current name (lobuxexa stays lobuxexa) and carries any baked URL."""
    text = LAUNCHER.read_text(encoding="utf-8")
    assert 'export BUXEXA_NAME="${SELF}"' in text
    assert "BUXEXA_BAKED_BASE_URL" in text
