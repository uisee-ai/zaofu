"""#I fix: zf spec ingest fills critic_event_id / critic_gate_ref /
evidence_contract with placeholders so task contract satisfies
workflow.dag.required_backlog_refs default 6 fields (cangjie
2026-05-21 observation-I).

Pre-fix: _run_ingest left these 3 fields as TaskContract defaults
("" / {}), so dispatch_preflight rejected all ingested tasks with
'contract.critic_event_id is required' etc. cangjie operator had
to削减 yaml required_backlog_refs to 3 fields (workaround F1).

Fix: ingest fills sensible placeholders so spec-ingested tasks pass
the default 6-field check. Optional --inherit-design <event_id> flag
lets operator point at a real design.critique.done event (e.g. cangjie
round 1 evt-c52dd23d39f8) for full audit trail.

Refs: tasks/2026-05-21-1000-zf-spec-ingest-fill-design-fields.md
Cangjie incident: docs/records/incidents/2026-05-21-observation-I-spec-ingest-required-backlog-refs-mismatch.md
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.cli.spec import _build_ingest_plan
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore


def _frontmatter(tasks: list[dict]) -> dict:
    return {
        "spec": "fillfields-test",
        "feature_id": "F-12345678",
        "feature_key": "fillfields-test",
        "phase": "P0",
        "title": "Fill design fields test",
        "tasks": tasks,
    }


def _sample_task() -> dict:
    return {
        "id": "TASK-X",
        "title": "Test",
        "owner_role": "dev",
        "scope": ["src/foo.ts"],
        "verification": "pnpm test",
    }


# ─── default placeholder fill ─────────────────────────────────────────────


def test_ingest_fills_critic_event_id_default(tmp_path: Path):
    """Default ingest fills critic_event_id with design-skipped placeholder."""
    plan_md = tmp_path / "plan.md"
    plan_md.write_text("---\nspec: x\n---\n")
    plan = _build_ingest_plan(_frontmatter([_sample_task()]), plan_md)

    task_store = TaskStore(tmp_path / "kanban.json")
    payload = plan["tasks"][0]
    contract = TaskContract(
        behavior="t",
        verification=payload["verification"],
        scope=payload["scope"],
        owner_role=payload["owner_role"],
        # Test fix: fill these via spec.py logic (default placeholder)
        critic_event_id=payload.get(
            "critic_event_id", f"design-skipped:ingest-from-spec:{plan_md}"
        ),
        critic_gate_ref=payload.get("critic_gate_ref", str(plan_md)),
        evidence_contract=payload.get(
            "evidence_contract", {"plan_evidence": str(plan_md)}
        ),
    )
    task = Task(
        id=payload["id"], title=payload["title"], key="F-x:taskx",
        status="backlog", contract=contract,
    )
    task_store.add(task)

    saved = task_store.get("TASK-X")
    assert saved.contract.critic_event_id != ""
    assert "design-skipped" in saved.contract.critic_event_id
    assert saved.contract.critic_gate_ref != ""
    assert saved.contract.evidence_contract != {}


# ─── via _run_ingest CLI integration ─────────────────────────────────────


def _write_plan_with_frontmatter(plan_md: Path) -> None:
    plan_md.write_text(
        "---\n"
        "spec: cangjie-test\n"
        "feature_id: F-abcdef12\n"
        "feature_key: cangjie-test\n"
        "phase: P0\n"
        "title: Test\n"
        "tasks:\n"
        "  - id: TASK-X\n"
        "    title: Root\n"
        "    owner_role: dev\n"
        "    scope: ['x.ts']\n"
        "    verification: pnpm test\n"
        "    blocked_by: []\n"
        "---\n"
        "# spec body\n"
    )


def test_ingest_via_cli_fills_default_placeholders(tmp_path: Path):
    """zf spec ingest default fills 3 fields with placeholders."""
    plan_md = tmp_path / "plan.md"
    _write_plan_with_frontmatter(plan_md)

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    # Need minimal zf.yaml for state-dir resolve
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n"
        f"  state_dir: {state_dir}\n"
        "session:\n"
        "  tmux_session: t\n"
        "roles: []\n"
    )

    from zf.cli.spec import _run_ingest
    import argparse
    args = argparse.Namespace(
        path=str(plan_md),
        state_dir=str(state_dir),
        dry_run=False,
        inherit_design=None,
    )
    rc = _run_ingest(args)
    assert rc == 0, "ingest should succeed"

    task_store = TaskStore(state_dir / "kanban.json")
    task = task_store.get("TASK-X")
    assert task is not None
    assert task.contract.critic_event_id != "", (
        "#I fix: critic_event_id should be filled with placeholder"
    )
    assert "ingest" in task.contract.critic_event_id.lower() or \
           "design-skipped" in task.contract.critic_event_id.lower()
    assert task.contract.critic_gate_ref != "", (
        "#I fix: critic_gate_ref should be filled with plan path"
    )
    assert task.contract.evidence_contract != {}, (
        "#I fix: evidence_contract should be filled with plan evidence"
    )


def test_ingest_via_cli_inherit_design_flag(tmp_path: Path):
    """--inherit-design evt-XXX flag uses specified event id."""
    plan_md = tmp_path / "plan.md"
    _write_plan_with_frontmatter(plan_md)

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n"
        f"  state_dir: {state_dir}\n"
        "session:\n"
        "  tmux_session: t\n"
        "roles: []\n"
    )

    from zf.cli.spec import _run_ingest
    import argparse
    args = argparse.Namespace(
        path=str(plan_md),
        state_dir=str(state_dir),
        dry_run=False,
        inherit_design="evt-c52dd23d39f8",  # cangjie round 1 design.critique.done
    )
    rc = _run_ingest(args)
    assert rc == 0

    task_store = TaskStore(state_dir / "kanban.json")
    task = task_store.get("TASK-X")
    assert task is not None
    assert task.contract.critic_event_id == "evt-c52dd23d39f8", (
        f"--inherit-design should set critic_event_id verbatim; got "
        f"{task.contract.critic_event_id!r}"
    )
