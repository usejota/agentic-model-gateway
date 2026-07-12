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
ALLOWLIST_PATH = Path(
    os.environ.get("CLAUDIM_ALLOWLIST_PATH")
    or Path.home() / ".claude" / "claudim-allowlist.json"
)


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
    except OSError, json.JSONDecodeError:
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
        except OSError, json.JSONDecodeError, KeyError, TypeError:
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
        if len(chosen) == 5:
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
    for value in (subagent_type, model):
        policy = _policy(value, catalog)
        if policy == "approval":
            return "ask", f"Subagent '{value}' requires per-spawn human approval."
        if policy == "delegate":
            return "allow", ""
    if isinstance(subagent_type, str) and subagent_type in allowlist:
        return "allow", ""
    if isinstance(subagent_type, str) and subagent_type.startswith(
        ("delegate-", "approval-")
    ):
        return "deny", f"Unknown or excluded delegate agent '{subagent_type}'."
    if isinstance(model, str):
        return "deny", f"Unknown or excluded delegate model '{model}'."
    if not catalog.loaded and not strict:
        return "allow", ""
    if not strict and not route_subagents():
        return "allow", ""
    return "deny", _routing_reason(catalog)


ROUTING_VALUE_RE = re.compile(r"\b(agentType|model)\s*:\s*(['\"])([^'\"]+)\2")


def decide_workflow(
    tool_input: dict[str, object],
    catalog: Catalog,
    allowlist: set[str],
    *,
    strict: bool,
) -> tuple[str, str]:
    del strict
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
    calls = len(re.findall(r"\bagent\s*\(", script))
    matches = list(ROUTING_VALUE_RE.finditer(script))
    if len(matches) < calls:
        return "deny", (
            "Regenerate the script: every agent() needs agentType (delegate-*) "
            "or model (a catalog id)."
        )
    approval = False
    invalid: list[str] = []
    for match in matches:
        value = match.group(3)
        if value in allowlist:
            continue
        policy = _policy(value, catalog)
        if policy == "approval":
            approval = True
        elif policy != "delegate":
            invalid.append(value)
    if invalid:
        return "deny", f"Workflow contains unknown or excluded routes: {invalid}."
    if approval:
        return "ask", "Workflow contains premium route(s) requiring human approval."
    return "allow", ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    tool_input = payload.get("tool_input")
    strict = enforce_mode()
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
