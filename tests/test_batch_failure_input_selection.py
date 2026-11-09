"""Failure projection keeps every authority input without repeated ledger scans."""
from dataclasses import asdict

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime import workflow_resume as resume


def event(kind, **payload):
    return ZfEvent(type=kind, payload=payload)


def case(kind):
    frozen = event("candidate.ready", schema_version="candidate-freeze-receipt.v1",
                   candidate_head_commit="a" * 40, workflow_run_id="run")
    wrapper = event("candidate.ready", candidate_snapshot_event_id=frozen.id)
    trigger = wrapper if kind.startswith("candidate") else event("flow.discovery.requested")
    started = event("fanout.started", fanout_id="target", topology="fanout_reader",
                    trigger_event_id=trigger.id)
    aggregate = event("fanout.aggregate.completed", fanout_id="target", status="failed",
                      failure_event="custom.reader.blocked", candidate_head_commit="a" * 40)
    rows = [frozen, trigger, started, aggregate]
    if kind == "custom":
        rows.append(event("custom.reader.blocked", fanout_id="target", gap_tasks=[{"task_id": "gap"}]))
    if kind == "candidate-infra":
        aggregate.payload["failure_kind"] = "infra"
    if kind == "candidate-rejected":
        aggregate.payload["failure_kind"] = "infra"
        rows.append(event("verify.custom.child.failed", fanout_id="target",
                          verification_result={"schema_version": "verification-result.v1", "verdict": "rejected"}))
    rows += [event("fanout.started", fanout_id="unrelated", trigger_event_id=frozen.id),
             event("custom.reader.blocked", fanout_id="unrelated", gap_tasks=[{"task_id": "other"}])]
    rows[2:2] = [event("worker.heartbeat", fanout_id="target") for _ in range(100)]
    return rows, aggregate


@pytest.mark.parametrize("kind", ["discovery", "custom", "candidate-infra", "candidate-rejected"])
def test_failure_inputs_preserve_full_ledger_decisions(kind):
    from zf.runtime.workflow_batch_failure_inputs import select_batch_failure_inputs

    rows, aggregate = case(kind)
    selected = select_batch_failure_inputs(rows)
    assert all(row.type != "worker.heartbeat" for row in selected)
    assert [id(row) for row in selected] == [id(row) for row in rows if row in selected]
    for helper in (resume._reader_stage_replan_owns_failed_batch,
                   resume._candidate_trigger_for_failed_fanout,
                   resume._candidate_runtime_failure_only):
        assert helper(selected, aggregate) == helper(rows, aggregate)
    trigger = resume._candidate_trigger_for_failed_fanout(rows, aggregate)
    if trigger:
        assert resume._candidate_authority_event(selected, trigger) == resume._candidate_authority_event(rows, trigger)


def test_arbitrary_trigger_refs_and_candidate_cycles_keep_order():
    from zf.runtime.workflow_batch_failure_inputs import select_batch_failure_inputs

    trigger = event("custom.trigger", opaque="preserve")
    first = event("candidate.ready")
    second = event("candidate.ready", candidate_event_id=first.id)
    first.payload["candidate_event_id"] = second.id
    started = event("fanout.started", trigger_event_id=" " + trigger.id + " ")
    rows = [trigger, first, event("worker.heartbeat"), second, started]
    selected = select_batch_failure_inputs(rows)
    assert selected == [trigger, first, second, started]
    assert resume._candidate_authority_event(selected, first) is first


def test_real_batch_entry_filters_once_and_refreshes_after_new_evidence(tmp_path, monkeypatch):
    rows, aggregate = case("candidate-infra")
    original = resume._reader_stage_replan_owns_failed_batch
    seen = []

    def observe(events, target):
        seen.append(events)
        return original(events, target)

    monkeypatch.setattr(resume, "_reader_stage_replan_owns_failed_batch", observe)
    before = resume.build_workflow_batch_resume_checkpoints(tmp_path, events=rows)
    assert before and before[0].safe_resume_action == "reemit_candidate_ready"
    assert all(event.type != "worker.heartbeat" for events in seen for event in events)
    rows.append(event("custom.reader.blocked", fanout_id="target", gap_tasks=[{"task_id": "gap"}]))
    rows.append(event("verify.custom.child.failed", fanout_id="target",
                      verification_result={"schema_version": "verification-result.v1", "verdict": "rejected"}))
    after = resume.build_workflow_batch_resume_checkpoints(tmp_path, events=rows)
    assert after == []
    assert asdict(before[0])["source_event_id"] == aggregate.id


def test_multiple_failures_share_inputs_but_preserve_ownership(tmp_path, monkeypatch):
    rows, _ = case("custom")
    rows.append(event("fanout.aggregate.completed", fanout_id="second", status="failed",
                      failure_event="different.failure", task_map_ref="map.json"))
    original = resume._candidate_trigger_for_failed_fanout
    snapshots = []

    def observe(events, target):
        snapshots.append(events)
        return original(events, target)

    monkeypatch.setattr(resume, "_candidate_trigger_for_failed_fanout", observe)
    result = resume.build_workflow_batch_resume_checkpoints(tmp_path, events=rows)
    assert [row.fanout_id for row in result] == ["second"]
    assert len(snapshots) == 2 and snapshots[0] is snapshots[1]
    assert all(row.type != "worker.heartbeat" for row in snapshots[0])
