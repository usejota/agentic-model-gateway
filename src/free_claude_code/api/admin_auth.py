"""Admin UI authentication helpers.

Layered on top of the loopback check in :mod:`api.admin_routes`. The loopback
check is a no-op when the admin port is tunneled (e.g. GCP IAP TCP forwarding),
since the tunnel terminates on the client's loopback interface. When
``ADMIN_API_TOKEN`` is set, callers must additionally present a matching token.
"""

import secrets

from fastapi import HTTPException, Request

from free_claude_code.config.settings import get_settings


def _extract_admin_token(request: Request) -> str | None:
    """Return the presented admin token from request headers, if any."""
    header = request.headers.get("x-admin-token") or request.headers.get(
        "authorization"
    )
    if not header:
        return None
    token = header.strip()
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()
    return token


def require_admin_token(request: Request) -> None:
    """Require a matching admin token when ``ADMIN_API_TOKEN`` is configured.

    When the configured secret is empty, this is a no-op so loopback-only
    access (local dev) is preserved. When set, a matching ``X-Admin-Token`` or
    ``Authorization: Bearer ...`` header is required. Uses
    :func:`secrets.compare_digest` for constant-time comparison (CWE-208).
    """
    expected = get_settings().admin_api_token.strip()
    if not expected:
        return

    token = _extract_admin_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing admin token")

    if not secrets.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid admin token")
