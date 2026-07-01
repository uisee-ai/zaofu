"""R22 no-livelock: an escalated pdd's impl fanout must not auto-resume.

After the candidate-rework cap escalates (human.escalate), a spurious
re-emitted task_map.ready re-armed the writer fanout and restarted the loop
(cj-min R22: 4× task_map.ready / 3× integration.failed, never converging). The
quarantine predicate makes escalation terminal until an operator clears it.
"""
from __future__ import annotations

from types import SimpleNamespace

from zf.runtime.rework_quarantine import is_pdd_rework_quarantined


def ev(etype, **payload):
    return SimpleNamespace(type=etype, payload=payload)


PDD = "CJMIN-PI-NODE-001"


def test_not_quarantined_before_escalation():
    events = [
        ev("task_map.ready", pdd_id=PDD),
        ev("integration.failed", pdd_id=PDD),
        ev("task_map.ready", pdd_id=PDD, rework_of="evt-1", rework_attempt=1),
    ]
    assert is_pdd_rework_quarantined(events, PDD) is False


def test_quarantined_after_candidate_rework_escalate():
    events = [
        ev("task_map.ready", pdd_id=PDD),
        ev("human.escalate", pdd_id=PDD, rework_source="integration.failed"),
        # the spurious fresh re-emit that R22 looped on:
        ev("task_map.ready", pdd_id=PDD),
    ]
    assert is_pdd_rework_quarantined(events, PDD) is True


def test_operator_authorized_task_map_lifts_quarantine():
    events = [
        ev("human.escalate", pdd_id=PDD, rework_source="integration.failed"),
        ev("task_map.ready", pdd_id=PDD, operator_authorized=True),
    ]
    assert is_pdd_rework_quarantined(events, PDD) is False


def test_explicit_cleared_event_lifts_quarantine():
    events = [
        ev("human.escalate", pdd_id=PDD, rework_source="review.rejected"),
        ev("candidate.rework.cleared", pdd_id=PDD),
    ]
    assert is_pdd_rework_quarantined(events, PDD) is False


def test_re_escalation_after_clear_re_quarantines():
    events = [
        ev("human.escalate", pdd_id=PDD, rework_source="integration.failed"),
        ev("candidate.rework.cleared", pdd_id=PDD),
        ev("human.escalate", pdd_id=PDD, rework_source="integration.failed"),
    ]
    assert is_pdd_rework_quarantined(events, PDD) is True


def test_unrelated_escalate_source_does_not_quarantine():
    # a non-candidate-level escalate (e.g. infra) must not quarantine the pdd
    events = [ev("human.escalate", pdd_id=PDD, rework_source="worker.stuck")]
    assert is_pdd_rework_quarantined(events, PDD) is False


def test_scoped_to_pdd():
    events = [ev("human.escalate", pdd_id="OTHER", rework_source="integration.failed")]
    assert is_pdd_rework_quarantined(events, PDD) is False
    assert is_pdd_rework_quarantined(events, "") is False
