"""Model routing for Claude-compatible requests."""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from config.provider_ids import SUPPORTED_PROVIDER_IDS
from config.settings import Settings

from .gateway_model_ids import ONE_M_SUFFIX, decode_gateway_model_id
from .models.anthropic import MessagesRequest, TokenCountRequest


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
        # Claude Code's ``has1mContext()`` checks the model name it sent for a
        # ``[1m]`` suffix. When the picker exposes the Fable override as
        # ``claude-fable-5[1m]`` (see routes._append_alias_1m_if_override_supports),
        # the request arrives here carrying that suffix. Strip it before
        # resolve_model so the alias maps to MODEL_FABLE like the bare
        # ``claude-fable-5`` does, and propagate one_m_context=True so the
        # response-side metadata reflects the user's pick.
        one_m_from_alias = claude_model_name.endswith(ONE_M_SUFFIX)
        alias_for_resolution = (
            claude_model_name[: -len(ONE_M_SUFFIX)]
            if one_m_from_alias
            else claude_model_name
        )

        (
            direct_provider_id,
            direct_provider_model,
            force_thinking_enabled,
            one_m_context,
        ) = self._direct_provider_model(claude_model_name)
        if direct_provider_id is not None and direct_provider_model is not None:
            thinking_enabled = (
                force_thinking_enabled
                if force_thinking_enabled is not None
                else self._settings.resolve_thinking(direct_provider_model)
            )
            logger.debug(
                "MODEL DIRECT: '{}' -> provider='{}' model='{}' thinking={} one_m={}",
                claude_model_name,
                direct_provider_id,
                direct_provider_model,
                thinking_enabled,
                one_m_context,
            )
            return ResolvedModel(
                original_model=claude_model_name,
                provider_id=direct_provider_id,
                provider_model=direct_provider_model,
                provider_model_ref=claude_model_name,
                thinking_enabled=thinking_enabled,
                one_m_context=one_m_context,
            )

        provider_model_ref = self._settings.resolve_model(alias_for_resolution)
        thinking_enabled = self._settings.resolve_thinking(alias_for_resolution)
        provider_id = Settings.parse_provider_type(provider_model_ref)
        provider_model = Settings.parse_model_name(provider_model_ref)
        if provider_model != alias_for_resolution:
            logger.debug(
                "MODEL MAPPING: '{}' -> '{}'", claude_model_name, provider_model
            )
        return ResolvedModel(
            original_model=claude_model_name,
            provider_id=provider_id,
            provider_model=provider_model,
            provider_model_ref=provider_model_ref,
            thinking_enabled=thinking_enabled,
            one_m_context=one_m_from_alias or one_m_context,
        )

    def _direct_provider_model(
        self, model_name: str
    ) -> tuple[str | None, str | None, bool | None, bool]:
        decoded = decode_gateway_model_id(model_name)
        if decoded is not None:
            if decoded.provider_id not in SUPPORTED_PROVIDER_IDS:
                return None, None, None, False
            # Gateway decoder already stripped any ``[1m]`` suffix into one_m_context;
            # provider_model is clean for upstream.
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
        # Raw ``provider/model`` typed by a user may carry the ``[1m]`` 1M signal.
        # ``[1m]`` is never part of a real upstream model id (no provider uses
        # brackets), so a trailing one is unambiguously the Claude Code signal:
        # strip it before forwarding upstream (the OpenRouter-400 regression guard).
        one_m_context = provider_model.endswith(ONE_M_SUFFIX)
        if one_m_context:
            provider_model = provider_model[: -len(ONE_M_SUFFIX)]
            if not provider_model:
                return None, None, None, False
        return provider_id, provider_model, None, one_m_context

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
