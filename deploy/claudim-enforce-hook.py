#!/usr/bin/env python3
"""PreToolUse routing and approval gate for native delegate subagents."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

LAUNCHER_NAME = os.environ.get("CLAUDIM_LAUNCHER_NAME") or "claudim"
ALLOWLIST_LOAD_ERRORS = (OSError, json.JSONDecodeError)
CATALOG_LOAD_ERRORS = (OSError, json.JSONDecodeError, KeyError, TypeError)
ALLOWLIST_PATH = Path(
    os.environ.get("CLAUDIM_ALLOWLIST_PATH")
    or Path.home() / ".claude" / "claudim-allowlist.json"
)
_LOCAL_ALIASES = frozenset({"opus", "sonnet", "haiku", "fable"})


@dataclass(frozen=True)
class Catalog:
    by_name: dict[str, str]
    by_id: dict[str, str]
    capabilities: dict[str, list[str]]
    loaded: bool


def enforce_mode() -> bool:
    return os.environ.get("CLAUDIM_ENFORCE", "") == "1"


def route_subagents() -> bool:
    return os.environ.get("CLAUDIM_ROUTE_SUBAGENTS", "1") != "0"


def load_allowlist() -> set[str]:
    try:
        data = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    except ALLOWLIST_LOAD_ERRORS:
        return set()
    raw = data.get("custom_agents", []) if isinstance(data, dict) else []
    return {str(value) for value in raw} if isinstance(raw, list) else set()


def load_catalog() -> Catalog:
    path = os.environ.get("CLAUDIM_CATALOG_PATH", "")
    if path:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            models = payload["models"]
            by_name = {
                model["agent_name"]: model["policy"]
                for model in models
                if isinstance(model, dict)
            }
            by_id = {
                model["id"]: model["policy"]
                for model in models
                if isinstance(model, dict)
            }
            capabilities = {
                model["agent_name"]: list(model.get("capabilities", []))
                for model in models
                if isinstance(model, dict)
            }
            return Catalog(by_name, by_id, capabilities, True)
        except CATALOG_LOAD_ERRORS:
            pass
    names = {
        name.strip()
        for name in os.environ.get("CLAUDIM_DELEGATE_AGENT_NAMES", "").split(",")
        if name.strip()
    }
    return Catalog(
        {
            name: "approval" if name.startswith("approval-") else "delegate"
            for name in names
        },
        {},
        {},
        bool(names),
    )


def _representatives(catalog: Catalog) -> list[str]:
    chosen: list[str] = []
    covered: set[str] = set()
    free = [name for name, policy in catalog.by_name.items() if policy == "delegate"]
    for name in free:
        capabilities = catalog.capabilities.get(name, [])
        if not set(capabilities).issubset(covered):
            chosen.append(name)
            covered.update(capabilities)
        if len(chosen) == 8:
            break
    for name in free:
        if name not in chosen:
            chosen.append(name)
        if len(chosen) >= 5:
            break
    return chosen[:8]


def _routing_reason(catalog: Catalog) -> str:
    choices = ", ".join(_representatives(catalog)) or "<delegate catalog unavailable>"
    return (
        f"This {LAUNCHER_NAME} session requires delegate routing. Retry with "
        f"subagent_type set to one of: {choices}. Never use approval-* as a cheap fallback."
    )


def _policy(value: object, catalog: Catalog) -> str | None:
    if not isinstance(value, str):
        return None
    return catalog.by_name.get(value) or catalog.by_id.get(value)


def decide_agent(
    tool_input: dict[str, object],
    catalog: Catalog,
    allowlist: set[str],
    *,
    strict: bool,
) -> tuple[str, str]:
    subagent_type = tool_input.get("subagent_type")
    model = tool_input.get("model")
    if not catalog.loaded and not strict:
        return "allow", ""

    # Agent's explicit model overrides the model attached to subagent_type.
    # Validate it first so neither a free agent nor the custom allowlist can
    # bypass approval/unknown-model policy.
    if isinstance(model, str):
        if model in _LOCAL_ALIASES:
            return "allow", ""
        policy = _policy(model, catalog)
        if policy == "approval":
            return "ask", f"Subagent model '{model}' requires per-spawn human approval."
        if policy == "delegate":
            return "allow", ""
        return "deny", f"Unknown or excluded delegate model '{model}'."

    policy = _policy(subagent_type, catalog)
    if policy == "approval":
        return "ask", f"Subagent '{subagent_type}' requires per-spawn human approval."
    if policy == "delegate":
        return "allow", ""
    if isinstance(subagent_type, str) and subagent_type in allowlist:
        return "allow", ""
    if isinstance(subagent_type, str) and subagent_type.startswith(
        ("delegate-", "approval-")
    ):
        return "deny", f"Unknown or excluded delegate agent '{subagent_type}'."
    if not strict and not route_subagents():
        return "allow", ""
    return "deny", _routing_reason(catalog)


def _advance_past_regex(script: str, index: int) -> int:
    """If ``script[index]`` starts a JS regex literal, skip it; else return ``index``."""
    if script[index] != "/":
        return index
    previous = script[index - 1] if index else ""
    if previous.isalnum() or previous in "_$)]>":
        return index
    pos = index + 1
    while pos < len(script):
        if script[pos] == "\\":
            pos += 2
            continue
        if script[pos] == "/":
            return pos + 1
        if script[pos] == "\n":
            return index
        pos += 1
    return index


def _skip_quoted(script: str, start: int, quote: str) -> int:
    index = start + 1
    while index < len(script):
        if script[index] == "\\":
            index += 2
            continue
        if script[index] == quote:
            return index + 1
        index += 1
    return len(script)


def _skip_comment(script: str, start: int) -> int:
    if script.startswith("//", start):
        newline = script.find("\n", start + 2)
        return len(script) if newline < 0 else newline + 1
    if script.startswith("/*", start):
        end = script.find("*/", start + 2)
        return len(script) if end < 0 else end + 2
    return start


def _agent_calls(script: str) -> list[str] | None:
    """Extract balanced agent(...) calls outside comments and string literals."""
    calls: list[str] = []
    index = 0
    while index < len(script):
        char = script[index]
        regex_end = _advance_past_regex(script, index)
        if regex_end != index:
            index = regex_end
            continue
        if char in "'\"`":
            index = _skip_quoted(script, index, char)
            continue
        comment_end = _skip_comment(script, index)
        if comment_end != index:
            index = comment_end
            continue
        match = _AGENT_CALL_RE.match(script, index)
        previous = script[index - 1] if index else ""
        if match is None or previous.isalnum() or (previous and previous in "_$"):
            index += 1
            continue
        call_start = index
        index = match.end()
        depth = 1
        while index < len(script) and depth:
            char = script[index]
            regex_end = _advance_past_regex(script, index)
            if regex_end != index:
                index = regex_end
                continue
            if char in "'\"`":
                index = _skip_quoted(script, index, char)
                continue
            comment_end = _skip_comment(script, index)
            if comment_end != index:
                index = comment_end
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            index += 1
        if depth:
            return None
        calls.append(script[call_start:index])
    return calls


_AGENT_CALL_RE = re.compile(r"agent\s*\(")
_ROUTING_RE = re.compile(r"(agentType|model)\s*:\s*(['\"])")


def _routing_values(call: str) -> dict[str, str]:
    values: dict[str, str] = {}
    index = 0
    while index < len(call):
        char = call[index]
        regex_end = _advance_past_regex(call, index)
        if regex_end != index:
            index = regex_end
            continue
        if char in "'\"`":
            index = _skip_quoted(call, index, char)
            continue
        comment_end = _skip_comment(call, index)
        if comment_end != index:
            index = comment_end
            continue
        match = _ROUTING_RE.match(call, index)
        previous = call[index - 1] if index else ""
        if match is None or previous.isalnum() or (previous and previous in "_$"):
            index += 1
            continue
        quote = match.group(2)
        value_start = match.end()
        value_end = value_start
        while value_end < len(call):
            if call[value_end] == "\\":
                value_end += 2
                continue
            if call[value_end] == quote:
                break
            value_end += 1
        if value_end >= len(call):
            return {}
        values[match.group(1)] = call[value_start:value_end]
        index = value_end + 1
    return values


def decide_workflow(
    tool_input: dict[str, object],
    catalog: Catalog,
    allowlist: set[str],
    *,
    strict: bool,
) -> tuple[str, str]:
    if "run_in_background" in tool_input:
        return (
            "deny",
            "Workflow does not accept run_in_background; remove it and retry.",
        )
    script = tool_input.get("script")
    if not isinstance(script, str) or not script.strip():
        return (
            "deny",
            "Workflow requires a readable canonical script for routing validation.",
        )
    if not catalog.loaded and not strict:
        return "allow", ""
    calls = _agent_calls(script)
    if calls is None:
        return "deny", "Workflow contains an incomplete agent() call."
    approval = False
    invalid: list[str] = []
    for call in calls:
        values = _routing_values(call)
        if not values:
            return "deny", (
                "Regenerate the script: every agent() needs agentType (delegate-*) "
                "or model (a catalog id)."
            )
        for field, value in values.items():
            if field == "model" and value in _LOCAL_ALIASES:
                continue
            policy = _policy(value, catalog)
            if policy == "approval":
                approval = True
            elif policy == "delegate" or (field == "agentType" and value in allowlist):
                continue
            else:
                invalid.append(value)
    if invalid:
        return "deny", f"Workflow contains unknown or excluded routes: {invalid}."
    if approval:
        return "ask", "Workflow contains premium route(s) requiring human approval."
    return "allow", ""


def main() -> int:
    strict = enforce_mode()
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        if not strict:
            return 0
        payload = None
    if not isinstance(payload, dict):
        if not strict:
            return 0
        decision, reason = "deny", "Malformed hook payload in strict routing mode."
        return emit_decision(decision, reason)
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        if not strict:
            return 0
        decision, reason = "deny", "Malformed tool_input in strict routing mode."
    else:
        catalog = load_catalog()
        allowlist = load_allowlist()
        if payload.get("tool_name") in ("Agent", "Task"):
            decision, reason = decide_agent(
                tool_input, catalog, allowlist, strict=strict
            )
        elif payload.get("tool_name") == "Workflow":
            decision, reason = decide_workflow(
                tool_input, catalog, allowlist, strict=strict
            )
        else:
            return 0
    return emit_decision(decision, reason)


def emit_decision(decision: str, reason: str) -> int:
    if decision != "allow":
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
