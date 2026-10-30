from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore
from zf.runtime.backend_session_reader import CodexSessionReader
from zf.runtime.housekeeping import apply_agent_usage_event
from zf.runtime.provider_usage_reconciliation import build_disk_usage_event
from zf.runtime.shutdown import GracefulShutdown


SESSION_ID = "22222222-2222-2222-2222-222222222222"


@pytest.mark.parametrize("boundary", ["reader", "task_slot"])
@pytest.mark.parametrize("fail_once", [False, True])
def test_session_boundary_accounts_tail_before_replacing_identity(tmp_path, monkeypatch, boundary, fail_once):
    from tests.test_role_lifecycle_runtime import _runtime, _seed_reader_fanout_context
    from zf.runtime import provider_usage_reconciliation as reconciliation
    from zf.runtime.task_pipeline_terminal import _release_archived_task_stage_slot

    runtime, role, transport = _runtime(tmp_path)
    _seed_reader_fanout_context(runtime, role, transport, task_id="OLD", trace_id="old-run")
    registry = runtime._role_lifecycle_registry()
    rollout = _write_rollout(tmp_path)
    registry.bind_codex_session(role.instance_id, SESSION_ID, session_path=rollout, observed_from="test")
    initial = build_disk_usage_event(role=role, usage=CodexSessionReader().read_latest_usage(rollout), config=runtime.config)
    runtime.event_writer.append(initial)
    tracker = CostTracker(runtime.state_dir / "cost.jsonl")
    apply_agent_usage_event(tracker, initial, role_backends={role.instance_id: role.backend})
    terminate = transport.terminate

    def finish(instance):
        terminate(instance)
        _append_usage(rollout, timestamp="2026-08-11T07:30:10+00:00",
                      input_tokens=55000, cached_input_tokens=43000, output_tokens=900)

    monkeypatch.setattr(transport, "terminate", finish)

    def retire():
        if boundary == "reader":
            return runtime._prepare_reader_fanout_context(
                role=role, task_id="NEW", trace_id="new-run", state="idle",
                provider_session_replaced=False, dispatch_id="new-dispatch",
            )[0]
        return _release_archived_task_stage_slot(
            runtime, registry=registry, role_instance=role.instance_id,
            binding_key="old-binding", task_id="OLD",
        )

    if fail_once:
        apply = reconciliation.apply_agent_usage_event
        monkeypatch.setattr(reconciliation, "apply_agent_usage_event",
                            lambda *a, **kw: (_ for _ in ()).throw(OSError("cost receipt unavailable")))
        assert not retire()
        assert str(runtime._role_lifecycle_registry().get(role.instance_id)) == SESSION_ID
        monkeypatch.setattr(reconciliation, "apply_agent_usage_event", apply)
    assert retire()
    assert str(runtime._role_lifecycle_registry().get(role.instance_id)) != SESSION_ID
    entries = tracker._read_entries()
    assert sum(int(e.get(k) or 0) for e in entries for k in ("input_tokens", "cache_read_tokens", "output_tokens")) == 55900
    tail = [e for e in runtime.event_log.read_all()
            if e.type == "agent.usage" and e.payload.get("capture_phase") == "session_boundary"]
    assert len(tail) == 1
    assert tail[0].payload["usage"]["combined_input_tokens"] == 55000


class _FinalUsageTransport:
    def __init__(self, rollout: Path) -> None:
        self.rollout = rollout
        self.shutdown_calls = 0

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return ""

    def shutdown(self, *, exclude_roles: set[str] | None = None) -> None:
        self.shutdown_calls += 1
        if self.shutdown_calls == 1:
            _append_usage(
                self.rollout,
                timestamp="2026-08-11T07:30:10+00:00",
                input_tokens=55_000,
                cached_input_tokens=43_000,
                output_tokens=900,
            )


class _NoopTransport:
    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return ""

    def shutdown(self, *, exclude_roles: set[str] | None = None) -> None:
        return None


def _append_usage(
    path: Path,
    *,
    timestamp: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
) -> None:
    event = {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_input_tokens,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": 50,
                    "total_tokens": input_tokens + output_tokens,
                },
                "last_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_input_tokens,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": 50,
                    "total_tokens": input_tokens + output_tokens,
                },
                "model_context_window": 258_400,
                "model": "gpt-5.6-sol",
            },
            "rate_limits": {"plan_type": "pro"},
        },
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def _write_rollout(project_root: Path) -> Path:
    rollout = project_root / "rollout.jsonl"
    rows = [
        {
            "timestamp": "2026-08-11T07:30:00+00:00",
            "type": "session_meta",
            "payload": {
                "id": SESSION_ID,
                "cwd": str(project_root),
                "model_provider": "openai",
            },
        },
        {
            "timestamp": "2026-08-11T07:30:01+00:00",
            "type": "turn_context",
            "payload": {"model": "gpt-5.6-sol"},
        },
    ]
    rollout.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    _append_usage(
        rollout,
        timestamp="2026-08-11T07:30:05+00:00",
        input_tokens=45_000,
        cached_input_tokens=35_000,
        output_tokens=700,
    )
    return rollout


@pytest.mark.parametrize(
    ("shutdown_method", "after_reconciliation_step"),
    [
        ("execute", "save_shutdown_snapshot"),
        ("execute_fast", "emit_completion"),
    ],
)
def test_non_force_shutdown_reconciles_final_provider_usage_once(
    tmp_path: Path,
    shutdown_method: str,
    after_reconciliation_step: str,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").touch()
    SessionStore(state_dir / "session.yaml").create(str(tmp_path))
    role = RoleConfig(
        name="judge-issue",
        instance_id="judge-issue",
        backend="codex",
    )
    config = ZfConfig(
        project=ProjectConfig(name="usage-tail"),
        session=SessionConfig(tmux_session="usage-tail"),
        roles=[role],
    )
    rollout = _write_rollout(tmp_path)
    RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).bind_codex_session(
        role.instance_id,
        SESSION_ID,
        session_path=rollout,
        observed_from="test",
    )

    reader = CodexSessionReader()
    initial_usage = reader.read_latest_usage(rollout)
    assert initial_usage is not None
    initial_event = build_disk_usage_event(
        role=role,
        usage=initial_usage,
        config=config,
    )
    log = EventLog(state_dir / "events.jsonl")
    EventWriter(log).append(initial_event)
    apply_agent_usage_event(
        CostTracker(state_dir / "cost.jsonl"),
        initial_event,
        role_backends={role.name: role.backend},
    )

    transport = _FinalUsageTransport(rollout)
    shutdown = GracefulShutdown(
        state_dir=state_dir,
        transport=transport,
        config=config,
    )
    steps = getattr(shutdown, shutdown_method)()

    assert steps.index("kill_session") < steps.index(
        "reconcile_provider_usage_tail"
    ) < steps.index(after_reconciliation_step)
    events = EventLog(state_dir / "events.jsonl").read_all()
    tail_events = [
        event
        for event in events
        if event.type == "agent.usage"
        and event.payload.get("capture_phase") == "shutdown_tail"
    ]
    assert len(tail_events) == 1
    assert tail_events[0].payload["usage"] == {
        "input_tokens": 12_000,
        "combined_input_tokens": 55_000,
        "output_tokens": 900,
        "cached_input_tokens": 43_000,
        "cache_read_input_tokens": 43_000,
        "cache_creation_input_tokens": 0,
        "reasoning_output_tokens": 50,
    }
    entries_before = CostTracker(state_dir / "cost.jsonl")._read_entries()
    assert sum(int(row.get("input_tokens") or 0) for row in entries_before) == 12_000
    assert sum(int(row.get("cache_read_tokens") or 0) for row in entries_before) == 43_000
    assert sum(int(row.get("output_tokens") or 0) for row in entries_before) == 900

    repeated_shutdown = GracefulShutdown(
        state_dir=state_dir,
        transport=transport,
        config=config,
    )
    getattr(repeated_shutdown, shutdown_method)()

    replayed_events = EventLog(state_dir / "events.jsonl").read_all()
    assert len([
        event
        for event in replayed_events
        if event.type == "agent.usage"
        and event.payload.get("capture_phase") == "shutdown_tail"
    ]) == 1
    assert CostTracker(state_dir / "cost.jsonl")._read_entries() == entries_before


def test_shutdown_tail_capture_failure_is_auditable_and_bounded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").touch()
    SessionStore(state_dir / "session.yaml").create(str(tmp_path))
    config = ZfConfig(
        project=ProjectConfig(name="usage-tail-failure"),
        session=SessionConfig(tmux_session="usage-tail-failure"),
        roles=[RoleConfig(name="judge-issue", backend="codex")],
    )

    def _fail_usage_read(**_kwargs):
        raise RuntimeError("malformed provider tail")

    monkeypatch.setattr(
        "zf.runtime.provider_usage_reconciliation._read_latest_role_usage",
        _fail_usage_read,
    )

    steps = GracefulShutdown(
        state_dir=state_dir,
        transport=_NoopTransport(),
        config=config,
    ).execute_fast()

    assert "emit_completion" in steps
    failures = [
        event
        for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "kernel.housekeeping.failed"
        and event.payload.get("step") == "provider_usage_shutdown_tail"
    ]
    assert len(failures) == 1
    assert failures[0].payload["exc_type"] == (
        "ProviderUsageTailReconciliationError"
    )
    assert failures[0].payload["exc_repr"] == "failed_roles=judge-issue"
