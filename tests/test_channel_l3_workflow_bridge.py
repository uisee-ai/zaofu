"""L3 — spec → workflow 桥接 (test plan §3 L3).

Source plan: docs/test_case/01-zaofu-channel-collab-test-plan.md §3 L3.
Doc 64 §7 mandates that any Channel → Workflow promotion carry a
``source_refs`` payload field (channel_id + synthesis_event_id + ...).

These tests pin three slices of that bridge:

- step 1 happy path: operator approve → ``workflow.invoke.requested``
  payload carries ``source_refs`` (channel_id + synthesis_event_id).
- step 3 projection: after ``workflow.invoke.accepted``, ``task.created``
  emitted with ``source_refs`` is captured by the channel projection so
  Web can render the causal link.
- step 4 (反例 a, scope guard): WorkstreamScopeGuard is not importable
  in the unit slice today — skipped with explicit reason (gap, not pass).
- step 5 (反例 b, CRITICAL schema strictness): emitting
  ``workflow.invoke.requested`` WITHOUT ``source_refs`` must violate
  schema. Today ``workflow_invoke_schema_rules()`` does NOT include
  ``source_refs`` in ``required`` — this test is intentionally RED on
  current main, driving the doc 64 §7 schema constraint.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.verification.event_schema import (
    EventSchemaRegistry,
    channel_event_schema_rules,
    workflow_invoke_schema_rules,
)
from zf.runtime.channel_projection import project_channel


CHANNEL_ID = "ch-zaofu"
THREAD_ID = "th-plan"


def _bootstrap_channel(log: EventLog) -> None:
    """Minimal channel state: created + one member + spec synthesis."""
    log.append(ZfEvent(
        type="channel.created",
        actor="web",
        payload={"channel_id": CHANNEL_ID, "name": "zaofu", "source": "web"},
        correlation_id=CHANNEL_ID,
    ))
    log.append(ZfEvent(
        type="channel.member.invited",
        actor="web",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": THREAD_ID,
            "member_id": "claude-arch",
            "persona": "Architect",
            "source": "web",
        },
        correlation_id=CHANNEL_ID,
    ))


def _emit_synthesis(writer: EventWriter, *, spec_path: str) -> ZfEvent:
    """Step 0 — arch produces a synthesis.proposed carrying spec_path."""
    return writer.emit(
        "channel.synthesis.proposed",
        actor="claude-arch",
        correlation_id=CHANNEL_ID,
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": THREAD_ID,
            "decision": "promote",
            "summary": "approve spec",
            "source": "agent",
            "spec_path": spec_path,
        },
    )


# ---------------------------------------------------------------------------
# L3 step 1 — happy path: workflow.invoke.requested carries source_refs.
# ---------------------------------------------------------------------------


def test_l3_step1_operator_invoke_carries_source_refs(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log, correlation_id=CHANNEL_ID)

    _bootstrap_channel(log)
    synthesis = _emit_synthesis(
        writer, spec_path=".zf/channels/zaofu/specs/X.md",
    )

    # step 1: operator approves → emit workflow.invoke.requested with source_refs.
    invoke = writer.emit(
        "workflow.invoke.requested",
        actor="operator",
        task_id="task-X",
        causation_id=synthesis.id,
        correlation_id=CHANNEL_ID,
        payload={
            "task_id": "task-X",
            "pattern_id": "star",
            "requested_by": "operator",
            "reason": "approved synthesis",
            "source": "operator",
            "source_refs": {
                "channel_id": CHANNEL_ID,
                "synthesis_event_id": synthesis.id,
            },
        },
    )

    # verify: payload carries source_refs and the refs point back to the synthesis.
    refs = invoke.payload.get("source_refs")
    assert isinstance(refs, dict), "workflow.invoke.requested payload must carry source_refs dict"
    assert refs.get("channel_id") == CHANNEL_ID
    assert refs.get("synthesis_event_id") == synthesis.id
    # And the event is causally tied to the synthesis (kernel writer enriches via causation_id).
    assert invoke.causation_id == synthesis.id


# ---------------------------------------------------------------------------
# L3 step 3 — projection captures task.created with source_refs after accept.
# ---------------------------------------------------------------------------


def test_l3_step3_projection_captures_workflow_link_after_accept(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log, correlation_id=CHANNEL_ID)

    _bootstrap_channel(log)
    synthesis = _emit_synthesis(
        writer, spec_path=".zf/channels/zaofu/specs/X.md",
    )

    source_refs = {
        "channel_id": CHANNEL_ID,
        "synthesis_event_id": synthesis.id,
    }
    requested = writer.emit(
        "workflow.invoke.requested",
        actor="operator",
        task_id="task-X",
        causation_id=synthesis.id,
        correlation_id=CHANNEL_ID,
        payload={
            "task_id": "task-X",
            "pattern_id": "star",
            "requested_by": "operator",
            "reason": "approved synthesis",
            "source": "operator",
            "source_refs": dict(source_refs),
        },
    )
    accepted = writer.emit(
        "workflow.invoke.accepted",
        actor="orchestrator",
        task_id="task-X",
        causation_id=requested.id,
        correlation_id=CHANNEL_ID,
        payload={
            "task_id": "task-X",
            "pattern_id": "star",
            "source_event_id": requested.id,
            "channel_id": CHANNEL_ID,
            "thread_id": THREAD_ID,
            "source_refs": dict(source_refs),
        },
    )
    # Unit slice — orchestrator fanout is mocked: emit task.created manually,
    # propagating source_refs from the accepted event (doc 64 §7 lineage).
    writer.emit(
        "task.created",
        actor="orchestrator",
        task_id="task-X",
        causation_id=accepted.id,
        correlation_id=CHANNEL_ID,
        payload={
            "task_id": "task-X",
            "source_refs": dict(source_refs),
        },
    )

    detail = project_channel(state_dir, CHANNEL_ID)
    assert detail is not None

    # verify: projection records the requested + accepted as workflow_requests
    # entries tied to task-X and pattern_id=star. This is the operator-visible
    # link (Web pane).
    workflow_requests = detail.get("workflow_requests") or []
    types_seen = [item.get("type") for item in workflow_requests]
    assert "workflow.invoke.requested" in types_seen
    assert "workflow.invoke.accepted" in types_seen
    star_tasks = [
        item for item in workflow_requests
        if item.get("task_id") == "task-X" and item.get("pattern_id") == "star"
    ]
    assert star_tasks, "projection must capture the star/task-X workflow link"


# ---------------------------------------------------------------------------
# L3 step 4 (反例 a — scope guard) — gap: WorkstreamScopeGuard not testable.
# ---------------------------------------------------------------------------


def test_l3_step4_scope_guard_overlap_rejection(tmp_path: Path) -> None:
    """Doc 64 §6: WorkstreamScopeGuard rejects spec.paths that overlap with
    an existing in-flight task's exclusive_files. Pure unit slice on the
    overlap check itself — the reactor wiring is covered separately by
    ``test_l3_step4_reactor_rejects_workflow_invoke_on_scope_overlap``.
    """
    from zf.core.task.schema import Task, TaskContract
    from zf.core.task.store import TaskStore
    from zf.runtime.workstream_scope_guard import check_workstream_scope

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")

    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-EXISTING",
        title="reserves src/foo.py",
        status="in_progress",
        contract=TaskContract(exclusive_files=["src/foo.py"]),
    ))

    # Overlap case — must be refused.
    overlap = check_workstream_scope(
        state_dir,
        proposed_paths=["src/foo.py"],
        proposed_task_id="TASK-NEW",
    )
    assert overlap.allowed is False
    assert any(o.task_id == "TASK-EXISTING" for o in overlap.overlaps)
    assert any("src/foo.py" in o.paths for o in overlap.overlaps)

    # Disjoint case — must be allowed.
    clear = check_workstream_scope(
        state_dir,
        proposed_paths=["src/bar.py"],
        proposed_task_id="TASK-NEW",
    )
    assert clear.allowed is True
    assert clear.overlaps == []


def test_l3_step4_reactor_rejects_workflow_invoke_on_scope_overlap(tmp_path: Path) -> None:
    """Integration slice — the reactor must short-circuit
    ``workflow.invoke.requested`` when the proposal's declared paths
    collide with an in-flight task's ``exclusive_files``. The expected
    outcome is a ``workflow.invoke.rejected`` event (with reason starting
    ``workstream_scope_overlap``) and NO ``workflow.invoke.accepted``.
    Also asserts that a ``channel.workflow.rejected`` signal flows back
    to the originating channel.
    """
    from zf.core.config.schema import (
        FanoutAggregateConfig,
        ProjectConfig,
        RoleConfig,
        WorkflowConfig,
        WorkflowStageConfig,
        ZfConfig,
    )
    from zf.core.task.schema import Task, TaskContract
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator import Orchestrator

    class _Transport:
        def send_task(self, *args, **kwargs):  # noqa: ANN001, ANN002, D401
            pass

        def is_alive(self, _role_name):  # noqa: ANN001
            return True

        def capture_log(self, _role_name, lines=200):  # noqa: ANN001
            return ""

        def poll_events(self):
            return []

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")

    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-EXISTING",
        title="reserves src/foo.py",
        status="in_progress",
        contract=TaskContract(exclusive_files=["src/foo.py"]),
    ))
    store.add(Task(
        id="TASK-NEW",
        title="new workstream",
        active_dispatch_id="disp-new",
    ))

    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="review-a", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="review-wave",
                trigger="candidate.ready",
                topology="fanout_reader",
                roles=["review-a"],
                target_ref="candidate/${task_id}",
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="review.approved",
                    failure_event="review.rejected",
                ),
            ),
        ]),
    )
    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator(state_dir, config, _Transport())  # type: ignore[arg-type]
    orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested",
        actor="operator",
        task_id="TASK-NEW",
        correlation_id=CHANNEL_ID,
        payload={
            "task_id": "TASK-NEW",
            "pattern_id": "review-wave",
            "dispatch_id": "disp-new",
            "requested_by": "operator",
            "reason": "promote workstream",
            "source": "operator",
            "channel_id": CHANNEL_ID,
            "thread_id": THREAD_ID,
            "paths": ["src/foo.py"],
            "source_refs": {
                "channel_id": CHANNEL_ID,
                "synthesis_event_id": "evt-synth-stub",
            },
        },
    )])

    events = log.read_all()
    types_seen = [e.type for e in events]
    assert "workflow.invoke.accepted" not in types_seen, (
        f"scope guard must prevent acceptance; saw events: {types_seen}"
    )
    rejected = [e for e in events if e.type == "workflow.invoke.rejected"]
    assert rejected, "expected workflow.invoke.rejected on scope overlap"
    assert str(rejected[-1].payload.get("reason") or "").startswith(
        "workstream_scope_overlap"
    ), f"unexpected reject reason: {rejected[-1].payload.get('reason')!r}"

    # Channel-facing signal so the originating UI/agent learns about the block.
    channel_rejected = [e for e in events if e.type == "channel.workflow.rejected"]
    assert channel_rejected, "expected channel.workflow.rejected back-signal"
    assert channel_rejected[-1].payload.get("channel_id") == CHANNEL_ID


# ---------------------------------------------------------------------------
# L3 step 5 — CRITICAL: schema must require source_refs on invoke.requested.
# ---------------------------------------------------------------------------


def test_l3_step5_schema_rejects_workflow_invoke_without_source_refs() -> None:
    """Doc 64 §7 mandates ``source_refs`` on every Channel/Squad → Workflow
    bridge event. This test asserts the registry returns a missing_required
    violation when ``workflow.invoke.requested`` lacks ``source_refs``.

    INTENTIONALLY RED on current main: the schema in
    ``workflow_invoke_schema_rules()`` lists ``source_refs`` as neither
    required nor optional. Making this test green requires adding
    ``source_refs`` to the ``required`` tuple for
    ``workflow.invoke.requested`` (and the bridge writer paths that emit
    it). The test is the forcing function.
    """
    registry = EventSchemaRegistry.from_dict(workflow_invoke_schema_rules())

    event = ZfEvent(
        type="workflow.invoke.requested",
        actor="operator",
        task_id="task-X",
        payload={
            "task_id": "task-X",
            "pattern_id": "star",
            "requested_by": "operator",
            "reason": "approved synthesis",
            "source": "operator",
            # NOTE: deliberately omit source_refs.
        },
    )

    violations = registry.validate(event)
    missing = [
        v for v in violations
        if v.code == "missing_required"
        and v.field_path == "payload.source_refs"
    ]
    assert missing, (
        "schema must reject workflow.invoke.requested without source_refs "
        "(doc 64 §7). Current schema is missing the constraint — add "
        "'source_refs' to workflow_invoke_schema_rules() required list."
    )


def test_l3_step5_schema_accepts_workflow_invoke_with_source_refs() -> None:
    """Positive control for step 5 — once source_refs is added to required
    (per doc 64 §7), a payload carrying it must validate cleanly. This guards
    against the schema being tightened in a way that breaks the happy path.
    """
    registry = EventSchemaRegistry.from_dict(workflow_invoke_schema_rules())

    event = ZfEvent(
        type="workflow.invoke.requested",
        actor="operator",
        task_id="task-X",
        payload={
            "task_id": "task-X",
            "pattern_id": "star",
            "requested_by": "operator",
            "reason": "approved synthesis",
            "source": "operator",
            "source_refs": {
                "channel_id": CHANNEL_ID,
                "synthesis_event_id": "evt-synth-1",
            },
        },
    )

    violations = registry.validate(event)
    # The optional list does not include source_refs today; the validator
    # may warn about an "unknown" field. The contract we care about: the
    # well-formed event has no ``missing_required`` violations.
    missing_required = [v for v in violations if v.code == "missing_required"]
    assert missing_required == [], (
        f"happy-path payload must have no missing_required violations; got: {missing_required}"
    )


def test_l3_step5_channel_schema_synthesis_does_not_silently_pass() -> None:
    """Adjacent guard — doc 64 §4 says ``channel.synthesis.proposed`` is the
    truth carrier for spec_path. The channel schema today does not enforce
    spec_path. We assert the well-formed (with spec_path in payload) event
    validates, and a payload missing the *currently required* fields fails.
    This prevents a regression where channel_event_schema_rules silently
    drops constraints around synthesis.
    """
    registry = EventSchemaRegistry.from_dict(channel_event_schema_rules())

    good = ZfEvent(
        type="channel.synthesis.proposed",
        payload={
            "channel_id": CHANNEL_ID,
            "thread_id": THREAD_ID,
            "decision": "promote",
            "summary": "approved",
            "source": "agent",
            "spec_path": ".zf/channels/zaofu/specs/X.md",
        },
    )
    assert registry.validate(good) == []

    bad = ZfEvent(
        type="channel.synthesis.proposed",
        payload={
            # missing channel_id, thread_id, decision, summary, source
            "spec_path": ".zf/channels/zaofu/specs/X.md",
        },
    )
    bad_violations = registry.validate(bad)
    missing_codes = {v.field_path for v in bad_violations if v.code == "missing_required"}
    # At minimum channel_id must be flagged — the synthesis is unaddressable
    # without it.
    assert "payload.channel_id" in missing_codes
