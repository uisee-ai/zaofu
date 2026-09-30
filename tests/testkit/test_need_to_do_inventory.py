"""Fail-closed tests for the current need-to-do status inventory."""

from dataclasses import asdict

import pytest

from tests.testkit.need_to_do_inventory import (
    COMPLETED_STATUS,
    CURRENT_INVENTORY,
    NeedToDoItem,
    current_inventory,
    inventory_from_mappings,
    validate_inventory,
)


def test_current_inventory_separates_open_items_from_framework_completion():
    result = current_inventory()
    assert result["schema_version"] == "zf-testkit-need-to-do-inventory.v1"
    assert "K547-codex-native" in result["open_item_ids"]
    assert "claude-native-pipeline" in result["open_item_ids"]
    assert "deterministic-mainline" in result["completed_framework_ids"]
    assert "guest-panic-recovery-framework" in result["completed_framework_ids"]
    assert result["product_pass_count"] == 0


@pytest.mark.parametrize("item_id", [
    "K547-codex-native", "claude-native-pipeline", "K552-F2-long-sample",
    "final-unified-acceptance", "issue-merge", "feishu-integration",
])
def test_open_or_excluded_items_never_count_as_product_pass(item_id):
    row = next(item for item in CURRENT_INVENTORY if item.item_id == item_id)
    changed = [asdict(item) if item.item_id != item_id else {
        **asdict(item), "counts_as_product_pass": True,
    } for item in CURRENT_INVENTORY]
    with pytest.raises(ValueError, match="cannot count as product pass"):
        inventory_from_mappings(changed)


def test_completed_framework_item_requires_evidence():
    rows = [asdict(item) for item in CURRENT_INVENTORY]
    rows[-1]["evidence_ref"] = ""
    with pytest.raises(ValueError, match="lacks evidence"):
        inventory_from_mappings(rows)


def test_duplicate_ids_and_unknown_status_fail_closed():
    rows = [asdict(CURRENT_INVENTORY[0]), asdict(CURRENT_INVENTORY[0])]
    with pytest.raises(ValueError, match="unique"):
        inventory_from_mappings(rows)
    rows[1]["item_id"] = "other"
    rows[1]["status"] = "pass"
    with pytest.raises(ValueError, match="unknown"):
        inventory_from_mappings(rows)


def test_open_item_requires_method_and_completion_condition():
    row = asdict(CURRENT_INVENTORY[0])
    row["completion_condition"] = ""
    with pytest.raises(ValueError, match="lacks method/condition"):
        validate_inventory([NeedToDoItem(**row)])


def test_status_guard_does_not_infer_a_product_verdict_from_completed_framework():
    result = current_inventory()
    assert all(item["status"] != "pass" for item in result["items"])
    assert "product verdict inference" in result["scope"]
