from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime import control_actions, run_manager


def _state(tmp_path: Path) -> tuple[EventLog, EventWriter]:
    log = EventLog(tmp_path / "events.jsonl")
    return log, EventWriter(log)


def _config() -> ZfConfig:
    return ZfConfig(project=ProjectConfig(name="operator-action-executor"))


@pytest.mark.parametrize(
    ("result", "expected_event", "expected_return"),
    [
        (
            {
                "ok": True,
                "status": "applied",
                "event_id": "primary-event",
                "emitted_event_ids": ["secondary-event", "", "third-event"],
                "reason": "action applied",
                "action_result": {"changed": True},
            },
            run_manager.RUN_MANAGER_ACTION_APPLIED,
            "applied",
        ),
        (
            {"ok": True, "status": "no_op"},
            run_manager.RUN_MANAGER_ACTION_APPLIED,
            "applied",
        ),
        (
            {"ok": False, "status": "failed"},
            run_manager.RUN_MANAGER_ACTION_FAILED,
            "failed",
        ),
        (
            {"ok": False, "status": "blocked"},
            run_manager.RUN_MANAGER_ACTION_BLOCKED,
            "blocked",
        ),
        (
            {"ok": False, "status": "approval_required"},
            run_manager.RUN_MANAGER_ACTION_BLOCKED,
            "blocked",
        ),
        ({"ok": False}, run_manager.RUN_MANAGER_ACTION_FAILED, "failed"),
    ],
)
def test_operator_action_executor_maps_exact_service_and_receipt_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: dict[str, Any],
    expected_event: str,
    expected_return: str,
) -> None:
    log, writer = _state(tmp_path)
    state_dir = tmp_path / "state"
    project_root = tmp_path / "project"
    config = _config()
    action = {
        "action": "fanout-aggregate-rebuild",
        "checkpoint_id": "checkpoint-1",
    }
    service_payload = {"source": "sentinel", "limit": 7}
    init_calls: list[tuple[Any, ...]] = []
    execute_calls: list[dict[str, Any]] = []
    post_calls: list[tuple[Any, ...]] = []
    payload_calls: list[dict[str, Any]] = []

    class FakeService:
        def __init__(
            self,
            actual_state_dir: Path,
            actual_writer: EventWriter,
            *,
            config: ZfConfig,
            project_root: Path | None,
            actor: str,
            source: str,
            surface: str,
        ) -> None:
            init_calls.append((
                actual_state_dir,
                actual_writer,
                config,
                project_root,
                actor,
                source,
                surface,
            ))

        def execute(
            self,
            *,
            action: str,
            requested_action: str,
            payload: dict[str, Any],
            requested: Any,
        ) -> dict[str, Any]:
            execute_calls.append({
                "action": action,
                "requested_action": requested_action,
                "payload": payload,
                "requested": requested,
            })
            return result

    def payload_for(actual_action: dict[str, Any]) -> dict[str, Any]:
        payload_calls.append(actual_action)
        return service_payload

    def post_verify(
        actual_writer: EventWriter,
        actual_action: dict[str, Any],
        actual_result: dict[str, Any],
        *,
        causation_id: str,
        state_dir: Path | None,
        config: ZfConfig | None,
    ) -> None:
        post_calls.append((
            actual_writer,
            actual_action,
            actual_result,
            causation_id,
            state_dir,
            config,
        ))

    monkeypatch.setattr(control_actions, "ControlledActionService", FakeService)
    monkeypatch.setattr(run_manager, "_operator_controlled_action_payload", payload_for)
    monkeypatch.setattr(run_manager, "_post_verify_action", post_verify)

    status = run_manager._execute_operator_controlled_action(
        state_dir=state_dir,
        writer=writer,
        config=config,
        project_root=project_root,
        action=action,
        causation_id="trigger-event",
    )

    assert status == expected_return
    planned, outcome = log.read_all()
    assert planned.type == run_manager.RUN_MANAGER_ACTION_PLANNED
    assert planned.actor == "run-manager"
    assert planned.causation_id == "trigger-event"
    assert planned.payload["schema_version"] == "run-manager.action.v1"
    assert planned.payload["action"] == "fanout-aggregate-rebuild"
    assert payload_calls == [action]
    assert init_calls == [(
        state_dir,
        writer,
        config,
        project_root,
        "run-manager",
        "run-manager",
        "run-manager",
    )]
    assert execute_calls == [{
        "action": "fanout-aggregate-rebuild",
        "requested_action": "fanout-aggregate-rebuild",
        "payload": service_payload,
        "requested": planned,
    }]
    assert outcome.type == expected_event
    assert outcome.actor == "run-manager"
    assert outcome.causation_id == planned.id
    assert outcome.payload["schema_version"] == "run-manager.action.v1"
    assert outcome.payload["status"] == str(result.get("status") or "")
    assert outcome.payload["controlled_action_result"] == result
    assert outcome.payload["event_id"] == str(result.get("event_id") or "")
    assert outcome.payload["reason"] == str(result.get("reason") or "")
    emitted_ids = [
        str(result.get("event_id") or ""),
        *[
            str(value)
            for value in result.get("emitted_event_ids") or []
            if str(value).strip()
        ],
    ]
    assert post_calls == [(
        writer,
        action,
        {**result, "emitted_event_ids": emitted_ids},
        outcome.id,
        state_dir,
        config,
    )]


def test_operator_action_executor_records_service_exception_without_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log, writer = _state(tmp_path)

    class FailingService:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def execute(self, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("operator action exploded")

    monkeypatch.setattr(control_actions, "ControlledActionService", FailingService)
    monkeypatch.setattr(
        run_manager,
        "_post_verify_action",
        lambda *_args, **_kwargs: pytest.fail(
            "failed service execution must not be post-verified"
        ),
    )

    status = run_manager._execute_operator_controlled_action(
        state_dir=tmp_path / "state",
        writer=writer,
        config=_config(),
        project_root=tmp_path / "project",
        action={"action": "failure-closeout-activate", "checkpoint_id": "cp-1"},
        causation_id="exception-trigger",
    )

    assert status == "failed"
    planned, failed = log.read_all()
    assert failed.type == run_manager.RUN_MANAGER_ACTION_FAILED
    assert failed.actor == "run-manager"
    assert failed.causation_id == planned.id
    assert failed.payload["schema_version"] == "run-manager.action.v1"
    assert failed.payload["status"] == "failed"
    assert failed.payload["reason"] == "operator action exploded"


def test_operator_action_executor_normalizes_missing_action_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _log, writer = _state(tmp_path)
    calls: list[tuple[str, str]] = []

    class FakeService:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def execute(
            self,
            *,
            action: str,
            requested_action: str,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            calls.append((action, requested_action))
            return {"ok": False, "status": "failed"}

    monkeypatch.setattr(control_actions, "ControlledActionService", FakeService)
    monkeypatch.setattr(run_manager, "_post_verify_action", lambda *_a, **_k: None)

    run_manager._execute_operator_controlled_action(
        state_dir=tmp_path / "state",
        writer=writer,
        config=_config(),
        project_root=None,
        action={},
        causation_id="missing-action-trigger",
    )

    assert calls == [("", "")]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ZF-TEST-053: contradictory ok=true/blocked result emits a blocked receipt "
        "but returns applied"
    ),
)
def test_operator_action_executor_does_not_report_applied_for_blocked_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _log, writer = _state(tmp_path)

    class ConflictingService:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def execute(self, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "status": "blocked"}

    monkeypatch.setattr(control_actions, "ControlledActionService", ConflictingService)
    monkeypatch.setattr(
        run_manager,
        "_post_verify_action",
        lambda *_args, **_kwargs: None,
    )

    status = run_manager._execute_operator_controlled_action(
        state_dir=tmp_path / "state",
        writer=writer,
        config=_config(),
        project_root=tmp_path / "project",
        action={"action": "fanout-aggregate-rebuild"},
        causation_id="conflict-trigger",
    )

    assert status == "blocked"
