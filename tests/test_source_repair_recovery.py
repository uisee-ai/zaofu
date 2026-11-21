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


@pytest.mark.parametrize("loaded", [False, True])
def test_agent_repair_without_workflow_checkpoint_resumes_through_bound_diagnosis(tmp_path, loaded):
    from zf.runtime.run_manager import _resident_diagnosis_reprompt_action

    root, repair, base, commit = _repair_repositories(tmp_path)
    state, log, writer = _state(tmp_path)
    writer.default_origin = "kernel"
    action = _action(repair, base, commit)
    action["continuation"].update(checkpoint_id="recommendation:original",
        safe_resume_action="agent_recommendation", task_id="task-original",
        workflow_run_id="run-original", recovery_case_id="original-case")
    with patch("zf.runtime.self_repair_runner.harness_root", return_value=root), patch(
        "zf.runtime.run_manager._spawn_source_repair_restart_actuator", return_value=1234,
    ):
        assert _execute_repair_closeout_apply(state_dir=state, writer=writer,
            project_root=tmp_path, action=action, causation_id="cause") == "applied"
    receipt = next(e.payload["apply_receipt"] for e in log.read_all()
                   if e.type == "run.manager.repair.merge.merged")
    if loaded:
        ref = write_sidecar_json(state, "diagnostics/runtime-load/new.json", {
            "schema_version": "runtime-load.v1", "state_dir": str(state.resolve()),
            "source_root": str(root), "source_revision": receipt["applied_commit"],
            "source_tree_sha256": receipt["source_tree_sha256"],
        }, kind="runtime-load", schema_version="runtime-load.v1", created_by="zf-cli", required=True)
        writer.emit("loop.started", payload={"runtime_load_ref": ref})
    def actions():
        return build_run_manager_projection(state, events=log.read_all(), config=_config())["pending_actions"]
    pending = actions()
    assert not any(a["action"] == "workflow-batch-resume" for a in pending)
    diagnoses = [a for a in pending if a.get("failure_class") == "self_repair_post_merge_continuation_missing_checkpoint"]
    assert bool(diagnoses) is loaded
    if loaded:
        diagnosis = diagnoses[0]
        assert diagnosis["workflow_run_id"] == "run-original"
        assert diagnosis["continuation"]["recovery_case_id"] == "original-case"
        assert diagnosis["checkpoint_id"] != "recommendation:original"
        dispatch = _resident_diagnosis_reprompt_action(diagnosis, {
            "status": "observing", "tmux_session": "resident", "briefing_path": "/tmp/brief",
            "instance_id": "run-manager",
        })
        assert dispatch is not None
        assert dispatch["request_kind"] == "recovery_diagnosis"
        writer.emit("run.manager.resident.prompted", payload={
            "source_action_checkpoint_id": diagnosis["checkpoint_id"],
        })
        assert not any(a.get("failure_class") == diagnosis["failure_class"] for a in actions())


@pytest.mark.parametrize("history", ["proven", "missing", "before_merge", "tampered"])
def test_newer_boot_does_not_turn_proven_restart_into_unknown(tmp_path, history):
    from datetime import datetime, timedelta, timezone
    from zf.core.events import ZfEvent
    from zf.runtime.run_manager import _pending_post_repair_continuation_actions, build_repair_merge_queue

    state, _, _ = _state(tmp_path)
    receipt = {"source_root": str(tmp_path / "harness"), "applied_commit": "repair-sha", "source_tree_sha256": "tree-sha"}
    ref = write_sidecar_json(state, "diagnostics/runtime-load/prior.json", {
        "schema_version": "runtime-load.v1", "state_dir": str(state.resolve()),
        "source_root": receipt["source_root"], "source_revision": receipt["applied_commit"],
        "source_tree_sha256": receipt["source_tree_sha256"],
    }, kind="runtime-load", schema_version="runtime-load.v1", created_by="zf-cli", required=True)
    prior = ZfEvent(type="loop.started", origin="kernel", payload={"runtime_load_ref": ref})
    merged = ZfEvent(type="run.manager.repair.merge.merged", origin="kernel",
        ts=(datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat(), payload={
            "queue_id": "repair", "apply_receipt": receipt, "control_plane_restart_required": True,
            "continuation": {"resume_original_workflow": True, "checkpoint_id": "original", "safe_resume_action": "needs_stage_dispatch"},
        })
    events = [merged, prior]
    if history == "missing": events = [merged]
    elif history == "before_merge": events = [prior, merged]
    elif history == "tampered": (state / ref["ref"]).write_text("{}")
    events.append(ZfEvent(type="loop.started", origin="kernel", payload={}))
    actions = _pending_post_repair_continuation_actions(build_repair_merge_queue(events), events, state_dir=state)
    assert not any(a["action"] == "workflow-batch-resume" for a in actions)
    assert len(actions) == 1
    assert actions[0]["failure_class"] == ("repair_runtime_binding_changed" if history == "proven" else "repair_restart_outcome_unknown")
