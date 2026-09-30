from .manifest import ProductManifest
import pytest

from .tiers import ci_plan, commands_for_ci_tier


def test_ci_plan_groups_commands_by_minimum_tier():
    manifest = ProductManifest({"commands": [
        {"id": "u", "tier": "unit"}, {"id": "e", "tier": "e2e"},
        {"id": "p", "tier": "perf"},
    ]})
    plan = ci_plan(manifest)
    assert plan["T0"] == ("u",)
    assert plan["T2"] == ("e",)
    assert plan["T3"] == ("p",)
    assert commands_for_ci_tier(manifest, "T2") == ("u", "e")


def test_ci_tier_rejects_unknown_tier():
    with pytest.raises(ValueError):
        commands_for_ci_tier(ProductManifest({"commands": []}), "T9")


def test_ci_plan_schedules_api_contract_by_its_declared_tier():
    manifest = ProductManifest({
        "commands": [{"id": "unit", "tier": "unit"}],
        "api_contracts": [{"id": "openapi", "tier": "integration"}],
    })

    assert ci_plan(manifest)["T1"] == ("openapi",)
    assert commands_for_ci_tier(manifest, "T1") == ("unit", "openapi")
