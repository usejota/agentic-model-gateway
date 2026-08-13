"""Tests for sanitize_tool_input (drop recursively-empty optional fields)."""

import json

from free_claude_code.core.anthropic.streaming import (
    AnthropicStreamLedger,
    ToolSchema,
    sanitize_tool_input,
)
from free_claude_code.providers.openai_chat.tool_calls import OpenAIToolCallAssembler

_MONITOR_SCHEMA = ToolSchema(
    name="Monitor",
    input_schema={
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "timeout_ms": {"type": "number"},
            "persistent": {"type": "boolean"},
            "command": {"type": "string"},
            "ws": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "protocols": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
        "required": ["description", "timeout_ms", "persistent"],
        "additionalProperties": False,
    },
)

_READ_SCHEMA = ToolSchema(
    name="Read",
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "pages": {"type": "string"},
            "offset": {"type": "number"},
        },
        "required": ["file_path"],
        "additionalProperties": False,
    },
)


def _schemas() -> dict[str, ToolSchema]:
    return {"Monitor": _MONITOR_SCHEMA, "Read": _READ_SCHEMA}


def test_drops_empty_optional_union_branch() -> None:
    parsed = {
        "description": "watch ci",
        "timeout_ms": 300000,
        "persistent": False,
        "command": "bash ./poll.sh 964",
        "ws": {"url": "", "protocols": []},
    }
    out = sanitize_tool_input("Monitor", parsed, _schemas())
    assert out == {
        "description": "watch ci",
        "timeout_ms": 300000,
        "persistent": False,
        "command": "bash ./poll.sh 964",
    }


def test_drops_empty_string_optional_scalar() -> None:
    parsed = {"file_path": "/etc/hosts", "pages": ""}
    out = sanitize_tool_input("Read", parsed, _schemas())
    assert out == {"file_path": "/etc/hosts"}


def test_keeps_required_key_even_when_empty() -> None:
    parsed = {"file_path": "", "pages": "1-3"}
    out = sanitize_tool_input("Read", parsed, _schemas())
    assert out == {"file_path": "", "pages": "1-3"}


def test_keeps_zero_and_false_optionals() -> None:
    parsed = {
        "description": "d",
        "timeout_ms": 0,
        "persistent": False,
        "command": "true",
    }
    out = sanitize_tool_input("Monitor", parsed, _schemas())
    assert out == parsed


def test_keeps_nonempty_optional_object() -> None:
    parsed = {
        "description": "d",
        "timeout_ms": 1000,
        "persistent": False,
        "ws": {"url": "wss://events.example.com", "protocols": []},
    }
    out = sanitize_tool_input("Monitor", parsed, _schemas())
    assert out == parsed


def test_drops_none_and_nested_empty() -> None:
    parsed = {"file_path": "/x", "pages": None, "offset": {}}
    out = sanitize_tool_input("Read", parsed, _schemas())
    assert out == {"file_path": "/x"}


def test_unknown_tool_passthrough() -> None:
    parsed = {"anything": ""}
    assert sanitize_tool_input("Nope", parsed, _schemas()) == parsed


def test_assembler_sanitizes_buffered_args() -> None:
    ledger = AnthropicStreamLedger("msg_t", "m")
    assembler = OpenAIToolCallAssembler(tool_schemas=_schemas())
    events = list(
        assembler.process_tool_call(
            {
                "index": 0,
                "id": "call_1",
                "function": {
                    "name": "Monitor",
                    "arguments": json.dumps(
                        {
                            "description": "watch ci",
                            "timeout_ms": 300000,
                            "persistent": False,
                            "command": "bash ./poll.sh 964",
                            "ws": {"url": "", "protocols": []},
                        }
                    ),
                },
            },
            ledger,
        )
    )
    text = "".join(events)
    assert '"ws"' not in text
    assert '\\"command\\":\\"bash ./poll.sh 964\\"' in text


def test_assembler_sanitizes_args_split_across_deltas() -> None:
    ledger = AnthropicStreamLedger("msg_t", "m")
    assembler = OpenAIToolCallAssembler(tool_schemas=_schemas())
    events = list(
        assembler.process_tool_call(
            {"index": 0, "id": "call_1", "function": {"name": "Read", "arguments": ""}},
            ledger,
        )
    )
    events += list(
        assembler.process_tool_call(
            {
                "index": 0,
                "function": {
                    "name": None,
                    "arguments": '{"file_path": "/etc/hosts", "pag',
                },
            },
            ledger,
        )
    )
    events += list(
        assembler.process_tool_call(
            {"index": 0, "function": {"name": None, "arguments": 'es": ""}'}},
            ledger,
        )
    )
    text = "".join(events)
    assert '"pages"' not in text
    assert '\\"file_path\\":\\"/etc/hosts\\"' in text
