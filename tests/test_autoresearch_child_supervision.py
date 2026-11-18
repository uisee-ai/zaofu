import subprocess
from types import SimpleNamespace

import pytest

from zf.autoresearch.child_supervision import child_wait, run_supervised
from zf.autoresearch.resident import _loop_runner_timeout_s
from zf.core.events import EventLog, EventWriter
from zf.core.state.session import SessionStore


@pytest.mark.parametrize("scope,waiting", [("run", True), ("task", False), ("side", False)])
def test_observes_owned_child_gate_without_answering_it(tmp_path, scope, waiting):
    state = tmp_path / "custom-state"
    (tmp_path / "zf.yaml").write_text("project:\n  name: child\n  state_dir: custom-state\nroles: []\n")
    SessionStore(state / "session.yaml").create(str(tmp_path))
    log = EventLog(state / "events.jsonl")
    EventWriter(log).emit("human.escalate", payload={
        "blocking_scope": scope, "decision_token": "plan", "reason": "uncovered mandatory goal",
    })
    before = log.path.read_bytes()
    diagnosis = child_wait(tmp_path)
    assert bool(diagnosis) == waiting
    if waiting:
        assert diagnosis["failure_class"] == "child_owner_wait"
        assert diagnosis["reproduction_status"] == "incomplete"
        assert diagnosis["pending_decisions"][0]["decision_token"] == "plan"
    assert log.path.read_bytes() == before


def test_foreign_child_state_is_not_observed(tmp_path):
    (tmp_path / "zf.yaml").write_text("project:\n  name: child\n  state_dir: ../outside\nroles: []\n")
    assert child_wait(tmp_path) == {}


@pytest.mark.parametrize("waiting", [True, False])
def test_supervision_reports_wait_before_deadline_or_exact_timeout(tmp_path, monkeypatch, waiting):
    class Process:
        returncode = None
        stdout = stderr = None
        calls = 0
        def communicate(self, timeout):
            self.calls += 1
            if self.returncode is None:
                raise subprocess.TimeoutExpired("child", timeout)
            return "partial", ""
        def poll(self):
            return self.returncode
        def terminate(self):
            self.returncode = -15
        def wait(self, timeout):
            return self.returncode
    process = Process()
    ticks = iter([0, 0, 10])
    monkeypatch.setattr("zf.autoresearch.child_supervision.child_wait", lambda _: (
        {"failure_class": "child_owner_wait", "reproduction_status": "incomplete"} if waiting else {}))
    result, diagnosis = run_supervised(["child"], worktree=tmp_path, timeout_s=10,
        popen=lambda *a, **k: process, monotonic=lambda: next(ticks))
    assert result.returncode == (75 if waiting else 124)
    assert diagnosis["failure_class"] == ("child_owner_wait" if waiting else "diagnosis_timeout")
    assert process.returncode == -15
    assert _loop_runner_timeout_s({"max_minutes": 30}) == 1800


def test_resident_persists_partial_diagnosis_and_stops_child(tmp_path, monkeypatch):
    from zf.autoresearch import resident
    from zf.autoresearch.loop_requests import build_loop_request_payload, LOOP_REQUESTED
    from zf.core.events import ZfEvent
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    state = tmp_path / "parent"
    payload = build_loop_request_payload({"trigger_id": "owner-wait"}, source_event_id="source")
    log = EventLog(state / "events.jsonl")
    log.append(ZfEvent(type=LOOP_REQUESTED, actor="test", payload=payload))
    cleaned = []
    monkeypatch.setattr("zf.autoresearch.child_supervision.run_supervised", lambda *a, **k: (
        subprocess.CompletedProcess(a[0], 75, "", ""),
        {"status": "waiting", "failure_class": "child_owner_wait", "reproduction_status": "incomplete"}))
    monkeypatch.setattr("zf.autoresearch.runtime_cleanup.stop_experiment_runtime",
        lambda path, **kw: cleaned.append(path) or {"status": "stopped"})
    resident.run_resident_once(state_dir=state, worktree_root=tmp_path / "worktrees",
        output_root=tmp_path / "out", execute=True, env={"ZF_AUTORESEARCH_RESIDENT": "authorized"})
    failed = next(e for e in log.read_all() if e.type == "autoresearch.loop.failed")
    assert failed.payload["diagnosis"]["failure_class"] == "child_owner_wait"
    assert hydrate_sidecar_ref(state, failed.payload["diagnosis_ref"]).payload == failed.payload["diagnosis"]
    assert len(cleaned) == 1
    assert not any(e.type == "human.escalation.acknowledged" for e in log.read_all())
