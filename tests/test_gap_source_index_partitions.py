import json

import pytest

from zf.runtime.module_gap_plan import _write_gap_source_index_amend_artifact
from zf.runtime.task_scoped_plan_inputs import task_input_paths


@pytest.mark.parametrize("parent", ["OLD", "MISSING"])
def test_source_index_amend_delivers_additive_predecessor_partition(tmp_path, parent):
    state = tmp_path / "state"
    source = state / "artifacts/source-index.json"
    source.parent.mkdir(parents=True)
    body = {
        "schema_version": "source-index.v1",
        "tasks": [{"task_id": "OLD", "source_ref": "requirements.md#closure"}],
        "task_input_policy": {
            "schema_version": "task-input-policy.v1",
            "shared_paths": ["$.schema_version"],
            "tasks": [{"task_id": "OLD", "paths": ["$.tasks[0]"]}],
        },
    }
    source.write_text(json.dumps(body))
    gaps = [
        {"task_id": "PRODUCER", "parent_task_id": parent},
        {"task_id": "SUCCESSOR", "parent_task_id": "OLD", "supersedes_task_ids": ["OLD"]},
    ]
    args = dict(state_dir=state, project_root=tmp_path,
                base_source_index_ref="artifacts/source-index.json", pdd_id="PRD",
                source_event_id="gap", gap_tasks=gaps)
    if parent == "MISSING":
        with pytest.raises(ValueError, match="explicit source input partition"):
            _write_gap_source_index_amend_artifact(**args)
        assert not list((state / "artifacts/PRD").rglob("source_index.json"))
    else:
        result = _write_gap_source_index_amend_artifact(**args)
        amended = json.loads((state / result["source_index_ref"]).read_text())
        for task in ["PRODUCER", "SUCCESSOR"]:
            assert task_input_paths(amended, task_id=task) == ["$.schema_version", "$.tasks[0]"]
        assert amended["tasks"] == [{"task_id": "SUCCESSOR", "source_ref": "requirements.md#closure"}]
        assert json.loads(source.read_text()) == body
