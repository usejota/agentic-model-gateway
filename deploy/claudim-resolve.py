#!/usr/bin/env python3
"""Resolve a human delegate-model query against a gateway catalog."""

from __future__ import annotations

import json
import re
import sys

LOCAL_ALIASES = {"opus", "sonnet", "haiku", "fable"}


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def candidate(model: dict[str, object]) -> dict[str, object]:
    return {
        key: model.get(key)
        for key in ("id", "agent_name", "display_name", "policy", "capabilities")
    }


def aliases(model: dict[str, object]) -> list[str]:
    value = model.get("aliases")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def resolve(catalog: dict[str, object], query: str) -> dict[str, object]:
    result: dict[str, object] = {"query": query}
    normalized = normalize(query)
    if normalized in LOCAL_ALIASES:
        return {
            **result,
            "status": "resolved",
            "id": normalized,
            "agent_name": None,
            "policy": "override",
            "candidates": [],
        }
    raw_models = catalog.get("models", [])
    models: list[dict[str, object]] = []
    if isinstance(raw_models, list):
        for raw_model in raw_models:
            if isinstance(raw_model, dict):
                models.extend([{str(key): value for key, value in raw_model.items()}])
    for key in ("id", "agent_name"):
        exact = [model for model in models if model.get(key) == query]
        if exact:
            return {
                **result,
                "status": "resolved",
                **candidate(exact[0]),
                "candidates": [],
            }
    alias_matches = [
        model
        for model in models
        if normalized in aliases(model)
        or normalized == normalize(str(model.get("display_name", "")))
    ]
    if len(alias_matches) == 1:
        return {
            **result,
            "status": "resolved",
            **candidate(alias_matches[0]),
            "candidates": [],
        }
    haystack_matches = [
        model
        for model in models
        if normalized
        and normalized
        in normalize(f"{model.get('agent_name', '')} {model.get('display_name', '')}")
    ]
    matches = alias_matches or haystack_matches
    if len(matches) == 1:
        return {
            **result,
            "status": "resolved",
            **candidate(matches[0]),
            "candidates": [],
        }
    if len(matches) > 1:
        return {
            **result,
            "status": "ambiguous",
            "id": None,
            "agent_name": None,
            "policy": None,
            "candidates": [candidate(model) for model in matches],
        }
    suggestions = sorted(
        models,
        key=lambda model: abs(
            len(normalized) - len(normalize(str(model.get("display_name", ""))))
        ),
    )[:5]
    return {
        **result,
        "status": "not_found",
        "id": None,
        "agent_name": None,
        "policy": None,
        "candidates": [candidate(model) for model in suggestions],
    }


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: claudim-resolve.py <query>")
    catalog = json.load(sys.stdin)
    json.dump(resolve(catalog, sys.argv[1]), sys.stdout, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
