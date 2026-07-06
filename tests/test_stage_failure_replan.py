"""reader stage 失败机械 replan(prod-e2e:prd/issue 两流死端根治)。"""

from __future__ import annotations

from types import SimpleNamespace

from zf.core.events.model import ZfEvent
from zf.runtime.stage_failure_replan import (
    STAGE_REPLAN_CAP,
    plan_reader_stage_replan,
)


def _config():
    stage = SimpleNamespace(
        id="issue-triage",
        topology="fanout_reader",
        trigger="issue.requested",
        failure_event="",
        aggregate=SimpleNamespace(failure_event="issue.triage.failed"),
    )
    return SimpleNamespace(workflow=SimpleNamespace(stages=[stage]))


def _failure(reason="task_map rejected", findings=None):
    return ZfEvent(type="issue.triage.failed", payload={
        "reason": reason,
        "trigger_event_id": "evt-origin",
        **({"findings": findings} if findings else {}),
    })


def test_replan_re_emits_trigger_with_feedback() -> None:
    origin = ZfEvent(type="issue.requested", payload={"issue_ref": "docs/issues/TODO.md"})
    failure = _failure(findings=[{"severity": "high", "message": "root paths unowned"}])
    replan, note = plan_reader_stage_replan(_config(), [origin, failure], failure)
    assert replan is not None and "issue-triage" in note
    assert replan.type == "issue.requested"
    assert replan.payload["issue_ref"] == "docs/issues/TODO.md"
    assert replan.payload["rework_attempt"] == 1
    assert replan.payload["rework_feedback"][0]["message"] == "root paths unowned"
    assert replan.causation_id == failure.id


def test_idempotent_per_failure_event() -> None:
    origin = ZfEvent(type="issue.requested", payload={})
    failure = _failure()
    already = ZfEvent(type="issue.requested", payload={"rework_attempt": 1},
                      causation_id=failure.id)
    replan, note = plan_reader_stage_replan(
        _config(), [origin, failure, already], failure,
    )
    assert replan is None and note == "already_replanned"


def test_cap_exhausted_escalates() -> None:
    origin = ZfEvent(type="issue.requested", payload={})
    priors = [_failure() for _ in range(STAGE_REPLAN_CAP)]
    failure = _failure()
    replan, note = plan_reader_stage_replan(
        _config(), [origin, *priors, failure], failure,
    )
    assert replan is None and note == "cap_exhausted"


def test_unknown_failure_event_ignored() -> None:
    failure = ZfEvent(type="something.else.failed", payload={})
    replan, note = plan_reader_stage_replan(_config(), [failure], failure)
    assert replan is None and note == "no_reader_stage_for_failure"
