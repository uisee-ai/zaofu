from dataclasses import replace

import pytest

from tests.test_writer_fanout_runtime import _state
from zf.core.events.model import ZfEvent
from zf.runtime.task_map import validate_task_map_payload
from zf.runtime.task_pipeline_entry import task_pipeline_entry_mode
from zf.runtime.writer_fanout_admission import load_writer_task_map, writer_task_items
from zf.runtime.writer_task_materialization import writer_task_pipeline_entry_mode


def test_top_level_read_only_task_materializes_at_verify_with_original_read_scope(tmp_path):
    state, _, _, orch = _state(tmp_path, synthesize_canonical=True)
    loaded = load_writer_task_map(stage=orch.config.workflow.stages[0], pdd_id="F-11111111",
        state_dir=state, project_root=tmp_path,
        event=ZfEvent(type="task_map.ready", payload={"pdd_id": "F-11111111"}))
    reads = ["src/read_only.py", "tests/test_read_only.py"]
    raw = {"task_id": "AUDIT", "title": "audit", "runtime_only": True, "write_free": True,
           "allowed_paths": [], "verification_read_paths": reads}
    items = writer_task_items({"tasks": [raw]})
    orch._ensure_writer_tasks_canonical(replace(loaded, task_items=items))
    audit = orch.task_store.get("AUDIT")
    assert task_pipeline_entry_mode(audit) == "verify_only"
    assert audit.contract.scope == []
    assert audit.contract.evidence_contract["verification_read_paths"] == reads
    assert audit.contract.evidence_contract["write_free"] is True


@pytest.mark.parametrize("extra, match", [
    ({"allowed_paths": ["writable"]}, "write scope"),
    ({"execution_mode": "standard"}, "entry mode"),
    ({"evidence_contract": {"write_free": False}}, "conflicting"),
    ({"evidence_contract": {"verification_read_paths": ["other"]}}, "conflicting"),
])
def test_conflicting_read_only_declarations_fail_before_approval_and_materialization(extra, match):
    raw = {"task_id": "AUDIT", "allowed_paths": [], "write_free": True, "runtime_only": True,
           "verification_read_paths": ["src/only.py"], **extra}
    result = validate_task_map_payload({"tasks": [raw]})
    assert any(match in error for error in result.errors)
    with pytest.raises(ValueError, match=match):
        writer_task_pipeline_entry_mode({}, raw)
