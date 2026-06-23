"""Centralized configuration using Pydantic Settings."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from dotenv import dotenv_values
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from .constants import HTTP_CONNECT_TIMEOUT_DEFAULT
from .nim import NimSettings
from .paths import default_claude_workspace_path, managed_env_path
from .provider_catalog import PROVIDER_CATALOG
from .provider_ids import SUPPORTED_PROVIDER_IDS
from .secret_source import SecretManagerError, fetch_secret


@dataclass(frozen=True, slots=True)
class ConfiguredChatModelRef:
    """A unique configured chat model reference and the env keys that set it."""

    model_ref: str
    provider_id: str
    model_id: str
    sources: tuple[str, ...]


def _env_files() -> tuple[Path, ...]:
    """Return env file paths in priority order (later overrides earlier)."""
    files: list[Path] = [
        Path(".env"),
        managed_env_path(),
    ]
    if explicit := os.environ.get("FCC_ENV_FILE"):
        files.append(Path(explicit))
    return tuple(files)


def _configured_env_files(model_config: Mapping[str, Any]) -> tuple[Path, ...]:
    """Return the currently configured env files for Settings."""
    configured = model_config.get("env_file")
    if configured is None:
        return ()
    if isinstance(configured, (str, Path)):
        return (Path(configured),)
    return tuple(Path(item) for item in configured)


def _env_file_value(path: Path, key: str) -> str | None:
    """Return a dotenv value when the file explicitly defines the key."""
    if not path.is_file():
        return None

    try:
        values = dotenv_values(path)
    except OSError:
        return None

    if key not in values:
        return None
    value = values[key]
    return "" if value is None else value


def _env_file_override(model_config: Mapping[str, Any], key: str) -> str | None:
    """Return the last configured dotenv value that explicitly defines a key."""
    configured_value: str | None = None
    for env_file in _configured_env_files(model_config):
        value = _env_file_value(env_file, key)
        if value is not None:
            configured_value = value
    return configured_value


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ==================== OpenRouter Config ====================
    open_router_api_key: str = Field(default="", validation_alias="OPENROUTER_API_KEY")

    # ==================== Mistral La Plateforme ====================
    mistral_api_key: str = Field(default="", validation_alias="MISTRAL_API_KEY")

    # ==================== Mistral Codestral (codestral.mistral.ai) ====================
    codestral_api_key: str = Field(default="", validation_alias="CODESTRAL_API_KEY")

    # ==================== DeepSeek Config ====================
    deepseek_api_key: str = Field(default="", validation_alias="DEEPSEEK_API_KEY")

    # ==================== Kimi Config ====================
    kimi_api_key: str = Field(default="", validation_alias="KIMI_API_KEY")

    # ==================== Wafer Config ====================
    wafer_api_key: str = Field(default="", validation_alias="WAFER_API_KEY")

    # ==================== OpenCode Zen / OpenCode Go ====================
    # Same key from opencode.ai/auth; zen uses prefix ``opencode/``, Go uses ``opencode_go/``.
    opencode_api_key: str = Field(default="", validation_alias="OPENCODE_API_KEY")

    # ==================== Z.ai Config ====================
    zai_api_key: str = Field(default="", validation_alias="ZAI_API_KEY")

    # ==================== Fireworks AI Config ====================
    fireworks_api_key: str = Field(default="", validation_alias="FIREWORKS_API_KEY")

    # ==================== Google Gemini (Google AI Studio) ====================
    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")

    # ==================== Groq (OpenAI-compatible) ====================
    groq_api_key: str = Field(default="", validation_alias="GROQ_API_KEY")

    # ==================== Cerebras Inference (OpenAI-compatible) ====================
    cerebras_api_key: str = Field(default="", validation_alias="CEREBRAS_API_KEY")

    # ==================== Messaging Platform Selection ====================
    # Valid: "telegram" | "discord" | "none"
    messaging_platform: str = Field(
        default="discord", validation_alias="MESSAGING_PLATFORM"
    )
    messaging_rate_limit: int = Field(
        default=1, validation_alias="MESSAGING_RATE_LIMIT"
    )
    messaging_rate_window: float = Field(
        default=1.0, validation_alias="MESSAGING_RATE_WINDOW"
    )

    # ==================== NVIDIA NIM Config ====================
    nvidia_nim_api_key: str = ""

    # ==================== LM Studio Config ====================
    lm_studio_base_url: str = Field(
        default="http://localhost:1234/v1",
        validation_alias="LM_STUDIO_BASE_URL",
    )

    # ==================== Llama.cpp Config ====================
    llamacpp_base_url: str = Field(
        default="http://localhost:8080/v1",
        validation_alias="LLAMACPP_BASE_URL",
    )

    # ==================== Ollama Config ====================
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        validation_alias="OLLAMA_BASE_URL",
    )

    # ==================== Model ====================
    # All Claude model requests are mapped to this single model (fallback)
    # Format: provider_type/model/name
    model: str = "nvidia_nim/nvidia/nemotron-3-super-120b-a12b"

    # Per-model overrides (optional, falls back to MODEL)
    # Each can use a different provider
    model_opus: str | None = Field(default=None, validation_alias="MODEL_OPUS")
    model_sonnet: str | None = Field(default=None, validation_alias="MODEL_SONNET")
    model_haiku: str | None = Field(default=None, validation_alias="MODEL_HAIKU")

    # Optional cross-model fallback chain. When a request's backend fails with an
    # overload/5xx error *before any response has streamed*, the proxy retries the
    # request on each of these models in order. Format: comma-separated
    # ``provider/model`` refs (same form as MODEL), e.g.
    # ``open_router/deepseek/deepseek-chat,groq/llama-3.3-70b``. Empty = disabled
    # (no fallback; behavior unchanged). This makes auto-mode's safety classifier
    # and all requests resilient to a single backend being briefly unavailable.
    fallback_models: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="FALLBACK_MODELS"
    )

    # Optional image reroute. When a request has image content (top-level user
    # message or nested in a tool_result) AND the resolved primary model
    # doesn't accept images, this provider+model handles just that turn.
    # Format: a single ``provider/model`` ref, same form as MODEL,
    # e.g. ``open_router/minimax/minimax-m3``. Empty = no reroute (the request
    # is sent upstream as-is). When unset, a text-only primary provider will
    # 400 on image content; setting IMAGE_ROUTE lets users keep a cheap
    # text-only default while still being able to paste screenshots.
    image_route: str | None = Field(default=None, validation_alias="IMAGE_ROUTE")

    # ==================== Per-Provider Proxy ====================
    nvidia_nim_proxy: str = Field(default="", validation_alias="NVIDIA_NIM_PROXY")
    open_router_proxy: str = Field(default="", validation_alias="OPENROUTER_PROXY")
    mistral_proxy: str = Field(default="", validation_alias="MISTRAL_PROXY")
    codestral_proxy: str = Field(default="", validation_alias="CODESTRAL_PROXY")
    lmstudio_proxy: str = Field(default="", validation_alias="LMSTUDIO_PROXY")
    llamacpp_proxy: str = Field(default="", validation_alias="LLAMACPP_PROXY")
    kimi_proxy: str = Field(default="", validation_alias="KIMI_PROXY")
    wafer_proxy: str = Field(default="", validation_alias="WAFER_PROXY")
    opencode_proxy: str = Field(default="", validation_alias="OPENCODE_PROXY")
    opencode_go_proxy: str = Field(default="", validation_alias="OPENCODE_GO_PROXY")
    zai_proxy: str = Field(default="", validation_alias="ZAI_PROXY")
    fireworks_proxy: str = Field(default="", validation_alias="FIREWORKS_PROXY")
    gemini_proxy: str = Field(default="", validation_alias="GEMINI_PROXY")
    groq_proxy: str = Field(default="", validation_alias="GROQ_PROXY")
    cerebras_proxy: str = Field(default="", validation_alias="CEREBRAS_PROXY")

    # ==================== Provider Rate Limiting ====================
    provider_rate_limit: int = Field(default=40, validation_alias="PROVIDER_RATE_LIMIT")
    provider_rate_window: int = Field(
        default=60, validation_alias="PROVIDER_RATE_WINDOW"
    )
    provider_max_concurrency: int = Field(
        default=5, validation_alias="PROVIDER_MAX_CONCURRENCY"
    )
    enable_model_thinking: bool = Field(
        default=True, validation_alias="ENABLE_MODEL_THINKING"
    )
    enable_opus_thinking: bool | None = Field(
        default=None, validation_alias="ENABLE_OPUS_THINKING"
    )
    enable_sonnet_thinking: bool | None = Field(
        default=None, validation_alias="ENABLE_SONNET_THINKING"
    )
    enable_haiku_thinking: bool | None = Field(
        default=None, validation_alias="ENABLE_HAIKU_THINKING"
    )

    # ==================== HTTP Client Timeouts ====================
    http_read_timeout: float = Field(
        default=120.0, validation_alias="HTTP_READ_TIMEOUT"
    )
    http_write_timeout: float = Field(
        default=10.0, validation_alias="HTTP_WRITE_TIMEOUT"
    )
    http_connect_timeout: float = Field(
        default=HTTP_CONNECT_TIMEOUT_DEFAULT,
        validation_alias="HTTP_CONNECT_TIMEOUT",
    )

    # ==================== Fast Prefix Detection ====================
    fast_prefix_detection: bool = True

    # ==================== Optimizations ====================
    enable_network_probe_mock: bool = True
    enable_title_generation_skip: bool = True
    enable_suggestion_mode_skip: bool = True
    enable_filepath_extraction_mock: bool = True

    # ==================== Local web server tools (web_search / web_fetch) ====================
    # Off by default: these tools perform outbound HTTP from the proxy (SSRF risk).
    enable_web_server_tools: bool = Field(
        default=False, validation_alias="ENABLE_WEB_SERVER_TOOLS"
    )
    # Comma-separated URL schemes allowed for web_fetch (default: http,https).
    web_fetch_allowed_schemes: str = Field(
        default="http,https", validation_alias="WEB_FETCH_ALLOWED_SCHEMES"
    )
    # When true, skip private/loopback/link-local IP blocking for web_fetch (lab only).
    web_fetch_allow_private_networks: bool = Field(
        default=False, validation_alias="WEB_FETCH_ALLOW_PRIVATE_NETWORKS"
    )

    # ==================== Debug / diagnostic logging (avoid sensitive content) ====================
    # When false (default), API and SSE helpers log only metadata (counts, lengths, ids).
    log_raw_api_payloads: bool = Field(
        default=False, validation_alias="LOG_RAW_API_PAYLOADS"
    )
    log_raw_sse_events: bool = Field(
        default=False, validation_alias="LOG_RAW_SSE_EVENTS"
    )
    # When false (default), unhandled exceptions log only type + route metadata (no message/traceback).
    log_api_error_tracebacks: bool = Field(
        default=False, validation_alias="LOG_API_ERROR_TRACEBACKS"
    )
    # When false (default), messaging logs omit text/transcription previews (metadata only).
    log_raw_messaging_content: bool = Field(
        default=False, validation_alias="LOG_RAW_MESSAGING_CONTENT"
    )
    # When true, log full Claude CLI stderr, non-JSON lines, and parser error text.
    log_raw_cli_diagnostics: bool = Field(
        default=False, validation_alias="LOG_RAW_CLI_DIAGNOSTICS"
    )
    # When true, log exception text / CLI error strings in messaging (may leak user content).
    log_messaging_error_details: bool = Field(
        default=False, validation_alias="LOG_MESSAGING_ERROR_DETAILS"
    )
    debug_platform_edits: bool = Field(
        default=False, validation_alias="DEBUG_PLATFORM_EDITS"
    )
    debug_subagent_stack: bool = Field(
        default=False, validation_alias="DEBUG_SUBAGENT_STACK"
    )

    # ==================== NIM Settings ====================
    nim: NimSettings = Field(default_factory=NimSettings)

    # ==================== Voice Note Transcription ====================
    voice_note_enabled: bool = Field(
        default=True, validation_alias="VOICE_NOTE_ENABLED"
    )
    # Device: "cpu" | "cuda" | "nvidia_nim"
    # - "cpu"/"cuda": local Whisper (requires voice_local extra: uv sync --extra voice_local)
    # - "nvidia_nim": NVIDIA NIM Whisper API (requires voice extra: uv sync --extra voice)
    whisper_device: str = Field(default="cpu", validation_alias="WHISPER_DEVICE")
    # Whisper model ID or short name (for local Whisper) or NVIDIA NIM model (for nvidia_nim)
    # Local Whisper: "tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo"
    # NVIDIA NIM: "nvidia/parakeet-ctc-1.1b-asr", "openai/whisper-large-v3", etc.
    whisper_model: str = Field(default="base", validation_alias="WHISPER_MODEL")
    # Hugging Face token for faster model downloads (optional, for local Whisper)
    hf_token: str = Field(default="", validation_alias="HF_TOKEN")

    # ==================== Bot Wrapper Config ====================
    telegram_bot_token: str | None = None
    allowed_telegram_user_id: str | None = None
    discord_bot_token: str | None = Field(
        default=None, validation_alias="DISCORD_BOT_TOKEN"
    )
    allowed_discord_channels: str | None = Field(
        default=None, validation_alias="ALLOWED_DISCORD_CHANNELS"
    )
    allowed_dir: str = ""
    max_message_log_entries_per_chat: int | None = Field(
        default=None, validation_alias="MAX_MESSAGE_LOG_ENTRIES_PER_CHAT"
    )

    # Auto-compaction window for Claude Code subprocess (default: 190k tokens).
    claude_code_auto_compact_window: int = Field(
        default=190000,
        validation_alias="CLAUDE_CODE_AUTO_COMPACT_WINDOW",
        ge=10000,
        le=1_000_000,
    )

    # ==================== Server ====================
    host: str = "0.0.0.0"
    port: int = 8082
    # Optional server API key to protect endpoints (Anthropic-style)
    # Set via env `ANTHROPIC_AUTH_TOKEN`. When empty, no auth is required.
    anthropic_auth_token: str = Field(
        default="", validation_alias="ANTHROPIC_AUTH_TOKEN"
    )
    # Optional admin UI secret. When empty, the admin UI stays loopback-only.
    # When set, every /admin route additionally requires a matching token
    # (`X-Admin-Token` or `Authorization: Bearer ...`). Set via env
    # `ADMIN_API_TOKEN`; intentionally not editable through the admin UI itself.
    admin_api_token: str = Field(default="", validation_alias="ADMIN_API_TOKEN")
    # Optional per-user proxy tokens for per-user audit logging. When set, each
    # token authenticates as a named identity so logs can attribute a request to
    # an individual. The shared ``anthropic_auth_token`` (if set) keeps working.
    #
    # Format (env ``PROXY_USER_TOKENS``): comma-separated ``name:token`` pairs,
    # e.g. ``alice:tok-a,bob:tok-b``. A JSON object mapping name->token is also
    # accepted, e.g. ``{"alice": "tok-a", "bob": "tok-b"}``. The token itself may
    # contain colons (only the first colon splits name from token in pair form).
    proxy_user_tokens: Annotated[dict[str, str], NoDecode] = Field(
        default_factory=dict, validation_alias="PROXY_USER_TOKENS"
    )

    # ==================== GCP Secret Manager (optional) ====================
    # When set, the proxy resolves the provider API key from Secret Manager at
    # startup and keeps it in memory instead of reading it from a plaintext
    # ``.env`` file on disk. Requires the optional ``gcp`` extra.
    # Value: a version resource, e.g.
    # ``projects/<project>/secrets/<name>/versions/latest``.
    provider_key_secret_resource: str = Field(
        default="", validation_alias="PROVIDER_KEY_SECRET_RESOURCE"
    )
    # Settings field to populate with the fetched secret. When empty, the active
    # provider's credential attribute (derived from ``model``) is used.
    provider_key_secret_target: str = Field(
        default="", validation_alias="PROVIDER_KEY_SECRET_TARGET"
    )

    # Handle empty strings for optional string fields
    @field_validator(
        "telegram_bot_token",
        "allowed_telegram_user_id",
        "discord_bot_token",
        "allowed_discord_channels",
        "model_opus",
        "model_sonnet",
        "model_haiku",
        "enable_opus_thinking",
        "enable_sonnet_thinking",
        "enable_haiku_thinking",
        mode="before",
    )
    @classmethod
    def parse_optional_str(cls, v: Any) -> Any:
        if v == "":
            return None
        return v

    @field_validator("max_message_log_entries_per_chat", mode="before")
    @classmethod
    def parse_optional_log_cap(cls, v: Any) -> Any:
        if v == "" or v is None:
            return None
        return v

    @field_validator("proxy_user_tokens", mode="before")
    @classmethod
    def parse_proxy_user_tokens(cls, v: Any) -> Any:
        """Parse per-user proxy tokens from a string or pass through a mapping.

        Accepts either a JSON object string (``{"alice": "tok-a"}``) or a
        comma-separated list of ``name:token`` pairs (``alice:tok-a,bob:tok-b``).
        Only the first colon in each pair splits name from token, so tokens may
        themselves contain colons. Blank entries and surrounding whitespace are
        ignored. An empty/blank value yields an empty mapping (no-op).
        """
        if v is None or v == "":
            return {}
        if isinstance(v, Mapping):
            return {str(k).strip(): str(val).strip() for k, val in v.items()}
        if not isinstance(v, str):
            return v

        text = v.strip()
        if not text:
            return {}
        if text.startswith("{"):
            import json

            parsed = json.loads(text)
            if not isinstance(parsed, Mapping):
                raise ValueError("PROXY_USER_TOKENS JSON must be an object")
            return {str(k).strip(): str(val).strip() for k, val in parsed.items()}

        result: dict[str, str] = {}
        for pair in text.split(","):
            entry = pair.strip()
            if not entry:
                continue
            if ":" not in entry:
                raise ValueError(
                    f"Invalid PROXY_USER_TOKENS entry {entry!r}; expected 'name:token'"
                )
            name, token = entry.split(":", 1)
            name = name.strip()
            token = token.strip()
            if not name or not token:
                raise ValueError(
                    f"Invalid PROXY_USER_TOKENS entry {entry!r}; "
                    "name and token must be non-empty"
                )
            result[name] = token
        return result

    @field_validator("fallback_models", mode="before")
    @classmethod
    def parse_fallback_models(cls, v: Any) -> Any:
        """Parse the fallback model chain from a comma-separated string.

        Accepts a comma-separated list of ``provider/model`` refs
        (``open_router/deepseek/deepseek-chat,groq/llama-3.3-70b``) or a list.
        Blank entries and surrounding whitespace are ignored. An empty/blank
        value yields an empty list (fallback disabled).
        """
        if v is None or v == "":
            return []
        if isinstance(v, (list, tuple)):
            return [str(item).strip() for item in v if str(item).strip()]
        if not isinstance(v, str):
            return v
        return [entry.strip() for entry in v.split(",") if entry.strip()]

    @field_validator("image_route", mode="before")
    @classmethod
    def parse_image_route(cls, v: Any) -> Any:
        """Parse ``IMAGE_ROUTE`` as a single ``provider/model`` ref.

        Empty/blank → ``None`` (reroute disabled). Otherwise validated for
        shape (must contain a ``/`` and the provider must be a known one).
        """
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        text = v.strip()
        if not text:
            return None
        if "/" not in text:
            raise ValueError(
                f"IMAGE_ROUTE must be a single 'provider/model' ref, got {v!r}"
            )
        provider = text.split("/", 1)[0]
        if provider not in SUPPORTED_PROVIDER_IDS:
            supported = ", ".join(f"'{p}'" for p in SUPPORTED_PROVIDER_IDS)
            raise ValueError(
                f"Invalid IMAGE_ROUTE provider: '{provider}'. Supported: {supported}"
            )
        return text

    @property
    def claude_workspace(self) -> str:
        """Return the fixed Claude data workspace path."""

        return str(default_claude_workspace_path())

    @property
    def claude_cli_bin(self) -> str:
        """Return the fixed Claude Code binary name."""

        return "claude"

    @field_validator("whisper_device")
    @classmethod
    def validate_whisper_device(cls, v: str) -> str:
        if v not in ("cpu", "cuda", "nvidia_nim"):
            raise ValueError(
                f"whisper_device must be 'cpu', 'cuda', or 'nvidia_nim', got {v!r}"
            )
        return v

    @field_validator("messaging_platform")
    @classmethod
    def validate_messaging_platform(cls, v: str) -> str:
        if v not in ("telegram", "discord", "none"):
            raise ValueError(
                f"messaging_platform must be 'telegram', 'discord', or 'none', got {v!r}"
            )
        return v

    @field_validator("messaging_rate_limit")
    @classmethod
    def validate_messaging_rate_limit(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("messaging_rate_limit must be > 0")
        return v

    @field_validator("messaging_rate_window")
    @classmethod
    def validate_messaging_rate_window(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("messaging_rate_window must be > 0")
        return float(v)

    @field_validator("web_fetch_allowed_schemes")
    @classmethod
    def validate_web_fetch_allowed_schemes(cls, v: str) -> str:
        schemes = [part.strip().lower() for part in v.split(",") if part.strip()]
        if not schemes:
            raise ValueError("web_fetch_allowed_schemes must list at least one scheme")
        for scheme in schemes:
            if not scheme.isascii() or not scheme.isalpha():
                raise ValueError(
                    f"Invalid URL scheme in web_fetch_allowed_schemes: {scheme!r}"
                )
        return ",".join(schemes)

    @field_validator("ollama_base_url")
    @classmethod
    def validate_ollama_base_url(cls, v: str) -> str:
        if v.rstrip("/").endswith("/v1"):
            raise ValueError(
                "OLLAMA_BASE_URL must be the Ollama root URL for native Anthropic "
                "messages, e.g. http://localhost:11434 (without /v1)."
            )
        return v

    @field_validator("model", "model_opus", "model_sonnet", "model_haiku")
    @classmethod
    def validate_model_format(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if "/" not in v:
            raise ValueError(
                f"Model must be prefixed with provider type. "
                f"Valid providers: {', '.join(SUPPORTED_PROVIDER_IDS)}. "
                f"Format: provider_type/model/name"
            )
        provider = v.split("/", 1)[0]
        if provider not in SUPPORTED_PROVIDER_IDS:
            supported = ", ".join(f"'{p}'" for p in SUPPORTED_PROVIDER_IDS)
            raise ValueError(f"Invalid provider: '{provider}'. Supported: {supported}")
        return v

    @model_validator(mode="after")
    def check_nvidia_nim_api_key(self) -> Settings:
        if (
            self.voice_note_enabled
            and self.whisper_device == "nvidia_nim"
            and not self.nvidia_nim_api_key.strip()
        ):
            raise ValueError(
                "NVIDIA_NIM_API_KEY is required when WHISPER_DEVICE is 'nvidia_nim'. "
                "Set it in your .env file."
            )
        return self

    @model_validator(mode="after")
    def prefer_dotenv_anthropic_auth_token(self) -> Settings:
        """Let explicit .env auth config override stale shell/client tokens."""
        dotenv_value = _env_file_override(self.model_config, "ANTHROPIC_AUTH_TOKEN")
        if dotenv_value is not None:
            self.anthropic_auth_token = dotenv_value
        return self

    def _resolve_secret_target_attr(self) -> str:
        """Return the Settings attribute name to populate from Secret Manager."""
        if self.provider_key_secret_target:
            target = self.provider_key_secret_target
            if not hasattr(self, target):
                raise ValueError(
                    f"PROVIDER_KEY_SECRET_TARGET={target!r} is not a known "
                    f"settings field."
                )
            return target

        descriptor = PROVIDER_CATALOG.get(self.provider_type)
        if descriptor is None or descriptor.credential_attr is None:
            raise ValueError(
                f"Cannot resolve provider key target for provider "
                f"{self.provider_type!r}; set PROVIDER_KEY_SECRET_TARGET "
                f"explicitly."
            )
        return descriptor.credential_attr

    @model_validator(mode="after")
    def resolve_provider_key_from_secret_manager(self) -> Settings:
        """Populate a provider key from GCP Secret Manager when configured.

        No-op when ``PROVIDER_KEY_SECRET_RESOURCE`` is unset, preserving the
        default disk/env-based behavior.
        """
        if not self.provider_key_secret_resource.strip():
            return self

        target = self._resolve_secret_target_attr()
        secret = fetch_secret(self.provider_key_secret_resource)
        if not secret.strip():
            raise SecretManagerError(
                f"Secret Manager resource "
                f"{self.provider_key_secret_resource!r} returned an empty value."
            )
        setattr(self, target, secret)
        return self

    def uses_process_anthropic_auth_token(self) -> bool:
        """Return whether proxy auth came from process env, not dotenv config."""
        if _env_file_override(self.model_config, "ANTHROPIC_AUTH_TOKEN") is not None:
            return False
        return bool(os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    @property
    def provider_type(self) -> str:
        """Extract provider type from the default model string."""
        return Settings.parse_provider_type(self.model)

    @property
    def model_name(self) -> str:
        """Extract the actual model name from the default model string."""
        return Settings.parse_model_name(self.model)

    def resolve_model(self, claude_model_name: str) -> str:
        """Resolve a Claude model name to the configured provider/model string.

        Classifies the incoming Claude model (opus/sonnet/haiku) and
        returns the model-specific override if configured, otherwise the fallback MODEL.
        """
        name_lower = claude_model_name.lower()
        if "opus" in name_lower and self.model_opus is not None:
            return self.model_opus
        if "haiku" in name_lower and self.model_haiku is not None:
            return self.model_haiku
        if "sonnet" in name_lower and self.model_sonnet is not None:
            return self.model_sonnet
        return self.model

    def configured_chat_model_refs(self) -> tuple[ConfiguredChatModelRef, ...]:
        """Return unique configured chat provider/model refs with source env keys."""
        candidates = (
            ("MODEL", self.model),
            ("MODEL_OPUS", self.model_opus),
            ("MODEL_SONNET", self.model_sonnet),
            ("MODEL_HAIKU", self.model_haiku),
        )
        sources_by_ref: dict[str, list[str]] = {}
        for source, model_ref in candidates:
            if model_ref is None:
                continue
            sources_by_ref.setdefault(model_ref, []).append(source)

        return tuple(
            ConfiguredChatModelRef(
                model_ref=model_ref,
                provider_id=Settings.parse_provider_type(model_ref),
                model_id=Settings.parse_model_name(model_ref),
                sources=tuple(sources),
            )
            for model_ref, sources in sources_by_ref.items()
        )

    def resolve_thinking(self, claude_model_name: str) -> bool:
        """Resolve whether thinking is enabled for an incoming Claude model name."""
        name_lower = claude_model_name.lower()
        if "opus" in name_lower and self.enable_opus_thinking is not None:
            return self.enable_opus_thinking
        if "haiku" in name_lower and self.enable_haiku_thinking is not None:
            return self.enable_haiku_thinking
        if "sonnet" in name_lower and self.enable_sonnet_thinking is not None:
            return self.enable_sonnet_thinking
        return self.enable_model_thinking

    def web_fetch_allowed_scheme_set(self) -> frozenset[str]:
        """Return normalized schemes allowed for web_fetch."""
        return frozenset(
            part.strip().lower()
            for part in self.web_fetch_allowed_schemes.split(",")
            if part.strip()
        )

    @staticmethod
    def parse_provider_type(model_string: str) -> str:
        """Extract provider type from any 'provider/model' string."""
        return model_string.split("/", 1)[0]

    @staticmethod
    def parse_model_name(model_string: str) -> str:
        """Extract model name from any 'provider/model' string."""
        return model_string.split("/", 1)[1]

    @property
    def image_route_parts(self) -> tuple[str, str] | None:
        """Return ``(provider_id, model_name)`` for ``IMAGE_ROUTE``, or None."""
        if not self.image_route:
            return None
        return (
            Settings.parse_provider_type(self.image_route),
            Settings.parse_model_name(self.image_route),
        )

    model_config = SettingsConfigDict(
        env_file=_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )


_settings_cache: Settings | None = None
_settings_env_mtimes: dict[str, float] = {}


def _any_env_file_changed() -> bool:
    """Check if any tracked env file has been modified since last check."""
    for path in _env_files():
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        key = str(path)
        prev = _settings_env_mtimes.get(key)
        if prev is None or prev != mtime:
            _settings_env_mtimes[key] = mtime
            return True
    return False


def clear_settings_cache() -> None:
    """Force re-creation of the Settings instance on next get_settings() call.

    Used by tests and admin routes that modify env files in-process and need
    the next call to re-read without waiting for a file-mtime change.
    """
    global _settings_cache
    _settings_cache = None


def get_settings() -> Settings:
    """Get settings instance, hot-reloaded when any env file changes."""
    global _settings_cache
    if _settings_cache is None or _any_env_file_changed():
        _settings_cache = Settings()
    return _settings_cache
