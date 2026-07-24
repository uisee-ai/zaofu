from __future__ import annotations

import pytest

from zf.runtime.plan_artifact_ports import (
    PLAN_ARTIFACT_PORT_ADAPTER_VERSION,
    normalize_plan_ports,
    plan_port_adapter,
)


@pytest.mark.parametrize(
    ("source", "canonical"),
    [
        ("product_spec", "requirement_spec"),
        ("prd_ref", "requirement_spec"),
        ("issue_ref", "issue_spec"),
        ("task_map", "task_map"),
    ],
)
def test_plan_port_adapter_has_one_versioned_mapping(source, canonical):
    assert plan_port_adapter(source) == {
        "logical_name": canonical,
        "source_logical_name": source,
        "adapter_version": PLAN_ARTIFACT_PORT_ADAPTER_VERSION,
    }


def test_normalize_plan_ports_rejects_alias_collisions():
    with pytest.raises(ValueError, match="duplicate canonical"):
        normalize_plan_ports([
            {"logical_name": "product_spec", "ref": "a", "sha256": "1"},
            {"logical_name": "prd_ref", "ref": "b", "sha256": "2"},
        ])
