"""B-M30-01 — multi-task WIP=1 queue hole.

When Layer 2 emits several ``task.assigned`` events for the same
worker within a short window (common in multi-module tasks — see
mixed-30min-baseline-20260421-0608), the C3 reassignment path in
``_dispatch_ready`` treats every such task as "new assignee, needs
dispatch" and bypasses WIP. That causes Layer 1 to send multiple
briefings to the same tmux pane in rapid succession, and only the
first one actually lands on the worker's TUI input.

The fix scopes the C3 bypass to true reassignments: a task whose
status is ``in_progress`` AND whose assignee was rotated. Backlog
tasks on their first dispatch take the normal ``_find_available_role``
path which enforces WIP=1, so parallel assigns queue naturally.

**Backend-agnostic**: this lives entirely in the orchestrator dispatch
logic. No tmux / claude / codex specifics — same behavior for every
worker backend. The test fixtures use a plain dev role to make that
explicit.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _make_orchestrator(tmp_path: Path):
    """Spin up a minimal Orchestrator with 3 backlog tasks already
    assigned to dev (simulating Layer 2's burst of ``zf kanban
    assign`` calls)."""
    from zf.core.config.schema import (
        ProjectConfig,
        RoleConfig,
        SessionConfig,
        WorkflowConfig,
        ZfConfig,
    )
    from zf.core.events.log import EventLog
    from zf.core.events.model import ZfEvent
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator import Orchestrator

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    events_path = state_dir / "events.jsonl"
    kanban_path = state_dir / "kanban.json"

    # Seed 3 backlog tasks all assigned to dev-1
    store = TaskStore(kanban_path)
    for tid, title in [
        ("T-A", "module a"),
        ("T-B", "module b"),
        ("T-C", "module c"),
    ]:
        store.add(Task(id=tid, title=title, status="backlog",
                        assigned_to="dev"))

    # Seed the event log with 3 task.assigned events so _reassigned_
    # pending_dispatch treats them as candidates (this matches what
    # Layer 2 does when it emits ``zf kanban assign``).
    log = EventLog(events_path)
    for tid in ("T-A", "T-B", "T-C"):
        log.append(ZfEvent(
            type="task.assigned", actor="orchestrator",
            task_id=tid,
            payload={"assignee": "dev", "role": "dev"},
        ))

    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(tmux_session="zf-t"),
        roles=[
            RoleConfig(name="orchestrator", backend="claude-code"),
            RoleConfig(name="dev", backend="claude-code",
                       permission_mode="bypass"),
        ],
        workflow=WorkflowConfig(),
    )

    # Transport stub that just records dispatches (no real tmux)
    class _StubTransport:
        def __init__(self) -> None:
            self.sends: list[str] = []

        def send_task(self, role_name, briefing_path, prompt):
            self.sends.append(role_name)

        def is_alive(self, role_name):
            return True

        def capture_log(self, role_name, lines=200):
            return ""

    transport = _StubTransport()
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    return orch, transport, store, log


def test_three_tasks_dispatch_one_by_one_not_all_at_once(tmp_path: Path):
    """Each ``task.assigned`` event triggers a separate run_once cycle
    in production. Over 3 wake cycles for 3 assigns to dev, only ONE
    task should end up in_progress — the others must wait in backlog
    for dev's WIP to free.

    Before the fix: reassigned C3 path bypasses WIP on every cycle
    because latest_dispatched is still empty → all 3 dispatch →
    tmux pane receives 3 briefings rapid-fire → only the first lands
    on the TUI and the other 2 silently disappear.
    """
    orch, transport, store, log = _make_orchestrator(tmp_path)
    # Simulate 3 separate wake cycles
    for _ in range(3):
        orch._dispatch_ready()  # type: ignore[attr-defined]
    # Only one task should have dispatched; the other two wait
    in_progress = [t for t in store.list_all() if t.status == "in_progress"]
    backlog = [t for t in store.list_all() if t.status == "backlog"]
    assert len(in_progress) == 1, (
        f"WIP=1 must limit concurrent in_progress for dev, got "
        f"{len(in_progress)} in_progress: "
        f"{[t.id for t in in_progress]}"
    )
    assert len(backlog) == 2, (
        f"expected 2 tasks still backlog awaiting dev, got {len(backlog)}"
    )
    # send_task called exactly once — not 3 times
    assert len(transport.sends) == 1, (
        f"expected 1 send_task, got {len(transport.sends)} — briefing "
        f"would flood the tmux pane otherwise"
    )


def test_second_dispatch_fires_after_worker_becomes_available(
    tmp_path: Path,
):
    """When the in-flight task completes (dev.build.done → move to
    review, dev's WIP drops), the next _dispatch_ready cycle picks
    up the next backlog task assigned to dev."""
    orch, transport, store, _ = _make_orchestrator(tmp_path)
    # First cycle dispatches T-A (or similar)
    orch._dispatch_ready()  # type: ignore[attr-defined]
    first_dispatched = next(
        t for t in store.list_all() if t.status == "in_progress"
    )

    # Simulate task completion: move to review so dev's WIP frees
    store.update(first_dispatched.id, status="review")

    # Second cycle: another backlog task dispatches
    transport.sends.clear()
    decisions = orch._dispatch_ready()  # type: ignore[attr-defined]
    assert len(decisions) == 1, (
        f"expected next backlog task to dispatch after dev frees, "
        f"got {len(decisions)}"
    )
    assert len(transport.sends) == 1


@pytest.mark.parametrize("dev_backend", ["claude-code", "codex"])
def test_multi_task_queue_hole_backend_agnostic(
    tmp_path: Path, dev_backend: str,
):
    """B-M30-01 fix is backend-agnostic: dispatch logic operates on
    task/role state, not tmux specifics. Verify the same flood
    prevention holds when dev is Codex (the backend that actually
    surfaced the bug in the 30-min mixed baseline) AND when dev is
    Claude (the other half of mixed-team runs).
    """
    from zf.core.config.schema import (
        ProjectConfig,
        RoleConfig,
        SessionConfig,
        WorkflowConfig,
        ZfConfig,
    )
    from zf.core.events.log import EventLog
    from zf.core.events.model import ZfEvent
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator import Orchestrator

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    store = TaskStore(state_dir / "kanban.json")
    for tid in ("T-1", "T-2", "T-3"):
        store.add(Task(id=tid, title=tid, status="backlog",
                        assigned_to="dev"))
    log = EventLog(state_dir / "events.jsonl")
    for tid in ("T-1", "T-2", "T-3"):
        log.append(ZfEvent(type="task.assigned", actor="orchestrator",
                            task_id=tid,
                            payload={"assignee": "dev"}))

    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(),
        roles=[
            RoleConfig(name="orchestrator", backend="claude-code"),
            RoleConfig(name="dev", backend=dev_backend,
                       permission_mode="bypass"),
        ],
        workflow=WorkflowConfig(),
    )

    class _StubTransport:
        def __init__(self):
            self.sends = []
        def send_task(self, role_name, briefing_path, prompt):
            self.sends.append(role_name)
        def is_alive(self, role_name): return True
        def capture_log(self, role_name, lines=200): return ""

    transport = _StubTransport()
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    # 3 wake cycles → should still only dispatch once
    for _ in range(3):
        orch._dispatch_ready()  # type: ignore[attr-defined]
    in_progress = [t for t in store.list_all() if t.status == "in_progress"]
    assert len(in_progress) == 1, (
        f"WIP must hold for both backends — {dev_backend} dev got "
        f"{len(in_progress)} concurrent in_progress"
    )
    assert len(transport.sends) == 1


def test_c3_reassignment_respects_existing_wip(tmp_path: Path):
    """B-M30-01 v2 (2026-04-21 post-fix real run): even C3 reassignment
    must honor WIP when the *new* assignee already has another
    in-flight task.

    Production scenario (mixed baseline, 3-module):
      1. arch completes TASK-A → layer 2 reassigns A to dev → dev gets it
      2. dev is working on A
      3. arch completes TASK-B → layer 2 reassigns B to dev
      4. Old code: `reassigned_in_flight(B) = True` → bypass WIP →
         B's briefing gets send_task'd to dev while dev is still on A.
         Dev TUI now has 2 briefings piled up.

    Expected: B's re-dispatch should wait until dev finishes A (i.e.
    A's status moves out of in_progress, freeing dev's WIP slot).
    """
    from zf.core.config.schema import (
        ProjectConfig, RoleConfig, SessionConfig, WorkflowConfig, ZfConfig,
    )
    from zf.core.events.log import EventLog
    from zf.core.events.model import ZfEvent
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator import Orchestrator

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    store = TaskStore(state_dir / "kanban.json")
    # Task A: already dispatched to dev, dev is working on it
    store.add(Task(id="T-A", title="module a",
                    status="in_progress", assigned_to="dev"))
    # Task B: just finished arch, layer 2 re-assigned to dev
    store.add(Task(id="T-B", title="module b",
                    status="in_progress", assigned_to="dev"))

    log = EventLog(state_dir / "events.jsonl")
    # A was dispatched earlier
    log.append(ZfEvent(type="task.assigned", actor="orchestrator",
                        task_id="T-A", payload={"assignee": "arch"}))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                        task_id="T-A", payload={"assignee": "arch"}))
    log.append(ZfEvent(type="task.assigned", actor="orchestrator",
                        task_id="T-A", payload={"assignee": "dev"}))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                        task_id="T-A", payload={"assignee": "dev"}))
    # B just finished arch, Layer 2 rotates to dev — but dev is busy
    log.append(ZfEvent(type="task.assigned", actor="orchestrator",
                        task_id="T-B", payload={"assignee": "arch"}))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                        task_id="T-B", payload={"assignee": "arch"}))
    log.append(ZfEvent(type="task.assigned", actor="orchestrator",
                        task_id="T-B", payload={"assignee": "dev"}))

    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(),
        roles=[
            RoleConfig(name="orchestrator", backend="claude-code"),
            RoleConfig(name="arch", backend="claude-code"),
            RoleConfig(name="dev", backend="codex",
                       permission_mode="bypass"),
        ],
        workflow=WorkflowConfig(),
    )

    class _StubTransport:
        def __init__(self):
            self.sends = []
        def send_task(self, role_name, briefing_path, prompt):
            self.sends.append(role_name)
        def is_alive(self, role_name): return True
        def capture_log(self, role_name, lines=200): return ""

    transport = _StubTransport()
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]

    # dev already has A in flight; B reassignment should WAIT
    orch._dispatch_ready()  # type: ignore[attr-defined]
    # Before the v2 fix, B would be dispatched to dev here → sends=["dev"]
    # After the v2 fix, no send (dev still busy on A)
    assert len(transport.sends) == 0, (
        f"C3 reassignment must not bypass WIP when worker has another "
        f"in-flight task; got sends={transport.sends}"
    )


def test_c3_reassignment_fires_after_worker_frees(tmp_path: Path):
    """Once A leaves in_progress (e.g. dev.build.done → review), B's
    re-dispatch to dev proceeds on the next cycle."""
    from zf.core.config.schema import (
        ProjectConfig, RoleConfig, SessionConfig, WorkflowConfig, ZfConfig,
    )
    from zf.core.events.log import EventLog
    from zf.core.events.model import ZfEvent
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator import Orchestrator

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    store = TaskStore(state_dir / "kanban.json")
    # A is now in review (dev freed)
    store.add(Task(id="T-A", title="a", status="review",
                    assigned_to="review"))
    # B still reassigned-to-dev, waiting
    store.add(Task(id="T-B", title="b", status="in_progress",
                    assigned_to="dev"))

    log = EventLog(state_dir / "events.jsonl")
    for t, a in [
        ("task.assigned", "arch"), ("task.dispatched", "arch"),
    ]:
        log.append(ZfEvent(type=t, actor="orch", task_id="T-B",
                            payload={"assignee": a}))
    # B reassigned to dev
    log.append(ZfEvent(type="task.assigned", actor="orch", task_id="T-B",
                        payload={"assignee": "dev"}))

    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(),
        roles=[
            RoleConfig(name="orchestrator", backend="claude-code"),
            RoleConfig(name="arch", backend="claude-code"),
            RoleConfig(name="dev", backend="codex",
                       permission_mode="bypass"),
            RoleConfig(name="review", backend="claude-code"),
        ],
        workflow=WorkflowConfig(),
    )

    class _StubTransport:
        def __init__(self):
            self.sends = []
        def send_task(self, role_name, briefing_path, prompt):
            self.sends.append(role_name)
        def is_alive(self, role_name): return True
        def capture_log(self, role_name, lines=200): return ""

    transport = _StubTransport()
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]

    decisions = orch._dispatch_ready()  # type: ignore[attr-defined]
    assert any(d.role == "dev" for d in decisions), (
        f"B should dispatch to dev now that A is in review "
        f"(dev's WIP has room); got {decisions}"
    )


def test_in_progress_reassignment_still_bypasses_wip(tmp_path: Path):
    """Regression guard for C3's original intent: when a task is
    already in_progress and Layer 2 rotates its assignee (e.g. after
    dev.build.done → review), that re-dispatch should proceed even
    if the new assignee has WIP=0 constraints (review just transitioned
    from idle, no other in_progress task)."""
    from zf.core.config.schema import (
        ProjectConfig,
        RoleConfig,
        SessionConfig,
        WorkflowConfig,
        ZfConfig,
    )
    from zf.core.events.log import EventLog
    from zf.core.events.model import ZfEvent
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator import Orchestrator

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()

    store = TaskStore(state_dir / "kanban.json")
    # Task already dispatched to dev (previous dispatch), now
    # Layer 2 rotates assignee to review
    store.add(Task(
        id="T-REWORK", title="review task", status="review",
        assigned_to="review",
    ))

    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="task.assigned", actor="orchestrator",
                        task_id="T-REWORK",
                        payload={"assignee": "dev"}))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                        task_id="T-REWORK",
                        payload={"assignee": "dev"}))
    # Rotate to review (this is the C3 scenario)
    log.append(ZfEvent(type="task.assigned", actor="orchestrator",
                        task_id="T-REWORK",
                        payload={"assignee": "review"}))

    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(),
        roles=[
            RoleConfig(name="orchestrator", backend="claude-code"),
            RoleConfig(name="dev", backend="claude-code"),
            RoleConfig(name="review", backend="claude-code"),
        ],
        workflow=WorkflowConfig(),
    )

    class _StubTransport:
        def __init__(self):
            self.sends = []
        def send_task(self, role_name, briefing_path, prompt):
            self.sends.append(role_name)
        def is_alive(self, role_name): return True
        def capture_log(self, role_name, lines=200): return ""

    transport = _StubTransport()
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]

    # Move task back into in_progress so the WIP counter sees it
    # (matches reality — review phase is still "task is being worked on")
    store.update("T-REWORK", status="in_progress")

    decisions = orch._dispatch_ready()  # type: ignore[attr-defined]
    # This is a true C3 reassignment (in_progress + rotated assignee)
    # → bypasses WIP, should dispatch to review
    assert any(d.role == "review" for d in decisions), (
        f"C3 in_progress rotation must re-dispatch to new assignee, "
        f"got {decisions}"
    )
    assert "review" in transport.sends
