from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.config.loader import load_config

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.workflow_resume import (
    _reader_stage_replan_checkpoints,
    apply_workflow_resume,
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


@pytest.mark.parametrize(("field", "value"), [
    ("candidate_ref", "refs/heads/candidate/other"),
    ("candidate_base_commit", "b" * 40),
    ("candidate_snapshot_event_id", "missing-snapshot"),
    ("task_map_ref", "other/task-map.json"),
    ("task_map_ref", "frozen/task-map.json"),
])
def test_batch_resume_checks_frozen_identity_before_dispatch(tmp_path, field, value):
    _, rows = _history()
    rows[0].payload["task_map_ref"] = "frozen/task-map.json"
    rows[3].payload[field] = value
    log = EventLog(tmp_path / "events.jsonl")
    for event in rows:
        log.append(event)
    checkpoint, = build_workflow_batch_resume_checkpoints(tmp_path)
    dispatched = []
    result = apply_workflow_resume(
        tmp_path, load_config(Path("examples/prod/controller/prd-fanout-v3.yaml")),
        checkpoint_id=checkpoint.checkpoint_id, gate_dispatcher=dispatched.append,
    )
    emitted = [event for event in log.read_all()[len(rows):] if event.type == "candidate.ready"]
    if value == rows[0].payload.get(field):
        assert result["applied"] == 1
        assert len(emitted) == 1 and dispatched == emitted
        assert emitted[0].payload["candidate_snapshot_event_id"] == rows[0].id
    else:
        assert result["rejected"] == 1
        assert field in result["batch_results"][0]["reason"]
        assert not emitted and not dispatched


@pytest.mark.parametrize(("route", "allowed"), [
    ("kernel_retry", True),
    ("stall_retry", True),
    ("foreign_retry", False),
    ("other_receipt", False),
])
def test_batch_snapshot_reference_tracks_the_original_receipt(tmp_path, route, allowed):
    _, rows = _history()
    current, retry = rows[:2]
    checkpoint, = build_workflow_batch_resume_checkpoints(tmp_path, events=rows)
    if route == "stall_retry":
        retry = _stall_copy(current)
    elif route == "foreign_retry":
        retry = replace(retry, origin="worker")
    elif route == "other_receipt":
        retry = _freeze("old")
        rows.insert(0, retry)
    if route != "other_receipt":
        rows[1] = retry
    checkpoint = replace(checkpoint, candidate_snapshot_event_id=retry.id)
    log = EventLog(tmp_path / "events.jsonl")
    for event in rows:
        log.append(event)

    result = _apply_batch_checkpoint(EventWriter(log), checkpoint, state_dir=tmp_path, events=rows)
    assert result.applied is allowed
    if not allowed:
        assert "candidate_snapshot_event_id" in result.reason


@pytest.mark.parametrize(("legacy_missing", "tasks", "allowed"), [
    ("", ["B", "A"], True),
    ("", ["A", "C"], False),
    ("freeze_id", ["A", "B"], False),
    ("candidate_base_commit", ["A", "B"], False),
    ("completed_task_ids", ["A", "B"], False),
])
def test_batch_rechecks_legacy_receipt_and_task_membership(tmp_path, legacy_missing, tasks, allowed):
    _, rows = _history()
    rows[0] = replace(rows[0], origin="")
    rows[0].payload["completed_task_ids"] = ["A", "B"]
    checkpoint, = build_workflow_batch_resume_checkpoints(tmp_path, events=rows)
    checkpoint = replace(checkpoint, completed_task_ids=tasks)
    if legacy_missing:
        del rows[0].payload[legacy_missing]
    log = EventLog(tmp_path / "events.jsonl")
    for event in rows:
        log.append(event)

    result = _apply_batch_checkpoint(EventWriter(log), checkpoint, state_dir=tmp_path, events=rows)
    assert result.applied is allowed
    if legacy_missing:
        assert legacy_missing in result.reason
    emitted = [event for event in log.read_all()[len(rows):] if event.type == "candidate.ready"]
    assert len(emitted) == int(allowed)


@pytest.mark.parametrize("receipt_kind", ["incomplete_native", "incomplete_originless", "pre_freeze"])
def test_batch_resume_checks_history_when_freeze_id_is_absent(tmp_path, receipt_kind):
    _, rows = _history()
    native, retry = rows[:2]
    for event in (native, retry):
        del event.payload["freeze_id"]
    if receipt_kind == "pre_freeze":
        del native.payload["schema_version"]
        rows.insert(0, _freeze("unrelated", run="another-run"))
    else:
        if receipt_kind == "incomplete_originless":
            rows[0] = replace(native, origin="")
        rows[3].payload["candidate_base_commit"] = "b" * 40
    log = EventLog(tmp_path / "events.jsonl")
    for event in rows:
        log.append(event)
    checkpoint, = build_workflow_batch_resume_checkpoints(tmp_path)
    assert not checkpoint.freeze_id and checkpoint.candidate_snapshot_event_id == native.id
    dispatched = []
    result = apply_workflow_resume(
        tmp_path, load_config(Path("examples/prod/controller/prd-fanout-v3.yaml")),
        checkpoint_id=checkpoint.checkpoint_id, gate_dispatcher=dispatched.append,
    )
    if receipt_kind == "pre_freeze":
        assert result["applied"] == 1 and len(dispatched) == 1
    else:
        assert result["rejected"] == 1 and "freeze_id" in result["batch_results"][0]["reason"]
        assert not dispatched
        assert not any(e.type == "candidate.ready" for e in log.read_all()[len(rows):])


@pytest.mark.parametrize("route", ["kernel", "worker", "originless", "other_run", "wrong_head", "wrong_tasks"])
def test_batch_resume_traces_kernel_retries_to_exact_originless_freeze(tmp_path, route):
    _, rows = _history()
    original, retry = rows[:2]
    rows[0] = replace(original, origin="")
    if route in {"worker", "originless"}:
        rows[1] = replace(retry, origin="worker" if route == "worker" else "")
    elif route == "other_run":
        retry.payload["workflow_run_id"] = "other"
    elif route == "wrong_head":
        retry.payload["candidate_head_commit"] = "b" * 40
    elif route == "wrong_tasks":
        retry.payload["completed_task_ids"] = ["other"]
    wrapper = ZfEvent(type="candidate.ready", origin="kernel", correlation_id="run",
                      payload={"workflow_run_id": "run", "candidate_snapshot_event_id": retry.id})
    rows[3].payload["candidate_snapshot_event_id"] = wrapper.id
    rows.insert(2, wrapper)
    log = EventLog(tmp_path / "events.jsonl")
    for event in rows:
        log.append(event)
    checkpoint, = build_workflow_batch_resume_checkpoints(tmp_path)
    assert checkpoint.candidate_snapshot_event_id == wrapper.id
    dispatched = []
    result = apply_workflow_resume(
        tmp_path, load_config(Path("examples/prod/controller/prd-fanout-v3.yaml")),
        checkpoint_id=checkpoint.checkpoint_id, gate_dispatcher=dispatched.append,
    )
    if route == "kernel":
        assert result["applied"] == 1 and len(dispatched) == 1
    else:
        assert result["rejected"] == 1
        assert "candidate_snapshot_event_id" in result["batch_results"][0]["reason"]
        assert not dispatched


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
