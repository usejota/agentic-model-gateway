"""Unit tests for claudim-enforce-hook.py — PreToolUse hook for --delegate mode."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HOOK_PATH = (
    Path(__file__).resolve().parent.parent / "deploy" / "claudim-enforce-hook.py"
)


def _run_hook(
    payload: dict[str, Any] | str,
    *,
    env: dict[str, str] | None = None,
    agent_names: str = "",
    strict: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run the hook script with the given stdin payload.

    If payload is a dict, it is JSON-serialized. If it's a str, it's passed raw.
    The ``agent_names`` param sets ``CLAUDIM_DELEGATE_AGENT_NAMES`` — the
    comma-separated list of agent names the launcher generated for this session.
    When ``strict=True``, ``CLAUDIM_ENFORCE=1`` is set, which causes unknown
    agents to be denied (strict mode) instead of allowed (transparent mode).
    """
    stdin = json.dumps(payload) if isinstance(payload, dict) else payload
    full_env = {**os.environ, **(env or {})}
    if agent_names:
        full_env["CLAUDIM_DELEGATE_AGENT_NAMES"] = agent_names
    if strict:
        full_env["CLAUDIM_ENFORCE"] = "1"
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=stdin,
        capture_output=True,
        text=True,
        env=full_env,
    )


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def _assert_allow(proc: subprocess.CompletedProcess[str]) -> None:
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def _assert_deny(
    proc: subprocess.CompletedProcess[str], *, reason_contains: str = ""
) -> None:
    assert proc.returncode == 0
    decision = json.loads(proc.stdout)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert decision["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    if reason_contains:
        assert (
            reason_contains
            in decision["hookSpecificOutput"]["permissionDecisionReason"]
        )


def _assert_ask(
    proc: subprocess.CompletedProcess[str], *, reason_contains: str = ""
) -> None:
    assert proc.returncode == 0
    decision = json.loads(proc.stdout)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert decision["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    if reason_contains:
        assert (
            reason_contains
            in decision["hookSpecificOutput"]["permissionDecisionReason"]
        )


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _agent_payload(subagent_type: object) -> dict[str, Any]:
    return {"tool_name": "Agent", "tool_input": {"subagent_type": subagent_type}}


def _task_payload(subagent_type: object) -> dict[str, Any]:
    return {"tool_name": "Task", "tool_input": {"subagent_type": subagent_type}}


def _workflow_payload(agents: object, **extra: Any) -> dict[str, Any]:
    return {"tool_name": "Workflow", "tool_input": {"agents": agents, **extra}}


# ---------------------------------------------------------------------------
# Agent / Task tool tests
# ---------------------------------------------------------------------------


class TestAgentTool:
    @pytest.mark.parametrize("strict", [False, True])
    def test_delegate_subagent_allowed(self, strict: bool):
        """delegate-* subagent_type -> ALLOW (no output, exit 0)."""
        _assert_allow(
            _run_hook(
                _agent_payload("delegate-sonnet"),
                agent_names="delegate-sonnet",
                strict=strict,
            )
        )

    @pytest.mark.parametrize("strict", [False, True])
    def test_delegate_subagent_with_suffix_allowed(self, strict: bool):
        """delegate-haiku-v2 -> ALLOW."""
        _assert_allow(
            _run_hook(
                _agent_payload("delegate-haiku-v2"),
                agent_names="delegate-haiku-v2",
                strict=strict,
            )
        )

    @pytest.mark.parametrize("strict", [False, True])
    def test_approval_subagent_ask(self, strict: bool):
        """approval-* subagent_type -> ASK."""
        _assert_ask(
            _run_hook(
                _agent_payload("approval-sonnet"),
                agent_names="approval-sonnet",
                strict=strict,
            ),
            reason_contains="premium model",
        )

    def test_random_subagent_deny_strict(self):
        """Random/custom subagent_type -> DENY (strict mode)."""
        _assert_deny(
            _run_hook(
                _agent_payload("random-agent"),
                agent_names="delegate-deepseek-v4-flash,delegate-glm-5-2",
                strict=True,
            ),
            reason_contains="not a recognized",
        )

    def test_random_subagent_allow_transparent(self):
        """Random/custom subagent_type -> ALLOW (transparent mode)."""
        _assert_allow(
            _run_hook(
                _agent_payload("random-agent"),
                agent_names="delegate-deepseek-v4-flash,delegate-glm-5-2",
            )
        )

    def test_empty_subagent_type_deny_strict(self):
        """Empty subagent_type -> DENY (strict mode)."""
        _assert_deny(
            _run_hook(
                _agent_payload(""),
                agent_names="delegate-deepseek-v4-flash",
                strict=True,
            ),
            reason_contains="without a subagent_type",
        )

    def test_empty_subagent_type_allow_transparent(self):
        """Empty subagent_type -> ALLOW (transparent mode)."""
        _assert_allow(
            _run_hook(_agent_payload(""), agent_names="delegate-deepseek-v4-flash")
        )

    def test_missing_subagent_type_deny_strict(self):
        """Missing subagent_type -> DENY (strict mode)."""
        _assert_deny(
            _run_hook(
                {"tool_name": "Agent", "tool_input": {}},
                agent_names="delegate-deepseek-v4-flash",
                strict=True,
            ),
            reason_contains="without a subagent_type",
        )

    def test_missing_subagent_type_allow_transparent(self):
        """Missing subagent_type -> ALLOW (transparent mode)."""
        _assert_allow(
            _run_hook(
                {"tool_name": "Agent", "tool_input": {}},
                agent_names="delegate-deepseek-v4-flash",
            )
        )

    def test_subagent_type_none_deny_strict(self):
        """None subagent_type -> DENY (strict mode)."""
        _assert_deny(
            _run_hook(
                _agent_payload(None),
                agent_names="delegate-deepseek-v4-flash",
                strict=True,
            ),
            reason_contains="without a subagent_type",
        )

    def test_subagent_type_none_allow_transparent(self):
        """None subagent_type -> ALLOW (transparent mode)."""
        _assert_allow(
            _run_hook(_agent_payload(None), agent_names="delegate-deepseek-v4-flash")
        )

    def test_subagent_type_int_deny_strict(self):
        """Non-string subagent_type (int) -> DENY (strict mode)."""
        _assert_deny(
            _run_hook(
                _agent_payload(42),
                agent_names="delegate-deepseek-v4-flash",
                strict=True,
            ),
            reason_contains="without a subagent_type",
        )

    def test_subagent_type_int_allow_transparent(self):
        """Non-string subagent_type (int) -> ALLOW (transparent mode)."""
        _assert_allow(
            _run_hook(_agent_payload(42), agent_names="delegate-deepseek-v4-flash")
        )


class TestTaskTool:
    @pytest.mark.parametrize("strict", [False, True])
    def test_delegate_subagent_allowed(self, strict: bool):
        """Task tool with delegate-* -> ALLOW."""
        _assert_allow(
            _run_hook(
                _task_payload("delegate-opus"),
                agent_names="delegate-opus",
                strict=strict,
            )
        )

    @pytest.mark.parametrize("strict", [False, True])
    def test_approval_subagent_ask(self, strict: bool):
        """Task tool with approval-* -> ASK."""
        _assert_ask(
            _run_hook(
                _task_payload("approval-opus"),
                agent_names="approval-opus",
                strict=strict,
            ),
            reason_contains="premium model",
        )

    def test_random_subagent_deny_strict(self):
        """Task tool with random subagent_type -> DENY (strict mode)."""
        _assert_deny(
            _run_hook(
                _task_payload("custom-thing"),
                agent_names="delegate-deepseek-v4-flash",
                strict=True,
            ),
            reason_contains="not a recognized",
        )

    def test_random_subagent_allow_transparent(self):
        """Task tool with random subagent_type -> ALLOW (transparent mode)."""
        _assert_allow(
            _run_hook(
                _task_payload("custom-thing"),
                agent_names="delegate-deepseek-v4-flash",
            )
        )

    def test_missing_subagent_type_deny_strict(self):
        """Task tool without subagent_type -> DENY (strict mode)."""
        _assert_deny(
            _run_hook(
                {"tool_name": "Task", "tool_input": {}},
                agent_names="delegate-deepseek-v4-flash",
                strict=True,
            ),
            reason_contains="without a subagent_type",
        )

    def test_missing_subagent_type_allow_transparent(self):
        """Task tool without subagent_type -> ALLOW (transparent mode)."""
        _assert_allow(
            _run_hook(
                {"tool_name": "Task", "tool_input": {}},
                agent_names="delegate-deepseek-v4-flash",
            )
        )


# ---------------------------------------------------------------------------
# Workflow tool tests
# ---------------------------------------------------------------------------


class TestWorkflowTool:
    _NAMES = "delegate-sonnet,delegate-haiku,approval-sonnet"

    @pytest.mark.parametrize("strict", [False, True])
    def test_run_in_background_deny(self, strict: bool):
        """Workflow with run_in_background -> DENY."""
        _assert_deny(
            _run_hook(
                {"tool_name": "Workflow", "tool_input": {"run_in_background": True}},
                strict=strict,
            ),
            reason_contains="run_in_background",
        )

    @pytest.mark.parametrize("strict", [False, True])
    def test_valid_delegate_agents_allow(self, strict: bool):
        """Workflow with valid delegate-* agents -> ALLOW."""
        _assert_allow(
            _run_hook(
                _workflow_payload(
                    [
                        {"type": "delegate-sonnet", "prompt": "do X"},
                        {"type": "delegate-haiku", "prompt": "do Y"},
                    ]
                ),
                agent_names=self._NAMES,
                strict=strict,
            )
        )

    def test_invalid_agent_types_deny_strict(self):
        """Workflow with invalid agent types -> DENY (strict mode)."""
        _assert_deny(
            _run_hook(
                _workflow_payload(
                    [
                        {"type": "delegate-sonnet", "prompt": "ok"},
                        {"type": "random-agent", "prompt": "bad"},
                    ]
                ),
                agent_names=self._NAMES,
                strict=True,
            ),
            reason_contains="random-agent",
        )

    def test_invalid_agent_types_allow_transparent(self):
        """Workflow with invalid agent types -> ALLOW (transparent mode)."""
        _assert_allow(
            _run_hook(
                _workflow_payload(
                    [
                        {"type": "delegate-sonnet", "prompt": "ok"},
                        {"type": "random-agent", "prompt": "bad"},
                    ]
                ),
                agent_names=self._NAMES,
            )
        )

    @pytest.mark.parametrize("strict", [False, True])
    def test_without_agents_field_allow(self, strict: bool):
        """Workflow without agents field -> ALLOW."""
        _assert_allow(
            _run_hook(
                {"tool_name": "Workflow", "tool_input": {}},
                agent_names=self._NAMES,
                strict=strict,
            )
        )

    @pytest.mark.parametrize("strict", [False, True])
    def test_agents_not_a_list_allow(self, strict: bool):
        """Workflow where agents is not a list -> ALLOW."""
        _assert_allow(
            _run_hook(
                {"tool_name": "Workflow", "tool_input": {"agents": "not-a-list"}},
                agent_names=self._NAMES,
                strict=strict,
            )
        )

    @pytest.mark.parametrize("strict", [False, True])
    def test_approval_agents_ask(self, strict: bool):
        """approval-* in Workflow agents -> ASK."""
        _assert_ask(
            _run_hook(
                _workflow_payload(
                    [{"type": "approval-sonnet", "prompt": "needs approval"}]
                ),
                agent_names=self._NAMES,
                strict=strict,
            ),
            reason_contains="approval",
        )

    @pytest.mark.parametrize("strict", [False, True])
    def test_mixed_delegate_and_approval_ask(self, strict: bool):
        """Mixed delegate-* and approval-* -> ASK (approval takes precedence)."""
        _assert_ask(
            _run_hook(
                _workflow_payload(
                    [
                        {"type": "delegate-haiku", "prompt": "cheap"},
                        {"type": "approval-sonnet", "prompt": "premium"},
                    ]
                ),
                agent_names=self._NAMES,
                strict=strict,
            )
        )

    @pytest.mark.parametrize("strict", [False, True])
    def test_workflow_agents_by_subagent_type_key(self, strict: bool):
        """Workflow agents identified by subagent_type key."""
        _assert_allow(
            _run_hook(
                _workflow_payload(
                    [{"subagent_type": "delegate-sonnet", "prompt": "ok"}]
                ),
                agent_names=self._NAMES,
                strict=strict,
            )
        )

    @pytest.mark.parametrize("strict", [False, True])
    def test_workflow_agents_by_name_key(self, strict: bool):
        """Workflow agents identified by name key."""
        _assert_allow(
            _run_hook(
                _workflow_payload([{"name": "delegate-haiku", "prompt": "ok"}]),
                agent_names=self._NAMES,
                strict=strict,
            )
        )

    @pytest.mark.parametrize("strict", [False, True])
    def test_workflow_entry_not_dict_skipped(self, strict: bool):
        """Non-dict entries in agents list are skipped."""
        _assert_allow(
            _run_hook(
                _workflow_payload(
                    [
                        {"type": "delegate-sonnet", "prompt": "ok"},
                        "not-a-dict",
                    ]
                ),
                agent_names=self._NAMES,
                strict=strict,
            )
        )

    @pytest.mark.parametrize("strict", [False, True])
    def test_empty_agents_list_allow(self, strict: bool):
        """Empty agents list -> ALLOW."""
        _assert_allow(
            _run_hook(_workflow_payload([]), agent_names=self._NAMES, strict=strict)
        )

    def test_workflow_unnamed_agent_deny_strict(self):
        """Agent with no type/name fields ends up as <unnamed> -> DENY (strict mode)."""
        _assert_deny(
            _run_hook(
                _workflow_payload([{"prompt": "no type here"}]),
                agent_names=self._NAMES,
                strict=True,
            ),
            reason_contains="<unnamed>",
        )

    def test_workflow_unnamed_agent_allow_transparent(self):
        """Agent with no type/name fields ends up as <unnamed> -> ALLOW (transparent mode)."""
        _assert_allow(
            _run_hook(
                _workflow_payload([{"prompt": "no type here"}]),
                agent_names=self._NAMES,
            )
        )


# ---------------------------------------------------------------------------
# Non-enforced tools
# ---------------------------------------------------------------------------


class TestNonEnforcedTools:
    @pytest.mark.parametrize(
        "tool_name",
        [
            "Bash",
            "Read",
            "Write",
            "Edit",
            "Glob",
            "Grep",
            "WebSearch",
            "WebFetch",
            "NotebookEdit",
        ],
    )
    def test_non_enforced_tool_pass_through(self, tool_name: str):
        """Non-enforced tools pass through (exit 0, no output)."""
        _assert_allow(_run_hook({"tool_name": tool_name, "tool_input": {}}))


# ---------------------------------------------------------------------------
# Malformed / edge-case input
# ---------------------------------------------------------------------------


class TestMalformedInput:
    def test_malformed_json_pass_through(self):
        """Malformed JSON input -> pass-through (exit 0, fail-open)."""
        proc = _run_hook("not valid json {{{")
        assert proc.returncode == 0

    def test_empty_input_pass_through(self):
        """Empty input -> pass-through (exit 0)."""
        proc = _run_hook("")
        assert proc.returncode == 0

    def test_tool_input_not_dict_strict(self):
        """tool_input is not a dict -> treated as empty dict, Agent denies (strict mode)."""
        _assert_deny(
            _run_hook({"tool_name": "Agent", "tool_input": "not-a-dict"}, strict=True),
            reason_contains="without a subagent_type",
        )

    def test_tool_input_not_dict_transparent(self):
        """tool_input is not a dict -> treated as empty dict, Agent allows (transparent mode)."""
        _assert_allow(_run_hook({"tool_name": "Agent", "tool_input": "not-a-dict"}))

    def test_missing_tool_name(self):
        """Missing tool_name -> treated as non-enforced (pass-through)."""
        _assert_allow(_run_hook({"tool_input": {"subagent_type": "delegate-sonnet"}}))


# ---------------------------------------------------------------------------
# Enforce mode tests
# ---------------------------------------------------------------------------


class TestEnforceMode:
    def test_claudim_enforce_absent_is_transparent(self):
        """CLAUDIM_ENFORCE ausente -> modo transparente (unknown agent -> allow)."""
        _assert_allow(
            _run_hook(
                _agent_payload("random-agent"),
                agent_names="delegate-deepseek-v4-flash",
            )
        )

    def test_claudim_enforce_zero_is_transparent(self):
        """CLAUDIM_ENFORCE=0 -> modo transparente (unknown agent -> allow)."""
        _assert_allow(
            _run_hook(
                _agent_payload("random-agent"),
                agent_names="delegate-deepseek-v4-flash",
                env={"CLAUDIM_ENFORCE": "0"},
            )
        )

    def test_claudim_enforce_one_is_strict(self):
        """CLAUDIM_ENFORCE=1 -> modo estrito (unknown agent -> deny)."""
        _assert_deny(
            _run_hook(
                _agent_payload("random-agent"),
                agent_names="delegate-deepseek-v4-flash",
                strict=True,
            ),
            reason_contains="not a recognized",
        )


# ---------------------------------------------------------------------------
# Custom allowlist tests
# ---------------------------------------------------------------------------


class TestCustomAllowlist:
    @pytest.mark.parametrize("strict", [False, True])
    def test_custom_agent_in_allowlist_allowed(self, tmp_path: Path, strict: bool):
        """Custom agent in allowlist -> ALLOW."""
        allowlist_path = tmp_path / "claudim-allowlist.json"
        allowlist_path.write_text(
            json.dumps({"custom_agents": ["my-custom-agent", "another-agent"]})
        )
        _assert_allow(
            _run_hook(
                _agent_payload("my-custom-agent"),
                env={"CLAUDIM_ALLOWLIST_PATH": str(allowlist_path)},
                strict=strict,
            )
        )

    @pytest.mark.parametrize("strict", [False, True])
    def test_custom_agent_not_in_allowlist(self, tmp_path: Path, strict: bool):
        """Custom agent NOT in allowlist -> DENY in strict, ALLOW in transparent."""
        allowlist_path = tmp_path / "claudim-allowlist.json"
        allowlist_path.write_text(json.dumps({"custom_agents": ["known-agent"]}))
        proc = _run_hook(
            _agent_payload("unknown-agent"),
            env={"CLAUDIM_ALLOWLIST_PATH": str(allowlist_path)},
            strict=strict,
        )
        if strict:
            _assert_deny(proc)
        else:
            _assert_allow(proc)

    @pytest.mark.parametrize("strict", [False, True])
    def test_custom_agent_in_workflow_allowed(self, tmp_path: Path, strict: bool):
        """Custom agent in Workflow agents -> ALLOW."""
        allowlist_path = tmp_path / "claudim-allowlist.json"
        allowlist_path.write_text(json.dumps({"custom_agents": ["my-custom-agent"]}))
        _assert_allow(
            _run_hook(
                _workflow_payload([{"type": "my-custom-agent", "prompt": "ok"}]),
                env={"CLAUDIM_ALLOWLIST_PATH": str(allowlist_path)},
                strict=strict,
            )
        )

    @pytest.mark.parametrize("strict", [False, True])
    def test_allowlist_file_missing(self, tmp_path: Path, strict: bool):
        """Missing allowlist file -> empty set, custom agents denied in strict, allowed in transparent."""
        proc = _run_hook(
            _agent_payload("some-agent"),
            env={"CLAUDIM_ALLOWLIST_PATH": str(tmp_path / "nonexistent.json")},
            strict=strict,
        )
        if strict:
            _assert_deny(proc)
        else:
            _assert_allow(proc)

    @pytest.mark.parametrize("strict", [False, True])
    def test_allowlist_malformed_json(self, tmp_path: Path, strict: bool):
        """Malformed allowlist JSON -> empty set, custom agents denied in strict, allowed in transparent."""
        allowlist_path = tmp_path / "claudim-allowlist.json"
        allowlist_path.write_text("not json {{{")
        proc = _run_hook(
            _agent_payload("some-agent"),
            env={"CLAUDIM_ALLOWLIST_PATH": str(allowlist_path)},
            strict=strict,
        )
        if strict:
            _assert_deny(proc)
        else:
            _assert_allow(proc)

    @pytest.mark.parametrize("strict", [False, True])
    def test_allowlist_no_custom_agents_key(self, tmp_path: Path, strict: bool):
        """Allowlist without custom_agents key -> empty set, delegate still works."""
        allowlist_path = tmp_path / "claudim-allowlist.json"
        allowlist_path.write_text(json.dumps({"other": "stuff"}))
        _assert_allow(
            _run_hook(
                _agent_payload("delegate-sonnet"),
                agent_names="delegate-sonnet",
                env={"CLAUDIM_ALLOWLIST_PATH": str(allowlist_path)},
                strict=strict,
            )
        )


# ---------------------------------------------------------------------------
# Deny reason content
# ---------------------------------------------------------------------------


class TestDenyReasonContent:
    def test_agent_deny_reason_mentions_claudim_models(self):
        """DENY reason mentions `claudim models --all` by default (retro-compat)."""
        proc = _run_hook(
            _agent_payload("bad-agent"),
            agent_names="delegate-deepseek-v4-flash",
            strict=True,
        )
        reason = json.loads(proc.stdout)["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        assert "claudim models --all" in reason

    def test_agent_deny_reason_mentions_allowlist(self):
        """DENY reason mentions the allowlist file (strict mode)."""
        proc = _run_hook(
            _agent_payload("bad-agent"),
            agent_names="delegate-deepseek-v4-flash",
            strict=True,
        )
        reason = json.loads(proc.stdout)["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        assert "claudim-allowlist.json" in reason

    def test_agent_deny_reason_uses_launcher_name_when_set(self, tmp_path: Path):
        """A renamed install (CLAUDIM_LAUNCHER_NAME) shows its own command name in
        DENY reasons, never the literal `claudim` (the allowlist path follows the
        name too, so no `claudim` token survives)."""
        proc = _run_hook(
            _agent_payload("bad-agent"),
            agent_names="delegate-deepseek-v4-flash",
            strict=True,
            env={
                "CLAUDIM_LAUNCHER_NAME": "buxexa",
                "CLAUDIM_ALLOWLIST_PATH": str(tmp_path / "buxexa-allowlist.json"),
            },
        )
        reason = json.loads(proc.stdout)["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        assert "buxexa --delegate" in reason
        assert "buxexa models --all" in reason
        assert "claudim" not in reason

    def test_agent_deny_reason_shows_effective_allowlist_path(self, tmp_path: Path):
        """DENY reason surfaces the effective allowlist path (CLAUDIM_ALLOWLIST_PATH),
        not a hardcoded filename."""
        allowlist = tmp_path / "custom-allowlist.json"
        proc = _run_hook(
            _agent_payload("bad-agent"),
            agent_names="delegate-deepseek-v4-flash",
            strict=True,
            env={"CLAUDIM_ALLOWLIST_PATH": str(allowlist)},
        )
        reason = json.loads(proc.stdout)["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        assert str(allowlist) in reason

    def test_workflow_deny_reason_uses_launcher_name_when_set(self, tmp_path: Path):
        """A renamed install shows its own command name in Workflow DENY reasons
        too — the decide_workflow path interpolates LAUNCHER_NAME the same way as
        decide_agent, so a hardcode-'claudim' regression there must not pass."""
        proc = _run_hook(
            _workflow_payload([{"type": "evil-agent", "prompt": "x"}]),
            agent_names="delegate-deepseek-v4-flash",
            strict=True,
            env={
                "CLAUDIM_LAUNCHER_NAME": "buxexa",
                "CLAUDIM_ALLOWLIST_PATH": str(tmp_path / "buxexa-allowlist.json"),
            },
        )
        reason = json.loads(proc.stdout)["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        assert "buxexa models --all" in reason
        assert "claudim" not in reason

    def test_workflow_deny_lists_offending_agents(self):
        """DENY reason for Workflow lists the offending agent names (strict mode)."""
        proc = _run_hook(
            _workflow_payload(
                [
                    {"type": "evil-agent", "prompt": "x"},
                    {"type": "bad-agent", "prompt": "y"},
                ]
            ),
            agent_names="delegate-deepseek-v4-flash",
            strict=True,
        )
        reason = json.loads(proc.stdout)["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        assert "evil-agent" in reason
        assert "bad-agent" in reason

    def test_workflow_deny_uses_unnamed_for_blank(self):
        """DENY reason uses <unnamed> for agents with blank type (strict mode)."""
        proc = _run_hook(
            _workflow_payload([{"type": "", "prompt": "x"}]),
            agent_names="delegate-deepseek-v4-flash",
            strict=True,
        )
        reason = json.loads(proc.stdout)["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        assert "<unnamed>" in reason


# ---------------------------------------------------------------------------
# JSON output structure
# ---------------------------------------------------------------------------


class TestOutputStructure:
    def test_deny_output_has_required_fields(self):
        """DENY output has hookSpecificOutput with all required fields (strict mode)."""
        proc = _run_hook(
            _agent_payload("bad-agent"),
            agent_names="delegate-deepseek-v4-flash",
            strict=True,
        )
        output = json.loads(proc.stdout)
        hso = output["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert isinstance(hso["permissionDecisionReason"], str)
        assert len(hso["permissionDecisionReason"]) > 0

    def test_ask_output_has_required_fields(self):
        """ASK output has hookSpecificOutput with all required fields."""
        proc = _run_hook(
            _agent_payload("approval-sonnet"), agent_names="approval-sonnet"
        )
        output = json.loads(proc.stdout)
        hso = output["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "ask"
        assert isinstance(hso["permissionDecisionReason"], str)
        assert len(hso["permissionDecisionReason"]) > 0

    def test_allow_output_is_empty(self):
        """ALLOW decision produces no stdout output."""
        proc = _run_hook(_agent_payload("delegate-haiku"), agent_names="delegate-haiku")
        assert proc.stdout.strip() == ""
