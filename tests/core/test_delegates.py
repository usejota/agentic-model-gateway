from typing import cast

from core.delegates import (
    DelegateModel,
    build_delegate_catalog,
    build_roster,
    capabilities_for,
    normalize_alias,
)


def _model(ref: str, vendor: str, capabilities: list[str]) -> DelegateModel:
    return DelegateModel(
        id=f"claude-3-freecc-no-thinking/{ref}",
        ref=ref,
        agent_name=f"delegate-{normalize_alias(ref.rsplit('/', 1)[-1])}",
        display_name=ref.rsplit("/", 1)[-1],
        vendor=vendor,
        policy="delegate",
        capabilities=capabilities,
        aliases=[],
    )


def test_capability_heuristics_can_return_multiple_capabilities() -> None:
    assert capabilities_for("open_router/acme/vision-coder-pro") == [
        "coding",
        "vision",
        "reasoning",
    ]


def test_roster_is_deterministic_and_honors_available_preference() -> None:
    models = [
        _model("p/a/code", "a", ["coding"]),
        _model("p/b/flash", "b", ["fast"]),
        _model("p/c/vision", "c", ["vision"]),
        _model("p/d/pro", "d", ["reasoning"]),
        _model("p/e/chat", "e", ["general"]),
    ]
    first = build_roster(models, 5, ["p/e/chat", "excluded/missing"])
    second = build_roster(models, 5, ["p/e/chat", "excluded/missing"])
    assert first == second
    assert first[0].ref == "p/e/chat"
    assert {cap for model in first for cap in model.capabilities} == {
        "coding",
        "fast",
        "vision",
        "reasoning",
        "general",
    }


def test_collision_names_are_stable_across_input_order() -> None:
    refs = ["router/vendor/model-x", "other/vendor/model-x"]

    def names(values: list[str]) -> dict[str, str]:
        catalog = build_delegate_catalog(
            values,
            exclusions=[],
            approvals=[],
            model_id_for_ref=lambda ref: f"test/{ref}",
            normalize_ref=lambda ref: ref,
        )
        models = catalog["models"]
        assert isinstance(models, list)
        result: dict[str, str] = {}
        for model in models:
            assert isinstance(model, dict)
            typed_model = cast(dict[str, object], model)
            result[str(typed_model["ref"])] = str(typed_model["agent_name"])
        return result

    assert names(refs) == names(list(reversed(refs)))
    assert all(
        name.startswith("delegate-vendor-model-x-") for name in names(refs).values()
    )


def test_vendor_filter_is_case_insensitive_and_id_builder_is_authoritative() -> None:
    catalog = build_delegate_catalog(
        ["router/OpenAI/model", "router/deepseek/model"],
        exclusions=[],
        approvals=[],
        model_id_for_ref=lambda ref: f"gateway::{ref}",
        normalize_ref=lambda ref: ref,
    )

    assert catalog["data"] == ["gateway::router/deepseek/model"]


# =============================================================================
# MODEL_DELEGATE_ALLOWLIST
# =============================================================================


_ID = lambda ref: f"gateway::{ref}"  # noqa: E731
_NORM = lambda ref: ref  # noqa: E731


def _data(catalog: dict[str, object]) -> list[object]:
    return cast(list[object], catalog["data"])


def _approval(catalog: dict[str, object]) -> list[object]:
    return cast(list[object], catalog["approval"])


def _models(catalog: dict[str, object]) -> list[object]:
    return cast(list[object], catalog["models"])


def test_allowlist_empty_is_same_catalog() -> None:
    refs = ["router/deepseek/model", "router/moonshotai/model"]
    without = build_delegate_catalog(
        refs, exclusions=[], approvals=[], model_id_for_ref=_ID, normalize_ref=_NORM
    )
    with_empty = build_delegate_catalog(
        refs,
        exclusions=[],
        approvals=[],
        allowlist=[],
        model_id_for_ref=_ID,
        normalize_ref=_NORM,
    )
    assert _data(without) == _data(with_empty)
    assert _approval(without) == _approval(with_empty)
    assert len(_models(without)) == len(_models(with_empty))


def test_allowlist_exact_refs_only_those_free() -> None:
    refs = ["router/a/model", "router/b/model", "router/c/model"]
    catalog = build_delegate_catalog(
        refs,
        exclusions=[],
        approvals=[],
        allowlist=["router/a/model", "router/b/model"],
        model_id_for_ref=_ID,
        normalize_ref=_NORM,
    )
    data = _data(catalog)
    assert len(data) == 2
    assert _ID("router/a/model") in data
    assert _ID("router/b/model") in data
    assert _ID("router/c/model") not in data
    assert _approval(catalog) == []


def test_allowlist_glob_family() -> None:
    refs = ["p/deepseek/v4-pro", "p/deepseek/v4-flash", "p/other/model"]
    catalog = build_delegate_catalog(
        refs,
        exclusions=[],
        approvals=[],
        allowlist=["p/deepseek/*"],
        model_id_for_ref=_ID,
        normalize_ref=_NORM,
    )
    assert len(_data(catalog)) == 2
    assert _ID("p/other/model") not in _data(catalog)


def test_allowlist_intersection_with_approval_is_approval() -> None:
    refs = ["router/v/model"]
    catalog = build_delegate_catalog(
        refs,
        exclusions=[],
        approvals=["router/*"],
        allowlist=["router/*"],
        model_id_for_ref=_ID,
        normalize_ref=_NORM,
    )
    assert _data(catalog) == []
    assert _approval(catalog) == [_ID("router/v/model")]


def test_approval_outside_allowlist_still_present() -> None:
    refs = ["router/free/model", "router/premium/model"]
    catalog = build_delegate_catalog(
        refs,
        exclusions=[],
        approvals=["router/premium/*"],
        allowlist=["router/free/*"],
        model_id_for_ref=_ID,
        normalize_ref=_NORM,
    )
    assert _data(catalog) == [_ID("router/free/model")]
    assert _approval(catalog) == [_ID("router/premium/model")]


def test_allowlist_with_exclusion_wins() -> None:
    refs = ["router/v/model"]
    catalog = build_delegate_catalog(
        refs,
        exclusions=["router/*"],
        approvals=[],
        allowlist=["router/*"],
        model_id_for_ref=_ID,
        normalize_ref=_NORM,
    )
    assert _data(catalog) == []
    assert _approval(catalog) == []


def test_allowlist_us_closed_vendor_not_free() -> None:
    refs = ["router/openai/gpt"]
    catalog = build_delegate_catalog(
        refs,
        exclusions=[],
        approvals=[],
        allowlist=["router/*"],
        model_id_for_ref=_ID,
        normalize_ref=_NORM,
    )
    assert _data(catalog) == []
    assert _approval(catalog) == []
    assert len(_models(catalog)) == 0


def test_roster_cannot_bypass_allowlist() -> None:
    refs = ["router/v/a", "router/v/b"]
    catalog = build_delegate_catalog(
        refs,
        exclusions=[],
        approvals=[],
        allowlist=["router/v/a"],
        preferred_refs=["router/v/b"],
        model_id_for_ref=_ID,
        normalize_ref=_NORM,
    )
    assert len(_data(catalog)) == 1
    assert _ID("router/v/b") not in _data(catalog)


def test_allowlist_legacy_shapes_consistent() -> None:
    refs = ["router/a/model"]
    catalog = build_delegate_catalog(
        refs,
        exclusions=[],
        approvals=[],
        allowlist=["router/*"],
        model_id_for_ref=_ID,
        normalize_ref=_NORM,
    )
    assert "data" in catalog
    assert "approval" in catalog
    assert "models" in catalog
    assert isinstance(catalog["data"], list)
    assert isinstance(catalog["approval"], list)
    assert isinstance(catalog["models"], list)
