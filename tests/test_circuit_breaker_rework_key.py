"""P1-7 (2026-07-09): circuit breaker key alignment.

The breaker failure counter (apply_circuit_breaker_failure) must key on the
role that will be *re-dispatched* for rework (the producer), because the
dispatch-time check (_circuit_for) keys on the dispatched role. The old code
keyed on event.actor — the gate that emitted the failure (review) — so the
breaker for the producer (dev) never accumulated and never tripped on the
productive review→dev rework loop.
"""

from __future__ import annotations

from pathlib import Path

from zf.core.errors.circuit_breaker import CircuitBreaker
from zf.core.events.model import ZfEvent
from zf.runtime.housekeeping import apply_circuit_breaker_failure


def _breaker(store_path: Path, role: str, max_failures: int = 3) -> CircuitBreaker:
    return CircuitBreaker(
        key=(role, "T1"),
        max_failures=max_failures,
        window_seconds=1800.0,
        store_path=store_path,
    )


def test_failure_accumulates_against_rework_target_not_gate(tmp_path: Path) -> None:
    store_path = tmp_path / "circuits.json"
    # A review.rejected (actor=review) whose rework re-dispatches to dev.
    for _ in range(3):
        apply_circuit_breaker_failure(
            ZfEvent(type="review.rejected", actor="review", task_id="T1"),
            store_path,
            role_name="dev",
        )
    # The dispatch loop checks the producer (dev) — it must see the failures.
    assert _breaker(store_path, "dev").can_proceed() is False
    # The gate (review) accumulated nothing — the old key-on-actor bug would
    # have logged here and left the dev breaker empty (never tripping).
    assert _breaker(store_path, "review").can_proceed() is True


def test_instance_suffix_stripped_to_role_name(tmp_path: Path) -> None:
    store_path = tmp_path / "circuits.json"
    for _ in range(3):
        apply_circuit_breaker_failure(
            ZfEvent(type="test.failed", actor="test", task_id="T1"),
            store_path,
            role_name="dev-2",  # instance id
        )
    # Keyed on the role *name* (dev), matching _circuit_for(role.name, task).
    assert _breaker(store_path, "dev").can_proceed() is False


def test_fallback_to_actor_when_no_role_name(tmp_path: Path) -> None:
    store_path = tmp_path / "circuits.json"
    for _ in range(3):
        apply_circuit_breaker_failure(
            ZfEvent(type="test.failed", actor="dev", task_id="T1"),
            store_path,
        )
    # Back-compat: no role_name → keyed on actor.
    assert _breaker(store_path, "dev").can_proceed() is False


def test_non_rework_event_ignored(tmp_path: Path) -> None:
    store_path = tmp_path / "circuits.json"
    for _ in range(5):
        apply_circuit_breaker_failure(
            ZfEvent(type="worker.heartbeat", actor="dev", task_id="T1"),
            store_path,
            role_name="dev",
        )
    assert _breaker(store_path, "dev").can_proceed() is True
