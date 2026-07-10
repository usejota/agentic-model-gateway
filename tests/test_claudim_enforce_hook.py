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
) -> subprocess.CompletedProcess[str]:
    """Run the hook script with the given stdin payload.

    If payload is a dict, it is JSON-serialized. If it's a str, it's passed raw.
    """
    stdin = json.dumps(payload) if isinstance(payload, dict) else payload
    full_env = {**os.environ, **(env or {})}
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
    def test_delegate_subagent_allowed(self):
        """delegate-* subagent_type -> ALLOW (no output, exit 0)."""
        _assert_allow(_run_hook(_agent_payload("delegate-sonnet")))

    def test_delegate_subagent_with_suffix_allowed(self):
        """delegate-haiku-v2 -> ALLOW."""
        _assert_allow(_run_hook(_agent_payload("delegate-haiku-v2")))

    def test_approval_subagent_ask(self):
        """approval-* subagent_type -> ASK."""
        _assert_ask(
            _run_hook(_agent_payload("approval-sonnet")),
            reason_contains="premium model",
        )

    def test_random_subagent_deny(self):
        """Random/custom subagent_type -> DENY."""
        _assert_deny(
            _run_hook(_agent_payload("random-agent")),
            reason_contains="not a delegate-* or approval-*",
        )

    def test_empty_subagent_type_deny(self):
        """Empty subagent_type -> DENY."""
        _assert_deny(
            _run_hook(_agent_payload("")),
            reason_contains="without a subagent_type",
        )

    def test_missing_subagent_type_deny(self):
        """Missing subagent_type -> DENY."""
        _assert_deny(
            _run_hook({"tool_name": "Agent", "tool_input": {}}),
            reason_contains="without a subagent_type",
        )

    def test_subagent_type_none_deny(self):
        """None subagent_type -> DENY."""
        _assert_deny(
            _run_hook(_agent_payload(None)),
            reason_contains="without a subagent_type",
        )

    def test_subagent_type_int_deny(self):
        """Non-string subagent_type (int) -> DENY."""
        _assert_deny(
            _run_hook(_agent_payload(42)),
            reason_contains="without a subagent_type",
        )


class TestTaskTool:
    def test_delegate_subagent_allowed(self):
        """Task tool with delegate-* -> ALLOW."""
        _assert_allow(_run_hook(_task_payload("delegate-opus")))

    def test_approval_subagent_ask(self):
        """Task tool with approval-* -> ASK."""
        _assert_ask(
            _run_hook(_task_payload("approval-opus")),
            reason_contains="premium model",
        )

    def test_random_subagent_deny(self):
        """Task tool with random subagent_type -> DENY."""
        _assert_deny(
            _run_hook(_task_payload("custom-thing")),
            reason_contains="not a delegate-* or approval-*",
        )

    def test_missing_subagent_type_deny(self):
        """Task tool without subagent_type -> DENY."""
        _assert_deny(
            _run_hook({"tool_name": "Task", "tool_input": {}}),
            reason_contains="without a subagent_type",
        )


# ---------------------------------------------------------------------------
# Workflow tool tests
# ---------------------------------------------------------------------------


class TestWorkflowTool:
    def test_run_in_background_deny(self):
        """Workflow with run_in_background -> DENY."""
        _assert_deny(
            _run_hook(
                {"tool_name": "Workflow", "tool_input": {"run_in_background": True}}
            ),
            reason_contains="run_in_background",
        )

    def test_valid_delegate_agents_allow(self):
        """Workflow with valid delegate-* agents -> ALLOW."""
        _assert_allow(
            _run_hook(
                _workflow_payload(
                    [
                        {"type": "delegate-sonnet", "prompt": "do X"},
                        {"type": "delegate-haiku", "prompt": "do Y"},
                    ]
                )
            )
        )

    def test_invalid_agent_types_deny(self):
        """Workflow with invalid agent types -> DENY."""
        _assert_deny(
            _run_hook(
                _workflow_payload(
                    [
                        {"type": "delegate-sonnet", "prompt": "ok"},
                        {"type": "random-agent", "prompt": "bad"},
                    ]
                )
            ),
            reason_contains="random-agent",
        )

    def test_without_agents_field_allow(self):
        """Workflow without agents field -> ALLOW."""
        _assert_allow(_run_hook({"tool_name": "Workflow", "tool_input": {}}))

    def test_agents_not_a_list_allow(self):
        """Workflow where agents is not a list -> ALLOW."""
        _assert_allow(
            _run_hook({"tool_name": "Workflow", "tool_input": {"agents": "not-a-list"}})
        )

    def test_approval_agents_ask(self):
        """approval-* in Workflow agents -> ASK."""
        _assert_ask(
            _run_hook(
                _workflow_payload(
                    [{"type": "approval-sonnet", "prompt": "needs approval"}]
                )
            ),
            reason_contains="approval",
        )

    def test_mixed_delegate_and_approval_ask(self):
        """Mixed delegate-* and approval-* -> ASK (approval takes precedence)."""
        _assert_ask(
            _run_hook(
                _workflow_payload(
                    [
                        {"type": "delegate-haiku", "prompt": "cheap"},
                        {"type": "approval-sonnet", "prompt": "premium"},
                    ]
                )
            )
        )

    def test_workflow_agents_by_subagent_type_key(self):
        """Workflow agents identified by subagent_type key."""
        _assert_allow(
            _run_hook(
                _workflow_payload(
                    [{"subagent_type": "delegate-sonnet", "prompt": "ok"}]
                )
            )
        )

    def test_workflow_agents_by_name_key(self):
        """Workflow agents identified by name key."""
        _assert_allow(
            _run_hook(_workflow_payload([{"name": "delegate-haiku", "prompt": "ok"}]))
        )

    def test_workflow_entry_not_dict_skipped(self):
        """Non-dict entries in agents list are skipped."""
        _assert_allow(
            _run_hook(
                _workflow_payload(
                    [
                        {"type": "delegate-sonnet", "prompt": "ok"},
                        "not-a-dict",
                    ]
                )
            )
        )

    def test_empty_agents_list_allow(self):
        """Empty agents list -> ALLOW."""
        _assert_allow(_run_hook(_workflow_payload([])))

    def test_workflow_unnamed_agent_deny(self):
        """Agent with no type/name fields ends up as <unnamed> -> DENY."""
        _assert_deny(
            _run_hook(_workflow_payload([{"prompt": "no type here"}])),
            reason_contains="<unnamed>",
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

    def test_tool_input_not_dict(self):
        """tool_input is not a dict -> treated as empty dict, Agent denies."""
        _assert_deny(
            _run_hook({"tool_name": "Agent", "tool_input": "not-a-dict"}),
            reason_contains="without a subagent_type",
        )

    def test_missing_tool_name(self):
        """Missing tool_name -> treated as non-enforced (pass-through)."""
        _assert_allow(_run_hook({"tool_input": {"subagent_type": "delegate-sonnet"}}))


# ---------------------------------------------------------------------------
# Custom allowlist tests
# ---------------------------------------------------------------------------


class TestCustomAllowlist:
    def test_custom_agent_in_allowlist_allowed(self, tmp_path: Path):
        """Custom agent in allowlist -> ALLOW."""
        allowlist_path = tmp_path / "claudim-allowlist.json"
        allowlist_path.write_text(
            json.dumps({"custom_agents": ["my-custom-agent", "another-agent"]})
        )
        _assert_allow(
            _run_hook(
                _agent_payload("my-custom-agent"),
                env={"CLAUDIM_ALLOWLIST_PATH": str(allowlist_path)},
            )
        )

    def test_custom_agent_not_in_allowlist_deny(self, tmp_path: Path):
        """Custom agent NOT in allowlist -> DENY."""
        allowlist_path = tmp_path / "claudim-allowlist.json"
        allowlist_path.write_text(json.dumps({"custom_agents": ["known-agent"]}))
        _assert_deny(
            _run_hook(
                _agent_payload("unknown-agent"),
                env={"CLAUDIM_ALLOWLIST_PATH": str(allowlist_path)},
            )
        )

    def test_custom_agent_in_workflow_allowed(self, tmp_path: Path):
        """Custom agent in Workflow agents -> ALLOW."""
        allowlist_path = tmp_path / "claudim-allowlist.json"
        allowlist_path.write_text(json.dumps({"custom_agents": ["my-custom-agent"]}))
        _assert_allow(
            _run_hook(
                _workflow_payload([{"type": "my-custom-agent", "prompt": "ok"}]),
                env={"CLAUDIM_ALLOWLIST_PATH": str(allowlist_path)},
            )
        )

    def test_allowlist_file_missing(self, tmp_path: Path):
        """Missing allowlist file -> empty set, custom agents denied."""
        _assert_deny(
            _run_hook(
                _agent_payload("some-agent"),
                env={"CLAUDIM_ALLOWLIST_PATH": str(tmp_path / "nonexistent.json")},
            )
        )

    def test_allowlist_malformed_json(self, tmp_path: Path):
        """Malformed allowlist JSON -> empty set, custom agents denied."""
        allowlist_path = tmp_path / "claudim-allowlist.json"
        allowlist_path.write_text("not json {{{")
        _assert_deny(
            _run_hook(
                _agent_payload("some-agent"),
                env={"CLAUDIM_ALLOWLIST_PATH": str(allowlist_path)},
            )
        )

    def test_allowlist_no_custom_agents_key(self, tmp_path: Path):
        """Allowlist without custom_agents key -> empty set, delegate still works."""
        allowlist_path = tmp_path / "claudim-allowlist.json"
        allowlist_path.write_text(json.dumps({"other": "stuff"}))
        _assert_allow(
            _run_hook(
                _agent_payload("delegate-sonnet"),
                env={"CLAUDIM_ALLOWLIST_PATH": str(allowlist_path)},
            )
        )


# ---------------------------------------------------------------------------
# Deny reason content
# ---------------------------------------------------------------------------


class TestDenyReasonContent:
    def test_agent_deny_reason_mentions_claudim_models(self):
        """DENY reason mentions `claudim models --all`."""
        proc = _run_hook(_agent_payload("bad-agent"))
        reason = json.loads(proc.stdout)["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        assert "claudim models --all" in reason

    def test_agent_deny_reason_mentions_allowlist(self):
        """DENY reason mentions the allowlist file."""
        proc = _run_hook(_agent_payload("bad-agent"))
        reason = json.loads(proc.stdout)["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        assert "claudim-allowlist.json" in reason

    def test_workflow_deny_lists_offending_agents(self):
        """DENY reason for Workflow lists the offending agent names."""
        proc = _run_hook(
            _workflow_payload(
                [
                    {"type": "evil-agent", "prompt": "x"},
                    {"type": "bad-agent", "prompt": "y"},
                ]
            )
        )
        reason = json.loads(proc.stdout)["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        assert "evil-agent" in reason
        assert "bad-agent" in reason

    def test_workflow_deny_uses_unnamed_for_blank(self):
        """DENY reason uses <unnamed> for agents with blank type."""
        proc = _run_hook(_workflow_payload([{"type": "", "prompt": "x"}]))
        reason = json.loads(proc.stdout)["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        assert "<unnamed>" in reason


# ---------------------------------------------------------------------------
# JSON output structure
# ---------------------------------------------------------------------------


class TestOutputStructure:
    def test_deny_output_has_required_fields(self):
        """DENY output has hookSpecificOutput with all required fields."""
        proc = _run_hook(_agent_payload("bad-agent"))
        output = json.loads(proc.stdout)
        hso = output["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert isinstance(hso["permissionDecisionReason"], str)
        assert len(hso["permissionDecisionReason"]) > 0

    def test_ask_output_has_required_fields(self):
        """ASK output has hookSpecificOutput with all required fields."""
        proc = _run_hook(_agent_payload("approval-sonnet"))
        output = json.loads(proc.stdout)
        hso = output["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "ask"
        assert isinstance(hso["permissionDecisionReason"], str)
        assert len(hso["permissionDecisionReason"]) > 0

    def test_allow_output_is_empty(self):
        """ALLOW decision produces no stdout output."""
        proc = _run_hook(_agent_payload("delegate-haiku"))
        assert proc.stdout.strip() == ""
