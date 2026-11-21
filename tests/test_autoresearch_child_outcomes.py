"""Owned diagnostic exits retain the child's actual, bound stop evidence."""
import subprocess

import pytest

from zf.autoresearch.child_supervision import child_wait, run_supervised
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.state.session import SessionStore


def child(root, *, failure_class="workflow_budget_exceeded", foreign=False):
    root.mkdir(parents=True, exist_ok=True)
    (root / "zf.yaml").write_text("project:\n  name: child\n  state_dir: runtime-data\nroles: []\n")
    state = root / "runtime-data"
    SessionStore(state / "session.yaml").create(str(root))
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    writer.append(ZfEvent(type="run.goal.started", origin="kernel",
        payload={"run_id":"diagnostic-run", "objective":"diagnose exact incident"}))
    budget = writer.append(ZfEvent(type="workflow.budget.exceeded", origin="kernel",
        payload={"workflow_run_id":"foreign" if foreign else "diagnostic-run",
                 "failure_class":failure_class, "exceeded_dimensions":["wall_clock"]}))
    stopped = writer.append(ZfEvent(type="run.goal.blocked", origin="kernel",
        causation_id=budget.id, payload={**budget.payload, "origin_event_id":budget.id,
        "status":"blocked", "reason":failure_class+":wall_clock"}))
    return state, writer, stopped


def test_child_budget_stop_is_not_generic_runner_failure(tmp_path):
    state, writer, stopped = child(tmp_path)
    before = writer.event_log.path.read_bytes()
    diagnosis = child_wait(tmp_path)
    assert diagnosis["failure_class"] == "child_budget_exceeded"
    assert diagnosis["child_workflow_run_id"] == "diagnostic-run"
    assert diagnosis["child_event_id"] == stopped.id
    assert diagnosis["reason"] == "workflow_budget_exceeded:wall_clock"
    assert diagnosis["exceeded_dimensions"] == ["wall_clock"]
    assert diagnosis["reproduction_status"] == "incomplete"
    assert writer.event_log.path.read_bytes() == before


def test_foreign_child_stop_cannot_become_owned_diagnosis(tmp_path):
    child(tmp_path, foreign=True)
    assert child_wait(tmp_path) == {}


def test_resumed_child_does_not_keep_old_budget_stop(tmp_path):
    _, writer, _ = child(tmp_path)
    writer.append(ZfEvent(type="run.goal.updated", origin="kernel",
        payload={"run_id":"diagnostic-run", "status":"active"}))
    assert child_wait(tmp_path) == {}


def test_unknown_stop_preserves_cause_without_claiming_a_budget_or_product_defect(tmp_path):
    _, writer, _ = child(tmp_path, failure_class="unknown")
    checkpoint = writer.append(ZfEvent(type="worker.work_record.published", origin="kernel",
        payload={"workflow_run_id": "diagnostic-run", "work_record_ref": {"ref": "checkpoint"}}))
    stopped = writer.append(ZfEvent(type="run.goal.blocked", origin="kernel",
        payload={"workflow_run_id": "diagnostic-run", "failure_class": "workflow_budget_exceeded",
                 "origin_event_id": "missing-budget-event", "reason": "unproven stop"}))
    result = child_wait(tmp_path)
    assert result["failure_class"] == "child_run_stopped"
    assert result["child_event_id"] == stopped.id
    assert result["checkpoint_event_id"] == checkpoint.id
    assert result["diagnosis_owner"] == "run_manager"
    assert result["exceeded_dimensions"] == []
    assert "event:" + checkpoint.id in result["evidence_refs"]


def test_fast_runner_exit_still_collects_child_stop(tmp_path):
    _, _, stopped = child(tmp_path)
    class Exited:
        returncode = 1
        stdout = stderr = None
        def communicate(self, timeout): return "partial", ""
        def poll(self): return self.returncode
    result, diagnosis = run_supervised(["child"], worktree=tmp_path, timeout_s=10,
        popen=lambda *a, **k: Exited(), monotonic=lambda:0)
    assert result.returncode == 1
    assert diagnosis["child_event_id"] == stopped.id


def test_resident_preserves_child_budget_reason_and_reference(tmp_path, monkeypatch):
    from zf.autoresearch import resident
    from zf.autoresearch.loop_requests import build_loop_request_payload, LOOP_REQUESTED
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    state = tmp_path / "parent"
    log = EventLog(state / "events.jsonl")
    log.append(ZfEvent(type=LOOP_REQUESTED, actor="test", payload=
        build_loop_request_payload({"trigger_id":"child-budget"}, source_event_id="source")))
    observed = {}
    def exited(command, *, worktree, **kwargs):
        _, _, stopped = child(worktree)
        observed["event_id"] = stopped.id
        return subprocess.CompletedProcess(command, 1, "", ""), {}
    monkeypatch.setattr("zf.autoresearch.child_supervision.run_supervised", exited)
    monkeypatch.setattr("zf.autoresearch.runtime_cleanup.stop_experiment_runtime",
        lambda *a, **k: {"status":"stopped"})
    resident.run_resident_once(state_dir=state, worktree_root=tmp_path/"worktrees",
        output_root=tmp_path/"out", execute=True, env={"ZF_AUTORESEARCH_RESIDENT":"authorized"})
    failed = next(e for e in log.read_all() if e.type=="autoresearch.loop.failed")
    diagnosis = hydrate_sidecar_ref(state,failed.payload["diagnosis_ref"]).payload
    assert diagnosis["failure_class"] == "child_budget_exceeded"
    assert diagnosis["child_event_id"] == observed["event_id"]
    assert not any(e.type=="human.escalation.acknowledged" for e in log.read_all())
