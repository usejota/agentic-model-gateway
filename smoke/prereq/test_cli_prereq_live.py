from __future__ import annotations

import mmap
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from smoke.lib.child_process import cmd_fcc_init, cmd_free_claude_code_serve
from smoke.lib.config import SmokeConfig
from smoke.lib.server import start_server
from smoke.lib.skips import skip_upstream_unavailable

pytestmark = [pytest.mark.live, pytest.mark.smoke_target("cli")]


def test_claude_code_one_m_regex_has_not_drifted(smoke_config: SmokeConfig) -> None:
    """Guard the per-model 1M context feature against Claude Code internals drift.

    The whole ``[1m]`` context strategy depends on Claude Code's has1mContext()
    predicate being a literal ``/\\[1m\\]/i`` regex (verified v2.1.190-2.1.195).
    If a Claude Code upgrade changes/removes it, the 1M variants silently stop
    working — fail loudly here so it is caught on upgrade rather than in prod.
    """
    claude_bin = shutil.which(smoke_config.claude_bin)
    if not claude_bin:
        pytest.skip(f"Claude CLI not found: {smoke_config.claude_bin}")
    real = Path(os.path.realpath(claude_bin))
    if not real.is_file():
        pytest.skip(f"Claude CLI binary not a regular file: {real}")

    needle = rb"/\[1m\]/i"
    with (
        open(real, "rb") as handle,
        mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped,
    ):
        found = mapped.find(needle) != -1
    assert found, (
        f"Claude Code {real} no longer contains the {needle!r} 1M-context regex; "
        "the [1m] suffix strategy may have drifted — re-verify api/gateway_model_ids.py "
        "and config/provider_catalog.py against the new Claude Code internals."
    )


def test_fcc_init_scaffolds_user_config(
    smoke_config: SmokeConfig, tmp_path: Path
) -> None:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    result = subprocess.run(
        cmd_fcc_init(),
        cwd=smoke_config.root,
        env=env,
        capture_output=True,
        text=True,
        timeout=smoke_config.timeout_s,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert (tmp_path / ".fcc" / ".env").is_file()


def test_free_claude_code_entrypoint_starts_server(smoke_config: SmokeConfig) -> None:
    with start_server(
        smoke_config,
        command=cmd_free_claude_code_serve(),
        env_overrides={"MESSAGING_PLATFORM": "none"},
        name="entrypoint",
    ) as server:
        assert server.process.poll() is None


def test_claude_cli_prompt_when_available(
    smoke_config: SmokeConfig, tmp_path: Path
) -> None:
    claude_bin = shutil.which(smoke_config.claude_bin)
    if not claude_bin:
        pytest.skip(f"Claude CLI not found: {smoke_config.claude_bin}")
    models = smoke_config.provider_models()
    if not models:
        pytest.skip("no configured provider model available for Claude CLI smoke")

    with start_server(
        smoke_config,
        env_overrides={"MODEL": models[0].full_model, "MESSAGING_PLATFORM": "none"},
        name="claude-cli",
    ) as server:
        env = os.environ.copy()
        env["ANTHROPIC_BASE_URL"] = server.base_url
        if smoke_config.settings.anthropic_auth_token:
            env["ANTHROPIC_AUTH_TOKEN"] = smoke_config.settings.anthropic_auth_token
        result = subprocess.run(
            [claude_bin, "-p", "Reply with exactly FCC_SMOKE_PONG"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=smoke_config.timeout_s,
            check=False,
        )
        server_log = server.log_path.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr or result.stdout
    assert "POST /v1/messages" in server_log, (
        "Claude CLI did not call the local Anthropic-compatible endpoint"
    )
    if "FCC_SMOKE_PONG" not in result.stdout:
        skip_upstream_unavailable(
            "Claude CLI reached the local proxy but returned no smoke token"
        )
