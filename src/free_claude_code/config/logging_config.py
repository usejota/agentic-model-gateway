"""Loguru-based structured logging configuration.

Structured logs are written as JSON lines to a configurable path (default
``logs/server.log``). Stdlib logging is intercepted and funneled to loguru.
Context vars (request_id, node_id, chat_id) from contextualize() are
included at top level for easy grep/filter.

Set ``FCC_JSON_LOGS=1`` (or ``true``/``yes``/``on``) to additionally emit the
same JSON lines to stdout, which journald forwards to Cloud Logging as
structured records. Unset/false leaves the human-readable file-only behavior
unchanged.
"""

import json
import logging
import os
import re
import sys
import threading
from pathlib import Path

from loguru import logger

_configured = False
_current_path: Path | None = None
_current_level = "INFO"
_current_verbose: bool | None = None
_sink_id: int | None = None

_THIRD_PARTY_LOGGERS = (
    "httpx",
    "httpcore",
    "httpcore.http11",
    "httpcore.connection",
    "telegram",
    "telegram.ext",
)

# Opt-in toggle: when truthy, an additional stdout sink emits one JSON object
# per line (same schema as the file sink). Intended for the GCP VM where
# journald -> Cloud Logging benefits from structured fields. Unset/false keeps
# the default behavior (file sink only, no stdout) for dev/tests.
_JSON_LOGS_ENV = "FCC_JSON_LOGS"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _json_logs_enabled() -> bool:
    """Return True when structured JSON stdout logging is opted in via env."""
    return os.getenv(_JSON_LOGS_ENV, "").strip().lower() in _TRUTHY


# Loguru ``logger.bind()`` key used by structured TRACE payloads; ``core/trace.py``
# uses the identical string constant ``TRACE_PAYLOAD_BINDING``.
_TRACE_PAYLOAD_BINDING = "trace_payload"

# Context keys we promote to top-level JSON for traceability / grep
_CONTEXT_KEYS = (
    "request_id",
    "node_id",
    "chat_id",
    "claude_session_id",
    "http_method",
    "http_path",
)

_TELEGRAM_BOT_RE = re.compile(
    r"(https?://api\.telegram\.org/)bot([0-9]+:[A-Za-z0-9_-]+)(/?)",
    re.IGNORECASE,
)
# Authorization: Bearer <token> (HTTP client / proxy debug lines)
_AUTH_BEARER_RE = re.compile(
    r"(\bAuthorization\s*:\s*Bearer\s+)([^\s'\"]+)",
    re.IGNORECASE,
)


def _redact_sensitive_substrings(message: str) -> str:
    """Remove obvious API tokens and secrets before JSON log line emission."""
    text = _TELEGRAM_BOT_RE.sub(r"\1bot<redacted>\3", message)
    return _AUTH_BEARER_RE.sub(r"\1<redacted>", text)


def _serialize_with_context(record) -> str:
    """Format record as JSON with context vars at top level.
    Returns a format template; we inject _json into record for output.
    """
    extra = record.get("extra", {})
    out = {
        "time": str(record["time"]),
        "level": record["level"].name,
        "message": _redact_sensitive_substrings(str(record["message"])),
        "module": record["name"],
        "function": record["function"],
        "line": record["line"],
    }
    trace_payload = extra.get(_TRACE_PAYLOAD_BINDING)
    for key in _CONTEXT_KEYS:
        if key in extra and extra[key] is not None:
            out[key] = extra[key]
    if isinstance(trace_payload, dict):
        for tk, tv in trace_payload.items():
            if tk in out:
                continue
            out[tk] = tv
        out["trace"] = True
    record["_json"] = json.dumps(out, default=str)
    return "{_json}\n"


class InterceptHandler(logging.Handler):
    """Redirect stdlib logging to loguru."""

    def __init__(self) -> None:
        super().__init__()
        self._local = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._local, "active", False):
            # Avoid deadlock when nested stdlib records fire during a loguru emit.
            return
        self._local.active = True
        try:
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno

            frame, depth = logging.currentframe(), 2
            while frame is not None and frame.f_code.co_filename == logging.__file__:
                frame = frame.f_back
                depth += 1

            logger.opt(depth=depth, exception=record.exc_info).log(
                level, record.getMessage()
            )
        finally:
            self._local.active = False


def _set_third_party_levels(verbose: bool) -> None:
    level = logging.NOTSET if verbose else logging.WARNING
    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(level)


def _add_file_sink(log_file: str | Path, level: str) -> int:
    log_path = Path(log_file)
    return logger.add(
        log_path,
        level=level,
        format=_serialize_with_context,
        encoding="utf-8",
        mode="a",
        rotation="50 MB",
        retention=20,
        enqueue=True,
    )


def configure_logging(
    log_file: str | Path,
    *,
    force: bool = False,
    verbose_third_party: bool = False,
    level: str = "INFO",
) -> None:
    """Configure loguru with JSON output to log_file and intercept stdlib logging.

    Idempotent: skips if already configured with the same path, level, and verbosity.
    On path or level change, replaces only the file sink without truncating.
    On verbosity change alone, updates only the third-party logger levels.
    Use force=True to reconfigure from scratch.

    When ``verbose_third_party`` is false, noisy HTTP and Telegram loggers are
    capped at WARNING unless explicitly configured otherwise.
    """
    global _configured, _current_path, _current_level, _current_verbose, _sink_id

    log_path = Path(log_file).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if (
        _configured
        and not force
        and log_path == _current_path
        and level == _current_level
        and verbose_third_party == _current_verbose
    ):
        return

    if not _configured or force:
        _configured = True

        logger.remove()

        log_path.write_text("")

        _sink_id = _add_file_sink(log_path, level)

        # Opt-in (FCC_JSON_LOGS truthy): also emit one JSON object per line to
        # stdout so journald -> Cloud Logging captures structured fields. The
        # formatter is shared with the file sink, so the schema is identical.
        # Unset/false keeps the file-only default that dev and tests rely on.
        if _json_logs_enabled():
            logger.add(
                sys.stdout,
                level=level,
                format=_serialize_with_context,
                enqueue=True,
            )

        intercept = InterceptHandler()
        logging.root.handlers = [intercept]
        logging.root.setLevel(logging.DEBUG)

        _set_third_party_levels(verbose_third_party)
    elif log_path != _current_path or level != _current_level:
        if _sink_id is not None:
            logger.remove(_sink_id)
        _sink_id = _add_file_sink(log_path, level)
        if verbose_third_party != _current_verbose:
            _set_third_party_levels(verbose_third_party)
    else:
        _set_third_party_levels(verbose_third_party)

    _current_path = log_path
    _current_level = level
    _current_verbose = verbose_third_party
