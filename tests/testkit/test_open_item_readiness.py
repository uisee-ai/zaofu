"""Readiness inventory contracts for the remaining explicit host tiers."""

from __future__ import annotations

import pytest

from tests.testkit.open_item_readiness import (
    inspect_long_sample_readiness,
    summarize_open_item_readiness,
)


def _evidence(**overrides):
    value = {
        "source_kind": "official_lossless_api",
        "reference": "research/allocations-v2.json",
        "sha256": "a" * 64,
        "complete_window_replay": True,
        "budget_compatible": True,
    }
    value.update(overrides)
    return value


def test_missing_long_sample_evidence_is_not_ready():
    result = inspect_long_sample_readiness(None)
    assert result["status"] == "not_ready"
    assert result["reason"] == "missing_reentry_evidence"
    assert result["execution_authorized"] is False
    assert result["counts_as_product_pass"] is False


@pytest.mark.parametrize("evidence", [
    {"source_kind": "official_lossless_api"},
    _evidence(complete_window_replay=False),
    _evidence(budget_compatible=False),
    _evidence(source_kind="stitched_local_capture"),
    _evidence(sha256="not-a-digest"),
])
def test_invalid_long_sample_evidence_stays_not_ready(evidence):
    result = inspect_long_sample_readiness(evidence)
    assert result["status"] == "not_ready"
    assert result["execution_authorized"] is False


def test_valid_long_sample_evidence_only_admits_operator_run():
    result = inspect_long_sample_readiness(_evidence())
    assert result["status"] == "ready_for_operator_run"
    assert result["execution_authorized"] is False
    assert result["counts_as_product_pass"] is False
    assert result["sha256"] == "a" * 64


def test_summary_keeps_every_open_item_non_authorizing():
    result = summarize_open_item_readiness()
    assert result["schema_version"] == "zf-testkit-open-item-readiness.v1"
    assert result["release_authorized"] is False
    assert all(
        item["execution_authorized"] is False
        for item in result["items"].values()
    )
    assert result["items"]["K552-F2-long-sample"]["status"] == "not_ready"
    assert result["items"]["final-unified-acceptance"]["status"] == "blocked_by_open_items"


@pytest.mark.parametrize("kwargs", [
    {"same_signature_scene_present": True},
    {"destructive_host_capability_present": True},
    {"same_signature_scene_present": True, "destructive_host_capability_present": True},
])
def test_external_readiness_flags_never_authorize_execution(kwargs):
    result = summarize_open_item_readiness(**kwargs)
    for item in result["items"].values():
        assert item["execution_authorized"] is False
        assert item["counts_as_product_pass"] is False
