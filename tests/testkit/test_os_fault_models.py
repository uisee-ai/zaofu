"""Contracts preventing OS-fault coverage from being overstated."""

from __future__ import annotations

import pytest

from tests.testkit.os_fault_models import OS_FAULT_MODELS, admit_os_fault_result


def _result(model_id):
    model = OS_FAULT_MODELS[model_id]
    result = {field: f"{field}-proof" for field in model.required_evidence}
    result["fault_model"] = model_id
    result.update({"before_disk_sha256": "a" * 64, "after_disk_sha256": "b" * 64})
    return result


@pytest.mark.parametrize("model_id", ["owned-qemu-sigkill", "guest-kernel-panic"])
def test_only_explicitly_covered_models_can_be_admitted(model_id):
    admitted = admit_os_fault_result(model_id, _result(model_id))
    assert admitted["status"] == "admitted"
    assert admitted["model_id"] == model_id


@pytest.mark.parametrize("model_id", ["physical-host-power-loss", "guest-filesystem-corruption"])
def test_open_or_destructive_models_are_not_inferred_from_existing_evidence(model_id):
    with pytest.raises(ValueError, match="remains open"):
        admit_os_fault_result(model_id, _result(model_id))


@pytest.mark.parametrize("mutation", ["wrong_model", "missing_evidence", "bad_digest", "unknown"])
def test_covered_model_requires_exact_identity_and_complete_evidence(mutation):
    result = _result("owned-qemu-sigkill")
    model_id = "owned-qemu-sigkill"
    if mutation == "wrong_model":
        result["fault_model"] = "guest-kernel-panic"
    elif mutation == "missing_evidence":
        result.pop("cleanup_ok")
    elif mutation == "bad_digest":
        result["before_disk_sha256"] = "not-a-digest"
    else:
        model_id = "missing-model"
    with pytest.raises(ValueError):
        admit_os_fault_result(model_id, result)
