"""Request detection utilities for API optimizations.

Detects quota checks, title generation, prefix detection, safety classifier,
suggestion mode, and filepath extraction requests to enable targeted handling.
"""

from free_claude_code.core.anthropic import (
    Message,
    MessagesRequest,
    extract_text_from_content,
)


def _single_user_turn(request_data: MessagesRequest) -> Message | None:
    """Return the sole conversational user turn, ignoring system context."""
    user_turn: Message | None = None
    for message in request_data.messages:
        if message.role == "system":
            continue
        if message.role != "user" or user_turn is not None:
            return None
        user_turn = message
    return user_turn


def _request_system_text(request_data: MessagesRequest) -> str:
    """Return top-level and inline system text for request-shape detection."""
    parts: list[str] = []
    if request_data.system:
        text = extract_text_from_content(request_data.system)
        if text:
            parts.append(text)
    for message in request_data.messages:
        if message.role != "system":
            continue
        text = extract_text_from_content(message.content)
        if text:
            parts.append(text)
    return "\n".join(parts)


def is_quota_check_request(request_data: MessagesRequest) -> bool:
    """Check if this is a quota probe request.

    Quota checks are typically simple requests with max_tokens=1
    and a single message containing the word "quota".
    """
    message = _single_user_turn(request_data)
    if request_data.max_tokens != 1 or message is None:
        return False
    text = extract_text_from_content(message.content)
    return "quota" in text.lower()


def is_title_generation_request(request_data: MessagesRequest) -> bool:
    """Check if this is a conversation title generation request.

    Title generation requests are detected by a system prompt containing
    title extraction instructions, no tools, and a single user message.

    Matches Claude Code session title prompts (sentence-case title, JSON
    \"title\" field, etc.).
    """
    if request_data.tools:
        return False
    system_text = _request_system_text(request_data).lower()
    if "title" not in system_text:
        return False
    return "sentence-case title" in system_text or (
        "return json" in system_text
        and "field" in system_text
        and ("coding session" in system_text or "this session" in system_text)
    )


def is_prefix_detection_request(request_data: MessagesRequest) -> tuple[bool, str]:
    """Check if this is a fast prefix detection request.

    Prefix detection requests contain a policy_spec block and
    a Command: section for extracting shell command prefixes.

    Returns:
        Tuple of (is_prefix_request, command_string)
    """
    message = _single_user_turn(request_data)
    if message is None:
        return False, ""

    content = extract_text_from_content(message.content)

    if "<policy_spec>" in content and "Command:" in content:
        try:
            cmd_start = content.rfind("Command:") + len("Command:")
            return True, content[cmd_start:].strip()
        except TypeError:
            return False, ""

    return False, ""


def classifier_detection_signals(request_data: MessagesRequest) -> dict[str, bool]:
    """Return the per-condition signals the classifier detector evaluates.

    Exposed (not just the final bool) so callers can trace *why* a
    classifier-shaped request did or did not match — the reroute silently
    no-ops on a miss, which is otherwise invisible in production.
    """
    system_text = (
        extract_text_from_content(request_data.system) if request_data.system else ""
    )
    messages_text = "".join(
        extract_text_from_content(message.content) for message in request_data.messages
    )
    combined = f"{system_text}\n{messages_text}"
    return {
        "no_tools": not request_data.tools,
        "has_transcript": "<transcript>" in combined,
        "has_verdict_block": "yes</block>" in combined or "no</block>" in combined,
        "has_security_monitor": "security monitor for autonomous" in combined,
    }


def is_classifier_shaped(signals: dict[str, bool]) -> bool:
    """Whether a request looks like a classifier attempt (worth tracing a miss).

    Broader than the strict match: any of the classifier-specific markers is
    enough to distinguish it from ordinary chat traffic, so a near-miss (e.g.
    marker present but verdict-block absent) gets logged instead of silently
    dropped.
    """
    return (
        signals["has_transcript"]
        or signals["has_verdict_block"]
        or signals["has_security_monitor"]
    )


def is_safety_classifier_request(request_data: MessagesRequest) -> bool:
    """Return whether this is Claude Code's auto-mode safety classifier prompt."""
    signals = classifier_detection_signals(request_data)
    return (
        signals["no_tools"]
        and signals["has_transcript"]
        and signals["has_verdict_block"]
    )


def is_suggestion_mode_request(request_data: MessagesRequest) -> bool:
    """Check if this is a suggestion mode request.

    Suggestion mode requests contain "[SUGGESTION MODE:" in the user's message,
    used for auto-suggesting what the user might type next.
    """
    for msg in request_data.messages:
        if msg.role == "user":
            text = extract_text_from_content(msg.content)
            if "[SUGGESTION MODE:" in text:
                return True
    return False


def is_filepath_extraction_request(
    request_data: MessagesRequest,
) -> tuple[bool, str, str]:
    """Check if this is a filepath extraction request.

    Filepath extraction requests have a single user message with
    "Command:" and "Output:" sections, asking to extract file paths
    from command output.

    Returns:
        Tuple of (is_filepath_request, command, output)
    """
    message = _single_user_turn(request_data)
    if message is None:
        return False, "", ""
    if request_data.tools:
        return False, "", ""

    content = extract_text_from_content(message.content)

    if "Command:" not in content or "Output:" not in content:
        return False, "", ""

    # Match if user content OR system block indicates filepath extraction
    user_has_filepaths = (
        "filepaths" in content.lower() or "<filepaths>" in content.lower()
    )
    system_text = _request_system_text(request_data)
    system_has_extract = (
        "extract any file paths" in system_text.lower()
        or "file paths that this command" in system_text.lower()
    )
    if not user_has_filepaths and not system_has_extract:
        return False, "", ""

    cmd_start = content.find("Command:") + len("Command:")
    output_marker = content.find("Output:", cmd_start)
    if output_marker == -1:
        return False, "", ""

    command = content[cmd_start:output_marker].strip()
    output = content[output_marker + len("Output:") :].strip()

    for marker in ["<", "\n\n"]:
        if marker in output:
            output = output.split(marker)[0].strip()

    return True, command, output
