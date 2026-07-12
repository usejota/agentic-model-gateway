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
