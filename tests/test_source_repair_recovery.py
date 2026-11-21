"""Actual repair apply and post-restart continuation must agree on deployment."""
from unittest.mock import patch

import pytest

from tests.test_run_manager import _config, _state
from tests.test_source_repair_apply import _git, _repair_repositories
from zf.runtime.run_manager import _execute_repair_closeout_apply, build_run_manager_projection
from zf.runtime.sidecar_refs import write_sidecar_json


def _action(worktree, base, repair):
    return {
        "action": "repair-closeout-apply", "queue_id": "repair-queue",
        "checkpoint_id": "apply-checkpoint", "candidate_id": "repair-queue",
        "safe_resume_action": "repair_closeout_apply", "fingerprint": "repair-failure",
        "worktree_path": str(worktree), "base_commit": base, "repair_commit": repair,
        "validation_event_id": "validated", "allow_paths": ["src/zf/**"], "deny_paths": [],
        "continuation_checkpoint_id": "original-checkpoint",
        "continuation_safe_resume_action": "needs_stage_dispatch",
        "continuation": {"resume_original_workflow": True,
                         "checkpoint_id": "original-checkpoint", "safe_resume_action": "needs_stage_dispatch"},
    }


@pytest.mark.parametrize("project", [None, "missing"])
def test_missing_restart_project_never_mutates_harness(tmp_path, project):
    root, repair, base, commit = _repair_repositories(tmp_path)
    state, log, writer = _state(tmp_path)
    writer.default_origin = "kernel"
    with patch("zf.runtime.self_repair_runner.harness_root", return_value=root), patch(
        "zf.runtime.run_manager._spawn_source_repair_restart_actuator"
    ) as spawn:
        result = _execute_repair_closeout_apply(state_dir=state, writer=writer,
            project_root=None if project is None else tmp_path / project,
            action=_action(repair, base, commit), causation_id="cause")
    assert _git(root, "rev-parse", "HEAD") == base
    assert result == "blocked"
    spawn.assert_not_called()
    assert not any(e.type == "runtime.restart.requested" for e in log.read_all())


@pytest.mark.parametrize("startup", ["no_receipt", "wrong_revision", "wrong_state", "wrong_root", "tampered", "valid", "later_stopped", "later_wrong"])
def test_resume_requires_latest_matching_runtime_load(tmp_path, startup):
    root, repair, base, commit = _repair_repositories(tmp_path)
    state, log, writer = _state(tmp_path)
    writer.default_origin = "kernel"
    with patch("zf.runtime.self_repair_runner.harness_root", return_value=root), patch(
        "zf.runtime.run_manager._spawn_source_repair_restart_actuator", return_value=1234,
    ):
        assert _execute_repair_closeout_apply(state_dir=state, writer=writer,
            project_root=tmp_path, action=_action(repair, base, commit), causation_id="cause") == "applied"
    receipt = next(e.payload["apply_receipt"] for e in log.read_all()
                   if e.type == "run.manager.repair.merge.merged")
    writer.emit("session.started", actor="zf-cli",
                payload={"control_plane_only": True, "workers_preserved": True})
    body = {"schema_version": "runtime-load.v1", "state_dir": str(state.resolve()),
            "source_root": str(root), "source_revision": _git(root, "rev-parse", "HEAD"),
            "source_tree_sha256": receipt.get("source_tree_sha256", ""), "pid": 5678}
    if startup == "wrong_revision": body["source_revision"] = base
    if startup == "wrong_state": body["state_dir"] = str(tmp_path / "foreign-state")
    if startup == "wrong_root": body["source_root"] = str(tmp_path / "foreign-harness")
    ref = write_sidecar_json(state, "diagnostics/runtime-load/new.json", body,
        kind="runtime-load", schema_version="runtime-load.v1", created_by="zf-cli", required=True)
    if startup == "tampered": (state / ref["ref"]).write_text("{}")
    writer.emit("loop.started", actor="zf-cli", payload={"runtime_load_ref": ref} if startup != "no_receipt" else {})
    if startup == "later_stopped": writer.emit("loop.stopped")
    if startup == "later_wrong": writer.emit("loop.started", payload={})
    actions = build_run_manager_projection(state, events=log.read_all(), config=_config())["pending_actions"]
    resumes = [a for a in actions if a.get("action") == "workflow-batch-resume"]
    assert bool(resumes) is (startup == "valid")
    if resumes: assert resumes[0]["checkpoint_id"] == "original-checkpoint"


@pytest.mark.parametrize("concurrent", [False, True])
def test_failed_actuator_spawn_reverts_only_unchanged_source(tmp_path, concurrent):
    root, repair, base, commit = _repair_repositories(tmp_path)
    state, log, writer = _state(tmp_path)

    def spawn(**kwargs):
        if concurrent: (root / "README.md").write_text("owner edit\n")
        raise OSError("spawn unavailable")

    with patch("zf.runtime.self_repair_runner.harness_root", return_value=root), patch(
        "zf.runtime.run_manager._spawn_source_repair_restart_actuator", side_effect=spawn,
    ):
        status = _execute_repair_closeout_apply(state_dir=state, writer=writer,
            project_root=tmp_path, action=_action(repair, base, commit), causation_id="cause")
    assert status == "failed"
    events = log.read_all()
    failed = next(e for e in events if e.type == "run.manager.action.failed")
    rollback = failed.payload["apply_receipt"]["rollback"]
    assert rollback["status"] == ("blocked" if concurrent else "reverted")
    if concurrent:
        assert (root / "README.md").read_text() == "owner edit\n"
    else:
        assert _git(root, "rev-parse", "HEAD^{tree}") == _git(root, "rev-parse", f"{base}^{{tree}}")
        assert _git(root, "rev-list", "--count", f"{base}..HEAD") == "2"
    assert not any(e.type in {"runtime.restart.requested", "run.manager.repair.merge.merged"} for e in events)


@pytest.mark.parametrize("status", ["merging", "merged"])
@pytest.mark.parametrize("elapsed", [0, 300])
def test_unknown_apply_or_restart_gets_diagnosis_after_deadline(tmp_path, status, elapsed):
    from datetime import datetime, timedelta, timezone
    from zf.core.events.model import ZfEvent

    state, log, writer = _state(tmp_path)
    event = ZfEvent(type="run.manager.repair.merge." + status, origin="kernel",
        ts=(datetime.now(timezone.utc) - timedelta(seconds=elapsed)).isoformat(), payload={
            "queue_id": "repair-queue", "candidate_id": "repair-queue", "status": status,
            "control_plane_restart_required": True, "continuation": {"workflow_run_id": "run-1"},
        })
    log.append(event)
    def diagnostics():
        return [a for a in build_run_manager_projection(state, events=log.read_all(), config=_config())["pending_actions"]
                if a.get("failure_class") == "repair_restart_outcome_unknown"]
    actions = diagnostics()
    assert bool(actions) is bool(elapsed)
    if elapsed:
        assert actions[0]["policy_decision"]["executable"]
        assert actions[0]["source_event_ids"] == [event.id]
        writer.emit("run.manager.action.applied", payload=actions[0])
        assert diagnostics() == []
