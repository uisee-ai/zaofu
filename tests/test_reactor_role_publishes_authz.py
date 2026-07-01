"""P0-1 live e2e fix (2026-05-18 zf-eval-test): cross-role hallucinated
events must be rejected by the kernel.

Symptom observed in /tmp/zf-eval-test/.zf/events.jsonl:

    04:49:26 task.dispatched   role=arch disp=disp-c0da66ceb1b3
    04:50:07 review.approved   actor=arch disp=NONE   ← arch hallucinated!
    04:50:13 test.passed       actor=arch disp=NONE   ← arch hallucinated!
    04:50:28 arch.proposal.done actor=arch disp=disp-c0da66ceb1b3  ← real

The LLM running as arch single-handedly emitted review.approved and
test.passed in addition to its own arch.proposal.done. Kernel happened
to reject these as ``dispatch_id_missing`` (because the active dispatch
was for arch, not review/test), but that's a fragile defense — if the
LLM had also copied the dispatch_id, kernel would have accepted
hallucinated reviews.

Real defense: ``_reject_invalid_lifecycle_event`` must also enforce
``event.type ∈ actor's role.publishes``.

Trusted actors (``zf-cli``, ``orchestrator``) are exempt — they emit
all event types as part of kernel-driven projections.
"""

from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    ContractDConfig,
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    VerificationConfig,
    WorkflowConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator


class _StubTransport:
    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        pass

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""


def _make_orch(tmp_path: Path) -> tuple[Orchestrator, TaskStore, EventLog]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    (state_dir / "kanban.json").write_text("[]\n")
    store = TaskStore(state_dir / "kanban.json")
    log = EventLog(state_dir / "events.jsonl")
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(tmux_session="zf-test"),
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(
                name="arch", backend="mock",
                publishes=["arch.proposal.done"],
            ),
            RoleConfig(
                name="dev", backend="mock",
                publishes=["dev.build.done", "dev.blocked"],
            ),
            RoleConfig(
                name="review", backend="mock",
                publishes=["review.approved", "review.rejected"],
            ),
            RoleConfig(
                name="test", backend="mock",
                publishes=["test.passed", "test.failed"],
            ),
        ],
        workflow=WorkflowConfig(),
        verification=VerificationConfig(
            contract=ContractDConfig(dispatch_token_required=True),
        ),
    )
    orch = Orchestrator(state_dir, config, _StubTransport())  # type: ignore[arg-type]
    return orch, store, log


def _events(log: EventLog) -> list[ZfEvent]:
    return log.read_all()


# ---------------------------------------------------------------------------
# Core: arch must not emit review.approved
# ---------------------------------------------------------------------------


def test_arch_emitting_review_approved_is_blocked(tmp_path: Path) -> None:
    """arch is not in review's role.publishes → kernel rejects."""
    orch, store, log = _make_orch(tmp_path)
    store.add(Task(
        id="T1", title="t1", status="in_progress",
        assigned_to="arch", active_dispatch_id="disp-arch-1",
    ))
    hallucinated = ZfEvent(
        type="review.approved",
        actor="arch",
        task_id="T1",
        payload={"summary": "looks good", "evidence_refs": ["x"]},
    )
    log.append(hallucinated)

    decision = orch._reject_invalid_lifecycle_event(hallucinated)  # type: ignore[attr-defined]

    assert decision is not None, "hallucinated event must be rejected"
    assert decision.action == "block"
    reason = (decision.reason or "").lower()
    assert "publish" in reason or "authorized" in reason or "not" in reason, (
        f"reason should mention role-publishes authz; got {decision.reason!r}"
    )
    # Kernel must emit a runtime.action.rejected audit event for visibility.
    rejects = [e for e in _events(log) if e.type == "runtime.action.rejected"]
    assert any(
        (e.payload or {}).get("reason") == "event_not_published_by_role"
        for e in rejects
    ), f"expected event_not_published_by_role audit; got {[(e.type, (e.payload or {}).get('reason')) for e in rejects]}"


def test_arch_emitting_test_passed_is_blocked(tmp_path: Path) -> None:
    orch, store, log = _make_orch(tmp_path)
    store.add(Task(
        id="T1", title="t1", status="in_progress",
        assigned_to="arch", active_dispatch_id="disp-arch-1",
    ))
    hallucinated = ZfEvent(
        type="test.passed",
        actor="arch",
        task_id="T1",
        payload={"summary": "tests ok", "tests_run": ["pytest"]},
    )
    log.append(hallucinated)

    decision = orch._reject_invalid_lifecycle_event(hallucinated)  # type: ignore[attr-defined]

    assert decision is not None
    assert decision.action == "block"


# ---------------------------------------------------------------------------
# Legitimate emit must still pass
# ---------------------------------------------------------------------------


def test_arch_emitting_arch_proposal_done_is_allowed(tmp_path: Path) -> None:
    """arch publishes arch.proposal.done → passes role-publishes check."""
    orch, store, log = _make_orch(tmp_path)
    store.add(Task(
        id="T1", title="t1", status="in_progress",
        assigned_to="arch", active_dispatch_id="disp-arch-1",
    ))
    legit = ZfEvent(
        type="arch.proposal.done",
        actor="arch",
        task_id="T1",
        payload={
            "dispatch_id": "disp-arch-1",
            "summary": "plan ready",
            "changed_files": [],
            "evidence_refs": ["docs/plan.md"],
        },
    )
    log.append(legit)

    decision = orch._reject_invalid_lifecycle_event(legit)  # type: ignore[attr-defined]

    # Either None (pass through to other handlers) or non-block decision.
    if decision is not None:
        assert decision.action != "block", (
            f"legitimate emit must not be blocked; got {decision}"
        )


def test_review_emitting_review_approved_is_allowed(tmp_path: Path) -> None:
    orch, store, log = _make_orch(tmp_path)
    store.add(Task(
        id="T1", title="t1", status="review",
        assigned_to="review", active_dispatch_id="disp-rev-1",
    ))
    legit = ZfEvent(
        type="review.approved",
        actor="review",
        task_id="T1",
        payload={
            "dispatch_id": "disp-rev-1",
            "summary": "approved",
            "evidence_refs": ["diff.txt"],
        },
    )
    log.append(legit)

    decision = orch._reject_invalid_lifecycle_event(legit)  # type: ignore[attr-defined]

    if decision is not None:
        assert decision.action != "block"


def test_test_passed_with_changed_files_is_blocked_as_gate_mutation(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orch(tmp_path)
    store.add(Task(
        id="T1", title="t1", status="testing",
        assigned_to="test", active_dispatch_id="disp-test-1",
    ))
    polluted = ZfEvent(
        type="test.passed",
        actor="test",
        task_id="T1",
        payload={
            "dispatch_id": "disp-test-1",
            "summary": "tests passed after editing artifact",
            "changed_files": ["proof.txt"],
            "evidence_refs": ["git:abc123"],
            "tests_run": ["test -f proof.txt"],
        },
    )
    log.append(polluted)

    decision = orch._reject_invalid_lifecycle_event(polluted)  # type: ignore[attr-defined]

    assert decision is not None
    assert decision.action == "block"
    assert "read-only gate" in (decision.reason or "")
    events = _events(log)
    assert any(
        e.type == "runtime.action.rejected"
        and (e.payload or {}).get("reason") == "readonly_gate_modified_files"
        for e in events
    )
    assert any(
        e.type == "gate.failed"
        and (e.payload or {}).get("gate") == "readonly_gate_integrity"
        for e in events
    )
    assert any(
        e.type == "dispatch.terminal.rejected"
        and (e.payload or {}).get("reason") == "readonly_gate_modified_files"
        for e in events
    )


# ---------------------------------------------------------------------------
# Trust escape: kernel-internal actors bypass
# ---------------------------------------------------------------------------


def test_zfcli_actor_bypasses_publishes_check(tmp_path: Path) -> None:
    """``zf-cli`` actor is kernel-driven (gate events, projections) and
    must be exempt from the role-publishes check."""
    orch, store, log = _make_orch(tmp_path)
    store.add(Task(
        id="T1", title="t1", status="in_progress",
        assigned_to="dev",
    ))
    # zf-cli emits static_gate.passed — not in any role.publishes but
    # kernel-internal. Must NOT be rejected by role-publishes authz.
    sysevt = ZfEvent(
        type="static_gate.passed",
        actor="zf-cli",
        task_id="T1",
        payload={"check_count": 1},
    )
    log.append(sysevt)

    decision = orch._reject_invalid_lifecycle_event(sysevt)  # type: ignore[attr-defined]

    if decision is not None:
        # Must not be blocked because actor is trusted.
        assert decision.action != "block"
    # No event_not_published_by_role audit emitted.
    rejects = [e for e in _events(log) if e.type == "runtime.action.rejected"]
    assert not any(
        (e.payload or {}).get("reason") == "event_not_published_by_role"
        for e in rejects
    )


def test_orchestrator_actor_bypasses_publishes_check(tmp_path: Path) -> None:
    """``orchestrator`` actor is also kernel-trusted."""
    orch, store, log = _make_orch(tmp_path)
    store.add(Task(
        id="T1", title="t1", status="in_progress",
        assigned_to="dev",
    ))
    sysevt = ZfEvent(
        type="dev.build.done",  # dev's event but orchestrator emits for projection
        actor="orchestrator",
        task_id="T1",
        payload={"source": "kernel_synthesis"},
    )
    log.append(sysevt)

    decision = orch._reject_invalid_lifecycle_event(sysevt)  # type: ignore[attr-defined]

    if decision is not None:
        assert decision.action != "block"


# ---------------------------------------------------------------------------
# Instance-id resolution: dev-1 actor must resolve to dev role
# ---------------------------------------------------------------------------


def test_instance_id_actor_resolves_to_role_publishes(tmp_path: Path) -> None:
    """A replica actor (e.g. ``dev-1``) must be resolved to its role
    (``dev``) for the publishes check."""
    orch, store, log = _make_orch(tmp_path)
    store.add(Task(
        id="T1", title="t1", status="in_progress",
        assigned_to="dev-1",
    ))
    legit = ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="T1",
        payload={"summary": "done", "changed_files": ["a.py"], "evidence_refs": ["x"]},
    )
    log.append(legit)

    decision = orch._reject_invalid_lifecycle_event(legit)  # type: ignore[attr-defined]

    if decision is not None:
        assert decision.action != "block", (
            f"dev-1 → dev role must resolve and allow dev.build.done; got {decision}"
        )
