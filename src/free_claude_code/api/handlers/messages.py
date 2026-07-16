"""Claude Messages API product flow."""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace

from fastapi.responses import JSONResponse, Response
from loguru import logger

from free_claude_code.api.detection import is_safety_classifier_request
from free_claude_code.api.optimization_handlers import try_optimizations
from free_claude_code.api.request_errors import (
    http_status_for_unexpected_api_exception,
    log_unexpected_api_exception,
    require_non_empty_messages,
    unexpected_http_exception,
)
from free_claude_code.api.request_ids import new_request_id
from free_claude_code.api.response_streams import (
    EmptyStreamError,
    anthropic_sse_streaming_response,
    terminal_execution_error_response,
    trace_terminal_execution_error,
)
from free_claude_code.api.web_tools.egress import (
    WebFetchEgressPolicy,
    web_fetch_allowed_scheme_set,
)
from free_claude_code.api.web_tools.request import (
    is_web_server_tool_request,
    unsupported_server_tool_error,
)
from free_claude_code.api.web_tools.streaming import stream_web_server_tool_response
from free_claude_code.application.errors import ApplicationError, InvalidRequestError
from free_claude_code.application.execution import ProviderExecutor, TokenCounter
from free_claude_code.application.ports import ProviderResolver
from free_claude_code.application.routing import ModelRouter, RoutedMessagesRequest
from free_claude_code.config.settings import Settings
from free_claude_code.core.anthropic import (
    MessagesRequest,
    aggregate_anthropic_sse_to_message,
    anthropic_error_payload,
    anthropic_error_type_for_failure,
    anthropic_failure_payload,
    anthropic_status_for_error_type,
    get_token_count,
)
from free_claude_code.core.anthropic.image_detection import (
    has_image_in_last_user_turn,
    has_images,
    strip_to_placeholders,
)
from free_claude_code.core.diagnostics import safe_exception_message
from free_claude_code.core.failures import (
    ExecutionFailure,
    FailureKind,
    find_execution_failure,
)
from free_claude_code.core.trace import trace_event


@dataclass(frozen=True)
class _MessagesStreamResult:
    body: AsyncIterator[str]


@dataclass(frozen=True)
class _MessagesCompleteResult:
    response: object


_MessagesResult = _MessagesStreamResult | _MessagesCompleteResult
MessageIntercept = Callable[[RoutedMessagesRequest], _MessagesResult | None]


def _strip_old_images_in_request(
    request: MessagesRequest,
) -> tuple[MessagesRequest, int]:
    """Return a copy of ``request`` with all image blocks replaced by placeholders.

    Used when the service decides NOT to reroute to ``IMAGE_ROUTE`` (the current
    user turn is text-only) but older turns still carry image content. Text-only
    primary models would 400 on raw image blocks; stripping on a deep-copied
    request keeps the session on a text-only model and lets the user continue.

    The roundtrip is via ``model_dump`` + ``model_validate`` so the pure-dict
    :func:`strip_to_placeholders` utility can do the work without knowing the
    Pydantic ``Message`` schema. The ``ImageCache`` is discarded — this is a
    one-way strip; the vision reroute builds a fresh request when the user
    actually needs to see the image. Returns the (possibly unchanged) request
    and the count of image blocks replaced (0 means no images, unchanged).
    """
    data = request.model_dump()
    messages = data.get("messages") or []
    if not has_images(messages):
        return request, 0
    stripped, cache = strip_to_placeholders(messages)
    if len(cache) == 0:
        return request, 0
    data["messages"] = stripped
    new_request = MessagesRequest.model_validate(data)
    return new_request, len(cache)


class MessagesHandler:
    """Handle Anthropic-compatible Messages requests."""

    def __init__(
        self,
        settings: Settings,
        provider_resolver: ProviderResolver,
        *,
        model_router: ModelRouter | None = None,
        token_counter: TokenCounter = get_token_count,
        provider_executor: ProviderExecutor | None = None,
        generation_id: int | None = None,
    ) -> None:
        self._settings = settings
        self._model_router = model_router or ModelRouter(settings)
        self._token_counter = token_counter
        self._provider_executor = provider_executor or ProviderExecutor(
            provider_resolver,
            token_counter=token_counter,
            generation_id=generation_id,
            log_raw_payloads=settings.log_raw_api_payloads,
        )
        self._message_intercepts: tuple[MessageIntercept, ...] = (
            self._intercept_web_server_tool,
            self._intercept_local_optimization,
        )

    async def create(
        self, request_data: MessagesRequest, *, request_id: str | None = None
    ) -> object:
        """Create an Anthropic-compatible message response."""
        request_id = request_id or new_request_id()
        try:
            require_non_empty_messages(request_data.messages)
            routed = self._model_router.resolve_messages_request(request_data)
            routed = self._apply_message_routing_policies(routed)
            self._reject_unsupported_server_tools(routed)

            result = self._run_message_intercepts(routed)
            if result is None:
                logger.debug("No optimization matched, routing to provider")
                fallbacks = self._fallback_candidates(routed.request)
                if fallbacks:
                    body = self._stream_with_fallback(
                        routed, fallbacks, request_id=request_id
                    )
                else:
                    body = self._provider_executor.stream(
                        routed,
                        wire_api="messages",
                        raw_log_label="FULL_PAYLOAD",
                        raw_log_payload=routed.request.model_dump(),
                        request_id=request_id,
                    )
                result = _MessagesStreamResult(body)
            return await self._to_public_response(
                result,
                stream=request_data.stream,
                request_id=request_id,
            )
        except ApplicationError:
            raise
        except ExecutionFailure as exc:
            return self._execution_failure_response(exc, request_id=request_id)
        except Exception as exc:
            failure = find_execution_failure(exc)
            if failure is not None:
                return self._execution_failure_response(failure, request_id=request_id)
            raise unexpected_http_exception(
                self._settings, exc, context="CREATE_MESSAGE_ERROR"
            ) from exc

    async def _to_public_response(
        self,
        result: _MessagesResult,
        *,
        stream: bool,
        request_id: str,
    ) -> object:
        if isinstance(result, _MessagesCompleteResult):
            return result.response
        if not stream:
            # Non-streaming clients (e.g. Claude Code utility calls) need a
            # complete JSON Message; the internal pipeline is always SSE, so
            # serving that raw here breaks the client SDK's response parse.
            try:
                message, error = await aggregate_anthropic_sse_to_message(result.body)
            except GeneratorExit:
                raise
            except asyncio.CancelledError:
                raise
            except ExecutionFailure as exc:
                return self._execution_failure_response(exc, request_id=request_id)
            except BaseExceptionGroup as exc:
                failure = find_execution_failure(exc)
                if failure is not None:
                    return self._execution_failure_response(
                        failure, request_id=request_id
                    )
                return self._unexpected_execution_error_response(
                    exc,
                    request_id=request_id,
                    context="CREATE_MESSAGE_NON_STREAM_ERROR",
                )
            except Exception as exc:
                return self._unexpected_execution_error_response(
                    exc,
                    request_id=request_id,
                    context="CREATE_MESSAGE_NON_STREAM_ERROR",
                )
            if error is not None:
                error_type, message_text = _stream_error_fields(error)
                status_code = anthropic_status_for_error_type(error_type)
                trace_terminal_execution_error(
                    wire_api="messages",
                    request_id=request_id,
                    status_code=status_code,
                    error_type=error_type,
                )
                return terminal_execution_error_response(
                    status_code=status_code,
                    content=anthropic_error_payload(
                        error_type=error_type,
                        message=message_text,
                        request_id=request_id,
                    ),
                )
            return JSONResponse(content=message)
        return await anthropic_sse_streaming_response(
            result.body,
            pre_start_error_response=lambda exc: self._pre_start_error_response(
                exc, request_id=request_id
            ),
            request_id=request_id,
        )

    def _pre_start_error_response(
        self, exc: BaseException, *, request_id: str
    ) -> Response:
        failure = find_execution_failure(exc)
        if failure is not None:
            return self._execution_failure_response(failure, request_id=request_id)
        context = (
            "CREATE_MESSAGE_EMPTY_STREAM"
            if isinstance(exc, EmptyStreamError)
            else "CREATE_MESSAGE_STREAM_START_ERROR"
        )
        return self._unexpected_execution_error_response(
            exc,
            request_id=request_id,
            context=context,
        )

    def _execution_failure_response(
        self, failure: ExecutionFailure, *, request_id: str
    ) -> JSONResponse:
        error_type = anthropic_error_type_for_failure(failure)
        trace_terminal_execution_error(
            wire_api="messages",
            request_id=request_id,
            status_code=failure.status_code,
            error_type=error_type,
            error=failure,
        )
        return terminal_execution_error_response(
            status_code=failure.status_code,
            content=anthropic_failure_payload(failure, request_id=request_id),
        )

    def _unexpected_execution_error_response(
        self,
        exc: BaseException,
        *,
        request_id: str,
        context: str,
    ) -> JSONResponse:
        log_unexpected_api_exception(
            self._settings,
            exc,
            context=context,
            request_id=request_id,
        )
        status_code = http_status_for_unexpected_api_exception(exc)
        trace_terminal_execution_error(
            wire_api="messages",
            request_id=request_id,
            status_code=status_code,
            error_type="api_error",
            error=exc,
        )
        return terminal_execution_error_response(
            status_code=status_code,
            content=anthropic_error_payload(
                error_type="api_error",
                message=safe_exception_message(exc),
                request_id=request_id,
            ),
        )

    def _reject_unsupported_server_tools(self, routed: RoutedMessagesRequest) -> None:
        tool_err = unsupported_server_tool_error(
            routed.request,
            web_tools_enabled=self._settings.enable_web_server_tools,
        )
        if tool_err is not None:
            raise InvalidRequestError(tool_err)

    def _apply_message_routing_policies(
        self, routed: RoutedMessagesRequest
    ) -> RoutedMessagesRequest:
        routed = self._maybe_reroute_for_classifier(routed)
        routed = self._maybe_reroute_for_images(routed)
        if not is_safety_classifier_request(routed.request):
            return routed
        changed = routed.resolved.thinking_enabled
        trace_event(
            stage="routing",
            event="free_claude_code.api.optimization.safety_classifier_no_thinking",
            source="api",
            model=routed.request.model,
            changed=changed,
        )
        if not changed:
            return routed
        return RoutedMessagesRequest(
            request=routed.request,
            resolved=replace(routed.resolved, thinking_enabled=False),
        )

    def _fallback_candidates(
        self, request_data: MessagesRequest
    ) -> list[RoutedMessagesRequest]:
        """Resolve ``FALLBACK_MODELS`` into routed requests for the same payload.

        Each fallback ref is resolved through the same router as the primary, so a
        fallback can target any provider/model. Returns ``[]`` when unset (no-op).
        When the request carries images and ``IMAGE_ROUTE`` is set, fallbacks that
        target a different provider are dropped — they would 400 on the image, so
        there is no point preflighting them.
        """
        candidates: list[RoutedMessagesRequest] = []
        image_route_target = self._settings.image_route_parts
        for ref in self._settings.fallback_models:
            resolved = self._model_router.resolve(ref)
            routed = request_data.model_copy(deep=True)
            routed.model = resolved.provider_model
            if (
                has_images(routed.messages)
                and image_route_target is not None
                and resolved.provider_id != image_route_target[0]
            ):
                continue
            candidates.append(RoutedMessagesRequest(request=routed, resolved=resolved))
        return candidates

    async def _stream_with_fallback(
        self,
        primary: RoutedMessagesRequest,
        fallbacks: list[RoutedMessagesRequest],
        *,
        request_id: str,
    ) -> AsyncIterator[str]:
        """Stream the primary; on a pre-output overload, fall back across models.

        Each candidate is opened (preflight is synchronous, so a preflight
        overload raises here) and its first event awaited. If a candidate raises
        an ``OVERLOADED`` failure *before* producing any output, the next
        candidate is tried. Once any candidate yields its first event we commit
        to it and pass the rest through unchanged — never switching mid-stream.
        If every candidate overloads, the last error is re-raised so the
        fail-closed contract (e.g. auto-mode classifier) is preserved.
        """
        candidates = [primary, *fallbacks]
        last_error: BaseException | None = None
        for index, cand in enumerate(candidates):
            is_last = index == len(candidates) - 1
            try:
                stream = self._provider_executor.stream(
                    cand,
                    wire_api="messages",
                    raw_log_label="FULL_PAYLOAD",
                    raw_log_payload=cand.request.model_dump(),
                    request_id=request_id,
                )
            except ExecutionFailure as exc:
                if is_last or exc.kind != FailureKind.OVERLOADED:
                    raise
                last_error = exc
                self._trace_fallback(cand, index, request_id, "preflight_overload")
                continue

            iterator = stream.__aiter__()
            try:
                first = await iterator.__anext__()
            except StopAsyncIteration:
                return
            except GeneratorExit, asyncio.CancelledError:
                raise
            except BaseException as exc:
                failure = find_execution_failure(exc)
                if is_last or failure is None or failure.kind != FailureKind.OVERLOADED:
                    raise
                last_error = exc
                self._trace_fallback(cand, index, request_id, "stream_overload")
                continue

            yield first
            async for chunk in iterator:
                yield chunk
            return

        if last_error is not None:
            raise last_error

    def _trace_fallback(
        self,
        routed: RoutedMessagesRequest,
        index: int,
        request_id: str,
        reason: str,
    ) -> None:
        logger.warning(
            "Model '{}' overloaded before output ({}); trying next candidate",
            routed.request.model,
            reason,
        )
        trace_event(
            stage="routing",
            event="free_claude_code.api.route.fallback",
            source="api",
            provider_id=routed.resolved.provider_id,
            provider_model=routed.resolved.provider_model,
            gateway_model=routed.request.model,
            fallback_index=index,
            reason=reason,
            request_id=request_id,
        )

    def _maybe_reroute_for_images(
        self, routed: RoutedMessagesRequest
    ) -> RoutedMessagesRequest:
        """Handle ``IMAGE_ROUTE`` for image content, scoped to the current turn.

        Rerouting is scoped to the last user turn, not the full history: Claude
        Code re-sends the entire conversation every turn, so a single image
        pasted in a previous turn would otherwise lock every subsequent turn
        (including text-only follow-ups) to the vision model — wasted tokens and
        a source of truncated tool-heavy streams on some vision providers.

        In order:
        - ``IMAGE_ROUTE`` unset → no-op.
        - Last user turn has an image → reroute to the vision model.
        - Last user turn is text-only but history has images → strip old images
          to ``[Image #N]`` placeholders so the text-only primary doesn't 400;
          no model swap.
        - No images anywhere → no-op.
        """
        image_route = self._settings.image_route_parts
        if image_route is None:
            return routed

        if has_image_in_last_user_turn(routed.request.messages):
            target_provider_id, target_model = image_route
            if (
                routed.resolved.provider_id == target_provider_id
                and routed.resolved.provider_model == target_model
            ):
                return routed

            resolved = self._model_router.resolve(
                f"{target_provider_id}/{target_model}"
            )
            new_request = routed.request.model_copy(deep=True)
            new_request.model = resolved.provider_model
            logger.info(
                "Image reroute: provider={} model={} (from provider={} model={})",
                resolved.provider_id,
                resolved.provider_model,
                routed.resolved.provider_id,
                routed.resolved.provider_model,
            )
            trace_event(
                stage="routing",
                event="free_claude_code.api.route.image_reroute",
                source="api",
                provider_id=resolved.provider_id,
                provider_model=resolved.provider_model,
                original_provider_id=routed.resolved.provider_id,
                original_provider_model=routed.resolved.provider_model,
                gateway_model=routed.request.model,
            )
            return RoutedMessagesRequest(request=new_request, resolved=resolved)

        if not has_images(routed.request.messages):
            return routed

        # Last user turn is text-only but older turns still have images. Strip
        # the old images to placeholders so the text-only primary can serve this
        # turn without a 400 — the user isn't asking about the image this turn.
        stripped_request, stripped_count = _strip_old_images_in_request(routed.request)
        if stripped_count == 0:
            return routed
        logger.info(
            "Image strip (no reroute): stripped={} (current turn text-only, "
            "history had images)",
            stripped_count,
        )
        trace_event(
            stage="routing",
            event="free_claude_code.api.route.image_strip",
            source="api",
            stripped_count=stripped_count,
            gateway_model=routed.request.model,
        )
        return RoutedMessagesRequest(request=stripped_request, resolved=routed.resolved)

    def _maybe_reroute_for_classifier(
        self, routed: RoutedMessagesRequest
    ) -> RoutedMessagesRequest:
        """Swap the primary provider to ``CLASSIFIER_ROUTE`` for classifier requests.

        No-op when ``CLASSIFIER_ROUTE`` is unset or the request doesn't match the
        safety classifier signature. When triggered, the request is deep-copied
        and re-pointed to the CLASSIFIER_ROUTE provider/model so the rest of the
        pipeline (preflight, dispatch, fallback) sees the new target.
        """
        classifier_route = self._settings.classifier_route_parts
        if classifier_route is None:
            return routed
        if not is_safety_classifier_request(routed.request):
            return routed

        target_provider_id, target_model = classifier_route
        if (
            routed.resolved.provider_id == target_provider_id
            and routed.resolved.provider_model == target_model
        ):
            return routed

        resolved = self._model_router.resolve(f"{target_provider_id}/{target_model}")
        new_request = routed.request.model_copy(deep=True)
        new_request.model = resolved.provider_model
        logger.info(
            "Classifier reroute: provider={} model={} (from provider={} model={})",
            resolved.provider_id,
            resolved.provider_model,
            routed.resolved.provider_id,
            routed.resolved.provider_model,
        )
        trace_event(
            stage="routing",
            event="free_claude_code.api.route.classifier_reroute",
            source="api",
            provider_id=resolved.provider_id,
            provider_model=resolved.provider_model,
            original_provider_id=routed.resolved.provider_id,
            original_provider_model=routed.resolved.provider_model,
            gateway_model=routed.request.model,
        )
        return RoutedMessagesRequest(request=new_request, resolved=resolved)

    def _run_message_intercepts(
        self, routed: RoutedMessagesRequest
    ) -> _MessagesResult | None:
        for intercept in self._message_intercepts:
            result = intercept(routed)
            if result is not None:
                return result
        return None

    def _intercept_web_server_tool(
        self, routed: RoutedMessagesRequest
    ) -> _MessagesResult | None:
        if not self._settings.enable_web_server_tools:
            return None
        if not is_web_server_tool_request(routed.request):
            return None

        input_tokens = self._token_counter(
            routed.request.messages, routed.request.system, routed.request.tools
        )
        trace_event(
            stage="routing",
            event="free_claude_code.api.optimization.web_server_tool",
            source="api",
            model=routed.request.model,
        )
        egress = WebFetchEgressPolicy(
            allow_private_network_targets=self._settings.web_fetch_allow_private_networks,
            allowed_schemes=web_fetch_allowed_scheme_set(
                self._settings.web_fetch_allowed_schemes
            ),
        )
        return _MessagesStreamResult(
            stream_web_server_tool_response(
                routed.request,
                input_tokens=input_tokens,
                web_fetch_egress=egress,
                verbose_client_errors=self._settings.log_api_error_tracebacks,
            ),
        )

    def _intercept_local_optimization(
        self, routed: RoutedMessagesRequest
    ) -> _MessagesResult | None:
        optimized = try_optimizations(routed.request, self._settings)
        if optimized is None:
            return None
        trace_event(
            stage="routing",
            event="free_claude_code.api.optimization.short_circuit",
            source="api",
            model=routed.request.model,
        )
        return _MessagesCompleteResult(optimized)


def _stream_error_fields(error: dict[str, object]) -> tuple[str, str]:
    raw_type = error.get("type")
    error_type = (
        raw_type.strip()
        if isinstance(raw_type, str) and raw_type.strip()
        else "api_error"
    )
    raw_message = error.get("message")
    message = (
        raw_message.strip()
        if isinstance(raw_message, str) and raw_message.strip()
        else "Provider request failed unexpectedly."
    )
    return error_type, message
