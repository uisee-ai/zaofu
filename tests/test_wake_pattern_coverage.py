"""Guard against B-NEW-10 regression: every event type that
``_apply_housekeeping`` handles must appear in ``WAKE_PATTERNS``,
otherwise the watcher silently drops the event and the housekeeping
branch never fires.

History: cangjie r-next-5/6/7 each emitted ~5 candidate.integration.completed
events but produced 0 ship.* events because the wake pattern was missing.
The B-NEW-3 fix (loader + _apply_housekeeping + ship.failed defense)
was correct in code but unreachable in practice.

This test extracts ``elif event.type == "..."`` literals from
orchestrator.py via AST and asserts each is in WAKE_PATTERNS.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


# P3 moved the fanout coordination methods (and their event.type
# comparisons) to orchestrator_fanout.py — scan both files so the
# coverage gate does not silently shrink when methods move between
# the two. (Extending to the rest of the orchestrator* family exposes
# 5 pre-existing uncovered comparisons — tracked as a follow-up, not
# widened here.)
_ORCH_PATHS = [
    Path(__file__).parent.parent / "src" / "zf" / "runtime" / name
    for name in ("orchestrator.py", "orchestrator_fanout.py")
]


def _housekeeping_event_types() -> set[str]:
    """Walk the orchestrator family AST and collect every string literal
    compared against ``event.type`` in an if/elif chain within
    ``_apply_housekeeping`` or any function it calls.

    Heuristic: any ``Compare`` of shape ``event.type == "literal"`` or
    ``"literal" == event.type``. Catches both _apply_housekeeping
    direct branches and event-typed dispatches in nearby helpers.
    """
    tree = ast.parse(
        "\n".join(p.read_text() for p in _ORCH_PATHS)
    )
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not (
            isinstance(node.left, ast.Attribute)
            and isinstance(node.left.value, ast.Name)
            and node.left.value.id == "event"
            and node.left.attr == "type"
        ):
            continue
        for op, right in zip(node.ops, node.comparators):
            if not isinstance(op, ast.Eq):
                continue
            if isinstance(right, ast.Constant) and isinstance(right.value, str):
                found.add(right.value)
    return found


# Events emitted from WITHIN run_once (i.e. as side effects of handling
# another event) and consumed in the same wake cycle. These do NOT need
# wake_patterns because the watcher is already inside run_once when they
# fire. Adding them would create wake-loops.
#
# Each entry must cite the source (emit site) so reviewers can verify
# the "internal-only" claim. If a new external service emits the event,
# remove it from this allowlist.
_INTERNAL_LOOP_EVENTS: dict[str, str] = {
    # Emitted by feature_completion.py:69 / feature_liveness.py:68 as
    # side effects of judge.passed / feature liveness transitions within
    # the same run_once cycle.
    "feature.status_changed": "emit: feature_completion.py:69 (inside run_once)",
    # Emitted by orchestrator.py:2054 (_dispatch_fanout_children) and
    # orchestrator.py:2088 (_apply_fanout_synth) inside run_once.
    "fanout.child.dispatched": "emit: orchestrator.py:2054 (inside run_once)",
    "fanout.synth.completed": "emit: orchestrator.py:2088 (inside run_once)",
    # Read (not handled) by _check_fanout_timeouts as the fallback baseline for a
    # child with no dispatch event; emitted by the orchestrator inside run_once.
    "fanout.started": "emit: orchestrator.py _maybe_start_*_fanout (inside run_once)",
    # 2026-06-10 review P1-4: read by Orchestrator.__init__ (restart-time
    # symmetric resume of a remediation safe-halt pause) — init runs before
    # the watcher exists, so no wake pattern applies. Emitters:
    # orchestrator_lifecycle.py _execute_safe_halt (inside run_once) and
    # Orchestrator.__init__ / maintenance.py exit_maintenance.
    "dispatch.paused": "read: orchestrator.py __init__ (before watcher)",
    "dispatch.resumed": "read: orchestrator.py __init__ (before watcher)",
    # 9e00e89 (R23 synth 6h hang): read by _check_fanout_synth_timeout as
    # the synth-timeout baseline; only emitter is the orchestrator itself
    # inside run_once — same shape as fanout.started above.
    "fanout.synth.dispatched": (
        "emit: orchestrator.py _apply_fanout_synth (inside run_once); "
        "read: _check_fanout_synth_timeout baseline"
    ),
}


def _reactor_handler_event_types() -> set[str]:
    """Collect every event_type registered in
    orchestrator_reactor._BUILTIN_HANDLER_METHODS — the reactor's
    primary dispatch table.
    """
    from zf.runtime.orchestrator_reactor import _BUILTIN_HANDLER_METHODS
    return {event_type for event_type, _ in _BUILTIN_HANDLER_METHODS}


@pytest.mark.parametrize("event_type", sorted(_reactor_handler_event_types()))
def test_reactor_handler_has_wake_pattern(event_type: str) -> None:
    """Same B-NEW-10 invariant for reactor handlers in
    orchestrator_reactor._BUILTIN_HANDLER_METHODS. The registry
    registers handlers fine, but if WAKE_PATTERNS lacks the entry the
    EventWatcher drops the event before run_once is called, so the
    handler is unreachable in the live loop.

    History: 2026-06-01 — channel.message.posted and
    channel.agent.reply.requested reactor handlers (commits f421bd3,
    7ef7520) passed unit tests but the L4 live runner timed out at
    603s because WAKE_PATTERNS did not list them.
    """
    from zf.runtime.wake_patterns import BATCH_PROCESSED_EVENTS, WAKE_PATTERNS
    if event_type in _INTERNAL_LOOP_EVENTS:
        pytest.skip(
            f"internal-loop event: {_INTERNAL_LOOP_EVENTS[event_type]}"
        )
    assert (
        event_type in WAKE_PATTERNS
        or event_type in BATCH_PROCESSED_EVENTS
    ), (
        f"\n\nReactor handler registered for '{event_type}' in "
        f"_BUILTIN_HANDLER_METHODS but the event type is NOT in "
        f"wake_patterns.WAKE_PATTERNS.\n"
        f"\n"
        f"Result: the handler will never fire in the live orchestrator "
        f"loop. EventWatcher drops the event before run_once is invoked.\n"
        f"\n"
        f"Fix: add '{event_type}' to WAKE_PATTERNS in "
        f"src/zf/runtime/wake_patterns.py."
    )


@pytest.mark.parametrize("event_type", sorted(_housekeeping_event_types()))
def test_housekeeping_branch_has_wake_pattern(event_type: str) -> None:
    """B-NEW-10: ``WAKE_PATTERNS`` must include every event type handled
    by ``_apply_housekeeping`` IF that event can be emitted externally
    (i.e. not as a side effect of run_once itself). Otherwise the
    watcher drops the event and the branch never fires.

    Internal-only events (emitted inside run_once and consumed in the
    same cycle) are allowlisted in ``_INTERNAL_LOOP_EVENTS`` with
    citation of emit site.
    """
    from zf.runtime.wake_patterns import (
        BATCH_PROCESSED_EVENTS,
        WAKE_PATTERNS,
    )
    if event_type in _INTERNAL_LOOP_EVENTS:
        pytest.skip(
            f"internal-loop event: {_INTERNAL_LOOP_EVENTS[event_type]}"
        )
    assert (
        event_type in WAKE_PATTERNS
        or event_type in BATCH_PROCESSED_EVENTS
    ), (
        f"\n\nevent.type=='{event_type}' is referenced in "
        f"orchestrator.py but NOT in wake_patterns.WAKE_PATTERNS.\n"
        f"\n"
        f"This is the B-NEW-10 pattern: the watcher silently drops "
        f"events not in WAKE_PATTERNS, so any _apply_housekeeping "
        f"branch keyed on '{event_type}' is unreachable in the live "
        f"event loop (only reachable via tick polling).\n"
        f"\n"
        f"Fix: either (a) add '{event_type}' to WAKE_PATTERNS in "
        f"src/zf/runtime/wake_patterns.py if external services emit "
        f"this event, or (b) add to _INTERNAL_LOOP_EVENTS in this test "
        f"with a citation of the emit site if it's a same-cycle "
        f"side-effect event."
    )


def test_fanout_stage_child_result_events_wake_run_once() -> None:
    """Reader-fanout child result events must be wake patterns.

    Regression: review/verify/judge children finished but the stage never
    advanced because compute_effective_wake_patterns added the stage
    trigger/success/failure events but NOT child_success/child_failure —
    so EventWatcher dropped review.child.completed and the manifest never
    updated (silent aggregate stall).
    """
    from types import SimpleNamespace

    from zf.runtime.wake_patterns import compute_effective_wake_patterns

    stage = SimpleNamespace(
        trigger="candidate.ready",
        aggregate=SimpleNamespace(
            success_event="review.approved",
            failure_event="review.rejected",
            child_success_event="review.child.completed",
            child_failure_event="review.child.failed",
            synth_role="review-synth",
        ),
    )
    config = SimpleNamespace(
        workflow=SimpleNamespace(stages=[stage], wake_extensions=None)
    )

    wp = compute_effective_wake_patterns(config)

    assert "review.child.completed" in wp
    assert "review.child.failed" in wp
    assert "review.approved" in wp  # success_event still present
    assert "candidate.ready" in wp  # trigger still present


def test_layer2_noise_events_never_wake_agent_turn():
    # B6a (R25 ISSUE-006): hook/telemetry 不该驱动 Layer 2 推理。
    from zf.runtime.wake_patterns import LAYER2_NOISE_EVENTS, WAKE_PATTERNS

    for hook in (
        "codex.hook.pre_tool_use", "codex.hook.post_tool_use",
        "agent.usage", "hook.orphan_event",
    ):
        assert hook in LAYER2_NOISE_EVENTS
    # 噪音事件仍可在 WAKE_PATTERNS(Layer 1 handler 需要),两集不冲突
    assert "codex.hook.pre_tool_use" in WAKE_PATTERNS
    # 决策类事件绝不在噪音集
    for decision_event in (
        "task_map.ready", "candidate.ready", "review.rejected",
        "plan.approval.requested", "human.escalate",
    ):
        assert decision_event not in LAYER2_NOISE_EVENTS
