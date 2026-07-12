from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "deploy" / "claudim-enforce-hook.py"
FIXTURES = ROOT / "smoke" / "fixtures" / "workflow_payloads"
MODELS = [
    {
        "id": "id/free",
        "agent_name": "delegate-free",
        "policy": "delegate",
        "capabilities": ["fast"],
    },
    {
        "id": "id/code",
        "agent_name": "delegate-code",
        "policy": "delegate",
        "capabilities": ["coding"],
    },
    {
        "id": "id/vision",
        "agent_name": "delegate-vision",
        "policy": "delegate",
        "capabilities": ["vision"],
    },
    {
        "id": "id/reason",
        "agent_name": "delegate-reason",
        "policy": "delegate",
        "capabilities": ["reasoning"],
    },
    {
        "id": "id/general",
        "agent_name": "delegate-general",
        "policy": "delegate",
        "capabilities": ["general"],
    },
    {
        "id": "id/premium",
        "agent_name": "approval-premium",
        "policy": "approval",
        "capabilities": ["reasoning"],
    },
]


def run(
    payload: dict,
    tmp_path: Path,
    *,
    strict: bool = False,
    extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"models": MODELS}))
    env = {**os.environ, "CLAUDIM_CATALOG_PATH": str(catalog)}
    if strict:
        env["CLAUDIM_ENFORCE"] = "1"
    if extra:
        env.update(extra)
    return subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
    )


def decision(proc: subprocess.CompletedProcess[str]) -> str:
    return (
        "allow"
        if not proc.stdout
        else json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]
    )


@pytest.mark.parametrize("strict", [False, True])
def test_agent_policy_matrix(tmp_path: Path, strict: bool) -> None:
    assert (
        decision(
            run(
                {
                    "tool_name": "Agent",
                    "tool_input": {"subagent_type": "delegate-free"},
                },
                tmp_path,
                strict=strict,
            )
        )
        == "allow"
    )
    assert (
        decision(
            run(
                {
                    "tool_name": "Agent",
                    "tool_input": {"subagent_type": "approval-premium"},
                },
                tmp_path,
                strict=strict,
            )
        )
        == "ask"
    )
    assert (
        decision(
            run(
                {
                    "tool_name": "Agent",
                    "tool_input": {"subagent_type": "delegate-fake"},
                },
                tmp_path,
                strict=strict,
            )
        )
        == "deny"
    )
    generic = run(
        {"tool_name": "Agent", "tool_input": {"subagent_type": "general-purpose"}},
        tmp_path,
        strict=strict,
    )
    assert decision(generic) == "deny"
    assert (
        json.loads(generic.stdout)["hookSpecificOutput"][
            "permissionDecisionReason"
        ].count("delegate-")
        >= 5
    )


@pytest.mark.parametrize("strict", [False, True])
def test_explicit_model_policy(tmp_path: Path, strict: bool) -> None:
    assert (
        decision(
            run(
                {"tool_name": "Agent", "tool_input": {"model": "id/free"}},
                tmp_path,
                strict=strict,
            )
        )
        == "allow"
    )
    assert (
        decision(
            run(
                {"tool_name": "Agent", "tool_input": {"model": "id/premium"}},
                tmp_path,
                strict=strict,
            )
        )
        == "ask"
    )
    assert (
        decision(
            run(
                {"tool_name": "Agent", "tool_input": {"model": "id/missing"}},
                tmp_path,
                strict=strict,
            )
        )
        == "deny"
    )


def test_transparent_escape_hatch(tmp_path: Path) -> None:
    payload = {"tool_name": "Agent", "tool_input": {"subagent_type": "general-purpose"}}
    assert (
        decision(run(payload, tmp_path, extra={"CLAUDIM_ROUTE_SUBAGENTS": "0"}))
        == "allow"
    )
    assert (
        decision(
            run(payload, tmp_path, strict=True, extra={"CLAUDIM_ROUTE_SUBAGENTS": "0"})
        )
        == "deny"
    )


def test_catalog_missing_fail_open_only_transparent(tmp_path: Path) -> None:
    payload = {"tool_name": "Agent", "tool_input": {"subagent_type": "general-purpose"}}
    missing = tmp_path / "missing.json"
    env = {
        **os.environ,
        "CLAUDIM_CATALOG_PATH": str(missing),
        "CLAUDIM_DELEGATE_AGENT_NAMES": "",
    }
    transparent = subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
    )
    assert decision(transparent) == "allow"
    env["CLAUDIM_ENFORCE"] = "1"
    strict = subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
    )
    assert decision(strict) == "deny"


def workflow(script: str, **extra: object) -> dict:
    return {"tool_name": "Workflow", "tool_input": {"script": script, **extra}}


@pytest.mark.parametrize("strict", [False, True])
def test_workflow_matrix_from_captured_shape(tmp_path: Path, strict: bool) -> None:
    assert (
        decision(
            run(
                workflow("return agent('x', {agentType: 'delegate-free'})"),
                tmp_path,
                strict=strict,
            )
        )
        == "allow"
    )
    assert (
        decision(
            run(
                workflow("return agent('x', {model: 'id/premium'})"),
                tmp_path,
                strict=strict,
            )
        )
        == "ask"
    )
    assert (
        decision(
            run(
                workflow(
                    "return agent('x', {agentType: 'delegate-fake', model: 'id/premium'})"
                ),
                tmp_path,
                strict=strict,
            )
        )
        == "deny"
    )
    captured = json.loads((FIXTURES / "parallel.json").read_text())
    assert decision(run(captured, tmp_path, strict=strict)) == "deny"
    assert (
        decision(
            run({"tool_name": "Workflow", "tool_input": {}}, tmp_path, strict=strict)
        )
        == "deny"
    )
    assert (
        decision(
            run(workflow("return 1", run_in_background=True), tmp_path, strict=strict)
        )
        == "deny"
    )
