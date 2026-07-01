from __future__ import annotations

from zf.runtime.run_manager_advisor import build_replan_advisor_projection


def test_replan_advisor_is_proposal_only_for_no_progress() -> None:
    advisor = build_replan_advisor_projection(
        [],
        no_progress={
            "status": "tripped",
            "items": [{
                "fingerprint": "ck-1",
                "event_id": "evt-1",
                "count": 3,
            }],
        },
        completion_profile={"blockers": []},
        repair_ledger={"summary": {}},
    )

    assert advisor["authority"] == "proposal_only"
    assert advisor["summary"]["recommendations"] == 1
    rec = advisor["recommendations"][0]
    assert rec["recommended_route"] == "reflection_replan_advisor"
    assert "task_map.ready" in rec["forbidden_direct_events"]

