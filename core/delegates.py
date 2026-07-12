"""Normalized delegate-model catalog and deterministic roster selection."""

from __future__ import annotations

import fnmatch
import hashlib
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Literal

CAPABILITY_ORDER = ("coding", "fast", "vision", "reasoning", "general")
CURATED_CAPABILITIES: dict[str, list[str]] = {
    "kimi-k2.7-code": ["coding", "reasoning"],
    "deepseek-v4-pro": ["coding", "reasoning"],
    "deepseek-v4-flash": ["fast", "general"],
    "glm-5.2": ["general", "reasoning"],
    "minimax-m3": ["general"],
    "mistral-small-3.2-24b-instruct": ["general"],
    "ministral-8b-2512": ["fast", "general"],
    "codestral-2508": ["coding"],
    "qwen3-vl": ["vision", "reasoning"],
}


@dataclass(frozen=True)
class DelegateModel:
    id: str
    ref: str
    agent_name: str
    display_name: str
    vendor: str
    policy: Literal["delegate", "approval"]
    capabilities: list[str]
    aliases: list[str]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def normalize_alias(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def normalize_approval_ref(ref: str, normalize_ref: Callable[[str], str]) -> str:
    ref = normalize_ref(ref)
    parts = ref.split("/")
    return "/".join(parts[1:]) if len(parts) >= 3 else ref


def delegate_vendor(ref: str) -> str:
    parts = ref.split("/")
    return (parts[1] if len(parts) >= 3 else parts[0]).lstrip("~")


def ref_matches(
    ref: str, patterns: list[str], normalize_ref: Callable[[str], str]
) -> bool:
    normalized_ref = normalize_approval_ref(ref, normalize_ref)
    return any(
        fnmatch.fnmatchcase(ref, pattern)
        or fnmatch.fnmatchcase(
            normalized_ref, normalize_approval_ref(pattern, normalize_ref)
        )
        for pattern in patterns
    )


def ref_in_catalog_union(
    ref: str,
    *,
    allowlist: list[str],
    approvals: list[str],
    normalize_ref: Callable[[str], str],
) -> bool:
    """True if ``ref`` would be admitted to the catalog.

    Approval matches are always admitted (as approval-gated); allowlist
    matches are admitted as free delegates. Both lists empty = empty
    catalog = nothing admitted.
    """
    if ref_matches(ref, approvals, normalize_ref):
        return True
    return ref_matches(ref, allowlist, normalize_ref)


def capabilities_for(ref: str) -> list[str]:
    tail = ref.rsplit("/", 1)[-1].lower()
    if tail in CURATED_CAPABILITIES:
        return list(CURATED_CAPABILITIES[tail])
    capabilities: list[str] = []
    checks = (
        ("coding", ("code", "coder", "codestral")),
        ("fast", ("flash", "lite", "mini", "8b")),
        ("vision", ("vl", "vision")),
        ("reasoning", ("max", "pro", "r1", "reason")),
    )
    for capability, tokens in checks:
        if any(token in tail for token in tokens):
            capabilities.append(capability)
    return capabilities or ["general"]


def _display_name(ref: str) -> str:
    tail = ref.rsplit("/", 1)[-1]
    return " ".join(
        part.upper() if part in {"ai", "vl"} else part.capitalize()
        for part in re.split(r"[-_.]+", tail)
    )


def _agent_names(
    refs: list[tuple[str, Literal["delegate", "approval"]]],
) -> dict[str, str]:
    base_names: dict[str, str] = {}
    base_counts: dict[str, int] = {}
    for ref, policy in refs:
        prefix = f"{policy}-"
        tail_slug = normalize_alias(ref.rsplit("/", 1)[-1]) or "model"
        base = prefix + tail_slug
        base_names[ref] = base
        base_counts[base] = base_counts.get(base, 0) + 1

    vendor_names: dict[str, str] = {}
    vendor_counts: dict[str, int] = {}
    for ref, policy in refs:
        base = base_names[ref]
        if base_counts[base] == 1:
            vendor_name = base
        else:
            tail_slug = normalize_alias(ref.rsplit("/", 1)[-1]) or "model"
            vendor_name = (
                f"{policy}-{normalize_alias(delegate_vendor(ref))}-{tail_slug}"
            )
        vendor_names[ref] = vendor_name
        vendor_counts[vendor_name] = vendor_counts.get(vendor_name, 0) + 1

    names: dict[str, str] = {}
    for ref, _policy in refs:
        name = vendor_names[ref]
        if vendor_counts[name] > 1:
            digest = hashlib.sha1(ref.encode()).hexdigest()[:6]
            name = f"{name}-{digest}"
        names[ref] = name
    return names


def build_roster(models: list[DelegateModel], size: int) -> list[DelegateModel]:
    if size <= 0:
        return []
    selected: list[DelegateModel] = []
    selected_refs: set[str] = set()
    covered: set[str] = set()
    vendor_counts: dict[str, int] = {}

    def add(model: DelegateModel) -> None:
        if model.ref in selected_refs or len(selected) >= size:
            return
        selected.append(model)
        selected_refs.add(model.ref)
        covered.update(model.capabilities)
        vendor_counts[model.vendor] = vendor_counts.get(model.vendor, 0) + 1

    for capability in CAPABILITY_ORDER:
        if capability in covered:
            continue
        candidates = [
            model
            for model in models
            if model.ref not in selected_refs and capability in model.capabilities
        ]
        diverse = [m for m in candidates if vendor_counts.get(m.vendor, 0) < 3]
        if diverse:
            add(diverse[0])
        elif candidates:
            add(candidates[0])

    for model in models:
        add(model)
    return selected


def build_delegate_catalog(
    refs: list[str],
    *,
    approvals: list[str],
    allowlist: list[str],
    model_id_for_ref: Callable[[str], str],
    normalize_ref: Callable[[str], str],
) -> dict[str, object]:
    """Classify refs into the delegate catalog.

    A ref is admitted as ``approval`` when it matches an approval pattern
    (approval wins over allowlist), as ``delegate`` (free) when it matches an
    allowlist pattern, and dropped otherwise. Both lists empty = empty
    catalog. ``model_id_for_ref`` maps each ref to the id advertised at
    ``/v1/models/delegates``; ``normalize_ref`` reduces gateway model ids
    to canonical provider/model form so a pattern written against one
    advertised variant matches the others.
    """
    classified: list[tuple[str, Literal["delegate", "approval"]]] = []
    for ref in refs:
        if ref_matches(ref, approvals, normalize_ref):
            classified.append((ref, "approval"))
        elif ref_matches(ref, allowlist, normalize_ref):
            classified.append((ref, "delegate"))

    names = _agent_names(classified)
    models = [
        DelegateModel(
            id=model_id_for_ref(ref),
            ref=ref,
            agent_name=names[ref],
            display_name=_display_name(ref),
            vendor=delegate_vendor(ref),
            policy=policy,
            capabilities=capabilities_for(ref),
            aliases=list(
                dict.fromkeys(
                    [
                        normalize_alias(ref.rsplit("/", 1)[-1]),
                        normalize_alias(_display_name(ref)),
                        normalize_alias(ref),
                    ]
                )
            ),
        )
        for ref, policy in classified
    ]
    ordered = build_roster(models, len(models))
    return {
        "data": [model.id for model in models if model.policy == "delegate"],
        "approval": [model.id for model in models if model.policy == "approval"],
        "models": [model.as_dict() for model in ordered],
    }
