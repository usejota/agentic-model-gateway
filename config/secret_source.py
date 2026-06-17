"""Optional GCP Secret Manager source for provider credentials.

This module lets the proxy read a provider API key directly from Google Cloud
Secret Manager at runtime instead of persisting it in a plaintext ``.env`` file
on disk. The dependency is optional: ``google-cloud-secret-manager`` is imported
lazily inside :func:`fetch_secret`, so the base install stays unchanged and the
package is only required when the feature is actually used.
"""

from __future__ import annotations


class SecretManagerError(RuntimeError):
    """Raised when a Secret Manager secret cannot be resolved."""


def fetch_secret(resource_name: str) -> str:
    """Return the secret payload for a Secret Manager version resource.

    ``resource_name`` is a fully-qualified version resource, e.g.
    ``projects/<project>/secrets/<name>/versions/latest``.

    Raises :class:`SecretManagerError` when the optional
    ``google-cloud-secret-manager`` package is missing or the fetch fails.
    """
    resource = resource_name.strip()
    if not resource:
        raise SecretManagerError("Secret Manager resource name must not be empty")

    try:
        from google.cloud import secretmanager
    except ImportError as exc:
        raise SecretManagerError(
            "google-cloud-secret-manager is required to resolve secrets from "
            "Secret Manager. Install the optional extra: "
            "`uv sync --extra gcp` (or `pip install free-claude-code[gcp]`)."
        ) from exc

    try:
        client = secretmanager.SecretManagerServiceClient()
        response = client.access_secret_version(name=resource)
        return response.payload.data.decode("utf-8")
    except Exception as exc:
        raise SecretManagerError(
            f"Failed to fetch secret from Secret Manager resource {resource!r}: {exc}"
        ) from exc
