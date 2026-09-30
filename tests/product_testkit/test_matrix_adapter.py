from __future__ import annotations

import pytest

from .manifest import ManifestError, validate_manifest
from .matrix_adapter import adapt_test_matrix, audit_test_matrix


def test_adapter_maps_product_tiers_and_preserves_explicit_contracts():
    manifest = adapt_test_matrix({
        "schema_version": "test-matrix.v1",
        "commands": [{"id": "smoke", "command": "pytest -q", "tier": "runtime",
                       "acceptance_ids": ["AC-1"], "domain_ids": ["P-04"]}],
    }, target_commit="a" * 40)
    validate_manifest(manifest)
    assert manifest["commands"][0]["tier"] == "integration"
    assert manifest["acceptances"] == [{"id": "AC-1"}]


@pytest.mark.parametrize("field", ["acceptance_ids", "domain_ids"])
def test_adapter_rejects_missing_explicit_bindings(field: str):
    command = {"id": "smoke", "command": "pytest -q", "tier": "runtime",
               "acceptance_ids": ["AC-1"], "domain_ids": ["P-04"]}
    command.pop(field)
    with pytest.raises(ManifestError, match=field):
        adapt_test_matrix({"schema_version": "test-matrix.v1", "commands": [command]}, target_commit="a" * 40)


def test_adapter_rejects_unknown_tier():
    with pytest.raises(ManifestError, match="cannot be mapped"):
        adapt_test_matrix({"schema_version": "test-matrix.v1", "commands": [{
            "id": "x", "command": "true", "tier": "custom", "acceptance_ids": ["a"], "domain_ids": ["P-01"]
        }]}, target_commit="a" * 40)


def test_audit_reports_all_missing_bindings_in_one_pass():
    issues = audit_test_matrix({"schema_version": "test-matrix.v1", "commands": [
        {"id": "one", "command": "true", "tier": "runtime"},
        {"id": "two", "command": "true", "tier": "unknown"},
    ]})
    assert issues == (
        "commands[0] requires acceptance_ids",
        "commands[0] requires domain_ids",
        "commands[1] tier cannot be mapped",
        "commands[1] requires acceptance_ids",
        "commands[1] requires domain_ids",
    )
