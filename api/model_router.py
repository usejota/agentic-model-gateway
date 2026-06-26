"""Model routing for Claude-compatible requests."""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from config.provider_catalog import PROVIDER_MODEL_CONTEXT
from config.provider_ids import SUPPORTED_PROVIDER_IDS
from config.settings import Settings

from .gateway_model_ids import decode_gateway_model_id
from .models.anthropic import MessagesRequest, TokenCountRequest

ONE_M_SUFFIX = "[1m]"


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    original_model: str
    provider_id: str
    provider_model: str
    provider_model_ref: str
    thinking_enabled: bool
    one_m_context: bool = False


@dataclass(frozen=True, slots=True)
class RoutedMessagesRequest:
    request: MessagesRequest
    resolved: ResolvedModel


@dataclass(frozen=True, slots=True)
class RoutedTokenCountRequest:
    request: TokenCountRequest
    resolved: ResolvedModel


class ModelRouter:
    """Resolve incoming Claude model names to configured provider/model pairs."""

    def __init__(self, settings: Settings):
        self._settings = settings

    def resolve(self, claude_model_name: str) -> ResolvedModel:
        (
            direct_provider_id,
            direct_provider_model,
            force_thinking_enabled,
            one_m_context,
        ) = self._direct_provider_model(claude_model_name)
        if direct_provider_id is not None and direct_provider_model is not None:
            provider_model = self._maybe_append_one_m_suffix(
                direct_provider_id, direct_provider_model, force=one_m_context
            )
            thinking_enabled = (
                force_thinking_enabled
                if force_thinking_enabled is not None
                else self._settings.resolve_thinking(provider_model)
            )
            logger.debug(
                "MODEL DIRECT: '{}' -> provider='{}' model='{}' thinking={} one_m={}",
                claude_model_name,
                direct_provider_id,
                provider_model,
                thinking_enabled,
                one_m_context,
            )
            return ResolvedModel(
                original_model=claude_model_name,
                provider_id=direct_provider_id,
                provider_model=provider_model,
                provider_model_ref=claude_model_name,
                thinking_enabled=thinking_enabled,
                one_m_context=provider_model.endswith(ONE_M_SUFFIX),
            )

        provider_model_ref = self._settings.resolve_model(claude_model_name)
        thinking_enabled = self._settings.resolve_thinking(claude_model_name)
        provider_id = Settings.parse_provider_type(provider_model_ref)
        provider_model = Settings.parse_model_name(provider_model_ref)
        provider_model = self._maybe_append_one_m_suffix(provider_id, provider_model)
        if provider_model != claude_model_name:
            logger.debug(
                "MODEL MAPPING: '{}' -> '{}'", claude_model_name, provider_model
            )
        return ResolvedModel(
            original_model=claude_model_name,
            provider_id=provider_id,
            provider_model=provider_model,
            provider_model_ref=provider_model_ref,
            thinking_enabled=thinking_enabled,
            one_m_context=provider_model.endswith(ONE_M_SUFFIX),
        )

    def _direct_provider_model(
        self, model_name: str
    ) -> tuple[str | None, str | None, bool | None, bool]:
        decoded = decode_gateway_model_id(model_name)
        if decoded is not None:
            if decoded.provider_id not in SUPPORTED_PROVIDER_IDS:
                return None, None, None, False
            return (
                decoded.provider_id,
                decoded.provider_model,
                decoded.force_thinking_enabled,
                decoded.one_m_context,
            )

        provider_id, separator, provider_model = model_name.partition("/")
        if not separator:
            return None, None, None, False
        if provider_id not in SUPPORTED_PROVIDER_IDS:
            return None, None, None, False
        if not provider_model:
            return None, None, None, False
        return provider_id, provider_model, None, False

    @staticmethod
    def _maybe_append_one_m_suffix(
        provider_id: str, provider_model: str, *, force: bool = False
    ) -> str:
        """Append the ``[1m]`` suffix when the catalog marks the model as 1M-capable.

        The suffix must be on the model string Claude Code sends (its
        ``has1mContext()`` parses the *request* model field) AND on the model
        string forwarded to upstream (OpenRouter routes by suffix). Catalog
        miss + no force = unchanged (200K default). Idempotent when already
        present.
        """
        if provider_model.endswith(ONE_M_SUFFIX):
            return provider_model
        spec = PROVIDER_MODEL_CONTEXT.get((provider_id, provider_model))
        if force or (spec is not None and spec.supports_1m):
            return f"{provider_model}{ONE_M_SUFFIX}"
        return provider_model

    def resolve_messages_request(
        self, request: MessagesRequest
    ) -> RoutedMessagesRequest:
        """Return an internal routed request context."""
        resolved = self.resolve(request.model)
        routed = request.model_copy(deep=True)
        routed.model = resolved.provider_model
        return RoutedMessagesRequest(request=routed, resolved=resolved)

    def resolve_token_count_request(
        self, request: TokenCountRequest
    ) -> RoutedTokenCountRequest:
        """Return an internal token-count request context."""
        resolved = self.resolve(request.model)
        routed = request.model_copy(
            update={"model": resolved.provider_model}, deep=True
        )
        return RoutedTokenCountRequest(request=routed, resolved=resolved)
