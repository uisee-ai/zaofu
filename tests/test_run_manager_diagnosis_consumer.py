"""Run Manager diagnosis must have a live consumer and a bound response."""
import json
import subprocess
from datetime import datetime, timezone

import pytest

from tests.test_run_manager_resident import _resident_config, _write_supervisor_attention
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.resident_request_obligations import resident_request_obligations
from zf.runtime.run_manager import build_run_manager_projection, run_manager_tick


def _setup(tmp_path, monkeypatch, *, route="run_manager_recovery", task_id=""):
    state = tmp_path / ".state"
    briefing = state / "briefings/resident.md"
    briefing.parent.mkdir(parents=True)
    briefing.write_text("initial briefing")
    _write_supervisor_attention(state)
    path = state / "projections/supervisor/snapshot.json"
    snapshot = json.loads(path.read_text())
    attention = snapshot["attention_items"][0]
    attention.update(suggested_route=route, task_id=task_id,
                     expected_output=["diagnosis_report", "recommended_run_manager_action", "evidence_refs", "expected_downstream_event"])
    path.write_text(json.dumps(snapshot))
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    writer.emit("run.manager.resident.spawned", payload={
        "ready": True, "session_mode": "dedicated", "tmux_session": "zf-rm",
    })
    writer.emit("run.manager.resident.prompted", payload={
        "prompted": True, "briefing_path": str(briefing),
    })
    writer.emit("run.manager.agent.observation", actor="run-manager", payload={"status": "watching"})
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["tmux", "display-message", "-p"]:
            return subprocess.CompletedProcess(args, 0, f"%9\tclaude\t{tmp_path}\t0\n", "")
        if args[:2] == ["tmux", "send-keys"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 1, "", "not a live host call")
    monkeypatch.setattr("zf.runtime.run_manager.subprocess.run", fake_run)
    monkeypatch.setattr("zf.runtime.run_manager.time.sleep", lambda _: None)
    config = _resident_config(session_mode="dedicated")
    return state, log, writer, config, calls


def _tick(state, log, writer, config, tmp_path):
    return run_manager_tick(state_dir=state, writer=writer, config=config,
                            project_root=tmp_path, event_log=log, spawn_repairs=False)


@pytest.mark.parametrize("task_id", ["", "T1"])
@pytest.mark.parametrize("old_autoresearch", [False, True])
def test_resident_consumes_owned_diagnosis_once_and_requires_response(
    tmp_path, monkeypatch, task_id, old_autoresearch,
):
    state, log, writer, cfg, calls = _setup(tmp_path, monkeypatch, task_id=task_id)
    if old_autoresearch:
        writer.emit("run.manager.autoresearch.requested", payload={
            "request_id": "unrelated-old-request", "checkpoint_id": "other-fault",
        })
    for _ in range(2):
        _tick(state, log, writer, cfg, tmp_path)
    events = log.read_all()
    prompts = [e for e in events if e.type == "run.manager.resident.prompted" and e.payload.get("reprompt")]
    assert len(prompts) == 1
    request = prompts[0].payload
    assert request["request_id"]
    assert request["expected_output"] == ["run.manager.agent.recommendation"]
    assert len([e for e in events if e.type == "run.manager.autoresearch.requested"]) == int(old_autoresearch)
    sent = next(c[4] for c in calls if c[:2] == ["tmux", "send-keys"] and c[4] != "Enter")
    assert "diagnosis_report" in sent and "recommended_run_manager_action" in sent
    assert "zf emit run.manager.agent.recommendation" in sent
    assert request["request_id"] in sent
    contexts = list((state / "diagnostics/run-manager").glob("*/context.json"))
    assert len(contexts) == 1
    assert json.loads(contexts[0].read_text())["action"]["diagnosis_expected_output"] == ["diagnosis_report", "recommended_run_manager_action", "evidence_refs", "expected_downstream_event"]
    assert list((state / "diagnostics/run-manager").glob("*/read_set.json"))
    assert any(e.type == "run.manager.action.effect.pending" for e in events)
    assert not any(e.type in {"task.done", "run.manager.action.effect.passed"} for e in events)
    from zf.runtime.recovery_case import build_recovery_case_projection
    assert all(c["status"] != "resolved" for c in build_recovery_case_projection(events)["cases"])
    def obligations():
        return resident_request_obligations(log.read_all(), now=datetime.now(timezone.utc), timeout_seconds=600)
    assert len(obligations()) == 1
    writer.emit("run.manager.agent.observation", actor="run-manager", payload={"request_id": request["request_id"]})
    writer.emit("run.manager.agent.recommendation", actor="run-manager", payload={"request_id": "wrong"})
    assert len(obligations()) == 1
    writer.emit("run.manager.agent.recommendation", actor="run-manager", payload={"request_id": request["request_id"]})
    assert not obligations()


def test_waiting_resident_does_not_receive_overlapping_diagnosis(tmp_path, monkeypatch):
    state, log, writer, cfg, calls = _setup(tmp_path, monkeypatch)
    writer.emit("run.manager.resident.prompted", payload={
        "prompted": True, "request_id": "pending-other",
        "briefing_path": str(state / "briefings/resident.md"),
        "expected_output": ["run.manager.agent.recommendation"],
    })
    _tick(state, log, writer, cfg, tmp_path)
    projection = build_run_manager_projection(state, events=log.read_all(), config=cfg, project_root=tmp_path)
    action = next(a for a in projection["pending_actions"] if a["action"] == "diagnose-attention")
    assert action["recovery_admission"]["status"] == "blocked"
    assert action["diagnosis_wait"]["request_id"] == "pending-other"
    assert action["diagnosis_wait"]["timeout_seconds"] == 600
    assert not any(c[:2] == ["tmux", "send-keys"] for c in calls)
    assert not any(e.type in {"run.manager.action.blocked", "run.manager.autoresearch.requested"} for e in log.read_all())
    writer.emit("run.manager.agent.recommendation", actor="run-manager", payload={
        "request_id": "pending-other", "recommended_route": "wait", "summary": "answered",
    })
    _tick(state, log, writer, cfg, tmp_path)
    assert len([e for e in log.read_all() if e.type == "run.manager.resident.prompted" and e.payload.get("reprompt")]) == 1


def test_explicit_autoresearch_route_is_preserved(tmp_path, monkeypatch):
    state, log, writer, cfg, calls = _setup(tmp_path, monkeypatch, route="autoresearch_trigger")
    result = _tick(state, log, writer, cfg, tmp_path)
    assert result.autoresearch_requested == 1
    assert not any(c[:2] == ["tmux", "send-keys"] for c in calls)


def test_failed_resident_delivery_keeps_fault_open_and_records_fallback(tmp_path, monkeypatch):
    state, log, writer, cfg, calls = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr("zf.runtime.run_manager.subprocess.run",
                        lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "pane absent"))
    result = _tick(state, log, writer, cfg, tmp_path)
    assert result.actions_blocked == 1
    assert result.autoresearch_requested == 1
    assert any(e.type == "run.manager.action.blocked" and "pane absent" in e.payload["reason"] for e in log.read_all())
    assert not any(e.type == "run.manager.action.verify.passed" for e in log.read_all())


def test_paused_run_does_not_prompt_resident(tmp_path, monkeypatch):
    state, log, writer, cfg, calls = _setup(tmp_path, monkeypatch)
    writer.emit("dispatch.paused", actor="operator")
    _tick(state, log, writer, cfg, tmp_path)
    assert not any(c[:2] == ["tmux", "send-keys"] for c in calls)
    assert not any(e.type == "run.manager.autoresearch.requested" for e in log.read_all())


def test_codex_resident_uses_bracketed_paste_for_complete_prompt(tmp_path, monkeypatch):
    state, log, writer, cfg, calls = _setup(tmp_path, monkeypatch)
    cfg.runtime.run_manager.backend = "codex"
    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["tmux", "display-message", "-p"]:
            return subprocess.CompletedProcess(args, 0, f"%9\tcodex\t{tmp_path}\t0\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr("zf.runtime.run_manager.subprocess.run", fake_run)
    result = _tick(state, log, writer, cfg, tmp_path)
    assert result.actions_applied == 1
    buffered = [c for c in calls if c[:2] == ["tmux", "set-buffer"]]
    assert len(buffered) == 1
    prompt = buffered[0][-1]
    assert len(prompt.encode()) > 8192
    assert "Current Focus" in prompt and "diagnosis_expected_output" in prompt
    paste = next(c for c in calls if c[:2] == ["tmux", "paste-buffer"])
    assert "-p" in paste and paste[-1] == "zf-rm:run-manager"
    inputs = [c for c in calls if c[:2] == ["tmux", "send-keys"]]
    assert [c[-1] for c in inputs] == ["C-u", "Enter"]
    assert any(c[:2] == ["tmux", "delete-buffer"] for c in calls)


def test_overdue_run_diagnosis_reuses_bound_context_without_task_id(tmp_path, monkeypatch):
    from datetime import timedelta
    state, log, writer, cfg, calls = _setup(tmp_path, monkeypatch)
    _tick(state, log, writer, cfg, tmp_path)
    first = next(e for e in log.read_all() if e.type == "run.manager.resident.prompted" and e.payload.get("reprompt"))
    later = datetime.now(timezone.utc) + timedelta(seconds=700)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return later
    monkeypatch.setattr("zf.runtime.run_manager.datetime", Clock)
    result = _tick(state, log, writer, cfg, tmp_path)
    assert result.actions_applied == 1
    prompts = [e for e in log.read_all() if e.type == "run.manager.resident.prompted" and e.payload.get("reprompt")]
    assert len(prompts) == 2
    assert prompts[1].payload["request_id"] == first.payload["request_id"]
    assert prompts[1].payload["diagnosis_context"] == first.payload["diagnosis_context"]
    assert len(list((state / "diagnostics/run-manager").glob("*/context.json"))) == 1
    repeated = [c[4] for c in calls if c[:2] == ["tmux", "send-keys"] and c[4] != "Enter"][-1]
    assert "diagnosis_report" in repeated and "recommended_run_manager_action" in repeated
    assert len(resident_request_obligations(log.read_all(), now=later, timeout_seconds=600)) == 1
