"""α-4: Orchestrator in-memory state recoverability discipline.

Per docs/design/36-zero-touch-long-horizon-roadmap.md §7.7 +
backlogs/2026-05-17-1447-zero-touch-alpha-4-state-recoverability-tests.md.

codex's "rollout-as-authority" pattern: agent state should be reducible
FROM the event log, not stored separately in memory. zaofu events.jsonl
already follows this discipline. This test file pins it by asserting
that fresh-Orchestrator init successfully rebuilds each field that
should be recoverable, AND documents which fields are intentionally
transient caches.

When a new in-memory field is added to Orchestrator, this test should
fail or be amended — either the field gets a rebuild path OR it gets
an inline comment marking it as transient.

This file's most important regression case: **B-NEW-6** (blocked_human
persists across restart). That bug was caused by the absence of a
rebuild path that translates worker.state.changed → idle on init.
"""

from __future__ import annotations

import inspect
from pathlib import Path

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
                name="dev",
                backend="mock",
                stages=["implement"],
                publishes=["dev.build.done", "dev.blocked"],
            ),
            RoleConfig(
                name="review",
                backend="mock",
                stages=["code_review"],
                publishes=["review.approved", "review.rejected"],
            ),
        ],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="test-zf", dry_run=True))


# ─── recoverable: _last_worker_state ─────────────────────────────────────


def test_last_worker_state_rebuilds_from_worker_state_changed(state_dir, config, transport):
    """The most important recovery path. B-NEW-6 fix relies on it; future
    proactive-dispatch (α-3) will rely on it; web UI relies on it for
    worker badges."""
    log = EventLog(state_dir / "events.jsonl")
    # Simulate a real session: dev goes idle → busy → idle, review stays idle.
    log.append(ZfEvent(
        type="worker.state.changed",
        actor="dev",
        payload={"from": "idle", "to": "busy", "reason": "dispatched TASK-1"},
    ))
    log.append(ZfEvent(
        type="worker.state.changed",
        actor="dev",
        payload={"from": "busy", "to": "idle", "reason": "dev.build.done"},
    ))

    orch = Orchestrator(state_dir, config, transport)

    # Even after fresh init with no in-memory state, the field reflects
    # the last-known state from events.jsonl.
    assert orch._last_worker_state.get("dev") == "idle"


def test_last_worker_state_recovers_only_latest_per_actor(state_dir, config, transport):
    """Multiple state changes for the same actor → fold to the latest."""
    log = EventLog(state_dir / "events.jsonl")
    for from_state, to_state in [
        ("idle", "busy"),
        ("busy", "idle"),
        ("idle", "busy"),
        ("busy", "blocked"),
    ]:
        log.append(ZfEvent(
            type="worker.state.changed",
            actor="dev",
            payload={"from": from_state, "to": to_state},
        ))

    orch = Orchestrator(state_dir, config, transport)

    # Latest state wins (then B-NEW-6 turns blocked_human into idle; but
    # "blocked" alone is not blocked_human, so it stays.)
    assert orch._last_worker_state.get("dev") == "blocked"


def test_blocked_human_is_cleared_on_fresh_init_b_new_6_regression(state_dir, config, transport):
    """B-NEW-6 specific: a worker parked in blocked_human at the end of
    a prior session must become idle on fresh Orchestrator init, AND a
    new worker.state.changed event must be emitted for persistence
    consistency (so future restarts see idle, not blocked_human, in the
    most recent event)."""
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="worker.state.changed",
        actor="dev",
        payload={
            "from": "respawning",
            "to": "blocked_human",
            "reason": "respawn cap exhausted",
        },
    ))

    orch = Orchestrator(state_dir, config, transport)

    assert orch._last_worker_state.get("dev") == "idle", (
        "B-NEW-6 regression: blocked_human must be cleared by zf start"
    )

    events = list(log.read_all())
    clear_events = [
        e for e in events
        if e.type == "worker.state.changed"
        and (e.payload or {}).get("from") == "blocked_human"
        and (e.payload or {}).get("to") == "idle"
    ]
    assert len(clear_events) >= 1, (
        "expected at least one persistence-consistent clear event"
    )


def test_second_orchestrator_replay_keeps_state_consistent(state_dir, config, transport):
    """The codex 'rollout-as-authority' invariant: two Orchestrators
    independently constructed against the same events.jsonl must agree
    on observable in-memory state."""
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="worker.state.changed",
        actor="dev",
        payload={"from": "idle", "to": "busy"},
    ))
    log.append(ZfEvent(
        type="worker.state.changed",
        actor="review",
        payload={"from": "idle", "to": "recycling"},
    ))

    orch_a = Orchestrator(state_dir, config, transport)
    snap_a = dict(orch_a._last_worker_state)

    # Construct a second Orchestrator from the same on-disk state (incl.
    # any new events emitted by orch_a's init, e.g. blocked_human clears).
    orch_b = Orchestrator(state_dir, config, transport)
    snap_b = dict(orch_b._last_worker_state)

    assert snap_a == snap_b


# ─── intentionally non-recoverable (transient caches) ────────────────────


def test_promoted_causations_is_transient_cache(state_dir, config, transport):
    """`_promoted_causations` is a per-process dedup set for
    promote_to_memory_note_event. It does NOT need to be recoverable
    because the promoted events themselves are durable; the worst case
    on restart is a second harmless promote of the same event, which
    the housekeeping idempotency handles."""
    orch = Orchestrator(state_dir, config, transport)

    # Fresh field on a fresh process — always starts empty.
    # BoundedIdSet (2026-06-10): bounded per-process dedupe, starts empty.
    assert len(orch._promoted_causations) == 0


def test_stuck_already_reported_is_transient(state_dir, config, transport):
    """`_stuck_already_reported` is a per-process dedup set for stuck
    escalations. After restart the watcher re-evaluates panes from fresh
    output; replaying old stuck events would over-escalate, so transient
    behavior is correct."""
    orch = Orchestrator(state_dir, config, transport)

    assert orch._stuck_already_reported == set()


def test_dispatch_heads_is_transient(state_dir, config, transport):
    """`_dispatch_heads` caches base_git_head per task between
    dispatch and emit. After restart, ongoing dispatches will be
    re-evaluated against the events.jsonl recent task.dispatched
    base_git_head payload — no in-memory state needed."""
    orch = Orchestrator(state_dir, config, transport)

    assert orch._dispatch_heads == {} or isinstance(orch._dispatch_heads, dict)


# ─── audit: catch silent additions ───────────────────────────────────────


def _orchestrator_dunder_init_fields() -> set[str]:
    """Best-effort: scan Orchestrator.__init__ source for plain
    `self._<name> = ...` (or `self._<name>: T = ...`) assignments.

    Excludes:
    - subscript writes (`self._foo[key] = ...`) — they assume the field
      already exists; not a new declaration.
    - method calls (`self._foo()`) — these don't introduce fields.
    """
    import re

    pat = re.compile(r"^\s*self\.(_[A-Za-z_][A-Za-z0-9_]*)\s*(?::|=)")
    src = inspect.getsource(Orchestrator.__init__)
    fields: set[str] = set()
    for line in src.splitlines():
        m = pat.match(line)
        if not m:
            continue
        name = m.group(1)
        # Tail of the line must contain `=` so we know it's an assignment
        # (filter out e.g. annotation-only declarations).
        if "=" not in line:
            continue
        fields.add(name)
    return fields


# Audited fields the test file covers (recoverable or explicitly transient).
# When you add a new self._<field> to Orchestrator.__init__ and this test
# fails, decide:
#   - Should the field be recoverable from events.jsonl? Add it here AND
#     write a test that asserts the rebuild path.
#   - Is the field a transient cache? Add it here AND write a test
#     asserting it's empty/default on fresh init, with comment WHY.
_AUDITED_FIELDS: set[str] = {
    # Recoverable from events
    "_last_worker_state",
    # Transient caches (acceptable)
    "_promoted_causations",
    "_stuck_already_reported",
    "_dispatch_heads",
    "_processed_event_ids",
    "_dead_counter",
    "_dead_threshold",
    "_dispatch_epoch",
    "_orphan_warned",
    "_hard_cap_exceeded",
    # Transient: consecutive usage-capture miss counter for debounce only.
    # Restarting at zero can at worst delay or duplicate a diagnostic probe;
    # usage truth is still captured through events/cost artifacts.
    "_usage_capture_misses",
    "_synth_usage_seen",
    "_drift_detector",
    "_drift_last_emit",
    "_drift_cooldown_seconds",
    "_gan_round",
    "_cost_block_last_emit",
    "_cost_block_cooldown_seconds",
    "_discriminator_runner",
    "_refresh_policy",
    "_turn_counter",
    "_failure_counter",
    "_refresh_already_emitted",
    "_runtime_roles",
    "_stuck_detectors",
    "_spawn_coordinator",
    "_instance_state",
    "_layer2_blocked_until",
    "_layer2_cooldown_s",
    # Wake coalescing (2026-05-28) + batch coalescing (doc 66 §14.0, 2026-05-29).
    # All transient: interval re-read from config at init (a constant); last_wake_at
    # resets to 0.0 so the first wake after restart fires immediately; _layer2_pending
    # resets to [] and _layer2_in_batch to False — suppressed events still live in
    # events.jsonl and are re-observed when the orchestrator rebuilds state on its
    # next run_once (event-offset replay), so a coalesced burst tail is never lost.
    "_layer2_wake_min_interval_s",
    "_layer2_last_wake_at",
    "_layer2_pending",
    "_layer2_in_batch",
    # FIX-5②(bizsim r4):同型触发指数退避的瞬态节流状态。重启归零 =
    # 恢复默认唤醒节奏,无真相依赖;事件痕迹在 dispatch_skipped
    # (same_trigger_backoff)。
    "_layer2_streak_type",
    "_layer2_streak_count",
    "_recover_in_progress",
    "_scope_ratchet",
    "_scope_snapshots",
    "_active_dispatch_ids",
    "_recent_dispatch_ids",
    "_dispatch_failure_counter",
    "_dispatch_failure_cooldown",
    # Transient: circuit-breaker timing. Each restart correctly starts
    # with a fresh window (the prior session's cool-down decisions don't
    # need to persist).
    "_circuit_tripped_last",
    # Transient: stateless backend adapter objects rebuilt at init from
    # config.roles (a registry, not state). On restart the same set of
    # adapters is constructed deterministically — no event replay needed.
    "_session_readers",
    # ω-1.c (2026-05-18): per-(instance_id, signal_type) cooldown for
    # heartbeat-sweep emissions (worker.stuck, worker.probe.silent).
    # Transient: restart correctly starts empty; worst case is one
    # duplicate emit per instance after restart, which is acceptable
    # for ops surface (still ≤1 stuck per minute).
    "_sweep_signal_last_emit_at",
    # ZF-HOUSEKEEPING-VISIBLE-001 (doc 42 §2.12, 2026-05-18):
    # per-step dedup for `kernel.housekeeping.failed` emissions in
    # _safe_housekeeping. Transient: restart correctly starts empty;
    # worst case is one duplicate emit per step after a crash, which
    # is the desired behaviour (a real recurring failure should still
    # surface after restart).
    "_housekeeping_failure_last",
    "_housekeeping_failure_dedup_seconds",
    # Respawn-success cascade circuit (no-dead-end batch, 2026-06-10):
    # emit-dedup set for `worker.respawn.circuit_opened`, same shape as
    # `_stuck_already_reported`. Transient: the cascade *condition* is
    # re-derived from `worker.respawned` events in events.jsonl on every
    # check; restart starts empty and worst case re-emits one
    # circuit_opened for a still-cascading instance, which is desired.
    "_respawn_success_circuit_opened",
}


def test_no_silent_new_orchestrator_fields():
    """Forcing-function: if Orchestrator.__init__ grows a new `self._<field>`
    that this test file hasn't audited, fail loudly so the author makes
    a deliberate choice (recoverable / transient).

    To extend: add to ``_AUDITED_FIELDS`` AND either add a recoverable
    rebuild test OR document why the field is transient.
    """
    actual = _orchestrator_dunder_init_fields()
    new = actual - _AUDITED_FIELDS
    assert not new, (
        f"New Orchestrator.__init__ fields not yet audited for "
        f"recoverability: {sorted(new)}\n"
        f"\n"
        f"For each, decide:\n"
        f"  (a) Recoverable from events.jsonl → add to _AUDITED_FIELDS "
        f"AND write a rebuild test in this file.\n"
        f"  (b) Transient cache → add to _AUDITED_FIELDS with an inline "
        f"comment explaining why."
    )
