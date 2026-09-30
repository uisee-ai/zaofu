from __future__ import annotations

import os
import signal
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime import run_manager


@pytest.fixture(autouse=True)
def _mutation_busy_loop_watchdog() -> Iterator[None]:
    """Turn non-progress mutants into deterministic failures, not runner timeouts."""
    if not os.environ.get("MUTANT_UNDER_TEST"):
        yield
        return

    def fail_on_stall(_signum: int, _frame: object) -> None:
        raise AssertionError("mutation caused a non-progress loop")

    previous = signal.signal(signal.SIGALRM, fail_on_stall)
    signal.setitimer(signal.ITIMER_REAL, 1.0)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _writer(tmp_path: Path) -> tuple[EventLog, EventWriter]:
    log = EventLog(tmp_path / "events.jsonl")
    return log, EventWriter(log)


def _pending(
    *,
    effect_id: str = "effect-1",
    expected: Any = ("candidate.ready",),
    failures: Any = ("integration.failed",),
    sequence: Any = None,
    correlation_id: str | None = "run-1",
    **payload: Any,
) -> ZfEvent:
    return ZfEvent(
        id=f"pending-{effect_id}",
        type=run_manager.RUN_MANAGER_ACTION_EFFECT_PENDING,
        correlation_id=correlation_id,
        payload={
            "schema_version": "run-manager.action.effect.v1",
            "action": "candidate-rework-apply",
            "checkpoint_id": "checkpoint-1",
            "effect_id": effect_id,
            "expected_event_types": expected,
            "failure_event_types": failures,
            "required_sequence": [] if sequence is None else sequence,
            "status": "pending",
            **payload,
        },
    )


def _event(
    event_type: str,
    *,
    event_id: str,
    correlation_id: str | None = "run-1",
    **payload: Any,
) -> ZfEvent:
    return ZfEvent(
        id=event_id,
        type=event_type,
        correlation_id=correlation_id,
        payload=payload,
    )


def _terminal_events(log: EventLog) -> list[ZfEvent]:
    return [
        event
        for event in log.read_all()
        if event.type
        in {
            run_manager.RUN_MANAGER_ACTION_EFFECT_PASSED,
            run_manager.RUN_MANAGER_ACTION_EFFECT_FAILED,
        }
    ]


def test_required_effect_sequence_reads_first_only_event_before_longer_cases() -> None:
    """Keep the cursor-progress mutation fail-fast before multi-event cases."""
    observed = _event("candidate.ready", event_id="first-only-event")

    assert run_manager._observe_required_effect_sequence(
        [observed],
        payload={},
        required_sequence=[["candidate.ready"]],
    ) is observed


def test_required_effect_sequence_cursor_progresses_across_noise() -> None:
    """Fail quickly if cursor advancement is mutated into a non-progress loop."""
    expected = _event("candidate.ready", event_id="expected-after-noise")
    events = [
        _event("worker.heartbeat", event_id="noise-zero"),
        _event("worker.heartbeat", event_id="noise-one"),
        expected,
    ]

    def fail_on_stall(_signum: int, _frame: object) -> None:
        raise AssertionError("effect sequence cursor did not make progress")

    previous = signal.signal(signal.SIGALRM, fail_on_stall)
    signal.setitimer(signal.ITIMER_REAL, 0.25)
    try:
        assert run_manager._observe_required_effect_sequence(
            events,
            payload={},
            required_sequence=[["candidate.ready"]],
        ) is expected
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def test_effect_settlement_emits_exact_pass_receipt(tmp_path: Path) -> None:
    log, writer = _writer(tmp_path)
    pending = _pending(workflow_run_id="run-1", fanout_id="fanout-1")
    observed = _event(
        "candidate.ready",
        event_id="candidate-ready-1",
        workflow_run_id="run-1",
        fanout_id="fanout-1",
    )

    assert run_manager._settle_pending_action_effects([pending, observed], writer) == 1

    [terminal] = _terminal_events(log)
    assert terminal.type == run_manager.RUN_MANAGER_ACTION_EFFECT_PASSED
    assert terminal.actor == "run-manager"
    assert terminal.causation_id == observed.id
    assert terminal.correlation_id == observed.correlation_id
    assert terminal.payload["schema_version"] == "run-manager.action.effect.v1"
    assert terminal.payload["action"] == "candidate-rework-apply"
    assert terminal.payload["checkpoint_id"] == "checkpoint-1"
    assert terminal.payload["fanout_id"] == "fanout-1"
    assert terminal.payload["effect_id"] == "effect-1"
    assert terminal.payload["effect_pending_event_id"] == pending.id
    assert terminal.payload["observed_event_id"] == observed.id
    assert terminal.payload["observed_event_type"] == "candidate.ready"
    assert terminal.payload["status"] == "passed"
    assert terminal.payload["reason"] == "candidate integration effect observed"


def test_effect_settlement_emits_exact_failure_receipt(tmp_path: Path) -> None:
    log, writer = _writer(tmp_path)
    pending = _pending(workflow_run_id="run-1")
    observed = _event(
        "integration.failed",
        event_id="integration-failed-1",
        workflow_run_id="run-1",
    )

    assert run_manager._settle_pending_action_effects([pending, observed], writer) == 1

    [terminal] = _terminal_events(log)
    assert terminal.type == run_manager.RUN_MANAGER_ACTION_EFFECT_FAILED
    assert terminal.causation_id == observed.id
    assert terminal.payload["status"] == "failed"
    assert terminal.payload["observed_event_type"] == "integration.failed"
    assert terminal.payload["reason"] == (
        "candidate integration recovery did not make progress"
    )


def test_effect_settlement_uses_pending_correlation_when_observed_has_none(
    tmp_path: Path,
) -> None:
    log, writer = _writer(tmp_path)
    pending = _pending(correlation_id="run-from-pending", fanout_id="fanout-1")
    observed = _event(
        "candidate.ready",
        event_id="candidate-ready-no-correlation",
        correlation_id=None,
        fanout_id="fanout-1",
    )

    assert run_manager._settle_pending_action_effects([pending, observed], writer) == 1

    [terminal] = _terminal_events(log)
    assert terminal.correlation_id == "run-from-pending"


@pytest.mark.parametrize(
    "events",
    [
        [_event("candidate.ready", event_id="before"), _pending()],
        [_pending(effect_id="")],
        [_event("candidate.ready", event_id="not-a-pending")],
        [_pending(expected=(), failures=()), _event("candidate.ready", event_id="ignored")],
    ],
)
def test_effect_settlement_ignores_non_settleable_input(
    tmp_path: Path,
    events: list[ZfEvent],
) -> None:
    log, writer = _writer(tmp_path)

    assert run_manager._settle_pending_action_effects(events, writer) == 0
    assert _terminal_events(log) == []


def test_non_event_before_pending_does_not_stop_later_settlement(tmp_path: Path) -> None:
    log, writer = _writer(tmp_path)
    pending = _pending()
    observed = _event("candidate.ready", event_id="ready-after-noise")

    assert run_manager._settle_pending_action_effects(
        [_event("worker.heartbeat", event_id="noise"), pending, observed],
        writer,
    ) == 1
    assert [event.payload["effect_id"] for event in _terminal_events(log)] == [
        "effect-1"
    ]


def test_empty_effect_id_does_not_stop_later_settlement(tmp_path: Path) -> None:
    log, writer = _writer(tmp_path)
    valid = _pending(effect_id="effect-valid")

    assert run_manager._settle_pending_action_effects(
        [
            _pending(effect_id=""),
            _event("candidate.ready", event_id="ignored-empty"),
            valid,
            _event("candidate.ready", event_id="ready-valid"),
        ],
        writer,
    ) == 1
    assert [event.payload["effect_id"] for event in _terminal_events(log)] == [
        "effect-valid"
    ]


def test_empty_terminal_id_does_not_suppress_literal_nonempty_effect(
    tmp_path: Path,
) -> None:
    log, writer = _writer(tmp_path)
    terminal_without_id = ZfEvent(
        type=run_manager.RUN_MANAGER_ACTION_EFFECT_PASSED,
        payload={},
    )
    pending = _pending(effect_id="XXXX")
    observed = _event("candidate.ready", event_id="ready-literal-id")

    assert run_manager._settle_pending_action_effects(
        [terminal_without_id, pending, observed], writer
    ) == 1
    assert [event.payload["effect_id"] for event in _terminal_events(log)] == [
        "XXXX"
    ]


@pytest.mark.parametrize(
    "terminal_type",
    [
        run_manager.RUN_MANAGER_ACTION_EFFECT_PASSED,
        run_manager.RUN_MANAGER_ACTION_EFFECT_FAILED,
    ],
)
def test_effect_settlement_is_idempotent_after_terminal_event(
    tmp_path: Path,
    terminal_type: str,
) -> None:
    log, writer = _writer(tmp_path)
    pending = _pending()
    existing = ZfEvent(type=terminal_type, payload={"effect_id": "effect-1"})
    observed = _event("candidate.ready", event_id="candidate-ready-late")

    assert run_manager._settle_pending_action_effects(
        [pending, existing, observed], writer
    ) == 0
    assert _terminal_events(log) == []


def test_effect_settlement_handles_independent_pending_effects(tmp_path: Path) -> None:
    log, writer = _writer(tmp_path)
    pending_one = _pending(effect_id="effect-1", fanout_id="fanout-1")
    pending_two = _pending(effect_id="effect-2", fanout_id="fanout-2")
    observed_two = _event(
        "integration.failed",
        event_id="failed-2",
        fanout_id="fanout-2",
    )
    observed_one = _event(
        "candidate.ready",
        event_id="ready-1",
        fanout_id="fanout-1",
    )

    assert run_manager._settle_pending_action_effects(
        [pending_one, pending_two, observed_two, observed_one], writer
    ) == 2

    terminals = _terminal_events(log)
    assert [(event.type, event.payload["effect_id"]) for event in terminals] == [
        (run_manager.RUN_MANAGER_ACTION_EFFECT_PASSED, "effect-1"),
        (run_manager.RUN_MANAGER_ACTION_EFFECT_FAILED, "effect-2"),
    ]


def test_unresolved_effect_does_not_stop_later_effect(tmp_path: Path) -> None:
    log, writer = _writer(tmp_path)
    unresolved = _pending(effect_id="effect-unresolved", fanout_id="fanout-unresolved")
    resolved = _pending(effect_id="effect-resolved", fanout_id="fanout-resolved")
    observed = _event(
        "candidate.ready",
        event_id="ready-resolved",
        fanout_id="fanout-resolved",
    )

    assert run_manager._settle_pending_action_effects(
        [unresolved, resolved, observed], writer
    ) == 1
    assert [event.payload["effect_id"] for event in _terminal_events(log)] == [
        "effect-resolved"
    ]


def test_duplicate_normal_pending_effect_is_settled_once(tmp_path: Path) -> None:
    log, writer = _writer(tmp_path)
    first = _pending(effect_id="effect-duplicate")
    second = _pending(effect_id="effect-duplicate")
    second.id = "pending-effect-duplicate-second"
    first_observed = _event("candidate.ready", event_id="ready-first")
    second_observed = _event("candidate.ready", event_id="ready-second")

    assert run_manager._settle_pending_action_effects(
        [first, first_observed, second, second_observed], writer
    ) == 1
    assert len(_terminal_events(log)) == 1


def test_required_effect_sequence_uses_last_ordered_matching_event(
    tmp_path: Path,
) -> None:
    log, writer = _writer(tmp_path)
    pending = _pending(
        expected=("run.goal.completed",),
        failures=("run.goal.blocked",),
        sequence=[
            ["flow.goal.closed"],
            ["judge.requested"],
            ["run.goal.completed", "run.goal.blocked"],
        ],
        workflow_run_id="run-1",
    )
    noise = _event("worker.heartbeat", event_id="noise", workflow_run_id="run-1")
    closed = _event("flow.goal.closed", event_id="closed", workflow_run_id="run-1")
    judge = _event("judge.requested", event_id="judge", workflow_run_id="run-1")
    completed = _event(
        "run.goal.completed", event_id="completed", workflow_run_id="run-1"
    )

    assert run_manager._settle_pending_action_effects(
        [pending, noise, closed, judge, completed], writer
    ) == 1

    [terminal] = _terminal_events(log)
    assert terminal.type == run_manager.RUN_MANAGER_ACTION_EFFECT_PASSED
    assert terminal.causation_id == completed.id
    assert terminal.payload["observed_event_id"] == completed.id


def test_required_sequence_may_start_immediately_after_pending(tmp_path: Path) -> None:
    log, writer = _writer(tmp_path)
    pending = _pending(
        expected=("run.goal.completed",),
        sequence=[["flow.goal.closed"], ["run.goal.completed"]],
        workflow_run_id="run-1",
    )
    closed = _event("flow.goal.closed", event_id="closed-first", workflow_run_id="run-1")
    completed = _event(
        "run.goal.completed", event_id="completed-second", workflow_run_id="run-1"
    )

    assert run_manager._settle_pending_action_effects(
        [pending, closed, completed], writer
    ) == 1
    assert _terminal_events(log)[0].payload["observed_event_id"] == completed.id


@pytest.mark.parametrize(
    "events",
    [
        [
            _event("judge.requested", event_id="judge"),
            _event("flow.goal.closed", event_id="closed"),
            _event("run.goal.completed", event_id="completed"),
        ],
        [
            _event("flow.goal.closed", event_id="closed"),
            _event("run.goal.completed", event_id="completed"),
        ],
        [
            _event(
                "flow.goal.closed",
                event_id="wrong-run",
                correlation_id="run-other",
                workflow_run_id="run-other",
            ),
            _event("judge.requested", event_id="judge", workflow_run_id="run-1"),
            _event("run.goal.completed", event_id="completed", workflow_run_id="run-1"),
        ],
    ],
)
def test_required_effect_sequence_rejects_wrong_order_missing_or_wrong_scope(
    tmp_path: Path,
    events: list[ZfEvent],
) -> None:
    log, writer = _writer(tmp_path)
    pending = _pending(
        expected=("run.goal.completed",),
        sequence=[
            ["flow.goal.closed"],
            ["judge.requested"],
            ["run.goal.completed"],
        ],
        workflow_run_id="run-1",
    )

    assert run_manager._settle_pending_action_effects([pending, *events], writer) == 0
    assert _terminal_events(log) == []


def test_required_effect_sequence_rejects_empty_stage() -> None:
    payload = {"workflow_run_id": "run-1"}
    events = [_event("flow.goal.closed", event_id="closed", workflow_run_id="run-1")]

    assert run_manager._observe_required_effect_sequence(
        events,
        payload=payload,
        required_sequence=[[]],
    ) is None


def test_required_effect_sequence_empty_contract_returns_none() -> None:
    assert run_manager._observe_required_effect_sequence(
        [],
        payload={},
        required_sequence=[],
    ) is None


def test_legacy_recovery_requires_action_and_known_failure_class(
    tmp_path: Path,
) -> None:
    log, writer = _writer(tmp_path)
    wrong_action = _pending(
        effect_id="wrong-action",
        action="candidate-rework-apply",
        failure_class="goal_closure_identity_invalid",
        expected=(),
        failures=(),
    )
    missing_class = _pending(
        effect_id="missing-class",
        action="fanout-aggregate-rebuild",
        failure_class="",
        expected=(),
        failures=(),
    )
    recovery = _event("flow.goal.closed", event_id="recovery")

    assert run_manager._settle_pending_action_effects(
        [wrong_action, recovery, missing_class, recovery], writer
    ) == 0
    assert _terminal_events(log) == []


def test_legacy_recovery_without_matching_event_stays_pending(tmp_path: Path) -> None:
    log, writer = _writer(tmp_path)
    pending = _pending(
        action="fanout-aggregate-rebuild",
        failure_class="goal_closure_identity_invalid",
        expected=(),
        failures=(),
        workflow_run_id="run-1",
    )

    assert run_manager._settle_pending_action_effects(
        [pending, _event("judge.requested", event_id="wrong-type")], writer
    ) == 0
    assert _terminal_events(log) == []


def test_legacy_recovery_before_pending_is_ignored(tmp_path: Path) -> None:
    log, writer = _writer(tmp_path)
    recovery = _event("flow.goal.closed", event_id="too-early", workflow_run_id="run-1")
    pending = _pending(
        action="fanout-aggregate-rebuild",
        failure_class="goal_closure_identity_invalid",
        expected=(),
        failures=(),
        workflow_run_id="run-1",
    )

    assert run_manager._settle_pending_action_effects([recovery, pending], writer) == 0
    assert _terminal_events(log) == []


def test_legacy_recovery_rejects_wrong_event_type_with_matching_scope(
    tmp_path: Path,
) -> None:
    log, writer = _writer(tmp_path)
    pending = _pending(
        action="fanout-aggregate-rebuild",
        failure_class="goal_closure_identity_invalid",
        expected=(),
        failures=(),
        workflow_run_id="run-1",
    )
    wrong = _event("judge.requested", event_id="wrong", workflow_run_id="run-1")

    assert run_manager._settle_pending_action_effects([pending, wrong], writer) == 0
    assert _terminal_events(log) == []


@pytest.mark.parametrize(
    ("failure_class", "event_type"),
    [
        ("goal_closure_identity_invalid", "flow.goal.closed"),
        ("goal_closure_aggregate_schema_failed", "goal.closure.synthesized"),
    ],
)
def test_legacy_goal_recovery_settles_on_exact_recovery_event(
    tmp_path: Path,
    failure_class: str,
    event_type: str,
) -> None:
    log, writer = _writer(tmp_path)
    pending = _pending(
        action="fanout-aggregate-rebuild",
        failure_class=failure_class,
        expected=("run.goal.completed",),
        sequence=[["not-yet-observed"]],
        workflow_run_id="run-1",
    )
    observed = _event(event_type, event_id="legacy-recovery", workflow_run_id="run-1")

    assert run_manager._settle_pending_action_effects([pending, observed], writer) == 1

    [terminal] = _terminal_events(log)
    assert terminal.type == run_manager.RUN_MANAGER_ACTION_EFFECT_PASSED
    assert terminal.actor == "run-manager"
    assert terminal.causation_id == observed.id
    assert terminal.correlation_id == "run-1"
    assert terminal.payload["schema_version"] == "run-manager.action.effect.v1"
    assert terminal.payload["action"] == "fanout-aggregate-rebuild"
    assert terminal.payload["failure_class"] == failure_class
    assert terminal.payload["effect_id"] == "effect-1"
    assert terminal.payload["effect_pending_event_id"] == pending.id
    assert terminal.payload["observed_event_id"] == observed.id
    assert terminal.payload["observed_event_type"] == event_type
    assert terminal.payload["status"] == "passed"
    assert terminal.payload["reason"] == "Goal closure aggregate recovery observed"


def test_legacy_recovery_uses_pending_correlation_and_settles_duplicates_once(
    tmp_path: Path,
) -> None:
    log, writer = _writer(tmp_path)
    first = _pending(
        effect_id="legacy-duplicate",
        correlation_id="run-from-pending",
        action="fanout-aggregate-rebuild",
        failure_class="goal_closure_identity_invalid",
        expected=(),
        failures=(),
    )
    second = _pending(
        effect_id="legacy-duplicate",
        correlation_id="run-from-pending",
        action="fanout-aggregate-rebuild",
        failure_class="goal_closure_identity_invalid",
        expected=(),
        failures=(),
    )
    second.id = "pending-legacy-duplicate-second"
    recovery_one = _event(
        "flow.goal.closed", event_id="recovery-one", correlation_id=None
    )
    recovery_two = _event(
        "flow.goal.closed", event_id="recovery-two", correlation_id=None
    )

    assert run_manager._settle_pending_action_effects(
        [first, recovery_one, second, recovery_two], writer
    ) == 1
    [terminal] = _terminal_events(log)
    assert terminal.correlation_id == "run-from-pending"
    assert terminal.payload["effect_id"] == "legacy-duplicate"


def test_multiple_legacy_effects_increment_settled_count(tmp_path: Path) -> None:
    log, writer = _writer(tmp_path)
    first = _pending(
        effect_id="legacy-one",
        action="fanout-aggregate-rebuild",
        failure_class="goal_closure_identity_invalid",
        expected=(),
        failures=(),
    )
    second = _pending(
        effect_id="legacy-two",
        action="fanout-aggregate-rebuild",
        failure_class="goal_closure_identity_invalid",
        expected=(),
        failures=(),
    )
    recovery_one = _event("flow.goal.closed", event_id="legacy-recovery-one")
    recovery_two = _event("flow.goal.closed", event_id="legacy-recovery-two")

    assert run_manager._settle_pending_action_effects(
        [first, recovery_one, second, recovery_two], writer
    ) == 2
    assert {event.payload["effect_id"] for event in _terminal_events(log)} == {
        "legacy-one",
        "legacy-two",
    }


def test_legacy_recovery_does_not_stop_later_normal_effect(tmp_path: Path) -> None:
    log, writer = _writer(tmp_path)
    legacy = _pending(
        effect_id="legacy",
        action="fanout-aggregate-rebuild",
        failure_class="goal_closure_identity_invalid",
        expected=(),
        failures=(),
    )
    normal = _pending(effect_id="normal")

    assert run_manager._settle_pending_action_effects(
        [
            legacy,
            _event("flow.goal.closed", event_id="legacy-recovery"),
            normal,
            _event("candidate.ready", event_id="normal-ready"),
        ],
        writer,
    ) == 2
    assert [event.payload["effect_id"] for event in _terminal_events(log)] == [
        "legacy",
        "normal",
    ]


@pytest.mark.parametrize(
    ("event", "payload", "expected"),
    [
        (
            _event(
                "candidate.ready",
                event_id="exact",
                workflow_run_id="run-1",
                run_id="run-1",
                fanout_id="fanout-1",
            ),
            {"workflow_run_id": "run-1", "run_id": "run-1", "fanout_id": "fanout-1"},
            True,
        ),
        (
            _event("candidate.ready", event_id="correlation-only"),
            {"workflow_run_id": "run-1"},
            True,
        ),
        (
            _event("candidate.ready", event_id="wrong-workflow", workflow_run_id="run-2"),
            {"workflow_run_id": "run-1"},
            False,
        ),
        (
            _event("candidate.ready", event_id="wrong-run", run_id="run-2"),
            {"run_id": "run-1"},
            False,
        ),
        (
            _event("candidate.ready", event_id="wrong-fanout", fanout_id="fanout-2"),
            {"fanout_id": "fanout-1"},
            False,
        ),
        (
            _event("candidate.ready", event_id="unscoped", correlation_id=None),
            {},
            True,
        ),
    ],
)
def test_effect_scope_matches_exact_identity(
    event: ZfEvent,
    payload: dict[str, Any],
    expected: bool,
) -> None:
    assert run_manager._effect_event_matches_scope(event, payload) is expected


def test_effect_scope_rejects_conflicting_run_when_workflow_matches() -> None:
    event = _event(
        "candidate.ready",
        event_id="conflicting-run",
        workflow_run_id="workflow-1",
        run_id="run-other",
    )

    assert run_manager._effect_event_matches_scope(
        event,
        {"workflow_run_id": "workflow-1", "run_id": "run-1"},
    ) is False


def test_unscoped_effect_accepts_scoped_event() -> None:
    event = _event(
        "candidate.ready",
        event_id="scoped-candidate",
        correlation_id="correlation-1",
        workflow_run_id="workflow-1",
        run_id="run-1",
        fanout_id="fanout-1",
    )

    assert run_manager._effect_event_matches_scope(event, {}) is True


@pytest.mark.parametrize(
    "payload",
    [
        {"workflow_run_id": "workflow-1"},
        {"run_id": "run-1"},
    ],
)
def test_effect_scope_rejects_wrong_correlation_when_body_has_no_run_identity(
    payload: dict[str, Any],
) -> None:
    event = _event(
        "candidate.ready",
        event_id="wrong-correlation",
        correlation_id="run-other",
    )

    assert run_manager._effect_event_matches_scope(event, payload) is False


def test_effect_scope_prefers_matching_workflow_body_over_wrong_correlation() -> None:
    event = _event(
        "candidate.ready",
        event_id="workflow-body",
        correlation_id="run-other",
        workflow_run_id="workflow-1",
    )

    assert run_manager._effect_event_matches_scope(
        event,
        {"workflow_run_id": "workflow-1"},
    ) is True


def test_effect_scope_prefers_matching_run_body_over_wrong_correlation() -> None:
    event = _event(
        "candidate.ready",
        event_id="run-body",
        correlation_id="run-other",
        run_id="run-1",
    )

    assert run_manager._effect_event_matches_scope(
        event,
        {"run_id": "run-1"},
    ) is True


def test_effect_scope_does_not_invent_missing_actual_run_identity() -> None:
    event = _event(
        "candidate.ready",
        event_id="missing-run-identity",
        correlation_id=None,
    )

    assert run_manager._effect_event_matches_scope(
        event,
        {"workflow_run_id": "XXXX"},
    ) is False


def test_candidate_retrigger_scope_allows_new_fanout_but_not_new_run() -> None:
    payload = {
        "action": "candidate-rework-apply",
        "candidate_rework_action": "retrigger",
        "workflow_run_id": "run-1",
        "fanout_id": "source-fanout",
    }
    admitted = _event(
        "fanout.started",
        event_id="new-fanout",
        workflow_run_id="run-1",
        fanout_id="new-fanout",
    )
    other_run = _event(
        "fanout.started",
        event_id="other-run",
        correlation_id="run-2",
        workflow_run_id="run-2",
        fanout_id="new-fanout",
    )

    assert run_manager._effect_event_matches_scope(admitted, payload) is True
    assert run_manager._effect_event_matches_scope(other_run, payload) is False


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ZF-TEST-052: a fanout-scoped effect accepts an event with no fanout identity"
    ),
)
def test_effect_scope_requires_present_fanout_identity() -> None:
    event = _event(
        "candidate.ready",
        event_id="missing-fanout",
        correlation_id=None,
    )

    assert run_manager._effect_event_matches_scope(
        event,
        {"fanout_id": "fanout-required"},
    ) is False
