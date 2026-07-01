"""P3/K5 (docs/impl/22-zaofu-canonical-dag.md): static_gate as
independent DAG stage between ⑤ implement and ⑦ code_review.

Triggers on dev.build.done. Runs quality_gates.static.required_checks.
Emits static_gate.passed | static_gate.failed | static_gate.skipped.
Opt-in via workflow.dag.enabled (backward compatibility).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    QualityGateConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    WorkflowDagConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.static_gate import (
    StaticCheckResult,
    StaticGateResult,
    build_static_gate_event,
    is_static_gate_enabled_for_event,
    run_static_gate,
)
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


# ---------- pure run_static_gate tests ----------


def _config_with_static(commands: list[str], *, dag_enabled: bool = True,
                        static_enabled: bool = True) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="sgtest"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        workflow=WorkflowConfig(dag=WorkflowDagConfig(enabled=dag_enabled)),
        quality_gates={
            "static": QualityGateConfig(
                enabled=static_enabled,
                required_checks=commands,
            ),
        },
    )


def test_run_static_gate_skips_when_static_not_configured(tmp_path: Path):
    cfg = ZfConfig(
        project=ProjectConfig(name="p"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock")],
        # no quality_gates entry
    )
    result = run_static_gate(config=cfg, project_root=tmp_path)
    assert result.skipped is True
    assert result.passed is True
    assert "not configured" in result.skip_reason


def test_run_static_gate_skips_when_static_disabled(tmp_path: Path):
    cfg = _config_with_static(["true"], static_enabled=False)
    result = run_static_gate(config=cfg, project_root=tmp_path)
    assert result.skipped is True
    assert "enabled=False" in result.skip_reason


def test_run_static_gate_skips_when_no_commands(tmp_path: Path):
    cfg = _config_with_static([])
    result = run_static_gate(config=cfg, project_root=tmp_path)
    assert result.skipped is True
    assert "empty" in result.skip_reason


def test_run_static_gate_pass_all(tmp_path: Path):
    cfg = _config_with_static(["true", "true"])
    result = run_static_gate(config=cfg, project_root=tmp_path)
    assert result.skipped is False
    assert result.passed is True
    assert len(result.checks) == 2
    assert all(c.passed and c.exit_code == 0 for c in result.checks)


def test_run_static_gate_first_fail(tmp_path: Path):
    cfg = _config_with_static(["false", "true"])  # first fails, second pass
    result = run_static_gate(config=cfg, project_root=tmp_path)
    assert result.skipped is False
    assert result.passed is False
    assert result.checks[0].passed is False
    assert result.checks[0].exit_code == 1
    assert result.checks[1].passed is True  # we don't short-circuit


def test_run_static_gate_truncates_long_output(tmp_path: Path):
    # echo a large blob
    blob = "x" * 8000
    cfg = _config_with_static([f"printf '%s' {blob}"])
    result = run_static_gate(config=cfg, project_root=tmp_path)
    assert result.passed is True
    assert len(result.checks[0].output) < 8000


def test_run_static_gate_invocation_error_is_caught(tmp_path: Path):
    # non-existent command — sh -c reports 127
    cfg = _config_with_static(["nonexistent_command_xyz_123"])
    result = run_static_gate(config=cfg, project_root=tmp_path)
    assert result.skipped is False
    assert result.passed is False
    assert result.checks[0].exit_code != 0


def test_run_static_gate_prefers_project_src_over_inherited_pythonpath(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project_src = tmp_path / "src"
    project_src.mkdir()
    (project_src / "gate_marker.py").write_text("VALUE = 'project'\n")

    external_src = tmp_path / "external" / "src"
    external_src.mkdir(parents=True)
    (external_src / "gate_marker.py").write_text("VALUE = 'external'\n")
    monkeypatch.setenv("PYTHONPATH", str(external_src))
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(tmp_path / "external"))

    script = tmp_path / "check_gate_env.py"
    script.write_text(
        "import os\n"
        "import gate_marker\n"
        "assert gate_marker.VALUE == 'project', gate_marker.VALUE\n"
        "assert os.environ['ZF_PROJECT_ROOT'] == os.getcwd()\n"
    )
    cfg = _config_with_static([f"python {script.name}"])

    result = run_static_gate(config=cfg, project_root=tmp_path)

    assert result.passed is True
    assert result.checks[0].exit_code == 0


def test_run_static_gate_drops_foreign_virtualenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    foreign_venv = tmp_path / "foreign" / ".venv"
    foreign_venv.mkdir(parents=True)
    monkeypatch.setenv("VIRTUAL_ENV", str(foreign_venv))

    script = tmp_path / "check_gate_venv.py"
    script.write_text(
        "import os\n"
        "assert os.environ.get('VIRTUAL_ENV', '') == ''\n"
    )
    cfg = _config_with_static([f"python {script.name}"])

    result = run_static_gate(config=cfg, project_root=tmp_path)

    assert result.passed is True
    assert result.checks[0].exit_code == 0


# ---------- is_static_gate_enabled_for_event ----------


def test_enabled_only_for_dev_build_done():
    cfg = _config_with_static(["true"])
    for event_type in ["arch.proposal.done", "test.passed", "review.approved"]:
        ev = ZfEvent(type=event_type, payload={})
        assert is_static_gate_enabled_for_event(ev, cfg) is False
    ev = ZfEvent(type="dev.build.done", payload={})
    assert is_static_gate_enabled_for_event(ev, cfg) is True


def test_disabled_when_dag_disabled():
    cfg = _config_with_static(["true"], dag_enabled=False)
    ev = ZfEvent(type="dev.build.done")
    assert is_static_gate_enabled_for_event(ev, cfg) is False


def test_disabled_when_static_disabled():
    cfg = _config_with_static(["true"], static_enabled=False)
    ev = ZfEvent(type="dev.build.done")
    assert is_static_gate_enabled_for_event(ev, cfg) is False


def test_disabled_when_no_quality_gates():
    cfg = ZfConfig(
        project=ProjectConfig(name="p"),
        session=SessionConfig(tmux_session="t"),
        roles=[],
        workflow=WorkflowConfig(dag=WorkflowDagConfig(enabled=True)),
    )
    ev = ZfEvent(type="dev.build.done")
    assert is_static_gate_enabled_for_event(ev, cfg) is False


# ---------- build_static_gate_event ----------


def test_build_event_passed():
    trigger = ZfEvent(
        type="dev.build.done", actor="dev-1", task_id="TASK-X",
        correlation_id="trace-1",
    )
    result = StaticGateResult(
        passed=True, checks=[
            StaticCheckResult(command="true", exit_code=0, passed=True,
                              output="", duration_seconds=0.1),
        ],
    )
    ev = build_static_gate_event(result, trigger_event=trigger)
    assert ev.type == "static_gate.passed"
    assert ev.task_id == "TASK-X"
    assert ev.causation_id == trigger.id
    assert ev.correlation_id == "trace-1"
    assert ev.payload["passed"] is True
    assert ev.payload["failed_count"] == 0
    assert ev.payload["trigger_event_id"] == trigger.id


def test_build_event_failed():
    trigger = ZfEvent(type="dev.build.done", task_id="TASK-Y")
    result = StaticGateResult(
        passed=False,
        checks=[
            StaticCheckResult(command="pnpm tsc", exit_code=1, passed=False,
                              output="error TS2304", duration_seconds=2.5),
        ],
    )
    ev = build_static_gate_event(result, trigger_event=trigger)
    assert ev.type == "static_gate.failed"
    assert ev.payload["passed"] is False
    assert ev.payload["failed_count"] == 1
    assert "pnpm tsc" in ev.payload["failed_commands"]


def test_build_event_skipped():
    trigger = ZfEvent(type="dev.build.done", task_id="TASK-Z")
    result = StaticGateResult(passed=True, skipped=True, skip_reason="disabled")
    ev = build_static_gate_event(result, trigger_event=trigger)
    assert ev.type == "static_gate.skipped"


def test_build_event_inherits_dispatch_id_from_trigger():
    """B-NEW-9 fix layer 1: static_gate.passed must carry dispatch_id from
    trigger so reconciler's stale-handoff guard can match it. Without this,
    static_gate.passed → review handoff strands because the guard rejects
    every kernel-emitted progress event that lacks dispatch_id."""
    trigger = ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="TASK-INHERIT",
        payload={"dispatch_id": "disp-9c2a", "state": "DONE"},
    )
    result = StaticGateResult(
        passed=True, checks=[
            StaticCheckResult(command="true", exit_code=0, passed=True,
                              output="", duration_seconds=0.1),
        ],
    )
    ev = build_static_gate_event(result, trigger_event=trigger)
    assert ev.payload["dispatch_id"] == "disp-9c2a"
    assert ev.payload["trigger_event_id"] == trigger.id


def test_build_event_no_dispatch_id_when_trigger_has_none():
    """Belt-and-suspenders: if trigger lacks dispatch_id (unexpected but
    possible for malformed events), don't set a bogus one."""
    trigger = ZfEvent(
        type="dev.build.done", task_id="TASK-NODISP",
        payload={"state": "DONE"},  # no dispatch_id
    )
    result = StaticGateResult(
        passed=True, checks=[
            StaticCheckResult(command="true", exit_code=0, passed=True,
                              output="", duration_seconds=0.1),
        ],
    )
    ev = build_static_gate_event(result, trigger_event=trigger)
    assert "dispatch_id" not in ev.payload


# ---------- orchestrator integration ----------


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="sgtest", dry_run=True))


def test_orchestrator_emits_static_gate_passed_on_dev_build_done(
    state_dir: Path, transport
):
    cfg = ZfConfig(
        project=ProjectConfig(name="p"),
        session=SessionConfig(tmux_session="sgtest"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        workflow=WorkflowConfig(dag=WorkflowDagConfig(enabled=True)),
        quality_gates={
            "static": QualityGateConfig(enabled=True, required_checks=["true"]),
        },
    )
    orch = Orchestrator(state_dir, cfg, transport)

    dev_event = ZfEvent(
        type="dev.build.done", actor="dev-1", task_id="TASK-K5",
        payload={"state": "DONE", "dispatch_id": "d-1",
                 "artifact_refs": ["a.py"], "evidence_refs": []},
    )
    orch.event_writer.append(dev_event)
    orch._apply_housekeeping(dev_event)

    log = EventLog(state_dir / "events.jsonl")
    gate_events = [e for e in log.read_all() if e.type.startswith("static_gate.")]
    assert len(gate_events) == 1
    assert gate_events[0].type == "static_gate.passed"
    assert gate_events[0].task_id == "TASK-K5"
    assert gate_events[0].causation_id == dev_event.id


def test_orchestrator_emits_static_gate_failed(state_dir: Path, transport):
    cfg = ZfConfig(
        project=ProjectConfig(name="p"),
        session=SessionConfig(tmux_session="sgtest"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        workflow=WorkflowConfig(dag=WorkflowDagConfig(enabled=True)),
        quality_gates={
            "static": QualityGateConfig(enabled=True, required_checks=["false"]),
        },
    )
    orch = Orchestrator(state_dir, cfg, transport)

    dev_event = ZfEvent(
        type="dev.build.done", actor="dev-1", task_id="TASK-FAIL",
        payload={"state": "DONE", "dispatch_id": "d-2",
                 "artifact_refs": ["a.py"], "evidence_refs": []},
    )
    orch.event_writer.append(dev_event)
    orch._apply_housekeeping(dev_event)

    log = EventLog(state_dir / "events.jsonl")
    gate_events = [e for e in log.read_all() if e.type.startswith("static_gate.")]
    assert len(gate_events) == 1
    assert gate_events[0].type == "static_gate.failed"
    assert gate_events[0].payload["failed_count"] == 1


def test_orchestrator_static_gate_runs_in_task_ref_workdir(
    tmp_path: Path,
    transport,
):
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf-test"
    workdir = state_dir / "workdirs" / "dev-1" / "project"
    workdir.mkdir(parents=True)
    (workdir / ".git").mkdir()
    (workdir / "marker.txt").write_text("dev output\n", encoding="utf-8")
    (project_root / "root-only.txt").write_text("not the gate cwd\n", encoding="utf-8")
    refs_dir = state_dir / "refs"
    refs_dir.mkdir(parents=True)
    (refs_dir / "task-index.json").write_text(
        (
            '{"TASK-WORKDIR":{"task_id":"TASK-WORKDIR",'
            f'"workdir":"{workdir.as_posix()}",'
            '"source_commit":"abc","source_branch":"worker/dev-1",'
            '"task_ref":"task/TASK-WORKDIR"}}\n'
        ),
        encoding="utf-8",
    )
    cfg = ZfConfig(
        project=ProjectConfig(name="p"),
        session=SessionConfig(tmux_session="sgtest"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        workflow=WorkflowConfig(dag=WorkflowDagConfig(enabled=True)),
        quality_gates={
            "static": QualityGateConfig(
                enabled=True,
                required_checks=["test -f marker.txt"],
            ),
        },
    )
    orch = Orchestrator(
        state_dir,
        cfg,
        transport,
        project_root=project_root,
    )

    dev_event = ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="TASK-WORKDIR",
        payload={"state": "DONE", "dispatch_id": "d-workdir"},
    )
    orch._maybe_run_static_gate(dev_event)

    log = EventLog(state_dir / "events.jsonl")
    gate_events = [e for e in log.read_all() if e.type.startswith("static_gate.")]
    assert len(gate_events) == 1
    assert gate_events[0].type == "static_gate.passed"
    assert gate_events[0].payload["workdir"] == str(workdir.resolve())


def test_orchestrator_skips_static_gate_when_dag_disabled(
    state_dir: Path, transport
):
    """Backward compat: projects without workflow.dag.enabled keep current
    behavior (no static_gate event emitted between dev and review)."""
    cfg = ZfConfig(
        project=ProjectConfig(name="p"),
        session=SessionConfig(tmux_session="sgtest"),
        roles=[RoleConfig(name="dev", backend="mock", role_kind="writer")],
        # dag NOT enabled
        quality_gates={
            "static": QualityGateConfig(enabled=True, required_checks=["false"]),
        },
    )
    orch = Orchestrator(state_dir, cfg, transport)
    dev_event = ZfEvent(
        type="dev.build.done", actor="dev-1", task_id="TASK-COMPAT",
        payload={"state": "DONE", "dispatch_id": "d-3",
                 "artifact_refs": [], "evidence_refs": []},
    )
    orch.event_writer.append(dev_event)
    orch._apply_housekeeping(dev_event)

    log = EventLog(state_dir / "events.jsonl")
    gate_events = [e for e in log.read_all() if e.type.startswith("static_gate.")]
    assert gate_events == [], "static_gate must NOT fire when dag disabled"


# ---------- rework_triage integration ----------


def test_static_gate_failed_routed_to_dev_by_default():
    from zf.runtime.rework_triage import classify_rework_trigger
    cfg = ZfConfig(
        project=ProjectConfig(name="p"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock")],
        workflow=WorkflowConfig(),  # no rework_routing override
    )
    ev = ZfEvent(
        type="static_gate.failed",
        task_id="T1",
        payload={"failed_count": 1, "failed_commands": ["pnpm tsc"]},
    )
    result = classify_rework_trigger(ev, cfg)
    assert result.classification == "product_issue"
    assert result.suspected_owner == "dev"
    assert result.should_increment_retry is True


def test_static_gate_failed_yaml_routing_overrides():
    from zf.runtime.rework_triage import classify_rework_trigger
    cfg = ZfConfig(
        project=ProjectConfig(name="p"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="arch", backend="mock")],
        workflow=WorkflowConfig(rework_routing={"static_gate.failed": "arch"}),
    )
    ev = ZfEvent(type="static_gate.failed", task_id="T2")
    result = classify_rework_trigger(ev, cfg)
    # P1/K2 priority: yaml routing wins
    assert result.classification == "yaml_routing"
    assert result.suspected_owner == "arch"


# ---------- wire-up grep self-check ----------


def test_grep_orchestrator_calls_maybe_run_static_gate():
    """Wire-up self-check: orchestrator._apply_housekeeping must invoke
    _maybe_run_static_gate on dev.build.done."""
    src = Path(__file__).resolve().parents[1] / "src/zf/runtime/orchestrator.py"
    text = src.read_text(encoding="utf-8")
    assert "_maybe_run_static_gate" in text
    assert text.count("_maybe_run_static_gate") >= 2  # def + call site


def test_grep_static_gate_module_exists():
    src = Path(__file__).resolve().parents[1] / "src/zf/runtime/static_gate.py"
    assert src.exists()
    text = src.read_text(encoding="utf-8")
    for sym in [
        "def run_static_gate",
        "def build_static_gate_event",
        "def is_static_gate_enabled_for_event",
        "class StaticGateResult",
        "class StaticCheckResult",
    ]:
        assert sym in text, f"static_gate.py must export {sym}"


def test_grep_static_gate_in_known_events():
    src = (
        Path(__file__).resolve().parents[1]
        / "src/zf/core/events/known_types.py"
    )
    text = src.read_text(encoding="utf-8")
    assert "static_gate.passed" in text
    assert "static_gate.failed" in text
    assert "static_gate.skipped" in text


def test_grep_static_gate_failed_in_triage_triggers():
    src = (
        Path(__file__).resolve().parents[1]
        / "src/zf/runtime/rework_triage.py"
    )
    text = src.read_text(encoding="utf-8")
    assert '"static_gate.failed"' in text
    # And the classifier branch for it
    assert 'if event.type == "static_gate.failed"' in text


def test_design_handoff_skill_exists():
    src = (
        Path(__file__).resolve().parents[1]
        / "skills/zf-harness-design-handoff/SKILL.md"
    )
    assert src.exists()
    text = src.read_text(encoding="utf-8")
    assert "name: zf-harness-design-handoff" in text
    assert "arch.proposal.done" in text
    assert "design.critique.done" in text
    assert "gate.failed" in text
    assert "zaofu_gate" in text  # yoke envelope reference
