"""Tests for config/logging_config.py."""

import io
import json
import logging
from pathlib import Path

from loguru import logger

from free_claude_code.config.logging_config import configure_logging


def test_configure_logging_creates_parent_directories(tmp_path) -> None:
    """Nested log path: parent directories are created before truncating."""
    log_file = tmp_path / "nested" / "dir" / "app.log"
    configure_logging(str(log_file), force=True)
    assert log_file.is_file()


def test_configure_logging_writes_json_to_file(tmp_path):
    """configure_logging writes JSON lines to the specified file."""
    log_file = str(tmp_path / "test.log")
    configure_logging(log_file, force=True)

    # Emit a log via stdlib (intercepted to loguru)
    logger = logging.getLogger("test.module")
    logger.info("Test message for JSON")

    # Force flush - loguru may buffer
    from loguru import logger as loguru_logger

    loguru_logger.complete()

    content = Path(log_file).read_text(encoding="utf-8")
    lines = [line for line in content.strip().split("\n") if line]
    assert len(lines) >= 1

    # Each line should be valid JSON
    for line in lines:
        record = json.loads(line)
        assert "text" in record or "message" in record or "record" in record


def test_configure_logging_idempotent(tmp_path):
    """configure_logging is idempotent - safe to call twice with force."""
    log_file = str(tmp_path / "test.log")
    configure_logging(log_file, force=True)
    configure_logging(log_file, force=True)  # Should not raise

    logger = logging.getLogger("test.idempotent")
    logger.info("After second configure")


def test_configure_logging_skips_when_already_configured(tmp_path):
    """Without force, second call is a no-op (avoids reconfig on hot reload)."""
    log_file = str(tmp_path / "test.log")
    configure_logging(log_file, force=True)
    # Second call without force - should skip; no exception, log file unchanged
    configure_logging(str(tmp_path / "other.log"), force=False)
    # Logs still go to first file
    logger = logging.getLogger("test.skip")
    logger.info("Still goes to first file")
    from loguru import logger as loguru_logger

    loguru_logger.complete()
    assert (tmp_path / "test.log").exists()
    assert "Still goes to first file" in (tmp_path / "test.log").read_text(
        encoding="utf-8"
    )


def test_telegram_bot_token_redacted_in_message_field(tmp_path) -> None:
    log_file = str(tmp_path / "redact.log")
    configure_logging(log_file, force=True, verbose_third_party=False)
    token = "123456:ABCDEF-ghij-klm"
    logger.info("Calling {}", f"https://api.telegram.org/bot{token}/getMe")
    logger.complete()
    text = Path(log_file).read_text(encoding="utf-8")
    assert token not in text
    assert "bot<redacted>/" in text or "redacted" in text


def test_bearer_substring_redacted_in_log_file(tmp_path) -> None:
    log_file = str(tmp_path / "bearer.log")
    configure_logging(log_file, force=True, verbose_third_party=False)
    secret = "ya29.secret-token-abc"
    logger.info("Request headers: Authorization: Bearer {}", secret)
    logger.complete()
    text = Path(log_file).read_text(encoding="utf-8")
    assert secret not in text
    assert "Bearer" in text


def test_httpx_logger_quieted_when_not_verbose_third_party(tmp_path) -> None:
    log_file = str(tmp_path / "quiet.log")
    configure_logging(log_file, force=True, verbose_third_party=False)
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


def test_httpx_resets_to_notset_when_verbose_third_party(tmp_path) -> None:
    log_file = str(tmp_path / "verbose.log")
    configure_logging(log_file, force=True, verbose_third_party=True)
    assert logging.getLogger("httpx").level == logging.NOTSET


def _capture_json_stdout(tmp_path, monkeypatch) -> io.StringIO:
    """Configure logging with JSON stdout opted in, capturing stdout to a buffer.

    Reuses the production stdout-JSON formatter by routing ``sys.stdout`` writes
    to an in-memory buffer, so the assertions exercise the real serialization.
    """
    buffer = io.StringIO()
    monkeypatch.setenv("FCC_JSON_LOGS", "1")
    monkeypatch.setattr("free_claude_code.config.logging_config.sys.stdout", buffer)

    log_file = str(tmp_path / "json.log")
    configure_logging(log_file, force=True)
    return buffer


def test_json_logs_disabled_by_default(tmp_path, monkeypatch) -> None:
    """Default (env unset): no stdout JSON sink is added; file-only behavior."""
    buffer = io.StringIO()
    monkeypatch.delenv("FCC_JSON_LOGS", raising=False)
    monkeypatch.setattr("free_claude_code.config.logging_config.sys.stdout", buffer)

    log_file = str(tmp_path / "default.log")
    configure_logging(log_file, force=True)
    logger.info("default-mode message")
    logger.complete()

    # Nothing emitted to stdout, but the file sink still receives the record.
    assert buffer.getvalue() == ""
    assert "default-mode message" in (tmp_path / "default.log").read_text(
        encoding="utf-8"
    )


def test_json_logs_emit_parseable_lines_with_expected_keys(
    tmp_path, monkeypatch
) -> None:
    """When opted in, stdout receives one parseable JSON object per record."""
    buffer = _capture_json_stdout(tmp_path, monkeypatch)

    logger.info("structured stdout message")
    logger.complete()

    lines = [line for line in buffer.getvalue().splitlines() if line]
    assert len(lines) >= 1
    record = json.loads(lines[-1])
    assert record["message"] == "structured stdout message"
    assert record["level"] == "INFO"
    assert "time" in record
    assert "module" in record


def test_json_logs_include_bound_context_fields(tmp_path, monkeypatch) -> None:
    """Bound context (e.g. request_id) appears at top level when present."""
    buffer = _capture_json_stdout(tmp_path, monkeypatch)

    logger.bind(request_id="req-123", chat_id="chat-9").info("with context")
    logger.complete()

    line = [line for line in buffer.getvalue().splitlines() if line][-1]
    record = json.loads(line)
    assert record["request_id"] == "req-123"
    assert record["chat_id"] == "chat-9"


def test_json_logs_safe_when_context_absent(tmp_path, monkeypatch) -> None:
    """No bound context: context keys are simply omitted, no error/empty fields."""
    buffer = _capture_json_stdout(tmp_path, monkeypatch)

    logger.info("no context bound")
    logger.complete()

    line = [line for line in buffer.getvalue().splitlines() if line][-1]
    record = json.loads(line)
    assert "request_id" not in record
    assert "chat_id" not in record
    assert record["message"] == "no context bound"


def test_json_logs_env_accepts_truthy_variants(monkeypatch) -> None:
    """Truthy string variants (true/yes/on) all enable the stdout JSON sink."""
    from free_claude_code.config.logging_config import _json_logs_enabled

    for value in ("1", "true", "TRUE", "Yes", "on"):
        monkeypatch.setenv("FCC_JSON_LOGS", value)
        assert _json_logs_enabled() is True
    for value in ("", "0", "false", "no", "off"):
        monkeypatch.setenv("FCC_JSON_LOGS", value)
        assert _json_logs_enabled() is False
