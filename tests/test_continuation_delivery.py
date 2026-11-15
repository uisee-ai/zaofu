import hashlib

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
