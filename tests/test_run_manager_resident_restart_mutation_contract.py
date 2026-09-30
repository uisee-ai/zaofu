from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    RuntimeRunManagerConfig,
    RuntimeRunManagerResidentAgentConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.runtime.run_manager_resident import (
    RUN_MANAGER_RESIDENT_RESTARTED,
    RUN_MANAGER_RESIDENT_RESTART_FAILED,
    RUN_MANAGER_RESIDENT_RESTART_REQUESTED,
    _apply_resident_restart_request,
    _consumed_restart_request_ids,
    _emit_restart_failed,
    _transport_for_role,
    apply_pending_resident_restart_requests,
)


def _config(
    *,
    enabled: bool = True,
    session_mode: str = "dedicated",
    prompt_on_start: bool = True,
    instance_id: str = "rm-restart",
) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="resident-restart-mutation"),
        session=SessionConfig(tmux_session="zf-workflow"),
        roles=[RoleConfig(name="developer", backend="codex")],
        runtime=RuntimeConfig(
            run_manager=RuntimeRunManagerConfig(
                backend="claude-code",
                resident_agent=RuntimeRunManagerResidentAgentConfig(
                    enabled=enabled,
                    transport="tmux",
                    instance_id=instance_id,
                    model="resident-model",
                    model_reasoning_effort="high",
                    prompt_on_start=prompt_on_start,
                    session_mode=session_mode,
                    tmux_session="zf-rm-only",
                ),
            ),
        ),
    )


def _request(
    *,
    event_id: str = "evt-restart-request",
    payload: Any = None,
    causation_id: str | None = None,
) -> ZfEvent:
    if payload is None:
        payload = {
            "instance_id": "rm-restart",
            "tmux_session": "zf-requested-rm",
        }
    return ZfEvent(
        id=event_id,
        type=RUN_MANAGER_RESIDENT_RESTART_REQUESTED,
        actor="run-manager-watchdog",
        causation_id=causation_id,
        payload=payload,
    )


class _WriterSpy:
    def __init__(self) -> None:
        self.events: list[ZfEvent] = []

    def emit(
        self,
        event_type: str,
        *,
        actor: str,
        causation_id: str | None = None,
        payload: dict[str, Any],
    ) -> ZfEvent:
        event = ZfEvent(
            id=f"evt-output-{len(self.events) + 1}",
            type=event_type,
            actor=actor,
            causation_id=causation_id,
            payload=payload,
        )
        self.events.append(event)
        return event


class _ReadLogSpy:
    def __init__(self, events: list[ZfEvent], *, error: bool = False) -> None:
        self.events = events
        self.error = error
        self.calls = 0

    def read_all(self) -> list[ZfEvent]:
        self.calls += 1
        if self.error:
            raise RuntimeError("read failed sentinel")
        return self.events


class _CoordinatorSpy:
    def __init__(self, *, error: bool = False) -> None:
        self.error = error
        self.calls: list[tuple[RoleConfig, Path | None]] = []

    def spawn(self, role: RoleConfig, *, cwd: Path | None = None) -> None:
        self.calls.append((role, cwd))
        if self.error:
            raise RuntimeError("spawn failed sentinel")


class _RestartTransportSpy:
    def __init__(
        self,
        *,
        running: bool = True,
        ready: bool = True,
        register_error: bool = False,
        role_probe_error: bool = False,
        running_error: bool = False,
        init_error: bool = False,
        terminate_error: bool = False,
        wait_error: bool = False,
        send_error: bool = False,
    ) -> None:
        self.running = running
        self.ready = ready
        self.register_error = register_error
        self.role_probe_error = role_probe_error
        self.running_error = running_error
        self.init_error = init_error
        self.terminate_error = terminate_error
        self.wait_error = wait_error
        self.send_error = send_error
        self.registered: list[RoleConfig] = []
        self.for_role_calls: list[str] = []
        self.running_calls = 0
        self.init_calls = 0
        self.terminated: list[str] = []
        self.wait_calls: list[tuple[str, str, float]] = []
        self.sent: list[tuple[str, Path, str, Any]] = []

    def register_role(self, role: RoleConfig) -> None:
        self.registered.append(role)
        if self.register_error:
            raise RuntimeError("register failed sentinel")

    def for_role(self, instance_id: str) -> _RestartTransportSpy:
        self.for_role_calls.append(instance_id)
        if self.role_probe_error:
            raise RuntimeError("role probe failed sentinel")
        return self

    def is_session_running(self) -> bool:
        self.running_calls += 1
        if self.running_error:
            raise RuntimeError("running probe failed sentinel")
        return self.running

    def init(self) -> None:
        self.init_calls += 1
        if self.init_error:
            raise RuntimeError("init failed sentinel")
        self.running = True

    def terminate(self, instance_id: str) -> None:
        self.terminated.append(instance_id)
        if self.terminate_error:
            raise RuntimeError("terminate failed sentinel")

    def wait_ready(self, instance_id: str, pattern: str, timeout: float) -> bool:
        self.wait_calls.append((instance_id, pattern, timeout))
        if self.wait_error:
            raise RuntimeError("wait failed sentinel")
        return self.ready

    def send_task(
        self,
        instance_id: str,
        briefing_path: Path,
        prompt: str,
        *,
        context: Any = None,
    ) -> None:
        self.sent.append((instance_id, briefing_path, prompt, context))
        if self.send_error:
            raise RuntimeError("send failed sentinel")


def _patch_content(
    monkeypatch: pytest.MonkeyPatch,
    *,
    requires_ready_wait: bool = True,
) -> list[str]:
    adapter_calls: list[str] = []
    monkeypatch.setattr(
        "zf.runtime.run_manager_resident.generate_role_instructions",
        lambda config, role, *, state_dir_ref, project_root: (
            f"INSTRUCTIONS:{config.project.name}:{role.instance_id}:"
            f"{state_dir_ref}:{project_root}\n"
        ),
    )
    monkeypatch.setattr(
        "zf.runtime.run_manager_resident.build_resident_run_manager_briefing",
        lambda *, project_root, state_dir, role: (
            f"BASE:{project_root}:{state_dir}:{role.instance_id}  \n\n"
        ),
    )
    monkeypatch.setattr(
        "zf.runtime.run_manager_resident.zf_cli_cmd",
        lambda: "zf-cli-sentinel",
    )

    def fake_adapter(backend: str) -> SimpleNamespace:
        adapter_calls.append(backend)
        return SimpleNamespace(
            requires_ready_wait=requires_ready_wait,
            ready_pattern="READY-SENTINEL",
        )

    monkeypatch.setattr(
        "zf.runtime.run_manager_resident.get_adapter",
        fake_adapter,
    )
    return adapter_calls


def test_restart_helpers_extract_consumed_ids_and_transport_routes() -> None:
    events = [
        ZfEvent(type="unrelated", id="evt-unrelated"),
        ZfEvent(
            type=RUN_MANAGER_RESIDENT_RESTARTED,
            id="evt-restarted",
            causation_id="evt-cause-ignored",
            payload={"source_restart_request_id": "  evt-source  "},
        ),
        ZfEvent(
            type=RUN_MANAGER_RESIDENT_RESTART_FAILED,
            id="evt-failed",
            causation_id="  evt-cause  ",
            payload={},
        ),
        ZfEvent(
            type=RUN_MANAGER_RESIDENT_RESTART_FAILED,
            id="evt-bad-payload",
            causation_id="evt-nondict-cause",
            payload=[],
        ),
        ZfEvent(
            type=RUN_MANAGER_RESIDENT_RESTARTED,
            id="evt-empty",
            payload={"source_restart_request_id": "   "},
        ),
        ZfEvent(
            type=RUN_MANAGER_RESIDENT_RESTARTED,
            id="evt-truly-empty",
            payload={},
        ),
    ]

    assert _consumed_restart_request_ids(events) == {
        "evt-source",
        "evt-cause",
        "evt-nondict-cause",
    }
    routed = object()
    transport = SimpleNamespace(for_role=lambda instance_id: (instance_id, routed))
    assert _transport_for_role(transport, "rm-one") == ("rm-one", routed)
    assert _transport_for_role(SimpleNamespace(for_role=None), "rm-two").for_role is None
    plain = object()
    assert _transport_for_role(plain, "rm-three") is plain


def test_restart_failed_event_has_exact_defaults_and_extra_override() -> None:
    writer = _WriterSpy()
    request = _request(
        payload={"instance_id": "rm-one", "tmux_session": "zf-one"}
    )

    _emit_restart_failed(
        writer,
        event=request,
        reason="failure sentinel",
        extra={"terminate_error": "boom", "terminated_old_slot": False},
    )
    _emit_restart_failed(
        writer,
        event=_request(event_id="evt-defaults", payload=[]),
        reason="default sentinel",
    )

    assert [(event.type, event.actor, event.causation_id) for event in writer.events] == [
        (RUN_MANAGER_RESIDENT_RESTART_FAILED, "run-manager-watchdog", request.id),
        (RUN_MANAGER_RESIDENT_RESTART_FAILED, "run-manager-watchdog", "evt-defaults"),
    ]
    assert writer.events[0].payload == {
        "schema_version": "run-manager.resident-restart.v1",
        "status": "failed",
        "source_restart_request_id": request.id,
        "reason": "failure sentinel",
        "instance_id": "rm-one",
        "tmux_session": "zf-one",
        "restart_scope": "resident_only",
        "workflow_restart": False,
        "terminate_error": "boom",
        "terminated_old_slot": False,
    }
    assert writer.events[1].payload == {
        "schema_version": "run-manager.resident-restart.v1",
        "status": "failed",
        "source_restart_request_id": "evt-defaults",
        "reason": "default sentinel",
        "instance_id": "run-manager",
        "tmux_session": "",
        "restart_scope": "resident_only",
        "workflow_restart": False,
    }


def test_apply_pending_restart_selects_first_unconsumed_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumed_request = _request(event_id="evt-consumed")
    pending_request = _request(event_id="evt-pending")
    later_request = _request(event_id="evt-later")
    events = [
        ZfEvent(type="unrelated", id="evt-unrelated"),
        consumed_request,
        ZfEvent(
            type=RUN_MANAGER_RESIDENT_RESTARTED,
            causation_id=consumed_request.id,
            payload={},
        ),
        pending_request,
        later_request,
    ]
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "zf.runtime.run_manager_resident._apply_resident_restart_request",
        lambda **kwargs: calls.append(kwargs),
    )
    state_dir = tmp_path / "state"
    project_root = tmp_path / "project"
    instructions_dir = tmp_path / "custom-instructions"
    writer = object()
    coordinator = object()
    transport = object()

    result = apply_pending_resident_restart_requests(
        config=_config(),
        state_dir=state_dir,
        project_root=project_root,
        writer=writer,
        event_log=_ReadLogSpy(events),
        coordinator=coordinator,
        transport=transport,
        instructions_dir=instructions_dir,
        dry_run=True,
    )

    assert result == 1
    assert calls == [{
        "event": pending_request,
        "config": calls[0]["config"],
        "state_dir": state_dir,
        "project_root": project_root,
        "writer": writer,
        "coordinator": coordinator,
        "transport": transport,
        "instructions_dir": instructions_dir,
        "dry_run": True,
    }]


def test_apply_pending_restart_fail_closed_empty_disabled_and_read_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "zf.runtime.run_manager_resident._apply_resident_restart_request",
        lambda **_kwargs: pytest.fail("no restart should be applied"),
    )
    disabled_log = _ReadLogSpy([_request()])
    assert apply_pending_resident_restart_requests(
        config=_config(enabled=False),
        state_dir=tmp_path / "state",
        project_root=tmp_path,
        writer=object(),
        event_log=disabled_log,
        coordinator=object(),
        transport=object(),
    ) == 0
    assert disabled_log.calls == 0

    for log in (_ReadLogSpy([]), _ReadLogSpy([_request()], error=True)):
        assert apply_pending_resident_restart_requests(
            config=_config(),
            state_dir=tmp_path / "state",
            project_root=tmp_path,
            writer=object(),
            event_log=log,
            coordinator=object(),
            transport=object(),
        ) == 0
        assert log.calls == 1


def test_apply_pending_restart_uses_state_scoped_default_instructions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "zf.runtime.run_manager_resident._apply_resident_restart_request",
        lambda **kwargs: calls.append(kwargs),
    )
    state_dir = tmp_path / "state"

    result = apply_pending_resident_restart_requests(
        config=_config(),
        state_dir=state_dir,
        project_root=tmp_path / "project",
        writer=object(),
        event_log=_ReadLogSpy([_request()]),
        coordinator=object(),
        transport=object(),
        instructions_dir=None,
    )

    assert result == 1
    assert calls[0]["instructions_dir"] == state_dir / "instructions"


@pytest.mark.parametrize(
    ("config", "payload", "expected_reason", "expected_instance"),
    [
        (
            _config(enabled=False),
            {"instance_id": "rm-restart"},
            "resident run manager role is disabled",
            "rm-restart",
        ),
        (
            _config(),
            {"instance_id": "wrong-instance"},
            "resident restart request instance mismatch "
            "(requested=wrong-instance, configured=rm-restart)",
            "wrong-instance",
        ),
    ],
)
def test_restart_request_rejects_disabled_or_mismatched_instance(
    tmp_path: Path,
    config: ZfConfig,
    payload: dict[str, Any],
    expected_reason: str,
    expected_instance: str,
) -> None:
    writer = _WriterSpy()
    request = _request(payload=payload)
    transport = _RestartTransportSpy()
    coordinator = _CoordinatorSpy()

    _apply_resident_restart_request(
        event=request,
        config=config,
        state_dir=tmp_path / "state",
        project_root=tmp_path / "project",
        writer=writer,
        coordinator=coordinator,
        transport=transport,
        instructions_dir=tmp_path / "instructions",
        dry_run=False,
    )

    assert len(writer.events) == 1
    assert writer.events[0].type == RUN_MANAGER_RESIDENT_RESTART_FAILED
    assert writer.events[0].payload["reason"] == expected_reason
    assert writer.events[0].payload["instance_id"] == expected_instance
    assert transport.registered == []
    assert coordinator.calls == []


def test_restart_success_emits_exact_causal_chain_and_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter_calls = _patch_content(monkeypatch)
    config = _config()
    request = _request()
    writer = _WriterSpy()
    transport = _RestartTransportSpy(running=False, ready=True)
    coordinator = _CoordinatorSpy()
    state_dir = tmp_path / "state"
    project_root = tmp_path / "project"
    instructions_dir = tmp_path / "nested" / "instructions"
    instructions_dir.mkdir(parents=True)
    (state_dir / "briefings").mkdir(parents=True)

    _apply_resident_restart_request(
        event=request,
        config=config,
        state_dir=state_dir,
        project_root=project_root,
        writer=writer,
        coordinator=coordinator,
        transport=transport,
        instructions_dir=instructions_dir,
        dry_run=False,
    )

    role = transport.registered[0]
    assert adapter_calls == ["claude-code"]
    assert transport.for_role_calls == ["rm-restart"]
    assert transport.running_calls == 1
    assert transport.init_calls == 1
    assert transport.terminated == ["rm-restart"]
    assert transport.wait_calls == [("rm-restart", "READY-SENTINEL", 60.0)]
    assert coordinator.calls == [(role, project_root)]
    instructions_path = instructions_dir / "rm-restart.md"
    assert instructions_path.read_text(encoding="utf-8") == (
        f"INSTRUCTIONS:resident-restart-mutation:rm-restart:{state_dir}:"
        f"{project_root}\n"
    )
    briefing_path = state_dir / "briefings" / "run-manager-resident.md"
    prompt = briefing_path.read_text(encoding="utf-8")
    assert prompt == (
        f"BASE:{project_root}:{state_dir}:rm-restart\n\n"
        "## Restart Recovery\n\n"
        "This resident agent was restarted by the L0 watchdog. "
        "First tick mode is observe-only: emit an observation before any "
        "mutating recommendation.\n"
    )
    assert len(transport.sent) == 1
    instance_id, sent_path, sent_prompt, context = transport.sent[0]
    assert (instance_id, sent_path, sent_prompt) == (
        "rm-restart",
        briefing_path,
        prompt,
    )
    assert (context.role_name, context.instance_id, context.backend) == (
        "run-manager",
        "rm-restart",
        "claude-code",
    )
    assert context.briefing_path == briefing_path

    spawned, prompted, restarted = writer.events
    assert (spawned.type, spawned.actor, spawned.causation_id) == (
        "run.manager.resident.spawned",
        "run-manager-watchdog",
        request.id,
    )
    assert spawned.payload == {
        "schema_version": "run-manager.resident.v1",
        "role": asdict(role),
        "backend": "claude-code",
        "transport": "tmux",
        "ready": True,
        "session_mode": "dedicated",
        "tmux_session": "zf-requested-rm",
        "restart": True,
        "source_restart_request_id": request.id,
        "terminated_old_slot": True,
        "terminate_error": "",
        "dedicated_session_init_attempted": True,
    }
    assert (prompted.type, prompted.actor, prompted.causation_id) == (
        "run.manager.resident.prompted",
        "run-manager-watchdog",
        spawned.id,
    )
    assert prompted.payload == {
        "schema_version": "run-manager.resident.v1",
        "instance_id": "rm-restart",
        "briefing_path": str(briefing_path),
        "prompted": True,
        "restart": True,
        "tmux_session": "zf-requested-rm",
        "target": "zf-requested-rm:rm-restart",
        "first_tick_mode": "observe_only",
        "source_restart_request_id": request.id,
        "zf_cli_cmd": "zf-cli-sentinel",
    }
    assert (restarted.type, restarted.actor, restarted.causation_id) == (
        RUN_MANAGER_RESIDENT_RESTARTED,
        "run-manager-watchdog",
        request.id,
    )
    assert restarted.payload == {
        "schema_version": "run-manager.resident-restart.v1",
        "status": "restarted",
        "source_restart_request_id": request.id,
        "instance_id": "rm-restart",
        "tmux_session": "zf-requested-rm",
        "session_mode": "dedicated",
        "restart_scope": "resident_only",
        "first_tick_mode": "observe_only",
        "workflow_restart": False,
        "state_dir": str(state_dir),
        "briefing_path": str(briefing_path),
        "spawned_event_id": spawned.id,
        "prompted_event_id": prompted.id,
        "ready": True,
        "prompted": True,
        "terminated_old_slot": True,
        "terminate_error": "",
    }


def test_restart_creates_missing_nested_instruction_parents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_content(monkeypatch, requires_ready_wait=False)
    instructions_dir = tmp_path / "missing" / "nested" / "instructions"
    writer = _WriterSpy()

    _apply_resident_restart_request(
        event=_request(),
        config=_config(prompt_on_start=False),
        state_dir=tmp_path / "state",
        project_root=tmp_path / "project",
        writer=writer,
        coordinator=_CoordinatorSpy(),
        transport=_RestartTransportSpy(),
        instructions_dir=instructions_dir,
        dry_run=False,
    )

    assert (instructions_dir / "rm-restart.md").is_file()
    assert [event.type for event in writer.events] == [
        "run.manager.resident.spawned",
        "run.manager.resident.prompted",
        RUN_MANAGER_RESIDENT_RESTARTED,
    ]


@pytest.mark.parametrize(
    (
        "transport_kwargs",
        "coordinator_error",
        "dry_run",
        "prompt_on_start",
        "requires_ready_wait",
        "expected_types",
        "expected_ready",
        "expected_prompted",
        "expected_error",
    ),
    [
        ({"terminate_error": True}, False, False, True, True,
         ["run.manager.resident.spawned", "run.manager.resident.prompted",
          RUN_MANAGER_RESIDENT_RESTARTED], True, True,
         "terminate failed sentinel"),
        ({"running_error": True}, False, False, True, True,
         ["run.manager.resident.spawned", "run.manager.resident.prompted",
          RUN_MANAGER_RESIDENT_RESTARTED], True, True,
         "running probe failed sentinel"),
        ({"wait_error": True}, False, False, True, True,
         ["run.manager.resident.spawned", "run.manager.resident.prompted",
          RUN_MANAGER_RESIDENT_RESTARTED], False, False, ""),
        ({"ready": False}, False, False, True, True,
         ["run.manager.resident.spawned", "run.manager.resident.prompted",
          RUN_MANAGER_RESIDENT_RESTARTED], False, False, ""),
        ({"send_error": True}, False, False, True, True,
         ["run.manager.resident.spawned", "run.manager.resident.prompted",
          RUN_MANAGER_RESIDENT_RESTARTED], True, False, ""),
        ({"register_error": True}, False, True, True, True,
         ["run.manager.resident.spawned", "run.manager.resident.prompted",
          RUN_MANAGER_RESIDENT_RESTARTED], True, False, ""),
        ({}, False, False, False, False,
         ["run.manager.resident.spawned", "run.manager.resident.prompted",
          RUN_MANAGER_RESIDENT_RESTARTED], True, False, ""),
        ({}, True, False, True, True,
         [RUN_MANAGER_RESIDENT_RESTART_FAILED], False, False, ""),
    ],
)
def test_restart_failure_and_control_branches_are_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport_kwargs: dict[str, Any],
    coordinator_error: bool,
    dry_run: bool,
    prompt_on_start: bool,
    requires_ready_wait: bool,
    expected_types: list[str],
    expected_ready: bool,
    expected_prompted: bool,
    expected_error: str,
) -> None:
    adapter_calls = _patch_content(
        monkeypatch,
        requires_ready_wait=requires_ready_wait,
    )
    config = _config(
        session_mode="shared" if dry_run else "dedicated",
        prompt_on_start=prompt_on_start,
    )
    request = _request(payload={})
    writer = _WriterSpy()
    transport = _RestartTransportSpy(**transport_kwargs)
    coordinator = _CoordinatorSpy(error=coordinator_error)

    _apply_resident_restart_request(
        event=request,
        config=config,
        state_dir=tmp_path / "state",
        project_root=tmp_path / "project",
        writer=writer,
        coordinator=coordinator,
        transport=transport,
        instructions_dir=tmp_path / "instructions",
        dry_run=dry_run,
    )

    assert [event.type for event in writer.events] == expected_types
    if coordinator_error:
        assert writer.events[0].payload == {
            "schema_version": "run-manager.resident-restart.v1",
            "status": "failed",
            "source_restart_request_id": request.id,
            "reason": "resident spawn failed: spawn failed sentinel",
            "instance_id": "run-manager",
            "tmux_session": "zf-rm-only",
            "restart_scope": "resident_only",
            "workflow_restart": False,
            "terminated_old_slot": True,
            "terminate_error": "",
            "session_mode": "dedicated",
        }
        return

    spawned, prompted, restarted = writer.events
    assert spawned.payload["ready"] is expected_ready
    assert spawned.payload["terminate_error"] == expected_error
    assert spawned.payload["terminated_old_slot"] is (not bool(expected_error))
    assert prompted.payload["prompted"] is expected_prompted
    assert restarted.payload["ready"] is expected_ready
    assert restarted.payload["prompted"] is expected_prompted
    assert restarted.payload["terminate_error"] == expected_error
    assert spawned.payload["dedicated_session_init_attempted"] is False
    if dry_run:
        assert adapter_calls == []
        assert transport.wait_calls == []
        assert transport.sent == []
        assert transport.running_calls == 0
        assert transport.init_calls == 0
    else:
        assert adapter_calls == ["claude-code"]
        assert len(transport.sent) == int(prompt_on_start and expected_ready)


def _transport_without_optional_restart_hooks() -> SimpleNamespace:
    return SimpleNamespace(
        wait_ready=lambda *_args, **_kwargs: True,
        send_task=lambda *_args, **_kwargs: None,
    )


def test_restart_supports_transport_without_optional_lifecycle_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_content(monkeypatch, requires_ready_wait=False)
    writer = _WriterSpy()

    _apply_resident_restart_request(
        event=_request(),
        config=_config(prompt_on_start=False),
        state_dir=tmp_path / "state",
        project_root=tmp_path / "project",
        writer=writer,
        coordinator=_CoordinatorSpy(),
        transport=_transport_without_optional_restart_hooks(),
        instructions_dir=tmp_path / "instructions",
        dry_run=False,
    )

    assert [event.type for event in writer.events] == [
        "run.manager.resident.spawned",
        "run.manager.resident.prompted",
        RUN_MANAGER_RESIDENT_RESTARTED,
    ]
    assert writer.events[0].payload["dedicated_session_init_attempted"] is False


def test_restart_does_not_initialize_when_running_probe_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_content(monkeypatch, requires_ready_wait=False)
    init_calls: list[bool] = []
    transport = SimpleNamespace(
        init=lambda: init_calls.append(True),
        terminate=lambda _instance_id: None,
        wait_ready=lambda *_args, **_kwargs: True,
        send_task=lambda *_args, **_kwargs: None,
    )
    writer = _WriterSpy()

    _apply_resident_restart_request(
        event=_request(),
        config=_config(prompt_on_start=False),
        state_dir=tmp_path / "state",
        project_root=tmp_path / "project",
        writer=writer,
        coordinator=_CoordinatorSpy(),
        transport=transport,
        instructions_dir=tmp_path / "instructions",
        dry_run=False,
    )

    assert init_calls == []
    assert writer.events[0].payload["dedicated_session_init_attempted"] is False
    assert writer.events[0].payload["terminate_error"] == ""


def test_restart_tolerates_false_running_probe_without_initializer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_content(monkeypatch, requires_ready_wait=False)
    transport = SimpleNamespace(
        is_session_running=lambda: False,
        terminate=lambda _instance_id: None,
        wait_ready=lambda *_args, **_kwargs: True,
        send_task=lambda *_args, **_kwargs: None,
    )
    writer = _WriterSpy()

    _apply_resident_restart_request(
        event=_request(),
        config=_config(prompt_on_start=False),
        state_dir=tmp_path / "state",
        project_root=tmp_path / "project",
        writer=writer,
        coordinator=_CoordinatorSpy(),
        transport=transport,
        instructions_dir=tmp_path / "instructions",
        dry_run=False,
    )

    assert writer.events[0].payload["dedicated_session_init_attempted"] is False
    assert writer.events[0].payload["terminate_error"] == ""


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ZF-TEST-051: missing transport terminator is reported as a successful "
        "old-slot termination"
    ),
)
def test_restart_without_terminator_does_not_claim_old_slot_was_terminated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_content(monkeypatch, requires_ready_wait=False)
    writer = _WriterSpy()

    _apply_resident_restart_request(
        event=_request(),
        config=_config(prompt_on_start=False),
        state_dir=tmp_path / "state",
        project_root=tmp_path / "project",
        writer=writer,
        coordinator=_CoordinatorSpy(),
        transport=_transport_without_optional_restart_hooks(),
        instructions_dir=tmp_path / "instructions",
        dry_run=False,
    )

    assert writer.events[0].payload["terminated_old_slot"] is False
    assert writer.events[-1].payload["terminated_old_slot"] is False
