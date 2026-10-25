"""Tests for zf start and zf stop commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from zf.cli.main import main


def test_idle_tick_catches_up_from_durable_offset_without_empty_pushed_events():
    from zf.cli.start import _run_orchestrator_idle_tick

    calls = []

    class FakeOrchestrator:
        def run_once(self, *args, **kwargs):
            calls.append((args, kwargs))
            return []

    _run_orchestrator_idle_tick(FakeOrchestrator())

    assert calls == [((), {})]


def test_simulation_processes_goal_terminal_before_auto_stop(tmp_path: Path):
    from zf.cli.watched_batch import process_watched_batch
    from zf.core.events.log import EventLog
    from zf.core.events.model import ZfEvent

    log = EventLog(tmp_path / "events.jsonl")
    terminal = ZfEvent(
        type="run.goal.completed",
        correlation_id="RUN-SIM",
        payload={
            "run_id": "RUN-SIM",
            "completed_task_ids": ["TASK-SIM"],
        },
    )
    log.append(terminal)
    order: list[str] = []

    class FakeOrchestrator:
        def run_once(self, *, events, consumed_offset):  # noqa: ANN001
            order.append(f"orchestrator:{events[0].type}")
            log.append(ZfEvent(
                type="task.status_changed",
                task_id="TASK-SIM",
                payload={"from": "in_progress", "to": "done"},
            ))

    class DenyRateLimiter:
        def allow(self, _event_type: str) -> bool:
            return False

    def stop_watcher() -> None:
        order.append("stop")

    stopped = process_watched_batch(
        [terminal],
        consumed_offset=log.current_offset(),
        wake_patterns=set(),
        wake_worthy_fn=lambda _event: False,
        rate_limiter=DenyRateLimiter(),
        orchestrator=FakeOrchestrator(),
        simulation=True,
        event_log=log,
        stop_watcher=stop_watcher,
    )

    assert stopped is True
    assert order == ["orchestrator:run.goal.completed", "stop"]
    assert [event.type for event in log.read_all()] == [
        "run.goal.completed",
        "task.status_changed",
        "simulation.done",
    ]


def test_startup_catchup_uses_durable_offset_path():
    from zf.cli.start import _run_startup_orchestrator_catchup

    calls = []

    class FakeLog:
        def append(self, event):  # noqa: ANN001
            raise AssertionError(f"unexpected failure event: {event}")

    class FakeOrchestrator:
        def run_once(self, *args, **kwargs):
            calls.append((args, kwargs))
            return []

    _run_startup_orchestrator_catchup(FakeOrchestrator(), FakeLog())

    assert calls == [((), {})]


def test_startup_catchup_skips_diagnostic_modes():
    from zf.cli.start import _maybe_run_startup_orchestrator_catchup

    calls = []

    class FakeLog:
        def append(self, event):  # noqa: ANN001
            raise AssertionError(f"unexpected failure event: {event}")

    class FakeOrchestrator:
        def run_once(self, *args, **kwargs):
            calls.append((args, kwargs))
            return []

    orchestrator = FakeOrchestrator()
    assert _maybe_run_startup_orchestrator_catchup(
        orchestrator,
        FakeLog(),
        dry_run=True,
        foreground=True,
    ) is False
    assert _maybe_run_startup_orchestrator_catchup(
        orchestrator,
        FakeLog(),
        dry_run=False,
        foreground=False,
    ) is False
    assert calls == []

    assert _maybe_run_startup_orchestrator_catchup(
        orchestrator,
        FakeLog(),
        dry_run=False,
        foreground=True,
    ) is True
    assert calls == [((), {})]


def test_control_plane_only_start_preserves_existing_worker_transport():
    from unittest.mock import MagicMock

    from zf.cli.start import _initialize_start_transport

    transport = MagicMock()
    transport.is_session_running.return_value = True

    ready = _initialize_start_transport(
        transport,
        control_plane_only=True,
        exclude_roles=set(),
    )

    assert ready is True
    transport.init.assert_not_called()


def test_control_plane_only_start_fails_closed_without_worker_transport():
    from unittest.mock import MagicMock

    from zf.cli.start import _initialize_start_transport

    transport = MagicMock()
    transport.is_session_running.return_value = False

    ready = _initialize_start_transport(
        transport,
        control_plane_only=True,
        exclude_roles=set(),
    )

    assert ready is False
    transport.init.assert_not_called()


def test_control_plane_only_start_does_not_requeue_live_worker_tasks(
    tmp_path: Path,
):
    from unittest.mock import patch

    from zf.cli.start import _requeue_inflight_for_start

    with patch(
        "zf.runtime.shutdown.requeue_stale_inflight_tasks",
    ) as requeue:
        reconciled = _requeue_inflight_for_start(
            state_dir=tmp_path,
            config=object(),
            control_plane_only=True,
        )

    assert reconciled is False
    requeue.assert_not_called()


def test_record_ready_worker_state_clears_stale_respawning(tmp_path: Path):
    from zf.cli.start import _record_ready_worker_state
    from zf.core.events.log import EventLog
    from zf.core.state.role_sessions import RoleSessionRegistry

    event_log = EventLog(tmp_path / "events.jsonl")
    registry = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    registry.record_heartbeat("verify-lane-0", {
        "instance_id": "verify-lane-0",
        "state": "respawning",
        "current_task_id": "",
    })

    _record_ready_worker_state(
        event_log=event_log,
        registry=registry,
        instance_id="verify-lane-0",
    )

    _last_at, payload = registry.get_last_heartbeat("verify-lane-0")
    assert payload is not None
    assert payload["state"] == "idle"
    events = event_log.read_all()
    assert events[-1].type == "worker.state.changed"
    assert events[-1].actor == "verify-lane-0"
    assert events[-1].payload["from"] == "respawning"
    assert events[-1].payload["to"] == "idle"


def test_record_ready_worker_state_does_not_overwrite_busy(tmp_path: Path):
    from zf.cli.start import _record_ready_worker_state
    from zf.core.events.log import EventLog
    from zf.core.state.role_sessions import RoleSessionRegistry

    event_log = EventLog(tmp_path / "events.jsonl")
    registry = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    registry.record_heartbeat("dev-lane-0", {
        "instance_id": "dev-lane-0",
        "state": "busy",
        "current_task_id": "CJMIN-PI-CORE-001",
    })

    _record_ready_worker_state(
        event_log=event_log,
        registry=registry,
        instance_id="dev-lane-0",
    )

    _last_at, payload = registry.get_last_heartbeat("dev-lane-0")
    assert payload is not None
    assert payload["state"] == "busy"
    assert event_log.read_all() == []


def test_record_ready_worker_state_refreshes_stale_idle_heartbeat(tmp_path: Path):
    from zf.cli.start import _record_ready_worker_state
    from zf.core.events.log import EventLog
    from zf.core.state.role_sessions import RoleSessionRegistry

    event_log = EventLog(tmp_path / "events.jsonl")
    registry = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    registry.record_heartbeat("orchestrator", {
        "instance_id": "orchestrator",
        "state": "idle",
        "current_task_id": "",
        "source": "old-runtime",
    })

    _record_ready_worker_state(
        event_log=event_log,
        registry=registry,
        instance_id="orchestrator",
    )

    _last_at, payload = registry.get_last_heartbeat("orchestrator")
    assert payload is not None
    assert payload["state"] == "idle"
    assert payload["source"] == "zf.start.ready"
    assert payload["reason"] == "worker pane ready on zf start"
    assert event_log.read_all() == []


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch):
    """Set up a minimal project directory with zf.yaml and .zf/."""
    monkeypatch.chdir(tmp_path)

    # Create zf.yaml
    config = {
        "version": "1.0",
        "project": {"name": "test-project", "state_dir": ".zf"},
        "session": {"tmux_session": "test-zf"},
        "orchestrator": {"backend": "mock"},
        "roles": [
            {"name": "dev", "backend": "mock"},
        ],
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))

    # Run zf init
    main(["init"])

    return tmp_path


class TestZfStart:
    def test_start_registered_in_cli(self):
        """zf start should be a recognized command."""
        with pytest.raises(SystemExit) as exc_info:
            main(["start", "--help"])
        assert exc_info.value.code == 0

    def test_simulation_rejects_normal_project_scope(
        self,
        project_dir: Path,
        capsys,
    ):
        result = main(["start", "--dry-run", "--simulation"])
        assert result == 1
        error = capsys.readouterr().err
        assert "direct child" in error
        assert "/tmp/zf-*" in error

    def test_start_requires_zf_yaml(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = main(["start", "--dry-run"])
        assert result != 0

    def test_start_requires_init(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
        result = main(["start", "--dry-run"])
        assert result != 0

    def test_start_requires_session_yaml_even_when_state_dir_exists(
        self,
        tmp_path: Path,
        monkeypatch,
        capsys,
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "zf.yaml").write_text(
            'version: "1.0"\n'
            "project:\n"
            "  name: test\n"
            "  state_dir: .zf-custom\n",
            encoding="utf-8",
        )
        (tmp_path / ".zf-custom").mkdir()

        result = main(["start", "--dry-run"])

        assert result == 1
        captured = capsys.readouterr()
        assert ".zf-custom/session.yaml" in captured.err
        assert "zf init --state-dir .zf-custom" in captured.err

    def test_start_rejects_tool_closure_errors(
        self,
        project_dir: Path,
        capsys,
    ):
        (project_dir / "zf.yaml").write_text(
            yaml.dump({
                "version": "1.0",
                "project": {"name": "test-project", "state_dir": ".zf"},
                "session": {"tmux_session": "test-zf"},
                "roles": [{
                    "name": "dev",
                    "backend": "claude-code",
                    "permission_mode": "allowlist",
                    "allowed_tools": ["*"],
                }],
            })
        )

        result = main(["start", "--dry-run"])

        assert result == 1
        captured = capsys.readouterr()
        assert "tool closure" in captured.err.lower()
        assert "wildcard" in captured.err.lower()

    def test_start_rejects_workflow_preflight_stop(
        self,
        project_dir: Path,
        capsys,
    ):
        (project_dir / "zf.yaml").write_text(
            yaml.dump({
                "version": "1.0",
                "project": {"name": "test-project", "state_dir": ".zf"},
                "session": {"tmux_session": "test-zf"},
                "roles": [{
                    "name": "dev",
                    "backend": "mock",
                    "triggers": ["task.dispatched"],
                    "publishes": ["dev.done"],
                }],
            })
        )

        result = main(["start", "--dry-run"])

        assert result == 1
        captured = capsys.readouterr()
        assert "workflow preflight" in captured.err.lower()
        assert "terminal_event_without_producer" in captured.err

    def test_start_can_skip_workflow_preflight_for_diagnosis(
        self,
        project_dir: Path,
        capsys,
    ):
        (project_dir / "zf.yaml").write_text(
            yaml.dump({
                "version": "1.0",
                "project": {"name": "test-project", "state_dir": ".zf"},
                "session": {"tmux_session": "test-zf"},
                "roles": [{
                    "name": "dev",
                    "backend": "mock",
                    "triggers": ["task.dispatched"],
                    "publishes": ["dev.done"],
                }],
            })
        )

        result = main(["start", "--dry-run", "--skip-workflow-inspect"])

        assert result == 0
        captured = capsys.readouterr()
        assert "dry-run" in captured.out.lower()

    def test_start_preflight_filters_expected_parity_bridge_warnings(
        self,
        project_dir: Path,
        capsys,
    ):
        (project_dir / "zf.yaml").write_text("""\
version: "1.0"
project: {name: hand-written-refactor, state_dir: .zf}
session: {tmux_session: test-zf}
roles:
- name: verify-lane-0
  backend: mock
  instance_id: verify-lane-0
  role_kind: reader
- name: module-parity-scan
  backend: mock
  instance_id: module-parity-scan
  role_kind: reader
- name: judge-refactor
  backend: mock
  instance_id: judge-refactor
  role_kind: reader
workflow:
  dag:
    external_triggers: [candidate.ready, module.parity.closed]
  stages:
  - id: verify
    trigger: candidate.ready
    topology: fanout_reader
    roles: [verify-lane-0]
    aggregate:
      mode: wait_for_all
      child_success_event: verify.child.completed
      child_failure_event: verify.child.failed
      success_event: verify.passed
      failure_event: verify.failed
  - id: module-parity
    trigger: verify.parity_scan.requested
    topology: fanout_reader
    roles: [module-parity-scan]
    aggregate:
      mode: wait_for_all
      child_success_event: module.parity.child.completed
      child_failure_event: module.parity.child.failed
      success_event: cangjie.module.parity.scan.completed
      failure_event: cangjie.module.parity.scan.failed
  - id: judge
    trigger: module.parity.closed
    topology: fanout_reader
    roles: [judge-refactor]
    aggregate:
      mode: wait_for_all
      child_success_event: judge.child.completed
      child_failure_event: judge.child.failed
      success_event: judge.passed
      failure_event: judge.failed
""")

        result = main(["start", "--dry-run"])

        assert result == 0
        captured = capsys.readouterr()
        assert "event_without_consumer" not in captured.err
        assert "dry-run" in captured.out.lower()

    def test_start_dry_run_succeeds(self, project_dir: Path, capsys, monkeypatch):
        from zf.cli import start

        batches = []
        original = start.process_watched_batch

        def record_batch(events, **kwargs):
            batches.append((events, kwargs["consumed_offset"]))
            return original(events, **kwargs)

        monkeypatch.setattr(start, "process_watched_batch", record_batch)
        result = main(["start", "--dry-run"])
        assert result == 0
        assert len(batches) == 1
        assert any(event.type == "loop.started" for event in batches[0][0])
        assert batches[0][1] > 0
        captured = capsys.readouterr()
        assert "started" in captured.out.lower() or "dry" in captured.out.lower()
        assert (
            project_dir
            / ".zf"
            / "artifacts"
            / "workflow-inspect"
            / "inspect.json"
        ).exists()

    def test_start_dry_run_wires_enabled_feishu_projection_sidecar(
        self,
        project_dir: Path,
        monkeypatch,
    ):
        config = yaml.safe_load(
            (project_dir / "zf.yaml").read_text(encoding="utf-8")
        )
        config["runtime"] = {
            "feishu_projection": {
                "enabled": True,
                "backend": "lark-cli",
            }
        }
        (project_dir / "zf.yaml").write_text(
            yaml.safe_dump(config),
            encoding="utf-8",
        )
        monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
        monkeypatch.setenv("FEISHU_APP_SECRET", "secret")
        monkeypatch.setenv("FEISHU_BITABLE_APP_TOKEN", "app")
        monkeypatch.setenv("FEISHU_BITABLE_TABLE_ID", "tbl")

        result = main(["start", "--dry-run"])

        assert result == 0
        events = [
            json.loads(line)
            for line in (
                project_dir / ".zf" / "events.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        started = [
            event
            for event in events
            if event["type"] == "feishu.kanban_projection.started"
        ]
        assert started
        assert started[-1]["payload"]["dry_run"] is True

    def test_start_registers_on_demand_role_without_spawning_or_materializing(
        self,
        project_dir: Path,
        capsys,
    ) -> None:
        (project_dir / "zf.yaml").write_text(
            yaml.safe_dump({
                "version": "1.0",
                "project": {"name": "test-project", "state_dir": ".zf"},
                "session": {"tmux_session": "test-zf"},
                "roles": [{
                    "name": "dev",
                    "backend": "claude-code",
                    "skills": ["zf-cr"],
                    "lifecycle": {
                        "mode": "on_demand",
                        "idle_seconds": 60,
                        "preserve_session": True,
                        "preserve_workdir": True,
                    },
                }],
            }),
            encoding="utf-8",
        )

        result = main(["start", "--dry-run"])

        assert result == 0
        assert "dormant (on demand)" in capsys.readouterr().out
        state_dir = project_dir / ".zf"
        events = [
            json.loads(line)
            for line in (state_dir / "events.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        assert any(
            event["type"] == "role.lifecycle.dormant"
            and event["payload"]["instance_id"] == "dev"
            for event in events
        )
        assert not any(
            event["type"] == "worker.launch_artifact.written"
            and event["payload"].get("instance_id") == "dev"
            for event in events
        )
        assert not (
            state_dir
            / "workdirs"
            / "dev"
            / "project"
            / ".claude"
            / "skills"
        ).exists()

    def test_start_prepares_headless_orchestrator_capabilities(
        self,
        project_dir: Path,
    ) -> None:
        (project_dir / "zf.yaml").write_text(
            yaml.safe_dump({
                "version": "1.0",
                "project": {"name": "test-project", "state_dir": ".zf"},
                "session": {"tmux_session": "test-zf"},
                "orchestrator": {"backend": "claude-code"},
                "roles": [{
                    "name": "orchestrator",
                    "backend": "claude-code",
                    "transport": "stream-json",
                    "permission_mode": "bypass",
                }],
            }),
            encoding="utf-8",
        )

        result = main(["start", "--dry-run"])

        assert result == 0
        state_dir = project_dir / ".zf"
        events = [
            json.loads(line)
            for line in (state_dir / "events.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        assert any(
            event["type"] == "worker.launch_artifact.written"
            and event["payload"].get("instance_id") == "orchestrator"
            for event in events
        )
        assert (
            state_dir / "private" / "artifact-read" / "roles"
            / "orchestrator.token"
        ).is_file()
        assert (
            state_dir / "private" / "result-submit" / "roles"
            / "orchestrator.token"
        ).is_file()

    def test_start_restores_on_demand_role_with_in_progress_task(
        self,
        project_dir: Path,
        capsys,
    ) -> None:
        (project_dir / "zf.yaml").write_text(
            yaml.safe_dump({
                "version": "1.0",
                "project": {"name": "test-project", "state_dir": ".zf"},
                "session": {"tmux_session": "test-zf"},
                "roles": [{
                    "name": "dev",
                    "backend": "claude-code",
                    "lifecycle": {
                        "mode": "on_demand",
                        "idle_seconds": 60,
                        "preserve_session": True,
                        "preserve_workdir": True,
                    },
                }],
            }),
            encoding="utf-8",
        )
        (project_dir / ".zf" / "kanban.json").write_text(
            json.dumps([{
                "id": "TASK-RESUME",
                "title": "resume owned work",
                "status": "in_progress",
                "assigned_to": "dev",
            }]),
            encoding="utf-8",
        )

        result = main(["start", "--dry-run"])

        assert result == 0
        capsys.readouterr()
        events = [
            json.loads(line)
            for line in (
                project_dir / ".zf" / "events.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(
            event["type"] == "worker.launch_artifact.written"
            and event["payload"].get("instance_id") == "dev"
            for event in events
        )
        assert not any(
            event["type"] == "role.lifecycle.dormant"
            and event["payload"].get("instance_id") == "dev"
            for event in events
        )

    def test_start_writes_run_contract_snapshot(self, project_dir: Path):
        result = main(["start", "--dry-run"])

        assert result == 0
        contract_ref = project_dir / ".zf" / "config" / "run-contract.json"
        assert contract_ref.exists()
        contract = json.loads(contract_ref.read_text(encoding="utf-8"))
        assert contract["schema_version"] == "run-contract.v1"
        assert contract["project"]["name"] == "test-project"
        assert contract["config"]["path"].endswith("zf.yaml")
        assert contract["contract_digest"]
        instructions = project_dir / ".zf" / "instructions" / "dev.md"
        assert "## Run Contract Context" in instructions.read_text(encoding="utf-8")
        hydration = json.loads(
            (project_dir / ".zf" / "projections" / "briefing-hydration.json").read_text(
                encoding="utf-8"
            )
        )
        assert hydration["schema_version"] == "briefing-hydration-report.v1"
        assert hydration["roles"][0]["has_run_contract_context"] is True

    def test_start_reconciles_research_generation_before_transport(
        self,
        project_dir: Path,
        monkeypatch,
    ):
        import zf.cli.start as start_mod
        import zf.runtime.research_generation as generation_mod

        calls: list[tuple[str, Path]] = []

        def _reconcile(*, config, state_dir, writer):  # noqa: ANN001
            calls.append((config.project.name, Path(state_dir)))
            assert writer.event_log.path == Path(state_dir) / "events.jsonl"
            return []

        original_make_transport = start_mod.make_transport

        def _make_transport(config, dry_run=False):  # noqa: ANN001
            assert calls == [("test-project", project_dir / ".zf")]
            return original_make_transport(config, dry_run=dry_run)

        monkeypatch.setattr(
            generation_mod,
            "reconcile_stale_research_generations",
            _reconcile,
        )
        monkeypatch.setattr(start_mod, "make_transport", _make_transport)

        assert main(["start", "--dry-run"]) == 0
        assert calls == [("test-project", project_dir / ".zf")]

    def test_start_fails_closed_on_strict_run_contract_drift(
        self,
        project_dir: Path,
        capsys,
    ):
        assert main(["start", "--dry-run", "--skip-workflow-inspect"]) == 0
        contract_ref = project_dir / ".zf" / "config" / "run-contract.json"
        original = json.loads(contract_ref.read_text(encoding="utf-8"))
        original.setdefault("workflow", {})["strictness"] = "full-parity"
        contract_ref.write_text(json.dumps(original), encoding="utf-8")

        changed = yaml.safe_load((project_dir / "zf.yaml").read_text(encoding="utf-8"))
        changed["roles"].append({"name": "verify", "backend": "mock"})
        (project_dir / "zf.yaml").write_text(yaml.dump(changed), encoding="utf-8")

        result = main(["start", "--dry-run", "--skip-workflow-inspect"])

        assert result == 1
        captured = capsys.readouterr()
        assert "run contract drift" in captured.err.lower()
        current = json.loads(contract_ref.read_text(encoding="utf-8"))
        assert current["contract_digest"] == original["contract_digest"]
        events = [
            json.loads(line)
            for line in (project_dir / ".zf" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        drift = [event for event in events if event["type"] == "config.run_contract.drift_detected"]
        assert drift[-1]["payload"]["severity"] == "STOP"

    def test_start_preserves_bound_manifest_for_strict_run_restart(
        self,
        project_dir: Path,
    ):
        from zf.core.config.loader import load_config
        from zf.runtime.run_contract import build_run_contract, write_run_contract

        manifest = project_dir / "workflow-input-manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": "workflow.input_manifest.v1",
            "kind": "prd",
            "strictness": "strict",
        }), encoding="utf-8")
        state_dir = project_dir / ".zf"
        original = build_run_contract(
            load_config(project_dir / "zf.yaml"),
            config_path=project_dir / "zf.yaml",
            project_root=project_dir,
            state_dir=state_dir,
            workflow_input_manifest_ref=str(manifest),
        )
        write_run_contract(state_dir, original)

        assert main(["start", "--dry-run", "--skip-workflow-inspect"]) == 0

        restarted = json.loads(
            (state_dir / "config" / "run-contract.json").read_text(encoding="utf-8")
        )
        assert restarted["contract_digest"] == original["contract_digest"]
        assert restarted["refs"]["workflow_input_manifest"] == [str(manifest)]

    def test_restart_blocks_strict_run_contract_drift_before_stop(
        self,
        project_dir: Path,
        capsys,
    ):
        assert main(["start", "--dry-run", "--skip-workflow-inspect"]) == 0
        contract_ref = project_dir / ".zf" / "config" / "run-contract.json"
        original = json.loads(contract_ref.read_text(encoding="utf-8"))
        original.setdefault("workflow", {})["strictness"] = "full-parity"
        contract_ref.write_text(json.dumps(original), encoding="utf-8")

        changed = yaml.safe_load((project_dir / "zf.yaml").read_text(encoding="utf-8"))
        changed["roles"].append({"name": "verify", "backend": "mock"})
        (project_dir / "zf.yaml").write_text(yaml.dump(changed), encoding="utf-8")

        result = main(["restart", "--dry-run"])

        assert result == 1
        captured = capsys.readouterr()
        assert "run contract drift" in captured.err.lower()
        events = [
            json.loads(line)
            for line in (project_dir / ".zf" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        checked = [event for event in events if event["type"] == "config.run_contract.resume_checked"]
        assert checked[-1]["payload"]["status"] == "STOP"
        assert not any(event["type"] == "runtime.stopped" for event in events)

    def test_recover_workflow_blocks_strict_run_contract_drift(
        self,
        project_dir: Path,
        capsys,
    ):
        assert main(["start", "--dry-run", "--skip-workflow-inspect"]) == 0
        contract_ref = project_dir / ".zf" / "config" / "run-contract.json"
        original = json.loads(contract_ref.read_text(encoding="utf-8"))
        original.setdefault("workflow", {})["strictness"] = "full-parity"
        contract_ref.write_text(json.dumps(original), encoding="utf-8")

        changed = yaml.safe_load((project_dir / "zf.yaml").read_text(encoding="utf-8"))
        changed["roles"].append({"name": "verify", "backend": "mock"})
        (project_dir / "zf.yaml").write_text(yaml.dump(changed), encoding="utf-8")

        result = main(["recover", "workflow", "--resume-pending", "--dry-run"])

        assert result == 1
        captured = capsys.readouterr()
        assert "run contract drift" in captured.err.lower()
        events = [
            json.loads(line)
            for line in (project_dir / ".zf" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        checked = [event for event in events if event["type"] == "config.run_contract.resume_checked"]
        assert checked[-1]["payload"]["status"] == "STOP"

    def test_start_writes_redacted_launch_artifact(
        self,
        project_dir: Path,
        monkeypatch,
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value-123456")

        result = main(["start", "--dry-run"])

        assert result == 0
        launch = project_dir / ".zf" / "workdirs" / "dev" / "runtime" / "launch.json"
        data = json.loads(launch.read_text(encoding="utf-8"))
        encoded = json.dumps(data)
        assert data["schema_version"] == "worker-launch.v1"
        assert data["role"] == "dev"
        assert data["backend"] == "mock"
        assert data["instructions_ref"].endswith(".zf/instructions/dev.md")
        assert data["harness_source"]["kind"] == "git_checkout"
        assert len(data["harness_source"]["commit"]) == 40
        assert data["harness_source"]["git_root"]
        assert "sk-test-secret-value-123456" not in encoded
        openai = [item for item in data["env"] if item["key"] == "OPENAI_API_KEY"]
        assert openai == [{"key": "OPENAI_API_KEY", "value": "<redacted>"}]

    def test_start_watcher_configured(self, project_dir: Path, capsys):
        main(["start", "--dry-run"])
        captured = capsys.readouterr()
        assert "watcher" in captured.out.lower() or "wake" in captured.out.lower()

    def test_start_creates_lock_file(self, project_dir: Path):
        main(["start", "--dry-run"])
        # In dry-run, lock may or may not persist (it's released on exit)
        # But the event should be emitted
        events_file = project_dir / ".zf" / "events.jsonl"
        events = events_file.read_text()
        assert "loop.started" in events or "session.started" in events

    def test_start_emits_events(self, project_dir: Path):
        main(["start", "--dry-run"])
        events_file = project_dir / ".zf" / "events.jsonl"
        lines = [line for line in events_file.read_text().strip().split("\n") if line.strip()]
        event_types = [json.loads(line)["type"] for line in lines]
        assert "loop.started" in event_types

    def test_start_warns_when_render_lock_drifted(self, project_dir: Path):
        lock = project_dir / ".zf" / "config" / "render-lock.json"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(
            json.dumps({
                "schema_version": "config-render-lock.v1",
                "input": {
                    "path": str(project_dir / "zf.yaml"),
                    "sha256": "stale-sha",
                    "profiles": [],
                },
            }) + "\n",
            encoding="utf-8",
        )

        result = main(["start", "--dry-run"])

        assert result == 0
        events_file = project_dir / ".zf" / "events.jsonl"
        lines = [line for line in events_file.read_text().strip().split("\n") if line.strip()]
        events = [json.loads(line) for line in lines]
        drift = [
            event for event in events
            if event["type"] == "config.render_lock.drift_detected"
        ]
        assert drift
        assert drift[-1]["payload"]["reason"] == "config sha256 changed"

    def test_start_updates_session(self, project_dir: Path):
        main(["start", "--dry-run"])
        session = yaml.safe_load((project_dir / ".zf" / "session.yaml").read_text())
        assert session["runtime_state"] in ("active", "running")


class TestZfStop:
    def test_stop_registered_in_cli(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["stop", "--help"])
        assert exc_info.value.code == 0

    def test_stop_without_running_session(self, project_dir: Path, capsys):
        result = main(["stop"])
        # Should handle gracefully (no tmux session to kill)
        assert result == 0 or result == 1

    def test_stop_emits_event_after_start(self, project_dir: Path):
        main(["start", "--dry-run"])
        main(["stop"])
        events_file = project_dir / ".zf" / "events.jsonl"
        events = events_file.read_text()
        assert "loop.stopped" in events or "session.stopped" in events

    def test_force_stop_cleans_feishu_projection_by_pidfile(
        self,
        project_dir: Path,
        monkeypatch,
    ):
        stopped = []
        monkeypatch.setattr(
            "zf.runtime.feishu_projection_sidecar."
            "stop_feishu_projection_sidecar_by_pidfile",
            lambda state_dir: stopped.append(Path(state_dir)) or True,
        )

        result = main(["stop", "--force"])

        assert result == 0
        assert stopped == [project_dir / ".zf"]


class TestBootTmuxErrorFailClosed:
    """ZF-E2E-PRDCTL-P2-7-6:boot spawn TmuxError → 清理 + 放锁 + 非零退出
    (deepwater boot 僵尸持锁:异常冒泡、前台路径 finally 不放锁)。"""

    def test_tmux_error_during_boot_releases_lock_and_exits_nonzero(
        self,
        project_dir: Path,
        monkeypatch,
        capsys,
    ):
        from zf.runtime.tmux import TmuxError
        import zf.cli.start as start_mod

        def _boom(config, dry_run=False):
            raise TmuxError("capture-pane failed: pane %2685 vanished")

        monkeypatch.setattr(start_mod, "make_transport", _boom)
        killed_sessions: list[list[str]] = []

        import subprocess as real_subprocess
        real_run = real_subprocess.run

        def _record_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd[:2] == ["tmux", "kill-session"]:
                killed_sessions.append(cmd)
                return real_subprocess.CompletedProcess(cmd, 0, "", "")
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr(real_subprocess, "run", _record_run)

        result = main(["start"])

        assert result == 1
        captured = capsys.readouterr()
        assert "tmux boot failed" in captured.err
        state_dir = project_dir / ".zf"
        # 锁文件已被显式清理;能再次拿锁 = 没有僵尸持锁。
        assert not (state_dir / "loop.lock").exists()
        assert killed_sessions, "boot cleanup must kill created tmux sessions"
