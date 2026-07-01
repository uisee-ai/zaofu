"""ω-1.b: `_on_gate_failed` must honor `payload.verdict == "SUSPEND"`.

Per docs/design/38-omega-1-baseline-and-verdict.md §4 + backlog
backlogs/2026-05-18-0243-omega-1b-verdict-aware-gate-failed.md.

r-next-10 evidence: evt-c1f8bedc5b5b (critic v3 emit gate.failed with
verdict="SUSPEND" at 01:55:12) was ignored by the kernel reactor —
`_on_gate_failed` blindly called `_route_rework_trigger` which led to
arch v4 dispatch (evt-22b6af7de87c at 01:55:17). LLM orchestrator
later (~150s) emitted operator-repair events, but arch v4 was already
running.

Fix: read payload.verdict in `_on_gate_failed`; "SUSPEND"
(case-insensitive) routes to `_on_suspended` (LH-3 path) instead of
rework. Backward compatible — missing or "reject" verdict still routes
to rework.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


# ─── fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    (sd / "logs").mkdir()
    log = EventLog(sd / "events.jsonl")
    log.append(ZfEvent(type="session.started", actor="zf-cli"))
    log.append(ZfEvent(type="loop.started", actor="zf-cli"))
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def config():
    return ZfConfig(
        project=ProjectConfig(name="test"),
        session=SessionConfig(tmux_session="test-zf"),
        roles=[
            RoleConfig(
                name="arch",
                backend="mock",
                stages=["design"],
                publishes=["arch.proposal.done"],
            ),
            RoleConfig(
                name="critic",
                backend="mock",
                stages=["design_critique"],
                publishes=["design.critique.done", "gate.failed"],
            ),
            RoleConfig(
                name="dev",
                backend="mock",
                stages=["implement"],
                publishes=["dev.build.done"],
            ),
        ],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="test-zf", dry_run=True))


@pytest.fixture
def orch(state_dir, config, transport):
    return Orchestrator(state_dir, config, transport)


def _seed_task(orch: Orchestrator, status: str = "awaiting_review") -> Task:
    task = Task(
        id="TASK-T1",
        title="t",
        status=status,
        assigned_to="arch",
        contract=TaskContract(behavior="b"),
    )
    orch.task_store.add(task)
    return task


def _gate_failed(verdict: str | None) -> ZfEvent:
    payload = {"summary": "test"}
    if verdict is not None:
        payload["verdict"] = verdict
    return ZfEvent(
        type="gate.failed",
        actor="critic",
        task_id="TASK-T1",
        payload=payload,
    )


# ─── ω-1.b verdict gate ──────────────────────────────────────────────────


def test_gate_failed_with_suspend_routes_to_on_suspended(orch, monkeypatch):
    _seed_task(orch)
    suspended_calls = []
    rework_calls = []
    monkeypatch.setattr(
        orch, "_on_suspended",
        lambda ev: suspended_calls.append(ev) or None,
    )
    monkeypatch.setattr(
        orch, "_route_rework_trigger",
        lambda *a, **kw: rework_calls.append((a, kw)) or None,
    )

    orch._on_gate_failed(_gate_failed("SUSPEND"))

    assert len(suspended_calls) == 1
    assert len(rework_calls) == 0


def test_gate_failed_with_suspend_lowercase_is_also_suspend(orch, monkeypatch):
    """case-insensitive: 'suspend', 'SUSPEND', 'Suspend' all trigger
    escalation."""
    _seed_task(orch)
    suspended_calls = []
    rework_calls = []
    monkeypatch.setattr(
        orch, "_on_suspended",
        lambda ev: suspended_calls.append(ev) or None,
    )
    monkeypatch.setattr(
        orch, "_route_rework_trigger",
        lambda *a, **kw: rework_calls.append((a, kw)) or None,
    )

    orch._on_gate_failed(_gate_failed("suspend"))

    assert len(suspended_calls) == 1
    assert len(rework_calls) == 0


def test_gate_failed_with_reject_verdict_routes_to_rework(orch, monkeypatch):
    """Default critic verdict='reject' still goes through rework."""
    _seed_task(orch)
    suspended_calls = []
    rework_calls = []
    monkeypatch.setattr(
        orch, "_on_suspended",
        lambda ev: suspended_calls.append(ev) or None,
    )
    monkeypatch.setattr(
        orch, "_route_rework_trigger",
        lambda *a, **kw: rework_calls.append((a, kw)) or None,
    )

    orch._on_gate_failed(_gate_failed("reject"))

    assert len(suspended_calls) == 0
    assert len(rework_calls) == 1


def test_gate_failed_with_no_verdict_routes_to_rework(orch, monkeypatch):
    """Backward compat: payloads without verdict (current cangjie
    workers) keep existing rework behavior — no regression."""
    _seed_task(orch)
    suspended_calls = []
    rework_calls = []
    monkeypatch.setattr(
        orch, "_on_suspended",
        lambda ev: suspended_calls.append(ev) or None,
    )
    monkeypatch.setattr(
        orch, "_route_rework_trigger",
        lambda *a, **kw: rework_calls.append((a, kw)) or None,
    )

    orch._on_gate_failed(_gate_failed(None))

    assert len(suspended_calls) == 0
    assert len(rework_calls) == 1


def test_gate_failed_with_unknown_verdict_routes_to_rework(orch, monkeypatch):
    """Defensive: arbitrary verdict strings (e.g. 'maybe', 'invalid')
    don't accidentally trigger SUSPEND — only the exact word 'suspend'
    (case-insensitive)."""
    _seed_task(orch)
    suspended_calls = []
    rework_calls = []
    monkeypatch.setattr(
        orch, "_on_suspended",
        lambda ev: suspended_calls.append(ev) or None,
    )
    monkeypatch.setattr(
        orch, "_route_rework_trigger",
        lambda *a, **kw: rework_calls.append((a, kw)) or None,
    )

    orch._on_gate_failed(_gate_failed("unsure"))

    assert len(suspended_calls) == 0
    assert len(rework_calls) == 1


def test_gate_failed_on_done_task_returns_none_regardless_of_verdict(orch):
    """Existing guard: tasks in done/cancelled/in_progress short-circuit
    early. SUSPEND verdict shouldn't bypass that guard."""
    _seed_task(orch, status="done")

    result = orch._on_gate_failed(_gate_failed("SUSPEND"))

    assert result is None


# ─── replay r-next-10 evt-c1f8bedc5b5b ───────────────────────────────────


def test_replay_r_next_10_suspend_evidence(orch, monkeypatch):
    """Direct replay of the critic v3 SUSPEND from r-next-10
    (evt-c1f8bedc5b5b at 01:55:12). After ω-1.b, kernel must NOT call
    `_route_rework_trigger`."""
    _seed_task(orch, status="awaiting_review")
    suspended_calls = []
    rework_calls = []
    monkeypatch.setattr(
        orch, "_on_suspended",
        lambda ev: suspended_calls.append(ev) or None,
    )
    monkeypatch.setattr(
        orch, "_route_rework_trigger",
        lambda *a, **kw: rework_calls.append((a, kw)) or None,
    )

    # Reconstructed shape from r-next-10 evt-c1f8bedc5b5b — task_id
    # adjusted to TASK-T1 since the seeded task is TASK-T1; _on_suspended
    # is mocked so the actual task_id doesn't need to match the real
    # cangjie one.
    replay_event = ZfEvent(
        type="gate.failed",
        actor="critic",
        task_id="TASK-T1",
        payload={
            "verdict": "SUSPEND",
            "summary": (
                "升级 TASK-EB3243 rework #2 verdict=SUSPEND, "
                "走人工 operator escalate."
            ),
            "rework_attempt": "2/3",
        },
    )
    orch._on_gate_failed(replay_event)

    assert len(suspended_calls) == 1, (
        "ω-1.b regression: SUSPEND verdict must route to _on_suspended"
    )
    assert len(rework_calls) == 0, (
        "ω-1.b regression: SUSPEND verdict must NOT call _route_rework_trigger "
        "(prevents arch v4 dispatch evt-22b6af7de87c-style waste)"
    )
