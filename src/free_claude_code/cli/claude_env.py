"""Shared Claude Code environment policy for FCC client surfaces."""

from collections.abc import Mapping

from free_claude_code.cli.proxy_auth import proxy_auth_token

# High default so Claude Code clamps it down to each model's real context window:
# 1M ([1m]) models compact near 1M while smaller models self-clamp to ~200K, with
# no per-model env needed. Overridable via the CLAUDE_CODE_AUTO_COMPACT_WINDOW
# setting; a caller's own shell value (in ``base_env``) still wins.
DEFAULT_AUTO_COMPACT_WINDOW = 1_000_000
CLAUDE_BINARY_NAME = "claude"


def build_claude_proxy_env(
    *,
    proxy_root_url: str,
    auth_token: str,
    base_env: Mapping[str, str],
    auto_compact_window: int = DEFAULT_AUTO_COMPACT_WINDOW,
) -> dict[str, str]:
    """Return the canonical environment for Claude Code proxy sessions."""

    # Claude's aggregate traffic flag also suppresses gateway model discovery.
    env = {
        key: value
        for key, value in base_env.items()
        if not key.startswith("ANTHROPIC_")
        and key != "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"
    }
    env["ANTHROPIC_BASE_URL"] = proxy_root_url
    env["ANTHROPIC_AUTH_TOKEN"] = proxy_auth_token(auth_token)
    env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
    # A caller's own shell value wins over the gateway default.
    if not env.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"):
        env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(auto_compact_window)
    env["DISABLE_AUTOUPDATER"] = "1"
    env["DISABLE_FEEDBACK_COMMAND"] = "1"
    env["DISABLE_ERROR_REPORTING"] = "1"
    env["DISABLE_TELEMETRY"] = "1"
    return env
