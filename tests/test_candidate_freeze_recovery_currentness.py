from dataclasses import replace
from types import SimpleNamespace

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.workflow_resume import (
    _reader_stage_replan_checkpoints,
    build_workflow_batch_resume_checkpoints,
)
from zf.runtime.workflow_resume_apply import _apply_batch_checkpoint, _apply_stage_replan


def _freeze(freeze_id, head="a" * 40, run="run"):
    return ZfEvent(type="candidate.ready", origin="kernel", correlation_id=run, payload={
        "workflow_run_id": run, "pdd_id": "PDD",
        "schema_version": "candidate-freeze-receipt.v1", "freeze_id": freeze_id,
        "candidate_ref": "refs/heads/candidate/PDD", "candidate_head_commit": head,
        "candidate_base_commit": "0" * 40, "completed_task_ids": ["A"],
    })


def _history():
    source = _freeze("old")
    retry = ZfEvent(type="candidate.ready", origin="kernel", correlation_id="run", payload={
        **source.payload, "candidate_snapshot_event_id": source.id,
    })
    del retry.payload["schema_version"]
    stage = SimpleNamespace(
        id="prd-lanes-verify", trigger="candidate.ready", attempt_domain="verify",
        topology="fanout_reader", failure_event="",
        aggregate=SimpleNamespace(failure_event="test.failed"),
    )
    started = ZfEvent(type="fanout.started", origin="kernel", correlation_id="run", payload={
        "fanout_id": "f1", "stage_id": stage.id, "trigger_event_id": retry.id,
        "workflow_run_id": "run", "pdd_id": "PDD",
    })
    aggregate = ZfEvent(type="fanout.aggregate.completed", origin="kernel", correlation_id="run", payload={
        "fanout_id": "f1", "stage_id": stage.id, "status": "failed",
        "failure_event": "test.failed", "failed_children": ["verify-lane-0"],
        "findings": [{"category": "runtime_failure", "child_id": "verify-lane-0",
                      "message": "provider exited"}],
    })
    failure = ZfEvent(type="test.failed", origin="kernel", correlation_id="run", payload={
        "fanout_id": "f1", "stage_id": stage.id, "workflow_run_id": "run", "pdd_id": "PDD",
        "candidate_event_id": retry.id, "reason": "needs rework",
    })
    config = SimpleNamespace(workflow=SimpleNamespace(stages=[stage]))
    return config, [source, retry, started, aggregate, failure]


@pytest.mark.parametrize("head", ["a" * 40, "b" * 40])
def test_superseded_freeze_blocks_stage_and_late_aggregate_checkpoints(tmp_path, head):
    config, rows = _history()
    baseline = [*rows[:3], _freeze("another", run="other-run"),
                replace(_freeze("old"), actor="zf-stall-redispatch"),
                replace(_freeze("untrusted"), origin="worker"), *rows[3:]]
    assert _reader_stage_replan_checkpoints(baseline, [], config, state_dir=tmp_path)
    assert build_workflow_batch_resume_checkpoints(tmp_path, events=baseline)

    # The batch failed *after* the new freeze and historical retry wrappers can
    # arrive even later. Neither occurrence gives the old contract new authority.
    new = _freeze("new", head)
    old_retry = replace(_freeze("old"), payload={**rows[0].payload, "rework_of": "old-failure"})
    stale = [*baseline[:3], new, old_retry, _stall_copy(rows[0]), *rows[3:]]
    assert _reader_stage_replan_checkpoints(stale, [], config, state_dir=tmp_path) == []
    assert build_workflow_batch_resume_checkpoints(tmp_path, events=stale) == []


def test_apply_rechecks_native_freeze_after_planning(tmp_path):
    config, rows = _history()
    stage, = _reader_stage_replan_checkpoints(rows, [], config, state_dir=tmp_path)
    batch, = build_workflow_batch_resume_checkpoints(tmp_path, events=rows)
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    current = _freeze("new")
    for event in [*rows, current]:
        log.append(event)

    stage_result = _apply_stage_replan(writer, stage, [], config=config, events=rows, state_dir=tmp_path)
    batch_result = _apply_batch_checkpoint(writer, batch, state_dir=tmp_path, events=rows)
    assert not stage_result.applied and current.id in stage_result.reason
    assert not batch_result.applied and current.id in batch_result.reason
    assert [event for event in log.read_all()[len(rows) + 1:] if event.type == "candidate.ready"] == []


def _stall_copy(source, **changes):
    return ZfEvent(type="candidate.ready", origin="kernel", actor="zf-stall-redispatch",
                   correlation_id=source.correlation_id, payload={
                       **source.payload, "original_trigger_event_id": source.id,
                       "redispatch_attempt": 1, "redispatch_fingerprint": "stall", **changes,
                   })


@pytest.mark.parametrize("bad_copy", ["stale", "missing", "tampered", "untrusted"])
def test_latest_freeze_preserves_only_exact_current_stall_copy(bad_copy):
    from zf.runtime.call_result_authority import CallResultAuthorityMixin
    from zf.runtime.candidate_success_currentness import _latest_frozen_candidate
    from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload

    old, new = _freeze("old"), _freeze("new")
    valid = _stall_copy(new)
    bad = {
        "stale": _stall_copy(old),
        "missing": _stall_copy(new, original_trigger_event_id="missing"),
        "tampered": _stall_copy(new, candidate_head_commit="b" * 40),
        "untrusted": replace(_stall_copy(new), origin="worker"),
    }[bad_copy]
    rows = [old, new, valid, bad]
    authority = SimpleNamespace(event_log=SimpleNamespace(read_all=lambda: rows))
    assert frozen_candidate_for_payload(rows, "run", payload={}) == valid
    assert _latest_frozen_candidate(rows, "run") == valid
    assert CallResultAuthorityMixin._latest_frozen_candidate_for_run(authority, "run") == valid
    assert frozen_candidate_for_payload(rows, "run", payload={"candidate_event_id": valid.id}) == valid
    with pytest.raises(ValueError, match="Stall retry"):
        frozen_candidate_for_payload(rows, "run", payload={"candidate_event_id": bad.id})


def test_equivalent_native_reannouncement_preserves_prior_stall_source():
    from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload

    source = _freeze("same")
    source.payload["freeze_receipt_digest"] = "d" * 64
    reannounced = _freeze("same")
    reannounced.payload["freeze_receipt_digest"] = source.payload["freeze_receipt_digest"]
    valid = _stall_copy(source)
    assert frozen_candidate_for_payload([source, reannounced, valid], "run", payload={}) == valid


def test_direct_reader_failure_keeps_its_explicit_freeze_before_fanout(tmp_path):
    config, rows = _history()
    failure = replace(rows[-1], payload={
        "workflow_run_id": "run", "pdd_id": "PDD", "candidate_event_id": rows[0].id,
        "reason": "needs rework",
    })
    assert _reader_stage_replan_checkpoints([rows[0], failure], [], config, state_dir=tmp_path)
    assert _reader_stage_replan_checkpoints(
        [rows[0], _freeze("new"), failure], [], config, state_dir=tmp_path,
    ) == []
