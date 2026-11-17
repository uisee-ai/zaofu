"""Trusted host failures need semantic diagnosis, not repeated Reader dispatch."""
from dataclasses import replace
import subprocess

import pytest

from tests.test_host_verification import prepare
from tests.test_task_host_actions import case
from tests.test_run_manager import _config
from zf.core.events import ZfEvent
from zf.runtime.host_verification import reconcile_host_verification
from zf.runtime.run_manager import build_run_manager_projection
from zf.runtime.host_probe_diagnosis import pending_host_probe_diagnoses
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json


def _failed_probe(case, monkeypatch):
    case[0].writer.default_origin = "kernel"
    service, root = prepare(case)
    monkeypatch.setattr("zf.runtime.host_verification._run_container", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 125, "", "runner rejected an argument"))
    assert reconcile_host_verification(service) == 1
    events = service.writer.event_log.read_all()
    failure = next(e for e in events if e.type == "runtime.action.attempt.completed" and e.payload.get("kind") == "verify")
    config = _config()
    config.runtime.host_verification = service.config.runtime.host_verification
    return service, root, events, failure, config


def test_actual_rm_projection_requests_bound_diagnosis_after_host_failure(case, monkeypatch):
    service, root, events, failure, config = _failed_probe(case, monkeypatch)
    # Re-dispatching the same candidate is not evidence that its runner recovered.
    events.append(ZfEvent(type="candidate.ready", origin="kernel", payload={
        "workflow_run_id": "run-1", "pdd_id": "T1", "commit": subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()}))
    projection = build_run_manager_projection(service.state_dir, events=events,
        config=config, project_root=service.project_root)
    actions = [a for a in projection["pending_actions"] if a.get("source_event_id") == failure.id]
    assert len(actions) == 1
    assert actions[0]["action"] == "diagnose-attention"
    assert actions[0]["policy_decision"]["decision"] == "needs_diagnosis"
    assert actions[0]["failure_class"] == "unknown"
    assert actions[0]["source_ref"] == failure.payload["receipt_ref"]["ref"]
    assert service.writer.event_log.read_all() == events[:-1]


@pytest.mark.parametrize("invalid", ["worker", "wrong_identity", "missing_start", "tampered_receipt", "disabled", "terminal", "new_target"])
def test_untrusted_or_obsolete_probe_does_not_open_diagnosis(case, monkeypatch, invalid):
    service, _, events, failure, config = _failed_probe(case, monkeypatch)
    if invalid == "worker":
        events = [replace(e, origin="worker") if e.id == failure.id else e for e in events]
    elif invalid == "wrong_identity":
        events = [replace(e, payload={**e.payload, "workflow_run_id": "other"}) if e.id == failure.id else e for e in events]
    elif invalid == "missing_start":
        events = [e for e in events if e.id != failure.causation_id]
    elif invalid == "tampered_receipt":
        (service.state_dir / failure.payload["receipt_ref"]["ref"]).write_text("{}")
    elif invalid == "disabled":
        config.runtime.host_verification.enabled = False
    else:
        events.append(ZfEvent(type="run.goal.completed" if invalid == "terminal" else "candidate.ready",
            origin="kernel", payload={"workflow_run_id": "run-1", "pdd_id": "T1", "commit": "d" * 40}))
    projection = build_run_manager_projection(service.state_dir, events=events,
        config=config, project_root=service.project_root)
    assert not any(a.get("source_event_id") == failure.id for a in projection["pending_actions"])


def _later_receipt(service, events, failure, *, code, origin="kernel"):
    receipt = dict(hydrate_sidecar_ref(service.state_dir, failure.payload["receipt_ref"]).payload)
    receipt.update(exit_code=code, status="completed" if code == 0 else "failed")
    start = replace(next(e for e in events if e.id == failure.causation_id), id="start-later")
    ref = write_sidecar_json(service.state_dir, "artifacts/later-probe.json", receipt,
        kind="test", schema_version="task-host-receipt.v1", created_by="test")
    completed = replace(failure, id="completed-later", causation_id=start.id, origin=origin,
        payload={**failure.payload, "receipt_ref": ref, "status": receipt["status"]})
    return [*events, start, completed]


@pytest.mark.parametrize("code,origin,retired", [(0, "kernel", True), (0, "worker", False), (125, "kernel", False)])
def test_only_trusted_success_retires_failure(case, monkeypatch, code, origin, retired):
    service, _, events, failure, config = _failed_probe(case, monkeypatch)
    later = _later_receipt(service, events, failure, code=code, origin=origin)
    actions = pending_host_probe_diagnoses(service.state_dir, later, config)
    assert len(actions) == (0 if retired else 1)
    if actions:
        assert actions[0]["source_event_id"] == failure.id


def test_failure_after_success_opens_new_request(case, monkeypatch):
    service, _, events, failure, config = _failed_probe(case, monkeypatch)
    later = _later_receipt(service, events, failure, code=0)
    start = replace(next(e for e in events if e.id == failure.causation_id), id="start-new")
    new_failure = replace(failure, id="failure-new", causation_id=start.id)
    actions = pending_host_probe_diagnoses(service.state_dir, [*later, start, new_failure], config)
    assert len(actions) == 1
    assert actions[0]["source_event_id"] == "failure-new"
