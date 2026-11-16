from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.provider_turn_liveness import active_codex_turn


def test_loop_stop_closes_prior_codex_turn_generation(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.append(ZfEvent(
        type="codex.hook.user_prompt_submit",
        actor="dev",
        payload={"session_id": "session-old", "turn_id": "turn-old"},
    ))

    assert active_codex_turn(log, state_dir, "dev") is not None

    writer.append(ZfEvent(type="loop.stopped", actor="zf-cli"))

    assert active_codex_turn(log, state_dir, "dev") is None

    writer.append(ZfEvent(
        type="codex.hook.user_prompt_submit",
        actor="dev",
        payload={"session_id": "session-new", "turn_id": "turn-new"},
    ))

    active = active_codex_turn(log, state_dir, "dev")
    assert active is not None
    assert active["turn_id"] == "turn-new"


def test_worker_respawn_closes_prior_codex_turn_generation(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.append(ZfEvent(
        type="codex.hook.user_prompt_submit",
        actor="dev",
        payload={"session_id": "session-old", "turn_id": "turn-old"},
    ))

    writer.append(ZfEvent(
        type="worker.respawned",
        actor="dev",
        payload={"instance_id": "dev", "reason": "manual_restart"},
    ))

    assert active_codex_turn(log, state_dir, "dev") is None

    writer.append(ZfEvent(
        type="codex.hook.user_prompt_submit",
        actor="dev",
        payload={"session_id": "session-new", "turn_id": "turn-new"},
    ))

    active = active_codex_turn(log, state_dir, "dev")
    assert active is not None
    assert active["turn_id"] == "turn-new"


def test_worker_launch_closes_prior_codex_turn_generation(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.append(ZfEvent(
        type="codex.hook.user_prompt_submit",
        actor="dev",
        payload={"session_id": "session-resumed", "turn_id": "turn-old"},
    ))

    writer.append(ZfEvent(
        type="worker.launch_artifact.written",
        actor="zf-cli",
        payload={
            "instance_id": "dev",
            "backend": "codex",
            "launch_attempt": 2,
            "is_resume": True,
        },
    ))

    assert active_codex_turn(log, state_dir, "dev") is None

    writer.append(ZfEvent(
        type="codex.hook.user_prompt_submit",
        actor="dev",
        payload={"session_id": "session-resumed", "turn_id": "turn-new"},
    ))

    active = active_codex_turn(log, state_dir, "dev")
    assert active is not None
    assert active["turn_id"] == "turn-new"


def test_on_demand_dormant_closes_prior_codex_turn_generation(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.append(ZfEvent(
        type="codex.hook.user_prompt_submit",
        actor="reader",
        payload={"session_id": "session-old", "turn_id": "turn-old"},
    ))

    writer.append(ZfEvent(
        type="role.lifecycle.dormant",
        actor="orchestrator",
        payload={
            "instance_id": "reader",
            "from": "active",
            "to": "dormant",
            "preserve_session": True,
        },
    ))

    assert active_codex_turn(log, state_dir, "reader") is None

    writer.append(ZfEvent(
        type="codex.hook.user_prompt_submit",
        actor="reader",
        payload={"session_id": "session-old", "turn_id": "turn-new"},
    ))

    active = active_codex_turn(log, state_dir, "reader")
    assert active is not None
    assert active["turn_id"] == "turn-new"


def test_archived_task_stage_suspension_closes_only_prior_turns() -> None:
    from zf.runtime.provider_turn_liveness import active_codex_turn_from_events

    old = ZfEvent(type="codex.hook.user_prompt_submit", actor="verify-1",
                  payload={"session_id": "old", "turn_id": "old"})
    suspended = ZfEvent(
        type="role.lifecycle.suspended", actor="orchestrator", origin="kernel",
        payload={"instance_id": "verify-1", "reason": "task_stage_session_archived",
                 "preserve_session": False},
    )
    assert active_codex_turn_from_events([old, suspended], "verify-1") is None
    new = ZfEvent(type="codex.hook.user_prompt_submit", actor="verify-1",
                  payload={"session_id": "new", "turn_id": "new"})
    assert active_codex_turn_from_events([old, suspended, new], "verify-1")["turn_id"] == "new"


def test_unrelated_or_preserved_suspension_does_not_close_turn() -> None:
    from zf.runtime.provider_turn_liveness import active_codex_turn_from_events

    old = ZfEvent(type="codex.hook.user_prompt_submit", actor="verify-1",
                  payload={"session_id": "old", "turn_id": "old"})
    for origin, instance, reason, preserve in [
        ("agent", "verify-1", "task_stage_session_archived", False),
        ("kernel", "verify-2", "task_stage_session_archived", False),
        ("kernel", "verify-1", "idle", False),
        ("kernel", "verify-1", "task_stage_session_archived", True),
        ("kernel", "verify-1", "task_stage_session_archived", None),
    ]:
        suspended = ZfEvent(
            type="role.lifecycle.suspended", actor="orchestrator", origin=origin,
            payload={"instance_id": instance, "reason": reason, "preserve_session": preserve},
        )
        assert active_codex_turn_from_events([old, suspended], "verify-1")["turn_id"] == "old"
