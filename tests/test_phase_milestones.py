"""Tests for phase milestone computation (doc 69 S-f, pure function)."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.phase_milestones import (
    compute_phase_milestones, emitted_milestone_keys,
)


def _phase(pid, *, status, completion, pass_rate=None, verdict="pending"):
    return {"phase_id": pid, "status": status, "completion_rate": completion,
            "pass_rate": pass_rate, "eval": {"verdict": verdict}}


def test_started_when_progressed():
    phases = [_phase("impl", status="in_progress", completion=0.5),
              _phase("acceptance", status="waiting", completion=0.0)]
    ms = compute_phase_milestones(feature_id="F-1", phases=phases, emitted_keys=set())
    types = {(t, p["phase_id"]) for t, p in ms}
    assert ("delivery.phase.started", "impl") in types
    assert ("delivery.phase.started", "acceptance") not in types  # still waiting


def test_evaluated_and_completed_when_done_not_failed():
    phases = [_phase("impl", status="done", completion=1.0, pass_rate=1.0, verdict="pass")]
    ms = compute_phase_milestones(feature_id="F-1", phases=phases, emitted_keys=set())
    types = {t for t, _ in ms}
    assert "delivery.phase.started" in types
    assert "delivery.phase.evaluated" in types
    assert "delivery.phase.completed" in types
    ev = next(p for t, p in ms if t == "delivery.phase.evaluated")
    assert ev["completion_rate"] == 1.0 and ev["pass_rate"] == 1.0


def test_evaluated_but_not_completed_when_failed():
    phases = [_phase("impl", status="done", completion=1.0, pass_rate=0.0, verdict="fail")]
    ms = compute_phase_milestones(feature_id="F-1", phases=phases, emitted_keys=set())
    types = {t for t, _ in ms}
    assert "delivery.phase.evaluated" in types
    assert "delivery.phase.completed" not in types  # failed → not completed


def test_idempotent_via_emitted_keys():
    phases = [_phase("impl", status="done", completion=1.0, pass_rate=1.0, verdict="pass")]
    emitted = {("delivery.phase.started", "F-1", "impl"),
               ("delivery.phase.evaluated", "F-1", "impl"),
               ("delivery.phase.completed", "F-1", "impl")}
    ms = compute_phase_milestones(feature_id="F-1", phases=phases, emitted_keys=emitted)
    assert ms == []  # all already emitted


def test_emitted_milestone_keys_from_events():
    events = [
        (1, ZfEvent(type="delivery.phase.started", id="m1",
                    payload={"feature_id": "F-1", "phase_id": "impl"})),
        (2, ZfEvent(type="dev.build.done", id="x", task_id="T1")),
    ]
    keys = emitted_milestone_keys(events)
    assert ("delivery.phase.started", "F-1", "impl") in keys
    assert len(keys) == 1
