"""Tests for scripts/install-claudim.sh — renameability, baked URL, name validation.

Runs the installer in a tmpdir with a ``file://`` source override so no network
is needed and the real ``~/.local/bin`` is never touched. Pairs with the static
launcher-renameability + upgrade-hardening checks on ``deploy/claudim``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "scripts" / "install-claudim.sh"
LAUNCHER = REPO_ROOT / "deploy" / "claudim"


def _file_url(p: Path) -> str:
    return f"file://{p}"


def _installer_env(bin_dir: Path, home: Path, **extra: str) -> dict[str, str]:
    """Env to run the installer offline into a throwaway bin/home.

    The launcher source is pointed at the local repo file via file://, so
    curl/wget never hit the network. CLAUDIM_BIN_DIR/HOME isolate the footprint.
    """
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("CLAUDIM_")},
        "HOME": str(home),
        "CLAUDIM_BIN_DIR": str(bin_dir),
        "CLAUDIM_SRC": _file_url(LAUNCHER),
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
    """Default install (no CLAUDIM_NAME) installs an executable `claudim`."""
    bin_dir = tmp_path / "bin"
    proc = _run_installer(bin_dir, tmp_path)
    assert proc.returncode == 0, proc.stderr
    launcher = bin_dir / "claudim"
    assert launcher.exists()
    assert launcher.stat().st_mode & 0o111  # executable


def test_install_renamed_name_creates_launcher(tmp_path: Path) -> None:
    """CLAUDIM_NAME=buxexa installs the binary under that name (not `claudim`)."""
    bin_dir = tmp_path / "bin"
    proc = _run_installer(bin_dir, tmp_path, CLAUDIM_NAME="buxexa")
    assert proc.returncode == 0, proc.stderr
    assert (bin_dir / "buxexa").exists()
    # The default name is NOT created — the install name fully parameterizes it.
    assert not (bin_dir / "claudim").exists()


# ---------------------------------------------------------------------------
# Baked gateway URL (CLAUDIM_DEFAULT_BASE_URL → CLAUDIM_BAKED_BASE_URL)
# ---------------------------------------------------------------------------


def test_install_bakes_default_base_url(tmp_path: Path) -> None:
    """CLAUDIM_DEFAULT_BASE_URL is stamped into the installed binary's
    CLAUDIM_BAKED_BASE_URL so a local-test wrapper points at its own gateway."""
    bin_dir = tmp_path / "bin"
    proc = _run_installer(
        bin_dir,
        tmp_path,
        CLAUDIM_NAME="loclaudim",
        CLAUDIM_DEFAULT_BASE_URL="http://localhost:8082",
    )
    assert proc.returncode == 0, proc.stderr
    text = (bin_dir / "loclaudim").read_text(encoding="utf-8")
    assert 'CLAUDIM_BAKED_BASE_URL="http://localhost:8082"' in text


def test_install_default_has_empty_baked_url(tmp_path: Path) -> None:
    """Default install leaves CLAUDIM_BAKED_BASE_URL empty (no bake)."""
    bin_dir = tmp_path / "bin"
    proc = _run_installer(bin_dir, tmp_path, CLAUDIM_NAME="claudim")
    assert proc.returncode == 0, proc.stderr
    text = (bin_dir / "claudim").read_text(encoding="utf-8")
    assert 'CLAUDIM_BAKED_BASE_URL=""' in text


# ---------------------------------------------------------------------------
# Name validation (rejects characters unsafe as a filename / sed replacement)
# ---------------------------------------------------------------------------


def test_install_rejects_invalid_name(tmp_path: Path) -> None:
    """A NAME with a shell/sed metacharacter is rejected before any download."""
    bin_dir = tmp_path / "bin"
    proc = _run_installer(bin_dir, tmp_path, CLAUDIM_NAME="bad/name")
    assert proc.returncode != 0
    assert "CLAUDIM_NAME must match [A-Za-z0-9_-]+" in proc.stderr
    # Nothing was installed — the bin dir may not even exist, and certainly no binary.
    assert not (bin_dir / "bad").exists()


# ---------------------------------------------------------------------------
# Static checks on the launcher: renameability + upgrade command-injection fix
# ---------------------------------------------------------------------------


def test_launcher_derives_self_from_argv0() -> None:
    """The launcher is renameable: SELF comes from $0, and user-facing messages
    use ${SELF} — never the hardcoded literal `claudim`."""
    text = LAUNCHER.read_text(encoding="utf-8")
    assert 'SELF="${0##*/}"' in text
    assert 'say()  { echo "[${SELF}] $*"' in text
    assert 'die()  { echo "[${SELF}] error: $*"' in text


def test_launcher_upgrade_quotes_installer_url() -> None:
    """The upgrade path shell-quotes CLAUDIM_INSTALLER with printf %q instead of
    interpolating it into a re-parsed shell string — CLAUDIM_INSTALLER is
    user-controlled (env), so unquoted interpolation would allow command
    injection into the upgrade subshell."""
    text = LAUNCHER.read_text(encoding="utf-8")
    assert '_installer_q="$(printf \'%q\' "${INSTALLER}")"' in text
    assert "curl -fsSL ${_installer_q} | sh" in text
    # The old, vulnerable interpolation must be gone.
    assert "curl -fsSL '${INSTALLER}'" not in text


def test_launcher_export_reinstall_name_on_upgrade() -> None:
    """`upgrade` exports CLAUDIM_NAME=$SELF so a renamed install reinstalls under
    its current name (loclaudim stays loclaudim) and carries any baked URL."""
    text = LAUNCHER.read_text(encoding="utf-8")
    assert 'export CLAUDIM_NAME="${SELF}"' in text
    assert "CLAUDIM_BAKED_BASE_URL" in text
