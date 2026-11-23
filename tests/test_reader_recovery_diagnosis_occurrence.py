"""Partial reader recovery must not hide a newer aggregate failure occurrence."""
from zf.core.config.schema import ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.runtime.run_manager import build_run_manager_projection, _pending_semantic_event_actions


def test_new_reader_failure_remains_dispatchable_after_prior_diagnosis(tmp_path):
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    config = ZfConfig()
    config.runtime.run_manager.resident_agent.enabled = True
    writer.emit("run.started", payload={"workflow_run_id": "run-1"})
    payload = {"workflow_run_id": "run-1", "trace_id": "run-1", "pdd_id": "P1",
               "stage_id": "prd-scan", "fanout_id": "scan-1", "failure_kind": "infra",
               "failed_children": ["product", "tech"], "reason": "both failed"}
    first = writer.emit("prd.scan.failed", payload=payload)
    first_action = _pending_semantic_event_actions(writer.event_log.read_all())[0]
    writer.emit("run.manager.resident.prompted", payload={
        "source_action_checkpoint_id": first_action["checkpoint_id"], "request_id": "diag-first"})
    writer.emit("run.manager.action.applied", payload={**first_action, "status": "applied"})
    second = writer.emit("prd.scan.failed", payload={**payload,
        "failed_children": ["tech"], "reason": "only tech remains failed"})
    projection = build_run_manager_projection(tmp_path, events=writer.event_log.read_all(), config=config)
    actions = [a for a in projection["pending_actions"] if a.get("failure_class") == "prd_scan_failed"]
    assert len(actions) == 1, actions
    assert actions[0]["source_event_ids"] == [second.id]
    assert actions[0]["checkpoint_id"] != first_action["checkpoint_id"]
    assert projection["continuation"]["next_operation"]["checkpoint_id"] == actions[0]["checkpoint_id"]
    writer.emit("run.manager.resident.prompted", payload={
        "source_action_checkpoint_id": actions[0]["checkpoint_id"], "request_id": "diag-second"})
    assert not _pending_semantic_event_actions(writer.event_log.read_all())
    assert sum(e.id == first.id for e in writer.event_log.read_all()) == 1


def test_latest_reader_failure_replaces_unhandled_old_occurrence(tmp_path):
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    payload = {"workflow_run_id": "run-1", "pdd_id": "P1", "stage_id": "prd-scan",
               "fanout_id": "scan-1", "failure_kind": "infra", "failed_children": ["tech"]}
    writer.emit("prd.scan.failed", payload={**payload, "reason": "old transport failure"})
    current = writer.emit("prd.scan.failed", payload={**payload, "reason": "new transport failure"})
    actions = _pending_semantic_event_actions(writer.event_log.read_all())
    assert len(actions) == 1
    assert actions[0]["source_event_ids"] == [current.id]
