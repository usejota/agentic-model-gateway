"""Product smoke for the IMAGE_ROUTE feature (image reroute to a vision model).

Validates end-to-end that when ``IMAGE_ROUTE`` is set and the request contains
image content, the gateway reroutes just that turn to the configured vision
model — without the user having to switch their primary MODEL.

The smoke is opt-in: it requires ``IMAGE_ROUTE`` and the matching provider
credentials to be configured. Otherwise the test skips with a ``missing_env``
marker so the rest of the live suite is unaffected.

This is a live smoke — it boots a server with a text-only primary and an
IMAGE_ROUTE pointing at a vision-capable model, then sends a request with a
tiny PNG and asserts the response came back successfully (vision model
processed the image, not 400ed).
"""

from __future__ import annotations

import os

import pytest

from smoke.lib.config import SmokeConfig
from smoke.lib.e2e import (
    ConversationDriver,
    ProviderMatrixDriver,
    SmokeServerDriver,
    assert_product_stream,
)

pytestmark = [pytest.mark.live, pytest.mark.smoke_target("api")]


# A 1x1 red pixel PNG, base64-encoded. Small enough to keep the smoke fast.
_TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/"
    "PchI7wAAAABJRU5ErkJggg=="
)


def _has_image_route() -> str | None:
    return os.environ.get("IMAGE_ROUTE") or None


@pytest.mark.parametrize(
    "primary_provider",
    ["deepseek"],
)
def test_image_reroutes_text_only_primary_to_vision(
    smoke_config: SmokeConfig,
    primary_provider: str,
) -> None:
    """Text-only primary + IMAGE_ROUTE + image content → vision model answers.

    Skipped when ``IMAGE_ROUTE`` is not configured or the vision provider
    credentials are missing (so the suite stays green on a default install).
    """
    image_route = _has_image_route()
    if image_route is None:
        pytest.skip("missing_env: set IMAGE_ROUTE for image-reroute smoke")
    # The primary needs to be text-only so rerouting is meaningful. Pick the
    # first configured smoke model for the requested provider.
    matrix = ProviderMatrixDriver(smoke_config)
    matching = [
        m for m in matrix.provider_smoke_models() if m.provider == primary_provider
    ]
    if not matching:
        pytest.skip(f"missing_env: provider {primary_provider} not configured")
    primary_full = matching[0]

    with SmokeServerDriver(
        smoke_config,
        name="product-image-reroute",
        env_overrides={
            "MODEL": primary_full.full_model,
            "IMAGE_ROUTE": image_route,
            "MESSAGING_PLATFORM": "none",
        },
    ).run() as server:
        conv = ConversationDriver(server, smoke_config)
        payload_text = "Describe what you see in one short sentence."
        # Send a user turn with both text and an image block. The gateway
        # should reroute to IMAGE_ROUTE and the response should describe the
        # image (not 400 with "image not supported").
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": payload_text},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": _TINY_PNG_BASE64,
                        },
                    },
                ],
            }
        ]
        turn = conv.stream(
            {"model": "claude-sonnet-4", "max_tokens": 128, "messages": messages}
        )

    assert_product_stream(turn.events)
    # The vision model produced text (not a refusal / 400). We don't assert on
    # image-content fidelity — a 1x1 pixel has no semantic content; we just
    # assert the response body has text.
    assert turn.text.strip()
