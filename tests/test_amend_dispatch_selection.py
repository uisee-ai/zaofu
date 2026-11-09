"""An admitted gap amendment selects its delta across historical base commits."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.writer_fanout_admission import load_writer_task_map, writer_fanout_dispatch_base_commit


def _fixture(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()
    git("init", "-q")
    heads = []
    for value in ("old", "new"):
        (tmp_path / "base.txt").write_text(value)
        git("add", "base.txt")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", value)
        heads.append(git("rev-parse", "HEAD"))
    data = {"schema_version": "task-map.v1", "amend": {
        "schema_version": "task-map-amend.v1", "resume_scope": "gap_tasks_only", "gap_task_ids": ["GAP"]},
        "tasks": [{"task_id": name, "title": name, "allowed_paths": [f"src/{name}.py"],
                   "verification": "git diff --check", "base_commit": base,
                   "source_refs": [f"git:{base}"]} for name, base in zip(("RETAINED", "GAP"), heads)]}
    return data, heads


def _load(tmp_path, data, payload=None):
    path = tmp_path / "task_map.json"
    path.write_text(json.dumps(data))
    return load_writer_task_map(stage=SimpleNamespace(task_map=""),
        event=ZfEvent(type="task_map.ready", payload={"task_map_ref": str(path), **(payload or {})}),
        pdd_id="PDD", state_dir=tmp_path / ".zf", project_root=tmp_path)


def test_gap_amendment_selects_only_new_work_without_rewriting_historical_bases(tmp_path):
    data, heads = _fixture(tmp_path)
    loaded = _load(tmp_path, data)
    assert loaded.requested_task_ids == ["GAP"]
    assert writer_fanout_dispatch_base_commit(loaded.task_items) == heads[1]
    assert data["tasks"][0]["base_commit"] == heads[0]


@pytest.mark.parametrize("ids", [[], ["MISSING"]])
def test_invalid_gap_selection_fails_closed(tmp_path, ids):
    data, _ = _fixture(tmp_path)
    data["amend"]["gap_task_ids"] = ids
    with pytest.raises((ValueError, RuntimeError)):
        _load(tmp_path, data)


def test_explicit_event_selection_keeps_its_authority(tmp_path):
    data, heads = _fixture(tmp_path)
    loaded = _load(tmp_path, data, {"task_ids": ["RETAINED"]})
    assert loaded.requested_task_ids == ["RETAINED"]
    assert writer_fanout_dispatch_base_commit(loaded.task_items) == heads[0]


def test_full_map_still_rejects_mixed_bases(tmp_path):
    data, _ = _fixture(tmp_path)
    data.pop("amend")
    with pytest.raises(ValueError, match="conflicting base_commit"):
        _load(tmp_path, data)
