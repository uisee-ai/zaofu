from __future__ import annotations

import hashlib
from pathlib import Path

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator


class _StubTransport:
    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        pass

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""


def _make_orchestrator(tmp_path: Path) -> tuple[Orchestrator, TaskStore]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="plan-only", state_dir=str(state_dir)),
        session=SessionConfig(tmux_session="zf-test"),
        roles=[
            RoleConfig(name="orchestrator", backend="mock", role_kind="reader"),
            RoleConfig(name="arch", backend="mock", role_kind="reader"),
            RoleConfig(name="critic", backend="mock", role_kind="reader"),
        ],
    )
    orch = Orchestrator(
        state_dir,
        config,
        _StubTransport(),  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    store = TaskStore(state_dir / "kanban.json")
    return orch, store


def _write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backlog_text() -> str:
    return """---
spec: cangjie-agent-full-stage-backlog
feature_id: F-43b1e1d5
feature_key: cangjie-ga
tasks:
  - id: TASK-CJGA01
    title: Runtime foundation
    owner_role: dev
    scope:
      - packages/core/**
    acceptance:
      - test -f README.md
    verification: test -f README.md
---

# Cangjie Agent Full Stage Backlog

TASK-CJGA01 builds the runtime foundation.
"""


def _manifest_for(workdir: Path) -> dict:
    files = {
        "docs/specs/foundations/cangjie-ga-capability-baseline.md": "# Baseline\n",
        "docs/decisions/ADR-001-cangjie-core-and-data-agent-boundary.md": "# ADR\n",
        "docs/plans/cangjie-agent-full-stage-implementation-plan.md": "# Plan\n",
        "docs/plans/cangjie-agent-full-stage-backlog.md": _backlog_text(),
        "docs/plans/phase-3/cangjie-data-agent-implementation-plan.md": "# P3 Plan\n",
        "docs/plans/phase-3/cangjie-data-agent-backlog.md": "# P3 Backlog\n",
    }
    refs = []
    for rel, text in files.items():
        sha = _write(workdir / rel, text)
        refs.append({
            "kind": "backlog_plan" if rel.endswith("backlog.md") else "plan",
            "path": rel,
            "sha256": sha,
            "summary": rel,
            "status": "accepted",
            "workdir_path": str(workdir),
        })
    return {
        "task_id": "TASK-PLAN",
        "role": "orchestrator",
        "skills_used": ["zf-harness-backlog-synthesis"],
        "artifact_refs": refs,
        "handoff_contract": {
            "spec_ref": "docs/specs/foundations/cangjie-ga-capability-baseline.md",
            "plan_ref": "docs/plans/cangjie-agent-full-stage-implementation-plan.md",
            "backlog_ref": "docs/plans/cangjie-agent-full-stage-backlog.md",
            "critic_event_id": "evt-critic",
            "critic_gate_ref": "approve",
        },
    }


def test_orchestrator_final_manifest_closes_plan_only_task(tmp_path: Path) -> None:
    orch, store = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-PLAN",
        title="plan",
        status="in_progress",
        assigned_to="critic",
        active_dispatch_id="disp-critic",
        contract=TaskContract(phase="plan"),
    ))
    orch.event_writer.append(ZfEvent(
        type="design.critique.done",
        id="evt-critic",
        actor="critic",
        task_id="TASK-PLAN",
        payload={"verdict": "approve", "dispatch_id": "disp-critic"},
    ))
    workdir = tmp_path / ".zf" / "workdirs" / "orchestrator" / "project"
    event = orch.event_writer.append(ZfEvent(
        type="artifact.manifest.published",
        actor="orchestrator",
        task_id="TASK-PLAN",
        payload=_manifest_for(workdir),
    ))

    decision = orch._on_artifact_manifest_published(event)  # type: ignore[attr-defined]

    assert decision is not None
    assert decision.action == "move"
    assert store.get("TASK-PLAN").status == "done"  # type: ignore[union-attr]
    assert (
        tmp_path / "docs/plans/cangjie-agent-full-stage-backlog.md"
    ).exists()
    events = orch.event_log.read_all()
    assert any(e.type == "artifact.promote.completed" for e in events)
    synthesized = [
        e for e in events
        if e.type == "artifact.manifest.published"
        and e.payload.get("artifact_refs", [{}])[0].get("kind") == "task_map"
    ]
    assert synthesized
    task_map_path = Path(synthesized[0].payload["artifact_refs"][0]["path"])
    assert task_map_path.exists()
    assert any(e.type == "discriminator.passed" for e in events)
    done = next(e for e in events if e.type == "task.done.evidence")
    assert done.payload["artifact_refs"] == [
        "docs/specs/foundations/cangjie-ga-capability-baseline.md",
        "docs/decisions/ADR-001-cangjie-core-and-data-agent-boundary.md",
        "docs/plans/cangjie-agent-full-stage-implementation-plan.md",
        "docs/plans/cangjie-agent-full-stage-backlog.md",
        "docs/plans/phase-3/cangjie-data-agent-implementation-plan.md",
        "docs/plans/phase-3/cangjie-data-agent-backlog.md",
    ]
    assert done.payload["artifact_manifest_refs"][0]["path"].startswith("docs/")
    assert done.payload["validation"]["task_map_ref"] == str(task_map_path)
    assert any(
        e.type == "task.status_changed"
        and e.payload.get("to") == "done"
        for e in events
    )


def test_layer2_reactor_closes_plan_only_task_from_final_manifest(
    tmp_path: Path,
) -> None:
    orch, store = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-PLAN",
        title="plan",
        status="in_progress",
        assigned_to="critic",
        active_dispatch_id="disp-critic",
        contract=TaskContract(phase="plan"),
    ))
    orch.event_writer.append(ZfEvent(
        type="design.critique.done",
        id="evt-critic",
        actor="critic",
        task_id="TASK-PLAN",
        payload={"verdict": "approve", "dispatch_id": "disp-critic"},
    ))
    workdir = tmp_path / ".zf" / "workdirs" / "orchestrator" / "project"
    event = orch.event_writer.append(ZfEvent(
        type="artifact.manifest.published",
        actor="orchestrator",
        task_id="TASK-PLAN",
        payload=_manifest_for(workdir),
    ))

    decisions = orch._react_to_events(pushed=[event])  # type: ignore[attr-defined]

    assert any(decision.action == "move" for decision in decisions)
    assert store.get("TASK-PLAN").status == "done"  # type: ignore[union-attr]
    events = orch.event_log.read_all()
    assert any(e.type == "task.done.evidence" for e in events)


def test_orchestrator_final_manifest_accepts_structural_candidate_task_map(
    tmp_path: Path,
) -> None:
    orch, store = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-PLAN",
        title="plan",
        status="in_progress",
        assigned_to="critic",
        active_dispatch_id="disp-critic",
        contract=TaskContract(phase="plan"),
    ))
    orch.event_writer.append(ZfEvent(
        type="design.critique.done",
        id="evt-critic",
        actor="critic",
        task_id="TASK-PLAN",
        payload={"verdict": "approve", "dispatch_id": "disp-critic"},
    ))
    workdir = tmp_path / ".zf" / "workdirs" / "orchestrator" / "project"
    manifest = _manifest_for(workdir)
    task_map_path = tmp_path / ".zf" / "artifacts" / "TASK-PLAN" / "task-map.json"
    task_map_sha = _write(
        task_map_path,
        '{"schema_version":"task-map.v1","tasks":[{"task_id":"TASK-CJGA01","wave":1,"blocked_by":[],"scope":["packages/core/**"]}]}\n',
    )
    manifest["artifact_refs"].append({
        "kind": "task_map",
        "path": str(task_map_path),
        "sha256": task_map_sha,
        "summary": "structural candidate",
        "status": "accepted",
    })
    manifest["handoff_contract"]["task_map_ref"] = str(task_map_path)
    event = orch.event_writer.append(ZfEvent(
        type="artifact.manifest.published",
        actor="orchestrator",
        task_id="TASK-PLAN",
        payload=manifest,
    ))

    decision = orch._on_artifact_manifest_published(event)  # type: ignore[attr-defined]

    assert decision is not None
    assert decision.action == "move"
    assert store.get("TASK-PLAN").status == "done"  # type: ignore[union-attr]
    done = next(e for e in orch.event_log.read_all() if e.type == "task.done.evidence")
    task_map_check = next(
        check for check in done.payload["validation"]["checks"]
        if check["name"] == "task_map_validate"
    )
    assert task_map_check["passed"] is True
    assert task_map_check["source"] == "candidate"


def test_plan_only_candidate_task_map_allows_human_markdown_backlog(
    tmp_path: Path,
) -> None:
    orch, store = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-PLAN",
        title="plan",
        status="in_progress",
        assigned_to="critic",
        active_dispatch_id="disp-critic",
        contract=TaskContract(phase="plan"),
    ))
    orch.event_writer.append(ZfEvent(
        type="design.critique.done",
        id="evt-critic",
        actor="critic",
        task_id="TASK-PLAN",
        payload={"verdict": "approve", "dispatch_id": "disp-critic"},
    ))
    workdir = tmp_path / ".zf" / "workdirs" / "orchestrator" / "project"
    manifest = _manifest_for(workdir)
    plain_backlog = """# Cangjie Agent Full-Stage Backlog

## Wave 1

| ID | Task | Acceptance | Verification |
| --- | --- | --- | --- |
| TASK-CJGA01 | Runtime foundation | CLI works | pytest |
"""
    backlog_rel = "docs/plans/cangjie-agent-full-stage-backlog.md"
    backlog_sha = _write(workdir / backlog_rel, plain_backlog)
    for ref in manifest["artifact_refs"]:
        if ref["path"] == backlog_rel:
            ref["sha256"] = backlog_sha
            break
    task_map_rel = "docs/plan-only/cangjie-ga/task-map.json"
    task_map_sha = _write(
        workdir / task_map_rel,
        '{"schema_version":"task-map.v1","tasks":[{"task_id":"TASK-CJGA01","wave":1,"blocked_by":[],"scope":["packages/core/**"],"verification":"pytest"}]}\n',
    )
    manifest["artifact_refs"].append({
        "kind": "task_map",
        "path": task_map_rel,
        "sha256": task_map_sha,
        "summary": "canonical structural task map",
        "status": "accepted",
        "workdir_path": str(workdir),
    })
    event = orch.event_writer.append(ZfEvent(
        type="artifact.manifest.published",
        actor="orchestrator",
        task_id="TASK-PLAN",
        payload=manifest,
    ))

    decision = orch._on_artifact_manifest_published(event)  # type: ignore[attr-defined]

    assert decision is not None
    assert decision.action == "move"
    assert store.get("TASK-PLAN").status == "done"  # type: ignore[union-attr]
    events = orch.event_log.read_all()
    assert not any(e.type == "task.done.blocked" for e in events)
    done = next(e for e in events if e.type == "task.done.evidence")
    task_map_check = next(
        check for check in done.payload["validation"]["checks"]
        if check["name"] == "task_map_validate"
    )
    assert task_map_check["passed"] is True
    assert task_map_check["source"] == "candidate"


def test_artifact_manifest_from_previous_owner_is_rejected_after_handoff(
    tmp_path: Path,
) -> None:
    orch, store = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-PLAN",
        title="plan",
        status="in_progress",
        assigned_to="critic",
        active_dispatch_id="disp-critic",
        contract=TaskContract(phase="plan"),
    ))
    event = ZfEvent(
        type="artifact.manifest.published",
        actor="arch",
        task_id="TASK-PLAN",
        payload={
            "task_id": "TASK-PLAN",
            "role": "arch",
            "artifact_refs": [],
        },
    )

    decision = orch._reject_invalid_lifecycle_event(event)  # type: ignore[attr-defined]

    assert decision is not None
    assert decision.action == "block"
    assert "actor arch not assigned" in decision.reason
    rejected = next(e for e in orch.event_log.read_all() if e.type == "runtime.action.rejected")
    assert rejected.payload["reason"] == "artifact_actor_not_assigned"
    assert rejected.payload["expected"] == "critic"
    assert rejected.payload["actual"] == "arch"


def test_orchestrator_final_manifest_bypasses_worker_ownership_guard(
    tmp_path: Path,
) -> None:
    orch, store = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-PLAN",
        title="plan",
        status="in_progress",
        assigned_to="critic",
        active_dispatch_id="disp-critic",
        contract=TaskContract(phase="plan"),
    ))
    event = ZfEvent(
        type="artifact.manifest.published",
        actor=None,
        task_id="TASK-PLAN",
        payload={
            "task_id": "TASK-PLAN",
            "role": "orchestrator",
            "artifact_refs": [],
        },
    )

    decision = orch._reject_invalid_lifecycle_event(event)  # type: ignore[attr-defined]

    assert decision is None
    assert not orch.event_log.read_all()


def test_orchestrator_final_manifest_can_reconcile_backlog_after_retry(
    tmp_path: Path,
) -> None:
    orch, store = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-PLAN",
        title="plan",
        status="backlog",
        assigned_to="",
        active_dispatch_id="disp-critic",
        contract=TaskContract(phase="plan"),
    ))
    orch.event_writer.append(ZfEvent(
        type="design.critique.done",
        id="evt-critic",
        actor="critic",
        task_id="TASK-PLAN",
        payload={"verdict": "approve", "dispatch_id": "disp-critic"},
    ))
    workdir = tmp_path / ".zf" / "workdirs" / "orchestrator" / "project"
    event = orch.event_writer.append(ZfEvent(
        type="artifact.manifest.published",
        actor="orchestrator",
        task_id="TASK-PLAN",
        payload=_manifest_for(workdir),
    ))

    decision = orch._on_artifact_manifest_published(event)  # type: ignore[attr-defined]

    assert decision is not None
    assert decision.action == "move"
    assert store.get("TASK-PLAN").status == "done"  # type: ignore[union-attr]


def test_orchestrator_final_manifest_rejects_invalid_candidate_task_map(
    tmp_path: Path,
) -> None:
    orch, store = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-PLAN",
        title="plan",
        status="in_progress",
        assigned_to="critic",
        active_dispatch_id="disp-critic",
        contract=TaskContract(phase="plan"),
    ))
    orch.event_writer.append(ZfEvent(
        type="design.critique.done",
        id="evt-critic",
        actor="critic",
        task_id="TASK-PLAN",
        payload={"verdict": "approve", "dispatch_id": "disp-critic"},
    ))
    workdir = tmp_path / ".zf" / "workdirs" / "orchestrator" / "project"
    manifest = _manifest_for(workdir)
    task_map_path = tmp_path / ".zf" / "artifacts" / "TASK-PLAN" / "task-map.json"
    task_map_sha = _write(
        task_map_path,
        '{"schema_version":"task-map.v1","tasks":[{"task_id":"TASK-A","blocked_by":["TASK-MISSING"],"verification":"true"}]}\n',
    )
    manifest["artifact_refs"].append({
        "kind": "task_map",
        "path": str(task_map_path),
        "sha256": task_map_sha,
        "summary": "invalid candidate",
        "status": "accepted",
    })
    event = orch.event_writer.append(ZfEvent(
        type="artifact.manifest.published",
        actor="orchestrator",
        task_id="TASK-PLAN",
        payload=manifest,
    ))

    decision = orch._on_artifact_manifest_published(event)  # type: ignore[attr-defined]

    assert decision is not None
    assert decision.action == "block"
    assert store.get("TASK-PLAN").status == "in_progress"  # type: ignore[union-attr]
    blocked = next(e for e in orch.event_log.read_all() if e.type == "task.done.blocked")
    assert "unknown task" in str(blocked.payload)


def test_orchestrator_final_manifest_blocks_conflicting_target(
    tmp_path: Path,
) -> None:
    orch, store = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-PLAN",
        title="plan",
        status="in_progress",
        assigned_to="critic",
        active_dispatch_id="disp-critic",
        contract=TaskContract(phase="plan"),
    ))
    _write(
        tmp_path / "docs/plans/cangjie-agent-full-stage-backlog.md",
        "different\n",
    )
    orch.event_writer.append(ZfEvent(
        type="design.critique.done",
        id="evt-critic",
        actor="critic",
        task_id="TASK-PLAN",
        payload={"verdict": "approve", "dispatch_id": "disp-critic"},
    ))
    workdir = tmp_path / ".zf" / "workdirs" / "orchestrator" / "project"
    event = orch.event_writer.append(ZfEvent(
        type="artifact.manifest.published",
        actor="orchestrator",
        task_id="TASK-PLAN",
        payload=_manifest_for(workdir),
    ))

    decision = orch._on_artifact_manifest_published(event)  # type: ignore[attr-defined]

    assert decision is not None
    assert decision.action == "block"
    assert store.get("TASK-PLAN").status == "in_progress"  # type: ignore[union-attr]
    events = orch.event_log.read_all()
    assert any(e.type == "artifact.promote.blocked" for e in events)
    assert any(e.type == "task.done.blocked" for e in events)
