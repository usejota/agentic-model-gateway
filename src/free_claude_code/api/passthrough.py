"""Transparent OpenRouter passthrough: forwards requests verbatim.

Clients that speak native OpenRouter (model listing, reasoning fields, etc.)
point their base URL at ``/openrouter/api`` and get OpenRouter unchanged; the
proxy only injects the server-side API key.
"""

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from free_claude_code.config.settings import Settings

from .dependencies import get_settings, require_proxy_auth

OPENROUTER_BASE_URL = "https://openrouter.ai/api"

# Hop-by-hop and connection-level headers never forwarded either direction.
_SKIP_REQUEST_HEADERS = frozenset(
    {"host", "authorization", "content-length", "connection"}
)
_SKIP_RESPONSE_HEADERS = frozenset(
    {"content-length", "transfer-encoding", "connection"}
)

router = APIRouter()


def _upstream_request(
    client: httpx.AsyncClient,
    request: Request,
    path: str,
    api_key: str,
    body: bytes,
) -> httpx.Request:
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _SKIP_REQUEST_HEADERS
    }
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
    body = await request.body()
    proxy = settings.open_router_proxy.strip() or None
    client = httpx.AsyncClient(proxy=proxy, timeout=httpx.Timeout(600.0))

    async def close() -> None:
        await client.aclose()

    try:
        upstream = await client.send(
            _upstream_request(
                client, request, path, settings.open_router_api_key, body
            ),
            stream=True,
        )
    except BaseException:
        await client.aclose()
        raise

    async def cleanup() -> None:
        await upstream.aclose()
        await close()

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _SKIP_RESPONSE_HEADERS
    }
    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(cleanup),
    )
