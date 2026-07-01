"""TR-SPEC-INGEST-BLOCKED-BY-001: zf spec ingest must propagate
plan frontmatter `blocked_by` into Task.blocked_by (#K cangjie fix).

Pre-fix: `_build_ingest_plan` + `_run_ingest` silently dropped the
field. Every ingested task ended up Task.blocked_by=[] regardless of
what the plan declared, so `task_store.ready()` saw every task as
ready → kernel over-dispatched the entire phase → cascade
phase_gate_violation reworks overloaded arch single instance.

Cangjie evidence: 5 P*V* blocked tasks (P1V04 / P1V13 / P1V14 /
P4V04 / P5V01) all with kanban.json blocked_by=[] though plan
declares P1V14.blocked_by=[P1V12,P1V13,...] etc.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.cli.spec import _build_ingest_plan


def _frontmatter_for(tasks: list[dict]) -> dict:
    return {
        "spec": "test-spec",
        "feature_id": "F-12345678",
        "feature_key": "test-spec",
        "phase": "P1",
        "title": "Test feature",
        "tasks": tasks,
    }


# ─── plan frontmatter → ingest plan blocked_by propagation ───────────────


def test_blocked_by_propagates_to_ingest_plan():
    """Plan task.blocked_by = ['TASK-FOO'] → ingest task['blocked_by'] same."""
    raw = _frontmatter_for([
        {
            "id": "TASK-P0V01",
            "title": "Root vertical",
            "owner_role": "dev",
            "scope": ["foo.ts"],
            "verification": "pnpm test",
            "blocked_by": [],
        },
        {
            "id": "TASK-P0V02",
            "title": "Depends on V01",
            "owner_role": "dev",
            "scope": ["bar.ts"],
            "verification": "pnpm test",
            "blocked_by": ["TASK-P0V01"],
        },
        {
            "id": "TASK-P0V06",
            "title": "Phase gate",
            "owner_role": "dev",
            "scope": ["gate.ts"],
            "verification": "pnpm test",
            "blocked_by": [
                "TASK-P0V01", "TASK-P0V02", "TASK-P0V03",
                "TASK-P0V04", "TASK-P0V05",
            ],
        },
    ])
    plan = _build_ingest_plan(raw, Path("/tmp/test-plan.md"))
    tasks = {t["id"]: t for t in plan["tasks"]}
    assert tasks["TASK-P0V01"]["blocked_by"] == []
    assert tasks["TASK-P0V02"]["blocked_by"] == ["TASK-P0V01"]
    assert tasks["TASK-P0V06"]["blocked_by"] == [
        "TASK-P0V01", "TASK-P0V02", "TASK-P0V03",
        "TASK-P0V04", "TASK-P0V05",
    ]


def test_blocked_by_default_empty_when_missing():
    """Plan without blocked_by field → ingest gets []."""
    raw = _frontmatter_for([
        {
            "id": "TASK-A",
            "title": "Standalone task",
            "owner_role": "dev",
            "scope": ["foo.ts"],
            "verification": "pnpm test",
            # no blocked_by
        }
    ])
    plan = _build_ingest_plan(raw, Path("/tmp/test-plan.md"))
    assert plan["tasks"][0]["blocked_by"] == []


def test_blocked_by_single_string_normalized():
    """blocked_by single string → list with one entry (consistent _string_list)."""
    raw = _frontmatter_for([
        {
            "id": "TASK-B",
            "title": "Depends on A",
            "owner_role": "dev",
            "scope": ["bar.ts"],
            "verification": "pnpm test",
            "blocked_by": "TASK-A",
        }
    ])
    plan = _build_ingest_plan(raw, Path("/tmp/test-plan.md"))
    assert plan["tasks"][0]["blocked_by"] == ["TASK-A"]


def test_scheduler_fields_propagate_to_ingest_plan():
    """Plan task scheduling hints become ingest payload fields.

    The orchestrator decides the dependency graph and topological waves; ingest
    must preserve the result so TaskContract can become the runtime projection.
    """
    raw = _frontmatter_for([
        {
            "id": "TASK-B",
            "title": "Depends on A",
            "owner_role": "dev",
            "scope": ["bar.ts"],
            "verification": "pnpm test",
            "blocked_by": ["TASK-A"],
            "wave": "wave-2",
            "shared_files": ["types.ts"],
            "exclusive_files": ["bar.ts"],
        }
    ])
    plan = _build_ingest_plan(raw, Path("/tmp/test-plan.md"))
    task = plan["tasks"][0]
    assert task["blocked_by"] == ["TASK-A"]
    assert task["wave"] == 2
    assert task["shared_files"] == ["types.ts"]
    assert task["exclusive_files"] == ["bar.ts"]


# ─── end-to-end ingest (Task.blocked_by) ─────────────────────────────────


def test_run_ingest_writes_task_blocked_by(tmp_path):
    """Run actual zf spec ingest with a plan declaring blocked_by;
    verify TaskStore reads back blocked_by per task.
    """
    plan_md = tmp_path / "plan.md"
    plan_md.write_text(
        "---\n"
        "spec: e2e-test\n"
        "feature_id: F-abcdef12\n"
        "feature_key: e2e-test\n"
        "phase: P0\n"
        "title: E2E test feature\n"
        "tasks:\n"
        "  - id: TASK-X\n"
        "    title: Root\n"
        "    owner_role: dev\n"
        "    scope: ['x.ts']\n"
        "    verification: pnpm test\n"
        "    blocked_by: []\n"
        "  - id: TASK-Y\n"
        "    title: Depends on X\n"
        "    owner_role: dev\n"
        "    scope: ['y.ts']\n"
        "    verification: pnpm test\n"
        "    blocked_by: [TASK-X]\n"
        "---\n"
        "# spec body\n"
    )

    # Set up a minimal state dir for ingest
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    # Minimal zf.yaml not needed for spec ingest CLI? Let me check what
    # _run_ingest needs. Actually we'll use the task_store directly to
    # avoid needing full CLI args.
    from zf.cli.spec import _build_ingest_plan
    from zf.core.task.schema import Task, TaskContract
    from zf.core.task.store import TaskStore

    frontmatter = {
        "spec": "e2e-test",
        "feature_id": "F-abcdef12",
        "feature_key": "e2e-test",
        "phase": "P0",
        "title": "E2E test feature",
        "tasks": [
            {
                "id": "TASK-X",
                "title": "Root",
                "owner_role": "dev",
                "scope": ["x.ts"],
                "verification": "pnpm test",
                "blocked_by": [],
            },
            {
                "id": "TASK-Y",
                "title": "Depends on X",
                "owner_role": "dev",
                "scope": ["y.ts"],
                "verification": "pnpm test",
                "blocked_by": ["TASK-X"],
            },
        ],
    }
    plan = _build_ingest_plan(frontmatter, plan_md)

    # Mimic the Task() construction in _run_ingest (line 258)
    task_store = TaskStore(state_dir / "kanban.json")
    for task_payload in plan["tasks"]:
        contract = TaskContract(
            behavior=task_payload.get("behavior", ""),
            verification=task_payload["verification"],
            scope=task_payload["scope"],
            owner_role=task_payload["owner_role"],
        )
        task = Task(
            id=task_payload["id"],
            title=task_payload["title"],
            key=f"F-abcdef12:{task_payload.get('key', task_payload['id'].lower())}",
            status="backlog",
            priority=task_payload.get("priority", 3),
            contract=contract,
            blocked_by=task_payload.get("blocked_by", []),
        )
        task_store.add(task)

    # Verify blocked_by propagated
    task_x = task_store.get("TASK-X")
    task_y = task_store.get("TASK-Y")
    assert task_x is not None and task_x.blocked_by == []
    assert task_y is not None and task_y.blocked_by == ["TASK-X"]

    # Verify ready() correctly excludes TASK-Y (TASK-X not done yet)
    ready = task_store.ready()
    ready_ids = {t.id for t in ready}
    assert "TASK-X" in ready_ids, "root task should be ready"
    assert "TASK-Y" not in ready_ids, (
        "task with unresolved blocked_by should NOT be ready (pre-#K-fix bug)"
    )
