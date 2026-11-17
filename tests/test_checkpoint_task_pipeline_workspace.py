"""Maintenance must preserve the current task checkout, not a lane's old repo."""
import base64
from pathlib import Path

import pytest

from tests.test_work_record import setup
from tests.test_work_record_dispatch import dispatch
from tests.test_maintenance_worktree_checkpoint import _git
from zf.core.config.schema import ProjectConfig, RoleConfig, RuntimeConfig, WorkdirConfig, ZfConfig
from zf.core.events import EventWriter
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.maintenance_worktree import checkpoint_root
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def case(tmp_path):
    state = tmp_path / "state"
    _, _, log, record, _ = setup(state, canonical_request=True)
    root = record.parent
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Checkpoint")
    _git(root, "config", "user.email", "checkpoint@example.invalid")
    (root / "code.txt").write_text("base")
    _git(root, "add", "code.txt")
    _git(root, "commit", "-qm", "test: base")
    (root / "code.txt").write_text("task candidate")
    (root / "untracked.txt").write_text("uncommitted task result")
    cfg = ZfConfig(project=ProjectConfig(name="checkpoint"), roles=[RoleConfig(name="dev-1")],
        runtime=RuntimeConfig(workdirs=WorkdirConfig(enabled=True, mode="worktree", root=str(state / "roles"))))
    # Model the admitted placement, including its configured root and metadata.
    import json
    target = state / "roles/tasks/r/T7/g1/project"
    target.parent.mkdir(parents=True)
    root.rename(target)
    record = target / record.name
    (target.parent / "meta.json").write_text(json.dumps({
        "task_id": "T7", "workflow_run_id": "r", "task_map_generation": "map-g1",
        "workspace_generation": 1,
    }))
    # A reused lane still has another task's checkout. Never snapshot it.
    lane = state / "roles/dev-1/project"
    lane.mkdir(parents=True)
    return state, log, record, cfg


def test_controlled_maintenance_snapshots_canonical_task_workspace(tmp_path):
    import json
    state, log, record, cfg = case(tmp_path)
    dispatch(log, record)
    writer = EventWriter(log)
    request = writer.emit("control.action.requested", actor="operator", payload={"action": "maintenance-prepare"})
    result = ControlledActionService(state, writer, config=cfg, project_root=tmp_path).execute(
        action="maintenance-prepare", requested_action="maintenance-prepare", requested=request,
        payload={"trigger_id": "patch", "task_id": "T7", "checkpoint": True, "assigned_worker": "dev-1"})
    assert result["ok"], result
    body = json.loads(Path(result["checkpoint_path"]).read_text())
    assert body["worktree_root"] == str(record.parent)
    snap = hydrate_sidecar_ref(state, body["workspace_snapshot_ref"]).payload
    assert b"task candidate" in base64.b64decode(snap["unstaged_patch_base64"])
    assert "untracked.txt" in {item["path"] for item in snap["untracked"]}
    assert snap["provider_quiescence"] == "unverified"


@pytest.mark.parametrize("fault", ["digest", "directory", "origin", "attempt_id", "lease_id", "workflow_run_id"])
def test_stale_pipeline_checkpoint_does_not_fall_back_to_lane(tmp_path, fault):
    state, log, record, cfg = case(tmp_path)
    dispatch(log, record, fault=fault)
    with pytest.raises(ValueError):
        checkpoint_root(state, tmp_path, cfg, "T7", "dev-1")
