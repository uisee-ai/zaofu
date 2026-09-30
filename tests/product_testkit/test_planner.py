from __future__ import annotations

import pytest

from .manifest import ProductManifest, ManifestError
from .planner import commands_for_domains, require_capability_decisions, require_domain_coverage, select_test_domains


def _manifest(capabilities: dict) -> ProductManifest:
    return ProductManifest({
        "schema_version": "product-test-manifest.v1", "project_type": "api",
        "source_root": ".", "target_commit": "a" * 40, "setup": [],
        "commands": [{"id": "unit", "command": "true", "tier": "unit",
                       "acceptance_ids": ["AC-1"], "mandatory": True,
                       "deterministic": True, "timeout_seconds": 10}],
        "acceptances": [{"id": "AC-1"}], "entrypoints": [],
        "capabilities": capabilities, "budgets": {},
        "not_applicable": {"web": {"reason": "no browser surface"}}, "cleanup": [],
    })


def test_planner_always_selects_core_and_api_domains():
    rows = select_test_domains(_manifest({"api": True}))
    selected = {domain for row in rows if row["status"] == "selected" for domain in row["domains"]}
    assert {"P-01", "P-05", "P-07", "P-11", "P-18"} <= selected


def test_planner_marks_undeclared_optional_capability_not_applicable():
    rows = select_test_domains(_manifest({}))
    web = next(row for row in rows if row["capability"] == "web")
    assert web["status"] == "not_applicable"


def test_planner_rejects_non_boolean_capability():
    with pytest.raises(ManifestError):
        select_test_domains(_manifest({"api": "yes"}))


def test_planner_rejects_unknown_capability_instead_of_silently_ignoring():
    with pytest.raises(ManifestError, match="unknown product capabilities: mobile"):
        select_test_domains(_manifest({"mobile": True}))


def test_commands_for_domains_requires_explicit_mapping():
    manifest = _manifest({"api": True})
    manifest.raw["commands"][0]["domain_ids"] = ["P-07"]
    assert commands_for_domains(manifest, {"P-07"})[0]["id"] == "unit"
    assert commands_for_domains(manifest, {"P-06"}) == ()


def test_commands_for_domains_rejects_unknown_domain():
    manifest = _manifest({})
    manifest.raw["commands"][0]["domain_ids"] = ["P-99"]
    with pytest.raises(ManifestError):
        commands_for_domains(manifest, {"P-01"})


def test_require_domain_coverage_fails_closed_for_unmapped_domain():
    manifest = _manifest({})
    manifest.raw["commands"][0]["domain_ids"] = ["P-01"]
    with pytest.raises(ManifestError, match="P-05"):
        require_domain_coverage(manifest, {"P-01", "P-05"})


def test_require_domain_coverage_accepts_all_explicitly_mapped_domains():
    manifest = _manifest({})
    manifest.raw["commands"][0]["domain_ids"] = ["P-01", "P-05"]
    require_domain_coverage(manifest, {"P-01", "P-05"})


def test_openapi_contract_is_a_first_class_domain_test_unit():
    manifest = _manifest({"api": True})
    manifest.raw["commands"][0]["domain_ids"] = ["P-01"]
    manifest.raw["api_contracts"] = [{"id": "openapi", "domain_ids": ["P-07", "P-11"]}]

    selected = commands_for_domains(manifest, {"P-07"})

    assert [item["id"] for item in selected] == ["openapi"]
    require_domain_coverage(manifest, {"P-01", "P-07", "P-11"})


def test_explicit_false_capability_requires_not_applicable_decision():
    manifest = _manifest({"api": False})
    with pytest.raises(ManifestError, match="api"):
        require_capability_decisions(manifest)
    manifest.raw["not_applicable"]["api"] = {"reason": "no API", "source": "project profile"}
    require_capability_decisions(manifest)
