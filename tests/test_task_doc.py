from __future__ import annotations

import argparse
import json
from pathlib import Path

from zf.core.task.schema import Task, TaskContract
from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.contract_validation import validate_task_contract
from zf.runtime.task_doc import (
    render_task_doc,
    verify_task_capsule,
    write_task_doc,
)
from zf.runtime.task_doc_audit import audit_task_docs
from zf.runtime.task_doc_ingest import ingest_task_doc
from zf.runtime.task_progress_projector import render_projected_evidence_doc


def _task(status: str = "in_progress") -> Task:
    return Task(
        id="TASK-DOC-1",
        title="Implement channel task doc flow",
        status=status,
        assigned_to="dev-1",
        active_dispatch_id="disp-1",
        contract=TaskContract(
            behavior="channel workers load task.md before editing",
            verification="pytest tests/test_task_doc.py",
            spec_ref="docs/design/71-md-first-task-contract-kanban-flow.md",
            plan_ref="docs/design/70-cc-inspired-idle-dispatch-notification-loop.md",
            tdd_ref="tests/test_task_doc.py",
            source_index_ref=".zf/artifacts/F-DOC/source-index.json",
            source_mode="canonical",
            source_key="docs/design/71-md-first-task-contract-kanban-flow.md#S12",
            source_ref="docs/design/71-md-first-task-contract-kanban-flow.md#S12",
            source_task_id="S12",
            source_title="Task Capsule materialization",
            source_excerpt=(
                "完整 plan section: agent-facing source.md must preserve the "
                "task goal, constraints, scope, verification, and acceptance."
            ),
            product_contract_ref="docs/design/71-md-first-task-contract-kanban-flow.md",
            scope=["src/zf/runtime/task_doc.py"],
            acceptance_criteria=[
                "agent-facing task.md contains the full task contract",
                "worker cannot mark completion by editing task.md",
            ],
            evidence_contract={
                "required_events": ["dev.build.done"],
            },
        ),
    )


def test_render_task_doc_is_agent_facing_contract() -> None:
    text = render_task_doc(_task(), generated_at="2026-05-30T00:00:00+00:00")

    assert "schema_version: task-doc.v1" in text
    assert "task_id: TASK-DOC-1" in text
    assert "status_hint: in_progress" in text
    assert "channel workers load task.md before editing" in text
    assert "spec_ref" in text
    assert "docs/design/71-md-first-task-contract-kanban-flow.md" in text
    assert "Acceptance Criteria" in text
    assert "required_events" in text
    assert "source_index_ref" in text
    assert "workers must not edit this file to mark completion" in text


def test_projected_evidence_doc_includes_lane_stage_completed(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="evt-lane-verify",
        type="lane.stage.completed",
        actor="zf-cli",
        task_id="TASK-DOC-1",
        payload={
            "task_id": "TASK-DOC-1",
            "stage_slot": "verify",
            "artifact_refs": ["reports/verify.json"],
            "evidence_refs": ["git:abc123", "reports/verify.json"],
        },
    ))

    text = render_projected_evidence_doc(
        state_dir,
        _task(status="review"),
        generated_at="2026-07-07T00:00:00+00:00",
    )

    assert "lane.stage.completed" in text
    assert "reports/verify.json" in text
    assert "git:abc123" in text


def test_write_task_doc_writes_capsule_manifest(tmp_path: Path) -> None:
    result = write_task_doc(tmp_path / ".zf", _task(status="done"), source_event="judge.passed")

    assert result.path.exists()
    assert result.source_path.exists()
    assert result.progress_path.exists()
    assert result.evidence_path.exists()
    assert result.manifest_path.exists()
    assert "status_hint: done" in result.path.read_text(encoding="utf-8")

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["task_id"] == "TASK-DOC-1"
    assert manifest["status"] == "done"
    assert manifest["source_event"] == "judge.passed"
    assert manifest["source_doc"] == str(result.source_path)
    assert manifest["source_index_ref"] == ".zf/artifacts/F-DOC/source-index.json"
    assert manifest["source_mode"] == "canonical"
    assert manifest["source_revision"] == result.source_revision
    assert manifest["contract_revision"] == result.contract_revision
    assert manifest["capsule_revision"] == result.capsule_revision
    assert verify_task_capsule(tmp_path / ".zf", _task(status="done")) == []


def test_write_task_doc_includes_resolved_source_refs_for_configured_state_dir(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "runtime-state"
    source_index = state_dir / "artifacts" / "F-DOC" / "source-index.json"
    source_index.parent.mkdir(parents=True)
    source_index.write_text(
        '{"schema_version":"source-index.v1","tasks":[]}\n',
        encoding="utf-8",
    )
    task = Task(
        id="TASK-RESOLVED-1",
        title="resolved refs",
        contract=TaskContract(
            behavior="load canonical source index",
            verification="true",
            source_index_ref="runtime-state/artifacts/F-DOC/source-index.json",
        ),
    )

    result = write_task_doc(state_dir, task, project_root=tmp_path)

    text = result.path.read_text(encoding="utf-8")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    resolved = manifest["resolved_refs"][0]
    assert "Resolved Source References" in text
    assert resolved["key"] == "source_index_ref"
    assert resolved["readable"] is True
    assert resolved["resolved_path"] == str(source_index)
    assert resolved["state_dir_relative_ref"] == "artifacts/F-DOC/source-index.json"


def test_write_task_doc_projects_progress_and_evidence_from_events(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    event_log = EventLog(state_dir / "events.jsonl")
    event_log.append(ZfEvent(
        id="evt-ref",
        type="task.ref.updated",
        actor="zf-cli",
        task_id="TASK-PROGRESS-1",
        payload={"source_commit": "abc123", "source_branch": "task/TASK-PROGRESS-1"},
    ))
    event_log.append(ZfEvent(
        id="evt-build",
        type="dev.build.done",
        actor="dev",
        task_id="TASK-PROGRESS-1",
        payload={
            "commands_run": [
                {"command": "uv run pytest tests/test_task_doc.py", "exit_code": 0}
            ],
        },
    ))
    task = Task(
        id="TASK-PROGRESS-1",
        title="progress projection",
        status="in_progress",
        contract=TaskContract(behavior="x", verification="true"),
    )

    result = write_task_doc(state_dir, task)

    progress = result.progress_path.read_text(encoding="utf-8")
    evidence = result.evidence_path.read_text(encoding="utf-8")
    assert "`task.ref.updated` `evt-ref`" in progress
    assert "`dev.build.done` `evt-build`" in progress
    assert "uv run pytest tests/test_task_doc.py" in evidence
    assert "abc123" in evidence


def test_task_doc_projects_candidate_terminal_event_by_completed_task_ids(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    task = Task(
        id="TASK-CANDIDATE-1",
        title="candidate projection",
        status="done",
        contract=TaskContract(behavior="x", verification="true"),
    )
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        id="evt-candidate-judge",
        type="judge.passed",
        actor="judge",
        payload={
            "completed_task_ids": [task.id],
            "evidence_refs": ["reports/judge.json"],
            "test_refs": ["pytest -q"],
        },
    ))

    result = write_task_doc(state_dir, task)
    evidence_text = result.evidence_path.read_text(encoding="utf-8")

    assert "judge.passed" in evidence_text
    assert "reports/judge.json" in evidence_text


def test_source_doc_prefers_source_index_excerpt(tmp_path: Path) -> None:
    result = write_task_doc(tmp_path / ".zf", _task())

    text = result.source_path.read_text(encoding="utf-8")

    assert "Task Capsule materialization" in text
    assert "完整 plan section" in text
    assert "channel workers load task.md before editing" not in text


def test_product_impacting_task_reports_missing_source_precedence(tmp_path: Path) -> None:
    config = ZfConfig(
        project=ProjectConfig(name="x"),
        roles=[RoleConfig(name="dev", role_kind="writer")],
    )
    task = Task(
        id="TASK-SOURCE-1",
        title="Change product behavior",
        assigned_to="dev",
        contract=TaskContract(
            behavior="change login behavior",
            verification="pytest",
            verification_tiers=["runtime"],
            owner_role="dev",
            scope=["src/login.py"],
        ),
    )

    errors = validate_task_contract(task, config=config, project_root=tmp_path)

    assert any("product_contract_ref or spec_skip_reason" in error for error in errors)


def test_spec_ref_satisfies_source_precedence(tmp_path: Path) -> None:
    config = ZfConfig(
        project=ProjectConfig(name="x"),
        roles=[RoleConfig(name="dev", role_kind="writer")],
    )
    task = Task(
        id="TASK-SOURCE-2",
        title="Change product behavior",
        assigned_to="dev",
        contract=TaskContract(
            behavior="change login behavior",
            verification="pytest",
            verification_tiers=["runtime"],
            owner_role="dev",
            scope=["src/login.py"],
            spec_ref="docs/spec.md",
        ),
    )

    errors = validate_task_contract(task, config=config, project_root=tmp_path)

    assert not any("product_contract_ref or spec_skip_reason" in error for error in errors)


def test_task_doc_audit_reports_missing_source_ref(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    task = Task(
        id="TASK-AUDIT-1",
        title="audit",
        contract=TaskContract(
            behavior="x",
            source_ref="docs/missing.md",
        ),
    )
    from zf.core.task.store import TaskStore

    TaskStore(state_dir / "kanban.json").add(task)
    write_task_doc(state_dir, task)

    report = audit_task_docs(state_dir, project_root=tmp_path)

    assert any(item["code"] == "source_missing" for item in report["findings"])


def test_task_doc_audit_does_not_treat_event_refs_as_files(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    task = Task(
        id="TASK-AUDIT-EVENT-REF",
        title="audit event ref",
        contract=TaskContract(
            behavior="x",
            verification="true",
            evidence_contract={
                "source_refs": {
                    "critic_event": "evt-abc123",
                    "critic_event_id": "(evt-def456)",
                },
                "artifact_refs": {
                    "gate": "event:evt-ghi789",
                    "source_arch_dispatch_id": "[disp-123456]",
                },
            },
        ),
    )
    from zf.core.task.store import TaskStore

    TaskStore(state_dir / "kanban.json").add(task)
    write_task_doc(state_dir, task)

    report = audit_task_docs(state_dir, project_root=tmp_path)

    assert not any(item["code"] == "source_missing" for item in report["findings"])


def test_contract_update_preserves_explicit_scope_and_task_doc_revisions(
    tmp_path: Path,
) -> None:
    from dataclasses import asdict

    from zf.core.task.store import TaskStore
    from zf.runtime.housekeeping import apply_task_contract_event

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    task = Task(
        id="TASK-SCOPE-1",
        title="prose scope task",
        contract=TaskContract(
            behavior="fix parser edge cases",
            verification="python -m pytest tests/test_parser.py",
            scope=[
                "读取 tests/test_parser.py 中现有失败测试",
                "描述 timestamp 与 leading spaces 的影响",
            ],
            shared_files=["tests/test_parser.py", "src/parser.py"],
        ),
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(task)
    write_task_doc(state_dir, task, project_root=tmp_path)
    store.update(task.id, contract=task.contract)

    apply_task_contract_event(
        store,
        ZfEvent(
            type="task.contract.update",
            actor="zf-cli",
            task_id=task.id,
            payload={"contract": asdict(task.contract)},
        ),
    )

    updated = store.get(task.id)
    assert updated is not None
    assert updated.contract.scope == task.contract.scope
    assert updated.contract.task_doc_ref == task.contract.task_doc_ref
    assert updated.contract.contract_revision == task.contract.contract_revision
    assert verify_task_capsule(state_dir, updated) == []


def test_task_doc_audit_all_includes_archived_done_tasks(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    from zf.core.task.store import TaskStore

    store = TaskStore(state_dir / "kanban.json")
    task = Task(
        id="TASK-ARCHIVED-AUDIT",
        title="archived audit",
        status="in_progress",
        contract=TaskContract(behavior="x", verification="true"),
    )
    store.add(task)
    archived = store.update(task.id, status="done")
    assert archived is not None
    write_task_doc(state_dir, archived)

    report = audit_task_docs(state_dir, project_root=tmp_path)

    assert report["task_count"] == 1
    assert any(
        item["task_id"] == "TASK-ARCHIVED-AUDIT"
        for item in report["findings"]
    ) or report["findings"] == []


def test_task_doc_audit_reports_stale_briefing_revision(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    task = Task(
        id="TASK-AUDIT-2",
        title="audit",
        contract=TaskContract(behavior="x"),
    )
    from zf.core.task.store import TaskStore

    TaskStore(state_dir / "kanban.json").add(task)
    write_task_doc(state_dir, task)
    briefing_dir = state_dir / "briefings"
    briefing_dir.mkdir()
    (briefing_dir / "TASK-AUDIT-2.json").write_text(
        json.dumps({
            "id": "TASK-AUDIT-2",
            "task_doc": {
                "source_revision": "source-old",
                "contract_revision": "contract-old",
                "capsule_revision": "capsule-old",
            },
        }),
        encoding="utf-8",
    )

    report = audit_task_docs(state_dir, project_root=tmp_path)

    assert any(item["code"] == "briefing_stale" for item in report["findings"])


def test_task_doc_audit_treats_lazy_backlog_capsule_missing_as_warning(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    from zf.core.task.store import TaskStore

    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-LAZY-1",
        title="lazy backlog",
        status="backlog",
        assigned_to="dev",
        contract=TaskContract(behavior="x", verification="true"),
    ))

    report = audit_task_docs(state_dir, project_root=tmp_path)

    assert not any(item["severity"] == "critical" for item in report["findings"])
    assert any(
        item["code"] == "capsule_not_materialized_until_dispatch"
        for item in report["findings"]
    )


def test_task_doc_ingest_updates_contract_and_clears_dispatch(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    task = Task(
        id="TASK-INGEST-1",
        title="ingest",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-old",
        contract=TaskContract(
            behavior="old behavior",
            verification="pytest old",
            scope=["src/old.py"],
            acceptance_criteria=["old acceptance"],
        ),
    )
    from zf.core.task.store import TaskStore

    store = TaskStore(state_dir / "kanban.json")
    store.add(task)
    result = write_task_doc(state_dir, task)
    text = result.path.read_text(encoding="utf-8")
    text = text.replace("- **Behavior**: old behavior", "- **Behavior**: new behavior")
    text = text.replace("- **Verification**: `pytest old`", "- **Verification**: `pytest new`")
    text = text.replace("  - `src/old.py`", "  - `src/new.py`")
    text = text.replace("1. old acceptance", "1. new acceptance")
    result.path.write_text(text, encoding="utf-8")

    ingest = ingest_task_doc(state_dir, "TASK-INGEST-1")
    updated = store.get("TASK-INGEST-1")

    assert ingest.active_dispatch_cleared is True
    assert updated is not None
    assert updated.active_dispatch_id == ""
    assert updated.contract.behavior == "new behavior"
    assert updated.contract.verification == "pytest new"
    assert updated.contract.scope == ["src/new.py"]
    assert updated.contract.acceptance_criteria == ["new acceptance"]


def test_task_doc_cli_rejects_worker_ingest_without_operator_ack(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from zf.cli.task_doc import _run_ingest
    from zf.core.task.store import TaskStore

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    task = Task(id="TASK-INGEST-2", title="ingest", status="in_progress")
    store = TaskStore(state_dir / "kanban.json")
    store.add(task)
    write_task_doc(state_dir, task)
    monkeypatch.setenv("ZF_ROLE_INSTANCE", "dev-1")

    code = _run_ingest(argparse.Namespace(
        state_dir=str(state_dir),
        task_id="TASK-INGEST-2",
        operator_ack_runtime_write=False,
    ))

    assert code == 1
    updated = store.get("TASK-INGEST-2")
    assert updated is not None
    assert updated.active_dispatch_id == task.active_dispatch_id
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(event.type == "task.doc.ingest.rejected" for event in events)
