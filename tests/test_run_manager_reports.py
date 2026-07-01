from __future__ import annotations

from zf.runtime.run_manager_reports import (
    build_regression_backlog_candidates,
    build_retrospective_markdown,
)


def test_retrospective_markdown_summarizes_projection() -> None:
    md = build_retrospective_markdown(projection={
        "summary": {
            "goal_status": "active",
            "completion_status": "blocked",
            "pending_actions": 1,
            "blocked_actions": 1,
        },
        "monitor": {"state": "needs_human"},
        "timeline": {"items": [{"event_type": "human.escalate", "decision": ""}]},
        "advisor": {
            "summary": {"recommendations": 1},
            "recommendations": [{
                "kind": "no_progress_replan_advice",
                "reason": "same fingerprint",
                "recommended_route": "reflection_replan_advisor",
            }],
        },
    })

    assert "# Run Manager Retrospective" in md
    assert "completion_status: blocked" in md
    assert "no_progress_replan_advice" in md


def test_regression_backlog_candidate_is_proposed_text() -> None:
    candidates = build_regression_backlog_candidates(projection={
        "no_progress": {
            "items": [{
                "fingerprint": "ck-1",
                "event_id": "evt-1",
                "count": 3,
            }],
        },
    })

    assert candidates[0]["status"] == "proposed"
    assert "> 状态: proposed" in candidates[0]["body"]
    assert "ck-1" in candidates[0]["body"]

