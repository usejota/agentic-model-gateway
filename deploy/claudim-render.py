#!/usr/bin/env python3
# claudim-render.py — turn `claude -p --output-format stream-json --verbose`
# into a compact Claude-Code-style pane feed for the tmux observer.
#
# The previous renderer (a heredoc inside deploy/claudim) was a debugging
# black box: tool calls printed as raw 200-char JSON; tool results as bare
# ✓/✗ with no content or error message. When a delegate subagent hit a
# single tool failure, the user saw "✗ tool result" with no idea WHY, and
# the subagent itself sometimes concluded (wrongly) that it had no tools.
# This renderer:
#   - renders tool_use as a single short line per tool (Read, Edit, Write,
#     Bash, Grep, Glob all have a compact form);
#   - surfaces tool_result CONTENT (first line + size for Read, first
#     output line for Bash, match counts for Grep/Glob) so the user can
#     verify the delegate actually got the data;
#   - ALWAYS surfaces the error text on ✗ (this is the bug fix);
#   - shows thinking blocks (dimmed, first 100 chars) so reasoning models
#     are not silent black boxes;
#   - prints a per-turn separator with elapsed time so the user can see
#     pacing and intervene in a stuck pane.
#
# stdout: the pane feed (ANSI colors). Also writes the final result to the
# path passed as argv[1] in the caller's format (text by default, raw JSON
# for --output-format json), so the orchestrator's captured stdout is
# unchanged by the live view.
#
# Usage (from deploy/claudim):
#   stream-json | python3 claudim-render.py <out_path> <text|json>
#
# Self-test (no claude invocation; pipes synthetic events):
#   python3 claudim-render.py <out> text < fixtures.jsonl
from __future__ import annotations

import json
import sys
import time

DIM = "\033[2m"
RST = "\033[0m"
GRY = "\033[90m"
GRN = "\033[32m"
RED = "\033[31m"
YEL = "\033[33m"


def _say(s: str = "") -> None:
    sys.stdout.write(s + "\n")
    sys.stdout.flush()


def _elapsed(start: float) -> str:
    secs = int(time.time() - start)
    return f"{secs // 60}:{secs % 60:02d}"


def _compact_tool(name: str, inp: dict) -> str:
    if name == "Read":
        return f"{GRY}●{RST} Read {inp.get('file_path', '?')}"
    if name == "Edit":
        path = inp.get("file_path", "?")
        old = (inp.get("old_string") or "").count("\n")
        new = (inp.get("new_string") or "").count("\n")
        return f"{GRY}●{RST} Edit {path} (-{old}/+{new})"
    if name == "Write":
        path = inp.get("file_path", "?")
        n = (inp.get("content") or "").count("\n") + 1
        return f"{GRY}●{RST} Write {path} ({n} lines)"
    if name == "Bash":
        cmd = (inp.get("command") or "").split("\n", 1)[0]
        if len(cmd) > 80:
            cmd = cmd[:80] + "…"
        return f"{GRY}●{RST} Bash $ {cmd}"
    if name == "Grep":
        return f"{GRY}●{RST} Grep {inp.get('pattern', '?')!r}"
    if name == "Glob":
        return f"{GRY}●{RST} Glob {inp.get('pattern', '?')}"
    return f"{GRY}●{RST} {name}"


def _compact_result(name: str, content: str, is_error: bool) -> str:
    if is_error:
        c = (content or "").strip()
        if len(c) > 240:
            c = c[:240] + "…"
        mark = f"{RED}✗{RST}"
        if c:
            return f"  {mark} {c}"
        return f"  {mark} (no message)"
    c = (content or "").strip()
    first = c.split("\n", 1)[0] if c else ""
    if name == "Read":
        if not c:
            return f"  {GRN}✓{RST} (empty file)"
        lines = c.count("\n") + 1
        if len(first) > 80:
            first = first[:80] + "…"
        return f"  {GRN}✓{RST} {lines} lines · {first}"
    if name == "Bash":
        if not c:
            return f"  {GRN}✓{RST} (no output)"
        if len(first) > 100:
            first = first[:100] + "…"
        return f"  {GRN}✓{RST} {first}"
    if name in ("Grep", "Glob"):
        m = first if first else ""
        if len(m) > 100:
            m = m[:100] + "…"
        return f"  {GRN}✓{RST} {m}" if m else f"  {GRN}✓{RST} ok"
    if not c:
        return f"  {GRN}✓{RST} ok"
    if len(first) > 100:
        first = first[:100] + "…"
    return f"  {GRN}✓{RST} {first}"


def render(out_path: str, fmt: str) -> None:
    start = time.time()
    turn = 0
    last_tool_names: dict[str, str] = {}
    result_payload = ""
    saw_result = False
    # stream-json callers expect the full event sequence on stdout, not just
    # the final result text — buffer every raw line and replay it verbatim so
    # the orchestrator's stream-json parser sees the same events it would have
    # without the live-view renderer in the pipe.
    raw_lines: list[str] = []

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if fmt == "stream-json":
            raw_lines.append(line)
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            _say(
                f"{DIM}─── session started ({ev.get('model', '?')}) · "
                f"{_elapsed(start)} ───{RST}"
            )
        elif t == "assistant":
            turn += 1
            _say(f"{GRY}─── T{turn} · {_elapsed(start)} ───{RST}")
            for block in (ev.get("message") or {}).get("content", []):
                bt = block.get("type")
                if bt == "thinking" and block.get("thinking"):
                    think = block["thinking"].strip().replace("\n", " ")
                    if len(think) > 100:
                        think = think[:100] + "…"
                    _say(f"  {DIM}✻ {think}{RST}")
                elif bt == "text" and block.get("text"):
                    _say(block["text"])
                elif bt == "tool_use":
                    tid = block.get("id", "")
                    if tid:
                        last_tool_names[tid] = block.get("name", "")
                    _say(
                        "  "
                        + _compact_tool(block.get("name", "?"), block.get("input", {}))
                    )
        elif t == "user":
            for block in (ev.get("message") or {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id", "")
                    name = last_tool_names.get(tid, "")
                    raw = block.get("content", "")
                    if isinstance(raw, list):
                        txts = [
                            c.get("text", "")
                            for c in raw
                            if isinstance(c, dict) and c.get("type") == "text"
                        ]
                        content = "\n".join(txts)
                    else:
                        content = raw or ""
                    _say(_compact_result(name, content, block.get("is_error", False)))
        elif t == "result":
            saw_result = True
            if fmt == "stream-json":
                # Full replay happens after the loop; nothing to set here.
                pass
            elif fmt == "json":
                result_payload = json.dumps(ev, ensure_ascii=False)
            else:
                result_payload = ev.get("result") or ""

    if fmt == "stream-json":
        result_payload = "\n".join(raw_lines)

    with open(out_path, "w") as f:
        f.write(result_payload)

    # If claude died (OOM, CLAUDIM_MAX_WAIT timeout, network drop) before
    # emitting a final `result` event, the orchestrator must NOT see exit 0 —
    # otherwise `claudim -p ... && next` proceeds on empty output. Surface the
    # failure via non-zero exit so the launcher propagates it.
    if not saw_result:
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        render(sys.argv[1], sys.argv[2])
    else:
        sys.exit("usage: claudim-render.py <out_path> <text|json>")
