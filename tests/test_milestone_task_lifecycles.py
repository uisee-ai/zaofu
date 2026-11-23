from datetime import datetime, timedelta, timezone

import pytest

from zf.core.events import ZfEvent
from zf.runtime.milestone_task_lifecycles import task_lifecycle_evidence


def test_lifecycles_separate_entry_stages_and_keep_retained_plan_generations():
    events = []
    epoch = datetime(2026, 11, 23, tzinfo=timezone.utc)

    def add(kind, second, *, tid="", origin="kernel", run="R", **values):
        event = ZfEvent(id=f"evt-{len(events)}", type=kind, ts=(epoch + timedelta(seconds=second)).isoformat(),
                        origin=origin, task_id=tid, payload={"workflow_run_id": run, "task_id": tid, **values})
        events.append(event)
        return event

    def operation(tid, stage, mode, gen, start, end, *, run="R", terminal_hash=None):
        oid = f"op-{len(events)}"
        key = {"operation_id": oid, "request_hash": oid + "-hash"}
        attempt = {"tid": tid, "run": run, "dispatch_id": oid + "-dispatch"}
        add("workflow.operation.started", start, active_attempt_id=oid + "-attempt", **attempt, **key)
        d = add("task.pipeline.stage.dispatched", start, task_map_generation=gen, plan_artifact_package_digest="pkg-"+gen,
                task_pipeline_stage=stage, stage_id=stage, task_pipeline_entry_mode=mode, attempt_id=oid + "-attempt", **attempt, **key)
        add("workflow.operation.settled", end, tid=tid, run=run, operation_id=oid, request_hash=terminal_hash or key["request_hash"])
        return d

    original = operation("audit", "verify", "verify_only", "old", 2, 12)
    operation("build", "impl", "standard", "old", 6, 17)
    operation("build", "verify", "standard", "old", 18, 19)
    operation("audit", "verify", "verify_only", "new", 21, 22, terminal_hash="different")
    operation("outside", "impl", "standard", "old", 5, 10)
    operation("audit", "verify", "verify_only", "unrelated", 5, 10)
    operation("build", "impl", "standard", "old", 5, 10, run="other")
    add("fanout.child.dispatched", 24, tid="build", stage_id="prd-lanes-verify")
    freeze = add("candidate.ready", 30, task_map_generation="new", candidate_head_commit="commit")
    operation("audit", "verify", "verify_only", "new", 32, 34)
    body = task_lifecycle_evidence(events, run="R", generation="new", target="commit", freeze_id=freeze.id,
                                   members=[{"task_id":"audit", "task_map_generation":"new"}, {"task_id":"build", "task_map_generation":"old"}])
    summary = body["summary"]
    old, new = summary["generations"]
    assert old["task_map_generation"] == "old" and old["first_entry_overlap_seconds"] == 6
    assert old["peak_by_entry_stage"] == {"impl": 1, "verify": 1}
    assert new["first_entry_overlap_seconds"] is None
    assert summary["task_ids"] == ["audit", "build"]
    audit, build = body["tasks"]
    assert [(r["dispatch_event"], r["task_map_generation"]) for r in audit["operations"]][:1] == [(original.id,"old")]
    assert len(audit["operations"]) == len(build["operations"]) == 2
    assert audit["operations"][1]["terminal"] is None
    assert [r["entry_stage"] for r in build["operations"]] == [True, False]
    assert task_lifecycle_evidence(events,run="R",generation="new",target="wrong",freeze_id=freeze.id,members=[]) is None


@pytest.mark.parametrize("recovering", [False, True])
def test_candidate_lifecycle_read_scope_and_replay(tmp_path, recovering):
    import json
    from zf.runtime.artifact_workdir_view import AttemptFiles
    from tests.test_candidate_verification_authority import _runtime, _seed_frozen_candidate
    from zf.runtime.artifact_read_ledger import ArtifactReadError, read_attempt_artifact
    from zf.runtime.call_result_runtime import prepare_call_operation
    from zf.runtime.milestone_handoff import milestone_readiness
    from zf.runtime.milestone_task_lifecycles import SOURCE, aggregate_recovery_lifecycles
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    runtime = _runtime(tmp_path)
    runtime.config.workflow.flow_metadata["milestone_handoff"] = True
    run, gen, task = "run-candidate-authority", "generation-1", "TASK-1"
    identity = {"workflow_run_id": run, "task_id": task, "operation_id": "source-op", "request_hash": "a" * 64}
    for kind, more in [
        ("workflow.operation.started", {"active_attempt_id": "a", "dispatch_id": "d"}),
        ("task.pipeline.stage.dispatched", {"attempt_id": "a", "dispatch_id": "d", "stage_id": "impl",
          "task_pipeline_stage": "impl", "task_pipeline_entry_mode": "standard", "task_map_generation": gen,
          "plan_artifact_package_digest": "b" * 64}),
        ("workflow.operation.settled", {}),
    ]:
        runtime.event_log.append(ZfEvent(type=kind, origin="kernel", task_id=task, payload={**identity, **more}))
    seeded = _seed_frozen_candidate(runtime)
    payload = {"workflow_run_id": run, "task_id": "FLOW-ANCHOR", "fanout_id": "fanout-candidate-verify",
               "child_id": "verify-lane-0", "role_instance": "verify-lane-0", "stage_id": "issue-lanes-verify",
               "output_profile_id": "candidate-verify", "artifact_package_mode": "shadow", "target_commit": seeded["candidate_head"],
               "canonical_success_event": "test.passed", "canonical_failure_event": "test.failed"}
    if recovering:
        payload["trigger_payload"] = {"operator_feedback": ["Reassess the prior failure against this freeze."]}
    def prepare():
        return prepare_call_operation(runtime, payload=payload, operation_type="fanout_reader_child", operation_key="verify-lane-0",
                stage_id="issue-lanes-verify", task_id="FLOW-ANCHOR", dispatch_id="dispatch-candidate-verify", causation_id="evt-candidate-ready")

    prepared = prepare()
    manifest = hydrate_sidecar_ref(runtime.state_dir, payload["attempt_source_manifest"]).payload
    ref = next(row for row in manifest["sources"] if row["source_id"] == SOURCE)
    assert ref["allowed_paths"] == ["$.summary", "$.tasks[0]"]
    selected = [row["json_path"] for row in payload["required_reads"] if row["source_id"] == SOURCE]
    assert selected == (["$.summary"] if recovering else [])
    if recovering:
        assert [row["source_id"] for row in payload["required_reads"][:2]] == ["critical-reader-repair-context", SOURCE]
    def read(path, actor="verify-lane-0"):
        return read_attempt_artifact(runtime.state_dir, manifest=manifest, source_id=SOURCE, artifact_id=SOURCE,
                                     json_path=path, actor=actor, purpose="candidate-verify", record=False)

    assert "source-op" in read("$.tasks[0]")["content"]
    identity = {"actor": "verify-lane-0", "role": "verify", "purpose": "candidate-verify", "provider": "codex",
                "operation_id": prepared.operation_id}
    view = AttemptFiles(runtime.state_dir, manifest, identity, payload["required_reads"], lambda: {})
    task_file = next(name for name, (row, _) in view.files.items()
                     if row["source_id"] == SOURCE and row["json_path"] == "$.tasks[0]")
    assert json.loads(view.read(task_file)[0]) == json.loads(read("$.tasks[0]")["content"])
    with pytest.raises(ArtifactReadError):
        read("$", "verify-lane-0")
    with pytest.raises(ArtifactReadError):
        read("$.summary", "other-reader")
    handoff = hydrate_sidecar_ref(runtime.state_dir, payload["milestone_handoff_ref"]).payload
    view = milestone_readiness(runtime.state_dir, runtime.event_log.read_all(), handoff)
    assert view["task_pipeline_lifecycles"]["summary"]["task_ids"] == [task]
    assert view["task_pipeline_lifecycles"]["sha256"] == ref["sha256"]
    candidate = {"operation_id": prepared.operation_id, "source_commit": seeded["candidate_head"],
                 "candidate_event_id": handoff["candidate_event_id"], "task_map_generation": gen}
    events = runtime.event_log.read_all()
    recovery = aggregate_recovery_lifecycles(runtime.state_dir, events, run, candidate)
    assert recovery["summary"] == view["task_pipeline_lifecycles"]["summary"]
    assert aggregate_recovery_lifecycles(runtime.state_dir, events, run, {**candidate, "source_commit": "other"}) is None
    frozen = (runtime.state_dir/ref["ref"]).read_bytes()
    prepare()
    replay = hydrate_sidecar_ref(runtime.state_dir, payload["attempt_source_manifest"]).payload
    assert next(row for row in replay["sources"] if row["source_id"] == SOURCE) == ref
    assert (runtime.state_dir/ref["ref"]).read_bytes() == frozen
