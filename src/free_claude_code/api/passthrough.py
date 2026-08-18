"""Transparent OpenRouter passthrough: forwards requests verbatim.

Clients that speak native OpenRouter (model listing, reasoning fields, etc.)
point their base URL at ``/openrouter/api`` and get OpenRouter unchanged; the
proxy only injects the server-side API key.
"""

from collections.abc import Mapping

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from free_claude_code.application.errors import ApplicationUnavailableError
from free_claude_code.config.settings import Settings

from .dependencies import get_settings, require_proxy_auth

OPENROUTER_BASE_URL = "https://openrouter.ai/api"

_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_SKIP_REQUEST_HEADERS = _HOP_BY_HOP_HEADERS | frozenset(
    {
        "host",
        "authorization",
        "content-length",
        "cookie",
        "x-api-key",
        "anthropic-auth-token",
    }
)
_SKIP_RESPONSE_HEADERS = _HOP_BY_HOP_HEADERS | frozenset({"content-length"})

router = APIRouter()


def _filter_headers(headers: Mapping[str, str], skip: frozenset[str]) -> dict[str, str]:
    deny = set(skip)
    connection = headers.get("connection")
    if connection:
        deny.update(
            token.strip().lower() for token in connection.split(",") if token.strip()
        )
    return {key: value for key, value in headers.items() if key.lower() not in deny}


def _upstream_request(
    client: httpx.AsyncClient,
    request: Request,
    path: str,
    api_key: str,
    body: bytes,
) -> httpx.Request:
    headers = _filter_headers(request.headers, _SKIP_REQUEST_HEADERS)
    headers["authorization"] = f"Bearer {api_key}"
    # Body streams raw; without this httpx would ask for gzip the client
    # never requested and the encoding header/bytes would disagree.
    headers.setdefault("accept-encoding", "identity")
    return client.build_request(
        request.method,
        f"{OPENROUTER_BASE_URL}/{path}",
        params=request.query_params,
        headers=headers,
        content=body,
    )


@router.api_route(
    "/openrouter/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def openrouter_passthrough(
    request: Request,
    path: str,
    settings: Settings = Depends(get_settings),
    _auth=Depends(require_proxy_auth),
):
    """Proxy any OpenRouter API call verbatim, injecting the server API key."""
    api_key = settings.open_router_api_key.strip()
    if not api_key:
        raise ApplicationUnavailableError(
            "OPENROUTER_API_KEY is not set. Add it to your .env file. "
            "Get a key at https://openrouter.ai/keys"
        )
    body = await request.body()
    proxy = settings.open_router_proxy.strip() or None
    client = httpx.AsyncClient(
        proxy=proxy,
        timeout=httpx.Timeout(
            settings.http_read_timeout,
            connect=settings.http_connect_timeout,
            read=settings.http_read_timeout,
            write=settings.http_write_timeout,
        ),
    )

    async def close() -> None:
        await client.aclose()

    try:
        upstream = await client.send(
            _upstream_request(client, request, path, api_key, body),
            stream=True,
        )
    except BaseException:
        await client.aclose()
        raise

    async def cleanup() -> None:
        await upstream.aclose()
        await close()

    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=_filter_headers(upstream.headers, _SKIP_RESPONSE_HEADERS),
        background=BackgroundTask(cleanup),
    )
