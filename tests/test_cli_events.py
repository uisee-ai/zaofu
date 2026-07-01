"""Tests for zf emit and zf events commands."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.signing import EventSigner
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


def _init(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
    main(["init"])


def _init_with_state_dir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\n  state_dir: runtime-state\n'
    )
    main(["init"])


def test_emit_appends_event(tmp_path: Path, monkeypatch, capsys):
    _init(tmp_path, monkeypatch)
    result = main(["emit", "dev.build.done"])
    assert result == 0
    captured = capsys.readouterr()
    assert "dev.build.done" in captured.out
    event = next(
        e for e in EventLog(tmp_path / ".zf" / "events.jsonl").read_all()
        if e.type == "dev.build.done"
    )
    index = json.loads((tmp_path / ".zf" / "event_index.json").read_text())
    assert event.id in index["event_by_id"]


def test_emit_with_payload(tmp_path: Path, monkeypatch, capsys):
    _init(tmp_path, monkeypatch)
    result = main(["emit", "test.passed", "--payload", '{"count": 5}'])
    assert result == 0
    events_text = (tmp_path / ".zf" / "events.jsonl").read_text()
    assert '"count":5' in events_text or '"count": 5' in events_text


def test_emit_with_payload_file(tmp_path: Path, monkeypatch):
    _init(tmp_path, monkeypatch)
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"count": 5}), encoding="utf-8")

    result = main(["emit", "test.passed", "--payload-file", str(payload)])

    assert result == 0
    events = EventLog(tmp_path / ".zf" / "events.jsonl").read_all()
    event = next(e for e in events if e.type == "test.passed")
    assert event.payload == {"count": 5}


def test_artifact_manifest_create_outputs_hash_and_canonical_kind(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    _init(tmp_path, monkeypatch)
    capsys.readouterr()
    (tmp_path / "SPEC.md").write_text("spec\n", encoding="utf-8")
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "plan.md").write_text("plan\n", encoding="utf-8")

    result = main([
        "artifact",
        "manifest",
        "create",
        "--task",
        "TASK-1",
        "--role",
        "arch",
        "--kind",
        "sdd=SPEC.md",
        "--kind",
        "full_stage_plan=tasks/plan.md",
    ])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    refs = data["manifest"]["artifact_refs"]
    assert [ref["kind"] for ref in refs] == ["spec", "implementation_plan"]
    assert refs[0]["sha256"] == hashlib.sha256(b"spec\n").hexdigest()
    assert refs[1]["status"] == "proposed"


def test_artifact_manifest_create_emit_appends_event(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    _init(tmp_path, monkeypatch)
    capsys.readouterr()
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "todo.md").write_text("todo\n", encoding="utf-8")

    result = main([
        "artifact",
        "manifest",
        "create",
        "--task",
        "TASK-1",
        "--role",
        "orchestrator",
        "--status",
        "accepted",
        "--kind",
        "p3_backlog=tasks/todo.md",
        "--emit",
    ])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["event_id"].startswith("evt-")
    events = EventLog(tmp_path / ".zf" / "events.jsonl").read_all()
    event = next(e for e in events if e.type == "artifact.manifest.published")
    assert event.payload["artifact_refs"][0]["kind"] == "backlog_plan"
    index = json.loads((tmp_path / ".zf" / "event_index.json").read_text())
    assert data["event_id"] in index["event_by_id"]


def test_artifact_manifest_create_uses_workdir_source_root(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    _init(tmp_path, monkeypatch)
    capsys.readouterr()
    workdir = tmp_path / ".zf" / "workdirs" / "arch" / "project"
    artifact = workdir / "docs" / "plans" / "task-plan.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("workdir plan\n", encoding="utf-8")

    result = main([
        "artifact",
        "manifest",
        "create",
        "--task",
        "TASK-1",
        "--role",
        "arch",
        "--workdir",
        str(workdir),
        "--kind",
        "full_stage_plan=docs/plans/task-plan.md",
    ])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    ref = data["manifest"]["artifact_refs"][0]
    assert ref["workdir_path"] == str(workdir)
    assert ref["sha256"] == hashlib.sha256(b"workdir plan\n").hexdigest()


def test_artifact_manifest_create_rejects_missing_file(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    _init(tmp_path, monkeypatch)
    capsys.readouterr()

    result = main([
        "artifact",
        "manifest",
        "create",
        "--task",
        "TASK-1",
        "--role",
        "arch",
        "--kind",
        "spec=missing.md",
    ])

    assert result == 2
    assert "artifact file not found" in capsys.readouterr().err


def test_emit_rejects_payload_file_and_inline_payload_together(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    _init(tmp_path, monkeypatch)
    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")

    result = main([
        "emit",
        "test.passed",
        "--payload",
        "{}",
        "--payload-file",
        str(payload),
    ])

    assert result == 1
    assert "use only one" in capsys.readouterr().out


def test_emit_rejects_payload_file_with_non_object_json(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    _init(tmp_path, monkeypatch)
    payload = tmp_path / "payload.json"
    payload.write_text("[]", encoding="utf-8")

    result = main(["emit", "test.passed", "--payload-file", str(payload)])

    assert result == 1
    assert "JSON object" in capsys.readouterr().out


def test_emit_dispatch_id_adds_payload_field(tmp_path: Path, monkeypatch):
    _init(tmp_path, monkeypatch)

    result = main([
        "emit",
        "dev.build.done",
        "--task",
        "T1",
        "--dispatch-id",
        "disp-123",
    ])

    assert result == 0
    events = EventLog(tmp_path / ".zf" / "events.jsonl").read_all()
    event = next(e for e in events if e.type == "dev.build.done")
    assert event.payload["dispatch_id"] == "disp-123"


def test_emit_quiesces_resident_observation_after_terminal_run(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    _init(tmp_path, monkeypatch)
    log = EventLog(tmp_path / ".zf" / "events.jsonl")
    log.append(ZfEvent(type="run.completed", payload={"status": "passed"}))

    result = main([
        "emit",
        "run.manager.agent.observation",
        "--actor",
        "run-manager",
        "--payload",
        '{"status":"watching"}',
    ])

    assert result == 0
    assert "Suppressed" in capsys.readouterr().out
    assert "run.manager.agent.observation" not in [
        event.type for event in log.read_all()
    ]


def test_emit_invalid_json_payload(tmp_path: Path, monkeypatch, capsys):
    _init(tmp_path, monkeypatch)
    result = main(["emit", "test.event", "--payload", "not json"])
    assert result == 1


def test_emit_not_initialized(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    result = main(["emit", "test.event"])
    assert result == 1


def test_events_lists_all(tmp_path: Path, monkeypatch, capsys):
    _init(tmp_path, monkeypatch)
    main(["emit", "a"])
    main(["emit", "b"])
    capsys.readouterr()  # clear
    result = main(["events"])
    assert result == 0
    captured = capsys.readouterr()
    assert "session.started" in captured.out
    assert "a" in captured.out
    assert "b" in captured.out


def test_events_filter_by_type(tmp_path: Path, monkeypatch, capsys):
    _init(tmp_path, monkeypatch)
    main(["emit", "dev.build.done"])
    main(["emit", "test.passed"])
    capsys.readouterr()
    result = main(["events", "--type", "dev.build.done"])
    assert result == 0
    captured = capsys.readouterr()
    assert "dev.build.done" in captured.out
    assert "test.passed" not in captured.out


def test_events_last_n(tmp_path: Path, monkeypatch, capsys):
    _init(tmp_path, monkeypatch)
    for i in range(5):
        main(["emit", f"event.{i}"])
    capsys.readouterr()
    result = main(["events", "--last", "2"])
    assert result == 0
    captured = capsys.readouterr()
    assert "event.4" in captured.out
    assert "event.3" in captured.out


def test_events_not_initialized(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    result = main(["events"])
    assert result == 1


def test_emit_uses_project_state_dir(tmp_path: Path, monkeypatch):
    _init_with_state_dir(tmp_path, monkeypatch)

    result = main(["emit", "dev.build.done"])

    assert result == 0
    events_text = (tmp_path / "runtime-state" / "events.jsonl").read_text()
    assert "dev.build.done" in events_text
    assert not (tmp_path / ".zf").exists()


def test_emit_explicit_state_dir_overrides_project_config(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\n  state_dir: runtime-state\n'
    )
    override = tmp_path / "override-state"
    override.mkdir()
    (override / "events.jsonl").write_text("", encoding="utf-8")

    result = main(["emit", "dev.build.done", "--state-dir", str(override)])

    assert result == 0
    assert "dev.build.done" in (override / "events.jsonl").read_text()
    assert not (tmp_path / "runtime-state").exists()


def test_emit_task_contract_update_writes_through_to_kanban(
    tmp_path: Path,
    monkeypatch,
):
    _init(tmp_path, monkeypatch)
    store = TaskStore(tmp_path / ".zf" / "kanban.json")
    store.add(Task(id="T1", title="x"))

    result = main([
        "emit",
        "task.contract.update",
        "--task",
        "T1",
        "--payload",
        json.dumps({
            "contract": {
                "goal": "Implement greet.",
                "verification_command": "python3 -m pytest tests/test_greet.py",
                "scope": {"create": ["src/greet.py", "tests/test_greet.py"]},
                "acceptance": ["python3 -m pytest tests/test_greet.py passes"],
                "owner_role": "dev",
                "spec_ref": "docs/specs/spec.md",
                "plan_ref": "docs/plans/plan.md",
                "tdd_ref": "docs/plans/tdd.md",
                "critic_gate_ref": "docs/plans/critic-gate.md",
                "critic_event_id": "evt-critic",
                "evidence_contract": {"dev_done_must_include": ["dispatch_id"]},
                "wave": 2,
                "shared_files": ["src/greet.py"],
                "exclusive_files": ["tests/test_greet.py"],
                "handoff_artifacts": ["docs/greet.md"],
            }
        }),
    ])

    assert result == 0
    task = store.get("T1")
    assert task.contract.behavior == "Implement greet."
    assert task.contract.verification == "python3 -m pytest tests/test_greet.py"
    assert task.contract.scope == ["src/greet.py", "tests/test_greet.py"]
    assert task.contract.owner_role == "dev"
    assert task.contract.spec_ref == "docs/specs/spec.md"
    assert task.contract.plan_ref == "docs/plans/plan.md"
    assert task.contract.tdd_ref == "docs/plans/tdd.md"
    assert task.contract.critic_gate_ref == "docs/plans/critic-gate.md"
    assert task.contract.critic_event_id == "evt-critic"
    assert task.contract.evidence_contract == {
        "dev_done_must_include": ["dispatch_id"],
    }
    assert task.contract.wave == 2
    assert task.contract.shared_files == ["src/greet.py"]
    assert task.contract.exclusive_files == ["tests/test_greet.py"]
    assert task.contract.handoff_artifacts == ["docs/greet.md"]


def test_emit_task_contract_update_projects_blocked_by_to_task(
    tmp_path: Path,
    monkeypatch,
):
    _init(tmp_path, monkeypatch)
    store = TaskStore(tmp_path / ".zf" / "kanban.json")
    store.add(Task(id="T1", title="base", status="done"))
    store.add(Task(id="T2", title="follow"))

    result = main([
        "emit",
        "task.contract.update",
        "--task",
        "T2",
        "--payload",
        json.dumps({
            "contract": {
                "behavior": "Follow base.",
                "verification": "pytest",
                "blocked_by": ["T1"],
            }
        }),
    ])

    assert result == 0
    task = store.get("T2")
    assert task.blocked_by == ["T1"]
    assert task.contract.behavior == "Follow base."


def test_emit_signs_when_event_signing_enabled(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ZF_EVENT_SECRET", "shared-secret")
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: test\n"
        "security:\n"
        "  event_signing:\n"
        "    enabled: true\n"
    )
    main(["init"])

    result = main(["emit", "dev.build.done"])

    assert result == 0
    raw = (tmp_path / ".zf" / "events.jsonl").read_text().splitlines()
    assert raw
    assert all('"event"' in line and '"sig"' in line for line in raw)
    events = EventLog(
        tmp_path / ".zf" / "events.jsonl",
        signer=EventSigner(b"shared-secret"),
    ).read_all()
    assert "dev.build.done" in [event.type for event in events]
