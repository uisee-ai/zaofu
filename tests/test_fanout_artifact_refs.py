from __future__ import annotations

from zf.core.config.schema import ZfConfig
from zf.runtime.fanout_artifact_refs import relocate_fanout_artifact_refs


def test_relocate_fanout_artifact_refs_canonicalizes_legacy_inventory_ref(tmp_path):
    payload = relocate_fanout_artifact_refs(
        payload={"hermes_source_inventory_ref": "docs/plans/source-inventory.json"},
        payload_sources=[],
        manifest={"fanout_id": "scan"},
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        config=ZfConfig(),
        roles=[],
    )

    assert payload["source_inventory_ref"] == "docs/plans/source-inventory.json"
    assert "hermes_source_inventory_ref" not in payload
