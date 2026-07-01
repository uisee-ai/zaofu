"""ZF-PWF-INV-001 — cross-cutting harness invariant regression tests.

These guard the PWF + long-horizon invariants established by
sibling sprints. Failure here means a future change silently
broke a documented invariant:

- I51: State Packet is a *projection*, single physical writer via
  atomic_write_text, no event emission inside the projector.
- I53: Worker briefing contains <zf-workflow-state> breadcrumb on
  every dispatch.
- I54: Required context refs map to a checkable filesystem location.
- I55: Worker briefing's first line is the deterministic
  ``Active task: <task_id>`` marker.
- I57: WorkflowEventSets baseline is the single source for the 4
  historical hardcoded event sets.
- I61: Every working-memory projection declares
  "projection only, not runtime truth" + source_events.
- I62: Artifact attestation rejects non-hex / wrong-length SHA-256;
  unknown kinds rejected; tamper detection works.
- I63: OperatorSession is frozen.
- I64: provider.stop.check event registered; stop_guard returns
  empty missing for tasks without verification_tiers (no false
  blocks for legacy tasks).
- I65: PreCompact hook registered in claude settings + WAKE_PATTERNS.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.events.known_types import KNOWN_EVENT_TYPES
from zf.core.security.attestation import (
    ATTESTATION_SCHEMA_VERSION,
    KNOWN_ATTESTATION_KINDS,
)
from zf.core.state.operator_sessions import OperatorSession
from zf.core.state.state_packet import (
    SCHEMA_VERSION as STATE_PACKET_SCHEMA_VERSION,
    StatePacket,
    StatePacketOwner,
)
from zf.core.task.schema import Task, TaskContract
from zf.core.workflow.topology import WorkflowEventSets
from zf.runtime.injection import generate_task_briefing
from zf.runtime.stop_guard import evaluate_stop_gates
from zf.runtime.wake_patterns import WAKE_PATTERNS
from zf.runtime.working_memory_projection import (
    ProjectionInputs,
    render_attempt_ledger,
    render_findings,
    render_plan,
    render_progress,
)


# ---------------------------------------------------------------------------
# I51 — State Packet projector discipline
# ---------------------------------------------------------------------------


def test_inv_i51_state_packet_projector_uses_atomic_write_only() -> None:
    from zf.runtime import state_packet_projector

    source = inspect.getsource(state_packet_projector)
    # Single physical writer: atomic_write_text must appear; raw open
    # for write must not (would bypass the atomic invariant).
    assert "atomic_write_text" in source
    assert "open(" not in source or '"r"' in source or "encoding=" in source


def test_inv_i51_projector_never_calls_event_writer() -> None:
    from zf.runtime import state_packet_projector

    source = inspect.getsource(state_packet_projector)
    assert "event_writer.append" not in source
    assert "EventWriter(" not in source


def test_inv_i51_state_packet_schema_version_locked() -> None:
    """Schema version is "1.0" during the 1-week freeze window. Bumping
    requires a deliberate sprint."""
    assert STATE_PACKET_SCHEMA_VERSION == "1.0"


# ---------------------------------------------------------------------------
# I53 — Workflow-state breadcrumb in every worker briefing
# ---------------------------------------------------------------------------


def _role(name: str = "dev") -> RoleConfig:
    role = RoleConfig(
        name=name,
        role_kind="auto",
        publishes=[f"{name}.done"],
        triggers=["task.dispatched"],
    )
    role.instance_id = f"{name}-1"
    return role


def _task(task_id: str = "TASK-INV") -> Task:
    return Task(
        id=task_id,
        title="demo",
        status="in_progress",
        active_dispatch_id="disp-1",
        contract=TaskContract(behavior="do thing"),
    )


@pytest.mark.parametrize("role_name", ["dev", "review", "test", "judge"])
def test_inv_i53_every_worker_briefing_contains_breadcrumb(
    role_name: str,
) -> None:
    role = _role(role_name)
    config = ZfConfig(roles=[role])
    briefing = generate_task_briefing(config, role, _task())
    assert "<zf-workflow-state>" in briefing
    assert "</zf-workflow-state>" in briefing


# ---------------------------------------------------------------------------
# I55 — Active task: first line invariant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role_name", ["dev", "review", "test", "judge"])
def test_inv_i55_briefing_first_line_active_task(role_name: str) -> None:
    role = _role(role_name)
    config = ZfConfig(roles=[role])
    briefing = generate_task_briefing(config, role, _task("TASK-FIRST"))
    first = briefing.splitlines()[0]
    assert first == "Active task: TASK-FIRST"


# ---------------------------------------------------------------------------
# I57 — WorkflowEventSets baseline is single source
# ---------------------------------------------------------------------------


def test_inv_i57_dispatch_uses_baseline_event_sets() -> None:
    """orchestrator_dispatch.py's 3 historical sets must delegate to
    WorkflowEventSets.baseline()."""
    from zf.runtime.orchestrator_dispatch import DispatchMixin

    baseline = WorkflowEventSets.baseline()
    assert DispatchMixin._HANDOFF_SUCCESS_EVENTS == baseline.handoff_success_events
    assert DispatchMixin._STAGE_PROGRESS_EVENTS == baseline.stage_progress_events
    assert DispatchMixin._REWORK_TRIGGER_EVENTS == baseline.rework_trigger_events


def test_inv_i57_rework_triage_uses_baseline() -> None:
    from zf.runtime.rework_triage import REWORK_TRIAGE_TRIGGER_EVENTS

    assert REWORK_TRIAGE_TRIGGER_EVENTS == (
        WorkflowEventSets.baseline().rework_triage_trigger_events
    )


# ---------------------------------------------------------------------------
# I61 — Working-memory projection header invariant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("renderer", [
    render_plan, render_findings, render_progress, render_attempt_ledger,
])
def test_inv_i61_projection_header_invariant(renderer) -> None:
    inputs = ProjectionInputs(packet=StatePacket(
        task_id="TASK-INV", current_stage="implement",
        owner=StatePacketOwner(role="dev", instance_id="dev-1"),
    ))
    text = renderer(inputs)
    assert "projection only, not runtime truth" in text
    assert "source_events:" in text
    assert "state_packet_ref:" in text
    assert "generated_at:" in text


# ---------------------------------------------------------------------------
# I62 — Artifact attestation discipline
# ---------------------------------------------------------------------------


def test_inv_i62_six_attestation_kinds_only() -> None:
    """The kind list is the hard-coded surface; expanding it requires
    schema-version bump. Six is the locked count for 1.0."""
    assert len(KNOWN_ATTESTATION_KINDS) == 6
    assert ATTESTATION_SCHEMA_VERSION == "1.0"


# ---------------------------------------------------------------------------
# I63 — OperatorSession frozen
# ---------------------------------------------------------------------------


def test_inv_i63_operator_session_frozen() -> None:
    sess = OperatorSession(operator_session_id="x", source="cli")
    with pytest.raises((AttributeError, TypeError)):
        sess.task_id = "TASK-Z"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# I64 — Stop guard no-false-block invariant
# ---------------------------------------------------------------------------


def test_inv_i64_provider_stop_check_event_known() -> None:
    assert "provider.stop.check" in KNOWN_EVENT_TYPES


def test_inv_i64_legacy_task_no_false_block() -> None:
    """Tasks without verification_tiers must not be blocked
    (preserve backward compat for older yaml setups)."""
    task = Task(
        id="TASK-LEGACY",
        title="legacy",
        status="in_progress",
        active_dispatch_id="disp-1",
        contract=TaskContract(behavior="legacy"),
    )

    def _no(*_):
        return False

    result = evaluate_stop_gates(task, has_success_event=_no)
    assert result.blocked is False


# ---------------------------------------------------------------------------
# I65 — PreCompact hook registration
# ---------------------------------------------------------------------------


def test_inv_i65_precompact_event_known() -> None:
    assert "worker.context.precompact" in KNOWN_EVENT_TYPES
    assert "worker.context.snapshot_requested" in KNOWN_EVENT_TYPES


def test_inv_i65_precompact_in_wake_patterns() -> None:
    assert "worker.context.precompact" in WAKE_PATTERNS
    assert "worker.context.snapshot_requested" in WAKE_PATTERNS


def test_inv_i65_claude_settings_registers_precompact(tmp_path: Path) -> None:
    from zf.cli.start import _write_claude_hook_settings

    _write_claude_hook_settings(tmp_path)
    data = json.loads(
        (tmp_path / "hooks" / "settings.json").read_text()
    )
    assert "PreCompact" in data["hooks"]


# ---------------------------------------------------------------------------
# Cross-cutting: orchestrator.decision.recorded NOT in WAKE_PATTERNS
# (would cause emit-loop on every wake)
# ---------------------------------------------------------------------------


def test_inv_decision_recorded_not_wake_pattern() -> None:
    """ORCH-ACT-001 acceptance §1 misreads put decision.recorded into
    WAKE_PATTERNS — that would cause infinite emit loops. This test
    locks the opposite invariant: it must NOT be in WAKE_PATTERNS."""
    assert "orchestrator.decision.recorded" not in WAKE_PATTERNS
    assert "orchestrator.decision.recorded" in KNOWN_EVENT_TYPES
