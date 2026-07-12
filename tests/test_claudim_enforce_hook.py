from __future__ import annotations

import ast
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
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDIM_")}
    env["CLAUDIM_CATALOG_PATH"] = str(catalog)
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


def run_with_env(
    raw_input: str,
    *,
    strict: bool,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, **env}
    if strict:
        full_env["CLAUDIM_ENFORCE"] = "1"
    return subprocess.run(
        ["python3", str(HOOK)],
        input=raw_input,
        text=True,
        capture_output=True,
        env=full_env,
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


@pytest.mark.parametrize("strict", [False, True])
def test_explicit_model_cannot_bypass_approval(tmp_path: Path, strict: bool) -> None:
    payload = {
        "tool_name": "Agent",
        "tool_input": {
            "subagent_type": "delegate-free",
            "model": "id/premium",
        },
    }
    assert decision(run(payload, tmp_path, strict=strict)) == "ask"


@pytest.mark.parametrize("strict", [False, True])
def test_allowlist_cannot_bypass_explicit_model_policy(
    tmp_path: Path, strict: bool
) -> None:
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(json.dumps({"custom_agents": ["custom-agent"]}))
    extra = {"CLAUDIM_ALLOWLIST_PATH": str(allowlist)}
    approval = {
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "custom-agent", "model": "id/premium"},
    }
    unknown = {
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "custom-agent", "model": "id/missing"},
    }
    assert decision(run(approval, tmp_path, strict=strict, extra=extra)) == "ask"
    assert decision(run(unknown, tmp_path, strict=strict, extra=extra)) == "deny"


@pytest.mark.parametrize("tool_name", ["Agent", "Task"])
@pytest.mark.parametrize("strict", [False, True])
def test_custom_allowlist_without_model_is_allowed(
    tmp_path: Path, tool_name: str, strict: bool
) -> None:
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(json.dumps({"custom_agents": ["custom-agent"]}))
    payload = {
        "tool_name": tool_name,
        "tool_input": {"subagent_type": "custom-agent"},
    }
    assert (
        decision(
            run(
                payload,
                tmp_path,
                strict=strict,
                extra={"CLAUDIM_ALLOWLIST_PATH": str(allowlist)},
            )
        )
        == "allow"
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


@pytest.mark.parametrize("catalog_contents", [None, "{broken"])
@pytest.mark.parametrize("subagent_type", ["delegate-free", "approval-premium"])
def test_missing_or_corrupt_catalog_fails_open_before_prefix_validation(
    tmp_path: Path, catalog_contents: str | None, subagent_type: str
) -> None:
    catalog = tmp_path / "catalog.json"
    if catalog_contents is not None:
        catalog.write_text(catalog_contents)
    payload = {
        "tool_name": "Agent",
        "tool_input": {"subagent_type": subagent_type},
    }
    proc = run_with_env(
        json.dumps(payload),
        strict=False,
        env={
            "CLAUDIM_CATALOG_PATH": str(catalog),
            "CLAUDIM_DELEGATE_AGENT_NAMES": "",
        },
    )
    assert decision(proc) == "allow"


@pytest.mark.parametrize("raw_input", ["{broken", "[]", "null"])
def test_malformed_payload_denied_only_in_strict(raw_input: str) -> None:
    assert decision(run_with_env(raw_input, strict=False, env={})) == "allow"
    assert decision(run_with_env(raw_input, strict=True, env={})) == "deny"


def test_hook_parses_as_python_39() -> None:
    ast.parse(HOOK.read_text(), feature_version=(3, 9))


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


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize(
    "script",
    [
        "// agentType: 'delegate-free'\nreturn agent('x')",
        "return agent(\"agentType: 'delegate-free'\")",
        "agent('x')",
        "agent('x', {agentType: 'delegate-fake'})",
        (
            "const a = agent('a', {agentType: 'delegate-free', model: 'id/free'}); "
            "const b = agent('b')"
        ),
    ],
)
def test_workflow_requires_route_on_each_call(
    tmp_path: Path, strict: bool, script: str
) -> None:
    assert decision(run(workflow(script), tmp_path, strict=strict)) == "deny"


@pytest.mark.parametrize("strict", [False, True])
def test_workflow_call_at_script_start_is_scanned(tmp_path: Path, strict: bool) -> None:
    script = "agent('x', {agentType: 'delegate-free'})"
    assert decision(run(workflow(script), tmp_path, strict=strict)) == "allow"


@pytest.mark.parametrize("strict", [False, True])
def test_workflow_effective_model_cannot_bypass_approval(
    tmp_path: Path, strict: bool
) -> None:
    script = "return agent('x', {agentType: 'delegate-free', model: 'id/premium'})"
    assert decision(run(workflow(script), tmp_path, strict=strict)) == "ask"


@pytest.mark.parametrize("strict", [False, True])
def test_workflow_invalid_route_wins_over_approval(
    tmp_path: Path, strict: bool
) -> None:
    script = (
        "const a = agent('a', {model: 'id/premium'}); "
        "const b = agent('b', {agentType: 'delegate-missing'})"
    )
    assert decision(run(workflow(script), tmp_path, strict=strict)) == "deny"


# =============================================================================
# review fixes
# =============================================================================


@pytest.mark.parametrize("strict", [False, True])
def test_agent_local_alias_model_is_allowed(tmp_path: Path, strict: bool) -> None:
    """Local aliases (opus, sonnet, haiku, fable) as explicit model → allow."""
    for alias in ("opus", "sonnet", "haiku", "fable"):
        assert (
            decision(
                run(
                    {"tool_name": "Agent", "tool_input": {"model": alias}},
                    tmp_path,
                    strict=strict,
                )
            )
            == "allow"
        )


@pytest.mark.parametrize("strict", [False, True])
def test_agent_alias_model_approval_subagent_type_asks(
    tmp_path: Path, strict: bool
) -> None:
    """Alias model + approval subagent_type → ask, consistent with Workflow."""
    payload = {
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "approval-premium", "model": "opus"},
    }
    assert decision(run(payload, tmp_path, strict=strict)) == "ask"


@pytest.mark.parametrize("strict", [False, True])
def test_agent_alias_model_delegate_subagent_type_allows(
    tmp_path: Path, strict: bool
) -> None:
    """Alias model + delegate subagent_type → allow."""
    payload = {
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "delegate-free", "model": "opus"},
    }
    assert decision(run(payload, tmp_path, strict=strict)) == "allow"


@pytest.mark.parametrize("strict", [False, True])
def test_workflow_local_alias_model_is_allowed(tmp_path: Path, strict: bool) -> None:
    script = "return agent('x', {model: 'opus'})"
    assert decision(run(workflow(script), tmp_path, strict=strict)) == "allow"


@pytest.mark.parametrize("strict", [False, True])
def test_workflow_regex_literal_does_not_swallow_scanning(
    tmp_path: Path, strict: bool
) -> None:
    """Regex literals are skipped; an unrouted call inside is still caught."""
    script = "const r = /'/; return agent('x')"
    assert decision(run(workflow(script), tmp_path, strict=strict)) == "deny"


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize(
    "script",
    [
        "const x = 1 / 2 + agent('a', {agentType: 'delegate-fake'}) / 3;",
        (
            "const r = total / count; "
            "agent('a', {agentType: 'delegate-fake'}); "
            "const q = a / b;"
        ),
    ],
)
def test_workflow_division_does_not_swallow_agent_calls(
    tmp_path: Path, strict: bool, script: str
) -> None:
    """Division operators must not be parsed as regex starts hiding agent()."""
    assert decision(run(workflow(script), tmp_path, strict=strict)) == "deny"
