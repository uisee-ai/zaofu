"""Exercise original IssueFlow writer receipts through the batch resume entry."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_resume import WorkflowBatchResumeCheckpoint, _candidate_reverify_checkpoint_recovered, build_workflow_batch_resume_checkpoints
from zf.runtime.workflow_resume_apply import _apply_batch_checkpoint, apply_workflow_resume


def _case():
    identity = {"workflow_run_id": "run-r", "task_map_generation": "map-g",
                "candidate_ref": "candidate/r", "candidate_head_commit": "a" * 40,
                "candidate_base_commit": "b" * 40, "task_map_ref": "maps/r.json",
                "fanout_id": "writer", "stage_id": "issue-impl", "pdd_id": "goal",
                "integration_attempt_id": "integrate-17", "completed_task_ids": ["T-17"],
                "workflow_generation": "wf-g", "request_revision": 2, "status": "completed"}

    def event(eid, kind, parent="", **payload):
        return ZfEvent(id=eid, type=kind, origin="kernel", actor="zf-cli", correlation_id="run-r",
                       causation_id=parent or None, payload={"workflow_run_id": "run-r", **payload})

    producer = event("integration", "fanout.aggregate.completed", **identity)
    source = event("candidate", "candidate.ready", producer.id, **identity)
    start = event("dispatch", "fanout.started", source.id, fanout_id="readers", stage_id="issue-verify",
                  trigger_event_id=source.id)
    failed = event("runtime-failure", "fanout.aggregate.completed", fanout_id="readers",
                   stage_id="issue-verify", status="failed")
    bad_retry = event("issued", "candidate.ready", failed.id, candidate_snapshot_event_id=source.id,
                      source="workflow_resume_batch", resume_checkpoint_ref="cp", idempotency_key="cp")
    rejected = event("reject", "task.fanout.rejected", bad_retry.id, stage_id="issue-verify",
                     reason="invalid_candidate_reader_assignment", error="Candidate Reader retrigger lacks its frozen candidate")
    checkpoint = WorkflowBatchResumeCheckpoint(
        checkpoint_id="cp", source_event_id=failed.id, source_event_type=failed.type,
        safe_resume_action="reemit_candidate_ready", blocking_event_id=failed.id,
        candidate_snapshot_event_id=source.id, workflow_run_id="run-r", trace_id="run-r",
        pdd_id="goal", stage_id="issue-verify", fanout_id="readers", upstream_fanout_id="readers",
        flow_kind="issue", candidate_ref=identity["candidate_ref"], task_map_ref="maps/r.json",
        candidate_head_commit="a"*40, candidate_base_commit="b"*40, completed_task_ids=["T-17"],
    )
    return [producer, source, start, failed, bad_retry, rejected], checkpoint


def test_failed_legacy_resume_reuses_its_producer_once(tmp_path):
    rows, _ = _case()
    rows[3].payload.update(completed_task_ids=["T-17"], failed_children=["verify-17"],
                           candidate_ref="candidate/r", candidate_head_commit="a" * 40,
                           findings=[{"category": "runtime_failure", "child_id": "verify-17"}])
    cp, = build_workflow_batch_resume_checkpoints(tmp_path, events=rows[:4])
    for key in ("resume_checkpoint_ref", "idempotency_key"):
        rows[4].payload[key] = cp.checkpoint_id
    assert not _candidate_reverify_checkpoint_recovered(rows, cp)
    log = EventLog(tmp_path / "events.jsonl")
    for event in rows:
        log.append(event)
    dispatch = []
    config = load_config(Path("examples/prod/controller/prd-fanout-v3.yaml"))
    args = {"checkpoint_id": cp.checkpoint_id, "gate_dispatcher": dispatch.append}
    result = apply_workflow_resume(tmp_path, config, **args)
    assert result["applied"] == 1 and len(dispatch) == 1
    handoff = dispatch[0]
    assert handoff.payload["candidate_event_id"] == "candidate"
    assert not handoff.payload["candidate_snapshot_event_id"]
    assert handoff.payload["fanout_id"] == handoff.payload["operator_recovery"]["upstream_fanout_id"] == "writer"
    for key in ("workflow_run_id", "workflow_generation", "task_map_generation", "candidate_head_commit",
                "completed_task_ids", "integration_attempt_id", "request_revision"):
        assert handoff.payload[key] == rows[1].payload[key]
    assert _candidate_reverify_checkpoint_recovered(log.read_all(), cp)
    assert apply_workflow_resume(tmp_path, config, **args)["applied"] == 0
    assert dispatch == [handoff]


@pytest.mark.parametrize("fault", ["wrong_run", "wrong_commit", "wrong_producer", "foreign_reader",
                                    "different_tasks", "new_integration", "new_map"])
def test_writer_replay_rejects_divergent_bindings(tmp_path, fault):
    rows, cp = _case()
    rows = rows[:4]
    if fault == "wrong_run":
        rows[1].payload["workflow_run_id"] = "other"
    elif fault == "wrong_commit":
        cp = replace(cp, candidate_head_commit="c" * 40)
    elif fault == "wrong_producer":
        rows[0].origin = "agent"
    elif fault == "foreign_reader":
        rows[2].payload["trigger_event_id"] = "other"
    elif fault == "different_tasks":
        cp = replace(cp, completed_task_ids=["T-else"])
    elif fault == "new_integration":
        successor = deepcopy(rows[1])
        successor.id, successor.payload["integration_attempt_id"] = "next", "next-integration"
        rows.append(successor)
    else:
        rows.append(ZfEvent(id="next", type="task_map.admitted", origin="kernel", actor="zf-cli",
                            payload={"workflow_run_id": "run-r", "task_map_generation": "map-next"}))
    _assert_rejected(tmp_path, rows, cp)


def _apply(tmp_path, rows, cp):
    log = EventLog(tmp_path / "events.jsonl")
    for event in rows:
        log.append(event)
    dispatch = []
    result = _apply_batch_checkpoint(EventWriter(log), cp, state_dir=tmp_path, events=rows, gate_dispatcher=dispatch.append)
    emitted = [e for e in log.read_all()[len(rows):] if e.type == "candidate.ready"]
    return result.applied, dispatch, emitted


def _assert_rejected(tmp_path, rows, cp):
    assert _apply(tmp_path, rows, cp) == (False, [], [])


def test_another_run_using_the_same_candidate_ref_does_not_supersede_this_replay(tmp_path):
    rows, cp = _case()
    foreign = deepcopy(rows[1])
    foreign.id, foreign.correlation_id = "foreign-candidate", "run-else"
    foreign.payload.update(workflow_run_id="run-else", candidate_head_commit="c" * 40)
    applied, dispatch, emitted = _apply(tmp_path, [*rows[:4], foreign], cp)
    assert applied and [e.id for e in dispatch] == [e.id for e in emitted]
    assert [e.payload["candidate_event_id"] for e in emitted] == ["candidate"]


@pytest.mark.parametrize("fault", ["other_run", "future_start", "future_candidate", "future_producer"])
def test_failure_binds_to_the_receipts_already_seen_on_its_own_run(tmp_path, fault):
    rows, cp = _case()
    producer, candidate, start, failed = rows[:4]
    if fault == "other_run":
        failed.correlation_id = failed.payload["workflow_run_id"] = "run-else"
        rows = [producer, candidate, start, failed]
    elif fault == "future_start":
        rows = [producer, candidate, failed, start]
    elif fault == "future_candidate":
        rows = [producer, start, failed, candidate]
    else:
        rows = [candidate, start, failed, producer]
    _assert_rejected(tmp_path, rows, cp)


@pytest.mark.parametrize("changed", [None, "workflow_run_id", "request_revision", "workflow_generation", "candidate_head_commit"])
def test_recovered_reader_wrapper_must_preserve_its_native_input(tmp_path, changed):
    rows, cp = _case()
    producer, source, first_start, first_failure = rows[:4]
    recovery = deepcopy(source)
    recovery.id, recovery.causation_id = "second-candidate", first_failure.id
    recovery.payload.update(source="workflow_resume_batch", candidate_event_id=source.id,
                            candidate_snapshot_event_id="", resume_checkpoint_ref=cp.checkpoint_id,
                            idempotency_key=cp.checkpoint_id, rework_of=first_failure.id,
                            operator_recovery={"source":"workflow_resume_batch", "upstream_fanout_id":"writer"})
    if changed == "workflow_run_id":
        recovery.correlation_id = recovery.payload[changed] = "run-else"
    elif changed:
        recovery.payload[changed] = 3 if changed == "request_revision" else "other"
    start, failed = deepcopy(first_start), deepcopy(first_failure)
    start.id, failed.id = "second-start", "second-failure"
    start.payload.update(fanout_id="second-readers", trigger_event_id=recovery.id)
    start.causation_id = recovery.id
    failed.payload["fanout_id"] = "second-readers"
    cp = replace(cp, checkpoint_id="cp-2", source_event_id=failed.id, blocking_event_id=failed.id,
                 fanout_id="second-readers", candidate_snapshot_event_id=recovery.id)
    replay = [producer, source, first_start, first_failure, recovery, start, failed]
    if changed:
        _assert_rejected(tmp_path, replay, cp)
    else:
        applied, dispatch, emitted = _apply(tmp_path, replay, cp)
        assert applied and [e.id for e in dispatch] == [e.id for e in emitted]
        assert [e.payload["candidate_event_id"] for e in emitted] == [source.id]


@pytest.mark.parametrize("event_type", ["fanout.started", "run.completed", "test.passed"])
@pytest.mark.parametrize("run", ["run-r", "other-run"])
def test_recovery_progress_only_consumes_its_bound_run(event_type, run):
    rows, cp = _case()
    old_retry = rows[4]
    assert not _candidate_reverify_checkpoint_recovered(rows, cp)
    payload = {"workflow_run_id": run}
    if event_type == "fanout.started":
        payload.update(trigger_event_id=old_retry.id, stage_id=cp.stage_id, fanout_id="new-readers")
    else:
        payload["resume_checkpoint_ref"] = cp.checkpoint_id
    progress = ZfEvent(id="progress", type=event_type, origin="kernel", actor="zf-cli",
                       correlation_id=run, payload=payload)
    assert _candidate_reverify_checkpoint_recovered(rows + [progress], cp) == (run == cp.workflow_run_id)
