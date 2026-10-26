"""Tests for zf emit and zf events commands."""

from __future__ import annotations

import json
import hashlib
from dataclasses import asdict
from pathlib import Path

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.signing import EventSigner
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task, TaskContract
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


def test_emit_schema_replacement_reports_actual_event_and_nonzero(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n  name: test\n"
        "verification:\n  event_schema:\n    mode: blocking\n"
        "workflow:\n  dag:\n    event_schemas:\n"
        "      verify.child.completed:\n"
        "        required: [fanout_id, child_id, status]\n",
        encoding="utf-8",
    )
    assert main(["init"]) == 0
    capsys.readouterr()

    result = main([
        "emit",
        "verify.child.completed",
        "--task",
        "T-1",
        "--payload",
        '{"fanout_id":"f-1","child_id":"c-1"}',
    ])

    captured = capsys.readouterr()
    assert result != 0
    assert "Emitted: discriminator.failed" in captured.out
    assert "Blocked: verify.child.completed" in captured.err
    events = EventLog(tmp_path / ".zf" / "events.jsonl").read_all()
    assert events[-1].type == "discriminator.failed"
    assert events[-1].payload["fanout_id"] == "f-1"
    assert events[-1].payload["child_id"] == "c-1"


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
    assert "use only one" in capsys.readouterr().err


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
    assert "JSON object" in capsys.readouterr().err


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


def test_emit_task_autofills_current_attempt_identity(
    tmp_path: Path,
    monkeypatch,
):
    _init(tmp_path, monkeypatch)
    TaskAttemptStore(tmp_path / ".zf" / "task_attempts.json").ensure_for_dispatch(
        run_id="RUN-1",
        task_id="T1",
        dispatch_id="disp-123",
        role="dev",
        instance_id="dev",
        operation_id="op-1",
        briefing_ref=".zf/briefings/dev-T1.md",
        created_at="2026-07-26T00:00:00+00:00",
        lease_expires_at="2026-07-27T00:00:00+00:00",
        max_attempts=3,
    )

    result = main([
        "emit",
        "dev.build.done",
        "--task",
        "T1",
        "--dispatch-id",
        "disp-123",
    ])

    assert result == 0
    event = next(
        event
        for event in EventLog(tmp_path / ".zf" / "events.jsonl").read_all()
        if event.type == "dev.build.done"
    )
    assert event.payload["workflow_run_id"] == "RUN-1"
    assert event.payload["operation_id"] == "op-1"
    assert event.payload["attempt_id"].startswith("ta-")
    assert event.payload["lease_id"].startswith("lease-")


def test_emit_rejects_stale_actor_before_binding_new_task_attempt(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    _init(tmp_path, monkeypatch)
    TaskAttemptStore(tmp_path / ".zf" / "task_attempts.json").ensure_for_dispatch(
        run_id="RUN-1",
        task_id="T1",
        dispatch_id="disp-verify",
        role="verify",
        instance_id="verify-lane-0",
        operation_id="op-verify",
        briefing_ref=".zf/briefings/verify-T1.md",
        created_at="2026-07-26T00:00:00+00:00",
        lease_expires_at="2026-07-27T00:00:00+00:00",
        max_attempts=3,
    )

    result = main([
        "emit",
        "dev.build.done",
        "--task",
        "T1",
        "--actor",
        "dev-lane-0",
    ])

    assert result == 2
    assert "owned by verify, verify-lane-0" in capsys.readouterr().err
    assert not any(
        event.type == "dev.build.done"
        for event in EventLog(tmp_path / ".zf" / "events.jsonl").read_all()
    )


def test_emit_rejects_duplicate_result_for_terminal_task_attempt(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    _init(tmp_path, monkeypatch)
    store = TaskAttemptStore(tmp_path / ".zf" / "task_attempts.json")
    attempt = store.ensure_for_dispatch(
        run_id="RUN-1",
        task_id="T1",
        dispatch_id="disp-dev",
        role="dev-lane-0",
        instance_id="dev-lane-0",
        operation_id="op-dev",
        briefing_ref=".zf/briefings/dev-T1.md",
        created_at="2026-07-26T00:00:00+00:00",
        lease_expires_at="2026-07-27T00:00:00+00:00",
        max_attempts=3,
    ).attempt
    store.update(
        attempt["attempt_id"],
        status="succeeded",
        updated_at="2026-07-26T00:01:00+00:00",
        terminal_event_id="evt-result",
    )

    result = main([
        "emit",
        "dev.build.done",
        "--task",
        "T1",
        "--actor",
        "dev-lane-0",
    ])

    assert result == 2
    assert "already terminal (status=succeeded)" in capsys.readouterr().err
    assert not any(
        event.type == "dev.build.done"
        for event in EventLog(tmp_path / ".zf" / "events.jsonl").read_all()
    )


def test_emit_dispatch_id_selects_one_of_multiple_current_attempt_lanes(
    tmp_path: Path,
    monkeypatch,
):
    _init(tmp_path, monkeypatch)
    store = TaskAttemptStore(tmp_path / ".zf" / "task_attempts.json")
    for role, operation_id, dispatch_id in (
        ("reader", "op-reader", "disp-reader"),
        ("critic", "op-critic", "disp-critic"),
    ):
        store.ensure_for_dispatch(
            run_id="RUN-1",
            task_id="T1",
            dispatch_id=dispatch_id,
            role=role,
            instance_id=role,
            operation_id=operation_id,
            briefing_ref=f".zf/briefings/{role}-T1.md",
            created_at="2026-07-26T00:00:00+00:00",
            lease_expires_at="2026-07-27T00:00:00+00:00",
            max_attempts=3,
        )

    result = main([
        "emit",
        "dev.build.done",
        "--task",
        "T1",
        "--dispatch-id",
        "disp-critic",
    ])

    assert result == 0
    event = next(
        event
        for event in EventLog(tmp_path / ".zf" / "events.jsonl").read_all()
        if event.type == "dev.build.done"
    )
    assert event.payload["operation_id"] == "op-critic"
    assert event.payload["dispatch_id"] == "disp-critic"
    assert event.payload["attempt_id"].startswith("ta-")
    assert event.payload["lease_id"].startswith("lease-")


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


def test_events_evidence_filters_before_last_without_mutation(tmp_path, monkeypatch, capsys):
    _init_with_state_dir(tmp_path, monkeypatch)
    log = EventLog(tmp_path / "runtime-state" / "events.jsonl")
    events = [
        ZfEvent(type="probe", actor="writer", task_id="T1", payload={"exit_code": 1}),
        ZfEvent(type="probe", actor="writer", task_id="T1", payload={"exit_code": 0}),
        ZfEvent(type="probe", actor="writer", task_id="T2"),
        ZfEvent(type="probe", actor="verifier", task_id="T1"),
    ]
    for event in events:
        log.append(event)
    before = log.path.read_bytes()
    capsys.readouterr()
    args = ["events", "--type", "probe", "--actor", "writer", "--task", "T1", "--json"]
    assert main([*args, "--last", "1"]) == 0
    assert [e["id"] for e in json.loads(capsys.readouterr().out)["data"]["events"]] == [events[1].id]
    assert main([*args, "--id", events[0].id, "--last", "1"]) == 0
    result = json.loads(capsys.readouterr().out)["data"]["events"]
    assert [e["id"] for e in result] == [events[0].id]
    assert result[0]["payload"] == {"exit_code": 1}
    assert main([*args, "--id", events[3].id]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["events"] == []
    assert log.path.read_bytes() == before


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


def test_emit_contract_change_request_applies_synchronously_and_rejects_stale(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n  name: test\n"
        "roles:\n"
        "  - {name: orchestrator, backend: mock, role_kind: reader}\n",
        encoding="utf-8",
    )
    assert main(["init"]) == 0
    capsys.readouterr()
    store = TaskStore(tmp_path / ".zf" / "kanban.json")
    store.add(Task(
        id="TASK-CHANGE",
        title="Change",
        contract=TaskContract(behavior="R1", verification="pytest"),
    ))
    payload = {
        "contract": asdict(TaskContract(
            behavior="R2",
            verification="pytest tests/test_current.py",
        )),
        "expected_authority_revision": "",
        "source": "orchestrator_replan",
    }

    result = main([
        "emit",
        "task.contract.change.requested",
        "--task",
        "TASK-CHANGE",
        "--actor",
        "orchestrator",
        "--payload",
        json.dumps(payload),
    ])

    assert result == 0
    current = store.get("TASK-CHANGE")
    assert current is not None
    assert current.contract.behavior == "R2"
    assert current.contract_authority_sequence == 1
    capsys.readouterr()

    payload["contract"]["behavior"] = "stale R3"
    stale = main([
        "emit",
        "task.contract.change.requested",
        "--task",
        "TASK-CHANGE",
        "--actor",
        "orchestrator",
        "--payload",
        json.dumps(payload),
    ])

    assert stale == 2
    assert "was not applied" in capsys.readouterr().err
    unchanged = store.get("TASK-CHANGE")
    assert unchanged is not None
    assert unchanged.contract.behavior == "R2"
    assert unchanged.contract_authority_sequence == 1


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
