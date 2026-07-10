#!/usr/bin/env python3
"""claudim-enforce-hook.py — PreToolUse hook for claudim --delegate mode.

Enforces delegate routing for Agent / Task / Workflow sub-agent spawns when
the session is in claudim --delegate mode. Without this hook, weak
orchestrators (e.g. a cheap model mapped to Opus) ignore the system prompt's
delegate instructions and spawn sub-agents on the session model, defeating
claudim's whole point: every sub-agent should be a cheap delegate (or a
premium approval-gated one), not the session model.

This hook is OPT-IN: it is only loaded when the user runs `claudim --delegate`
(or `loclaudim`, which wraps it). Normal `claudim` sessions are unaffected
— the user's life stays clean.

Decisions
---------
Agent / Task tool:
  - subagent_type starts with ``delegate-``: ALLOW (free delegate).
  - subagent_type starts with ``approval-``: ASK (premium model, per-spawn
    human approval — the approval feature).
  - subagent_type in the custom allowlist
    (``~/.claude/claudim-allowlist.json`` → ``custom_agents``): ALLOW (escape
    hatch for user-defined agents the user has explicitly opted in).
  - Anything else: DENY with a clear reason pointing at ``claudim models --all``
    and the allowlist escape hatch.

Workflow tool:
  - ``run_in_background`` in tool_input: DENY (the Workflow tool rejects this
    param with an InputValidationError; deny early with a clear reason so
    the orchestrator retries without it — saves a confusing 400 in chat).
  - Any sub-agent whose type is not delegate-*/approval-*/allowlist: DENY.

I/O
---
Stdin:  the hook JSON from Claude Code (tool_name, tool_input, ...).
Stdout: empty for ALLOW, or a JSON decision:
    {"hookSpecificOutput": {"hookEventName": "PreToolUse",
     "permissionDecision": "deny"|"ask",
     "permissionDecisionReason": "..."}}
Exit:   always 0 (the decision is in the stdout JSON).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ALLOWLIST_PATH = Path(
    os.environ.get("CLAUDIM_ALLOWLIST_PATH")
    or Path.home() / ".claude" / "claudim-allowlist.json"
)


def load_allowlist() -> set[str]:
    try:
        raw_text = ALLOWLIST_PATH.read_text(encoding="utf-8")
    except OSError:
        return set()
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return set()
    if not isinstance(data, dict):
        return set()
    raw = data.get("custom_agents", [])
    return {str(x) for x in raw} if isinstance(raw, list) else set()


def load_agent_names() -> set[str]:
    """Return the exact set of agent names the launcher generated for this session."""
    raw = os.environ.get("CLAUDIM_DELEGATE_AGENT_NAMES", "")
    if not raw:
        return set()
    return {name.strip() for name in raw.split(",") if name.strip()}


def decide_agent(
    subagent_type: object, agent_names: set[str], allowlist: set[str]
) -> tuple[str, str]:
    if not isinstance(subagent_type, str) or not subagent_type:
        return "deny", (
            "Agent/Task called without a subagent_type. In claudim --delegate "
            "mode, specify a delegate-* (free) or approval-* (premium, "
            "approval-required) agent. Run `claudim models --all` for the list."
        )
    if subagent_type in agent_names:
        if subagent_type.startswith("approval-"):
            return "ask", (
                f"Subagent '{subagent_type}' uses a premium model that requires "
                "per-spawn approval. Approve to let it run, or deny and pick a "
                "cheaper delegate-* instead."
            )
        return "allow", ""
    if subagent_type in allowlist:
        return "allow", ""
    return "deny", (
        f"Subagent '{subagent_type}' is not a recognized delegate-* or "
        "approval-* agent. In claudim --delegate mode only the agents "
        "generated for this session or agents listed in "
        "~/.claude/claudim-allowlist.json are allowed. Run "
        "`claudim models --all` to see available delegates."
    )


def decide_workflow(
    tool_input: dict, agent_names: set[str], allowlist: set[str]
) -> tuple[str, str]:
    if "run_in_background" in tool_input:
        return "deny", (
            "The Workflow tool does NOT accept the 'run_in_background' "
            "parameter (the Agent tool does, the Workflow tool does not). "
            "Remove it and retry. Claude Code will surface this as an "
            "InputValidationError otherwise."
        )
    agents = tool_input.get("agents")
    if not isinstance(agents, list):
        return "allow", ""
    bad: list[str] = []
    has_approval = False
    for entry in agents:
        if not isinstance(entry, dict):
            continue
        atype = (
            entry.get("type") or entry.get("subagent_type") or entry.get("name") or ""
        )
        if not isinstance(atype, str):
            continue
        if atype in agent_names or atype in allowlist:
            if atype.startswith("approval-"):
                has_approval = True
            continue
        bad.append(atype or "<unnamed>")
    if bad:
        return "deny", (
            f"Workflow sub-agent(s) not allowed in --delegate mode: {bad}. "
            "Each must be a recognized agent from this session or in "
            "~/.claude/claudim-allowlist.json. Run "
            "`claudim models --all` for the delegate list."
        )
    if has_approval:
        return "ask", (
            "Workflow contains approval-* sub-agent(s) that use a premium "
            "model and require per-spawn approval. Approve to let them run, "
            "or deny and pick cheaper delegate-* alternatives instead."
        )
    return "allow", ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # malformed input: pass through, don't break the session
    if not isinstance(payload, dict):
        return 0  # non-object JSON: pass through, don't break the session
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    allowlist = load_allowlist()
    agent_names = load_agent_names()

    if tool_name in ("Agent", "Task"):
        decision, reason = decide_agent(
            tool_input.get("subagent_type"), agent_names, allowlist
        )
    elif tool_name == "Workflow":
        decision, reason = decide_workflow(tool_input, agent_names, allowlist)
    else:
        return 0  # not a tool we enforce; pass through

    if decision == "allow":
        return 0
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
