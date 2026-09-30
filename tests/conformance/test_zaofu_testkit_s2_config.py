"""S2 conformance cases for configuration and CLI preflight boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from tests.testkit.runtime_case_evidence import attach_runtime_case_evidence


@pytest.mark.parametrize(
    "argv",
    (
        ("events", "--last", "10", "--json"),
        ("runs", "list", "--json"),
        ("trace", "spans"),
        ("metrics", "snapshot", "--format", "json"),
        ("projection", "status", "--json"),
        ("feature", "list"),
        ("goal", "show", "--json"),
        ("kanban", "--json"),
    ),
    ids=("events", "runs", "trace", "metrics", "projection", "feature", "goal", "kanban"),
)
def test_read_only_cli_surface_uses_configured_state_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    request,
    argv: tuple[str, ...],
) -> None:
    """Representative read-only commands must not fall back to ``.zf``."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-cli-surface\n"
        "  state_dir: runtime-state\n"
        "roles: []\n",
        encoding="utf-8",
    )
    attach_runtime_case_evidence(
        request, tmp_path / "runtime-state",
        given="a project declares an explicit non-default state_dir",
        when=f"run read-only CLI surface {argv!r} after init",
        then="the command reads and writes only the configured state root",
        forbidden=["checkout .zf creation", "state outside configured root"],
        inputs={"argv": list(argv)},
    )

    assert main(["init"]) == 0
    capsys.readouterr()
    assert main(list(argv)) == 0
    capsys.readouterr()

    state_dir = tmp_path / "runtime-state"
    assert state_dir.exists()
    assert not (tmp_path / ".zf").exists()
    assert all(path.is_relative_to(state_dir) for path in state_dir.rglob("*"))


@pytest.mark.parametrize(
    "argv",
    (
        ("memory", "show"),
        ("metrics", "snapshot", "--format", "json"),
        ("cost", "--json"),
        ("state", "retention-plan", "--json"),
    ),
    ids=("memory", "metrics", "cost", "state-retention"),
)
def test_nested_diagnostic_cli_surfaces_share_project_state_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    request,
    argv: tuple[str, ...],
) -> None:
    """Diagnostic commands launched below root must not create a second ``.zf``."""
    project_root = tmp_path / "project"
    nested = project_root / "src" / "worker"
    nested.mkdir(parents=True)
    state_dir = project_root / "runtime-state"
    (project_root / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-nested-diagnostics\n"
        "  state_dir: runtime-state\n"
        "roles: []\n",
        encoding="utf-8",
    )
    attach_runtime_case_evidence(
        request, state_dir,
        given="a diagnostic CLI is launched from a nested project directory",
        when=f"run nested diagnostic command {argv!r}",
        then="diagnostics use the owning project state root",
        forbidden=["nested .zf creation", "project-root .zf creation"],
        inputs={"argv": list(argv), "nested": str(nested)},
    )
    monkeypatch.chdir(project_root)
    assert main(["init"]) == 0
    capsys.readouterr()

    monkeypatch.chdir(nested)
    assert main(list(argv)) == 0
    output = capsys.readouterr().out

    assert state_dir.exists()
    assert not (nested / ".zf").exists()
    assert not (project_root / ".zf").exists()
    if argv[0] == "state":
        assert str(state_dir.resolve()) in output
    elif argv[0] == "metrics":
        metrics = json.loads(output)
        assert metrics["events_considered"] >= 1
    assert all(path.is_relative_to(state_dir) for path in state_dir.rglob("*"))


def test_web_api_surface_uses_configured_state_dir_without_checkout_fallback(
    tmp_path: Path,
    request,
) -> None:
    """The Web projection must read and create state only below its explicit root."""
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from zf.web.server import create_app

    state_dir = tmp_path / "runtime-state"
    attach_runtime_case_evidence(
        request, state_dir,
        given="Web API is created with an explicit runtime state root",
        when="request the state projection",
        then="the response reports and uses that state root",
        forbidden=["checkout .zf creation", "state outside configured root"],
    )
    state_dir.mkdir()
    client = TestClient(create_app(state_dir, project_root=tmp_path))

    response = client.get("/api/state")

    assert response.status_code == 200
    assert response.json()["state_dir"] == str(state_dir.resolve())
    assert not (tmp_path / ".zf").exists()
    assert all(path.is_relative_to(state_dir) for path in state_dir.rglob("*"))


def test_validate_accepts_custom_state_dir_without_checkout_fallback(
    tmp_path: Path,
    monkeypatch,
    capsys,
    request,
) -> None:
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "runtime-state"
    config = tmp_path / "zf.yaml"
    attach_runtime_case_evidence(
        request, state_dir,
        given="zf.yaml declares a valid custom state_dir",
        when="run zf validate against that configuration",
        then="validation succeeds and creates no checkout fallback state",
        forbidden=[".zf outside configured root"],
    )
    config.write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-config\n"
        "  state_dir: runtime-state\n",
        encoding="utf-8",
    )

    assert main(["validate", "--path", str(config)]) == 0
    captured = capsys.readouterr()
    assert "valid" in (captured.out + captured.err).lower()
    assert not (tmp_path / ".zf").exists()
    assert state_dir.exists()
    assert all(
        path.is_relative_to(state_dir)
        for path in state_dir.rglob("*")
    )


def test_cli_state_dir_override_beats_configured_state_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    request,
) -> None:
    """An explicit CLI state root must be reflected in command identity."""
    monkeypatch.chdir(tmp_path)
    configured = tmp_path / "configured-state"
    override = tmp_path / "override-state"
    attach_runtime_case_evidence(
        request, override,
        given="configuration and CLI provide different state roots",
        when="initialize and query events with an explicit CLI override",
        then="command identity and writes use the override root",
        forbidden=[".zf fallback", "override/configured state mixing"],
        inputs={"override": str(override)},
    )
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-state-override\n"
        "  state_dir: configured-state\n"
        "roles: []\n",
        encoding="utf-8",
    )

    assert main(["init"]) == 0
    capsys.readouterr()
    assert main(["init", "--state-dir", str(override), "--no-workspace-register"]) == 0
    capsys.readouterr()
    assert main([
        "events",
        "--state-dir",
        str(override),
        "--last",
        "1",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["identity"]["state_dir"] == str(override.resolve())
    assert override.exists()
    assert configured.exists()
    assert not (tmp_path / ".zf").exists()


def test_validate_rejects_missing_project_with_nonzero_exit(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "invalid.yaml"
    config.write_text("version: '1.0'\nroles: []\n", encoding="utf-8")

    assert main(["validate", "--path", str(config)]) == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "project" in output.lower() or "error" in output.lower()


def test_cold_start_rejects_uninitialized_workspace(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "zf.yaml"
    config.write_text(
        "version: '1.0'\nproject:\n  name: s2-cold-start\n",
        encoding="utf-8",
    )

    assert main(["validate", "--path", str(config), "--cold-start"]) == 1
    assert "fail" in capsys.readouterr().out.lower()


def test_emit_honors_custom_state_dir_without_checkout_fallback(
    tmp_path: Path,
    monkeypatch,
    capsys,
    request,
) -> None:
    """The worker-facing CLI must append to the configured runtime ledger."""

    monkeypatch.delenv("ZF_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("ZF_STATE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "runtime-state"
    attach_runtime_case_evidence(
        request, state_dir,
        given="worker-facing emit runs with a configured state_dir",
        when="emit one task.created event",
        then="the event is appended to the configured ledger",
        forbidden=["checkout .zf creation", "event in another root"],
        task_id="TASK-S2-EMIT",
    )
    state_dir.mkdir()
    config = tmp_path / "zf.yaml"
    config.write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-emit\n"
        "  state_dir: runtime-state\n",
        encoding="utf-8",
    )

    assert main(["emit", "task.created", "--task", "TASK-S2-EMIT"]) == 0
    assert "Emitted: task.created" in capsys.readouterr().out

    event_log = EventLog(state_dir / "events.jsonl")
    try:
        events = event_log.read_all()
    finally:
        event_log.close()
    assert len(events) == 1
    assert events[0].type == "task.created"
    assert events[0].task_id == "TASK-S2-EMIT"
    assert not (tmp_path / ".zf").exists()


def test_doctor_honors_custom_state_dir_without_creating_checkout_state(
    tmp_path: Path,
    monkeypatch,
    capsys,
    request,
) -> None:
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "runtime-state"
    attach_runtime_case_evidence(
        request, state_dir,
        given="doctor is run for a project with a custom state_dir",
        when="execute zf doctor",
        then="diagnostics report the configured state root",
        forbidden=["checkout .zf creation"],
    )
    state_dir.mkdir()
    config = tmp_path / "zf.yaml"
    config.write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-doctor\n"
        "  state_dir: runtime-state\n"
        "roles: []\n",
        encoding="utf-8",
    )

    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "state_dir:" in output
    assert str(tmp_path / "runtime-state") in output
    assert not (tmp_path / ".zf").exists()


def test_skills_doctor_honors_custom_state_dir_without_checkout_state(
    tmp_path: Path,
    monkeypatch,
    capsys,
    request,
) -> None:
    """The skills diagnostic must resolve the same configured project root."""
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "zf.yaml"
    state_dir = tmp_path / "runtime-state"
    attach_runtime_case_evidence(
        request, state_dir,
        given="skills doctor is run for a project with a custom state_dir",
        when="execute zf skills doctor",
        then="skills diagnostics resolve the configured project root",
        forbidden=["checkout .zf creation"],
    )
    config.write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-skills-doctor\n"
        "  state_dir: runtime-state\n"
        "roles: []\n",
        encoding="utf-8",
    )

    assert main(["skills", "doctor"]) == 0
    output = capsys.readouterr().out
    assert "OK: skills" in output
    assert str(tmp_path / "runtime-state") in output
    assert not (tmp_path / ".zf").exists()


def test_status_after_init_reads_custom_state_dir(
    tmp_path: Path,
    monkeypatch,
    capsys,
    request,
) -> None:
    """The status projection must read the initialized configured state root."""
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "runtime-state"
    attach_runtime_case_evidence(
        request, state_dir,
        given="an initialized project uses a custom state_dir",
        when="run zf status after init",
        then="status reads the initialized custom runtime state",
        forbidden=["checkout .zf fallback"],
    )
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-status\n"
        "  state_dir: runtime-state\n"
        "roles: []\n",
        encoding="utf-8",
    )

    assert main(["init"]) == 0
    capsys.readouterr()
    assert main(["status"]) == 0
    output = capsys.readouterr().out
    assert "Events:" in output
    assert "session.started" in output
    assert (tmp_path / "runtime-state" / "session.yaml").exists()
    assert not (tmp_path / ".zf").exists()


def test_state_retention_plan_uses_custom_state_dir_and_protects_truth(
    tmp_path: Path,
    monkeypatch,
    capsys,
    request,
) -> None:
    """Retention inventory must inspect, not relocate, canonical runtime state."""
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "runtime-state"
    attach_runtime_case_evidence(
        request, state_dir,
        given="an initialized project has canonical runtime files",
        when="run the retention plan in dry inspection mode",
        then="protected truth files are reported from the configured root",
        forbidden=["relocating canonical files", "checkout .zf fallback"],
    )
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-retention\n"
        "  state_dir: runtime-state\n"
        "roles: []\n",
        encoding="utf-8",
    )

    assert main(["init"]) == 0
    capsys.readouterr()
    assert main(["state", "retention-plan", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["state_dir"] == str(tmp_path / "runtime-state")
    assert report["status"] == "ready"
    protected = {item["path"] for item in report["protected"]}
    assert "events.jsonl" in protected
    assert "kanban.json" in protected
    assert not (tmp_path / ".zf").exists()


def test_state_reconcile_dry_run_uses_custom_state_dir(
    tmp_path: Path,
    monkeypatch,
    capsys,
    request,
) -> None:
    """Reconcile must inspect the configured state root without mutating it."""
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "runtime-state"
    attach_runtime_case_evidence(
        request, state_dir,
        given="an initialized project has a configured runtime state root",
        when="run state reconcile in dry-run mode",
        then="reconcile inspects without mutating canonical state",
        forbidden=["checkout .zf fallback", "canonical mutation during dry-run"],
    )
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-reconcile\n"
        "  state_dir: runtime-state\n"
        "roles: []\n",
        encoding="utf-8",
    )

    assert main(["init"]) == 0
    capsys.readouterr()
    assert main(["state", "reconcile", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "nothing to reconcile" in output.lower()
    assert (tmp_path / "runtime-state" / "kanban.json").exists()
    assert not (tmp_path / ".zf").exists()


def test_state_clean_dry_run_preserves_truth_and_reports_archive_blockers(
    tmp_path: Path,
    monkeypatch,
    capsys,
    request,
) -> None:
    """Dry-run cleanup must never delete truth files or hide archive blockers."""
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "runtime-state"
    attach_runtime_case_evidence(
        request, state_dir,
        given="runtime state contains canonical files and diagnostic logs",
        when="run state clean in dry-run mode",
        then="truth files and archive blockers are reported and preserved",
        forbidden=["canonical deletion", "checkout .zf fallback"],
    )
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-clean\n"
        "  state_dir: runtime-state\n"
        "roles: []\n",
        encoding="utf-8",
    )

    assert main(["init"]) == 0
    capsys.readouterr()
    logs = tmp_path / "runtime-state" / "logs"
    logs.mkdir(exist_ok=True)
    marker = logs / "keep-until-confirmed.log"
    marker.write_text("diagnostic", encoding="utf-8")

    assert main(["state", "clean", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Truth files preserved" in output
    assert "Archive blockers" in output
    assert str(tmp_path / "runtime-state") in output
    assert marker.exists()
    assert (tmp_path / "runtime-state" / "events.jsonl").exists()
    assert not (tmp_path / ".zf").exists()


def test_state_clean_confirm_archive_removes_only_rebuildable_projections(
    tmp_path: Path,
    monkeypatch,
    capsys,
    request,
) -> None:
    """Confirmed cleanup must preserve every canonical truth file."""
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "runtime-state"
    attach_runtime_case_evidence(
        request, state_dir,
        given="runtime state has canonical truth and rebuildable projections",
        when="run state clean with explicit archive confirmation",
        then="only rebuildable projections are removed",
        forbidden=["events.jsonl deletion", "kanban.json deletion", "session.yaml deletion"],
    )
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-clean-confirm\n"
        "  state_dir: runtime-state\n"
        "roles: []\n",
        encoding="utf-8",
    )

    assert main(["init"]) == 0
    capsys.readouterr()
    state_dir = tmp_path / "runtime-state"
    (state_dir / "logs").mkdir(exist_ok=True)
    (state_dir / "logs" / "diagnostic.log").write_text("diagnostic", encoding="utf-8")
    (state_dir / "diagnostics").mkdir(exist_ok=True)

    assert main(["state", "clean", "--confirm", "--archive"]) == 0
    output = capsys.readouterr().out
    assert "Clean completed" in output
    assert not (state_dir / "logs").exists()
    assert not (state_dir / "diagnostics").exists()
    for truth_file in ("events.jsonl", "kanban.json", "session.yaml"):
        assert (state_dir / truth_file).exists()
    assert not (tmp_path / ".zf").exists()


def test_kanban_reads_tasks_from_custom_state_dir(
    tmp_path: Path,
    monkeypatch,
    capsys,
    request,
) -> None:
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "runtime-state"
    attach_runtime_case_evidence(
        request, state_dir,
        given="kanban contains a task under the configured state root",
        when="run the kanban CLI projection",
        then="the task is read from the configured root",
        forbidden=["checkout .zf read", "cross-root task leakage"],
        task_id="TASK-S2-KANBAN",
    )
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-S2-KANBAN", title="custom state task"),
    )
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-kanban\n"
        "  state_dir: runtime-state\n"
        "roles: []\n",
        encoding="utf-8",
    )

    assert main(["kanban"]) == 0
    output = capsys.readouterr().out
    assert "TASK-S2-KANBAN" in output
    assert not (tmp_path / ".zf" / "kanban.json").exists()


def test_events_trace_reads_custom_state_dir_and_causation_chain(
    tmp_path: Path,
    monkeypatch,
    capsys,
    request,
) -> None:
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "runtime-state"
    attach_runtime_case_evidence(
        request, state_dir,
        given="the configured ledger contains a parent and causally linked child event",
        when="query the child event trace",
        then="both events and their causation chain are reported",
        forbidden=["trace from another root", "checkout .zf fallback"],
    )
    config = tmp_path / "zf.yaml"
    config.write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: s2-trace\n"
        "  state_dir: runtime-state\n"
        "roles: []\n",
        encoding="utf-8",
    )
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    first = ZfEvent(type="task.created", id="EV-S2-TRACE-ROOT")
    second = ZfEvent(
        type="task.dispatched",
        id="EV-S2-TRACE-CHILD",
        causation_id=first.id,
    )
    log.append(first)
    log.append(second)

    assert main([
        "events",
        "--state-dir",
        str(state_dir),
        "trace",
        second.id,
    ]) == 0
    output = capsys.readouterr().out
    assert first.id in output and second.id in output
    assert "task.created" in output and "task.dispatched" in output
    assert not (tmp_path / ".zf").exists()
