import hashlib
import pytest

from tests.test_completed_turn_continuation import case
from zf.core.events import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.run_manager_progress import task_evidence_signals


def test_lost_delivery_result_is_reconciled_by_exact_provider_receipt(tmp_path):
    runtime, store, attempt, transport, _ = case(tmp_path)
    def accepted_then_lost(role, path, prompt, **kwargs):
        runtime.event_writer.append(ZfEvent(type="codex.hook.user_prompt_submit", origin="kernel",
            actor=role, payload={"session_id": "session-1", "turn_id": "turn-2",
                "submitted_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}))
        raise OSError("delivery result lost")
    transport.send_task = accepted_then_lost
    runtime.run_once(events=[])
    restarted = Orchestrator(runtime.state_dir, runtime.config, transport)
    restarted.run_once(events=[])
    restarted.run_once(events=[])
    events = runtime.event_log.read_all()
    receipts = [e for e in events if e.type == "worker.continuation.reconciled"]
    assert len(receipts) == 1
    assert receipts[0].payload["delivery_status"] == "observed"
    assert receipts[0].payload["receipt_event_id"]
    assert receipts[0].payload["business_completed"] is False
    assert store.get(attempt["attempt_id"])["status"] == "sent"
    signal = task_evidence_signals(events, ["T1"])["T1"]["continuation"]
    assert signal["delivery_status"] == "observed"
    assert signal["next_action"] == "observe_bound_progress"


def test_other_prompt_or_actor_cannot_resolve_unknown_delivery(tmp_path):
    runtime, _, _, transport, _ = case(tmp_path)
    prompts = []
    def lost(role, path, prompt, **kwargs):
        prompts.append(prompt)
        raise OSError("unknown")
    transport.send_task = lost
    runtime.run_once(events=[])
    for actor, prompt, origin in [("other", prompts[0], "kernel"),
                                  ("verify-1", "different", "kernel"),
                                  ("verify-1", prompts[0], "worker")]:
        runtime.event_writer.append(ZfEvent(type="codex.hook.user_prompt_submit", origin=origin,
            actor=actor, payload={"session_id": "s", "turn_id": "t",
                "submitted_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}))
    runtime.run_once(events=[])
    events = runtime.event_log.read_all()
    assert len(prompts) == 1
    assert not any(e.type == "worker.continuation.reconciled" for e in events)


def test_hook_records_digest_without_copying_prompt(tmp_path, monkeypatch):
    from tests.test_hook_recv_codex import _invoke
    from zf.core.events import EventLog

    _invoke(tmp_path, "codex.hook.user_prompt_submit", "codex", {
        "session_id": "s", "turn_id": "t", "prompt": "private prompt body",
    }, monkeypatch)
    event = next(e for e in EventLog(tmp_path / "events.jsonl").read_all()
                 if e.type == "codex.hook.user_prompt_submit")
    assert event.origin == "kernel"
    assert event.payload["submitted_prompt_sha256"] == hashlib.sha256(b"private prompt body").hexdigest()
    assert "private prompt body" not in event.to_json()


@pytest.mark.parametrize("paused", [False, True])
def test_actual_hook_cli_lost_notification_reconciles_after_controller_restart(tmp_path, paused):
    import json
    import subprocess
    import sys
    from zf.core.state.role_sessions import RoleSessionRegistry
    from zf.runtime.task_handoff import _unconfirmed_continuations

    runtime, store, attempt, transport, _ = case(tmp_path)
    registry = RoleSessionRegistry(runtime.state_dir / "role_sessions.yaml", project_root=tmp_path)
    registry.get_or_create("verify-1", backend="codex")
    session_id = "99999999-9999-9999-9999-999999999999"
    assert registry.bind_codex_session("verify-1", session_id)
    sends = []
    def accepted_then_lost(role, path, prompt, **kwargs):
        sends.append(prompt)
        result = subprocess.run([sys.executable, "-m", "zf.cli.main", "hook-recv",
            "--event", "codex.hook.user_prompt_submit", "--backend", "codex",
            "--state-dir", str(runtime.state_dir)], text=True, capture_output=True, timeout=15,
            input=json.dumps({"session_id": session_id, "turn_id": "new-turn", "prompt": prompt}))
        assert result.returncode == 0, result.stderr
        raise OSError("lost transport return after actual hook CLI")
    transport.send_task = accepted_then_lost
    runtime.run_once(events=[])
    if paused:
        runtime.event_writer.append(ZfEvent(type="dispatch.paused", actor="operator", origin="kernel"))
    restarted = Orchestrator(runtime.state_dir, runtime.config, transport)
    restarted.run_once(events=[])
    restarted.run_once(events=[])
    events = runtime.event_log.read_all()
    assert len(sends) == 1
    assert len([e for e in events if e.type == "worker.continuation.reconciled"]) == 1
    assert _unconfirmed_continuations(events, "T1", attempt["attempt_id"]) == []
    assert store.get(attempt["attempt_id"])["status"] == "sent"


def test_corrupt_ledger_never_launches_a_recovery_turn_or_rewrites_history(tmp_path):
    runtime, _, _, transport, _ = case(tmp_path)
    path = runtime.state_dir / "events.jsonl"
    original = path.read_bytes() + b'{"type":"approval.requested",broken}\n'
    path.write_bytes(original)
    runtime.run_once(events=[])
    assert not transport.sends
    assert path.read_bytes().startswith(original)
