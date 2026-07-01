"""backlog 0820 block B last mile: self-repair dispatch consumer planning."""

from __future__ import annotations

from types import SimpleNamespace

from zf.runtime.repair_dispatch import (
    DISPATCH_REQUESTED,
    DISPATCHED,
    build_repair_briefing,
    pending_repair_dispatches,
    repair_branch_name,
)


def _req_ev(fp, attempt, eid="e1"):
    return SimpleNamespace(type=DISPATCH_REQUESTED, id=eid, payload={
        "fingerprint": fp, "attempt": attempt, "candidate_id": "HIC-1",
        "candidate_path": "/s/HIC-1.json",
        "repair_task_payload": {
            "title": "fix respawn stall",
            "contract": {"scope": ["src/zf/**"], "verification": "pytest tests/test_x.py", "behavior": "respawn debounce"},
        },
    })


def _dispatched_ev(fp, attempt):
    return SimpleNamespace(type=DISPATCHED, id="d1", payload={"fingerprint": fp, "attempt": attempt})


def test_pending_excludes_already_dispatched():
    events = [_req_ev("fp-a", 1), _req_ev("fp-b", 1), _dispatched_ev("fp-a", 1)]
    pending = pending_repair_dispatches(events)
    assert [r.fingerprint for r in pending] == ["fp-b"]


def test_pending_empty_when_no_requests():
    assert pending_repair_dispatches([_dispatched_ev("x", 1)]) == []


def test_branch_name_is_safe_and_attempt_tagged():
    req = pending_repair_dispatches([_req_ev("failure:fatal:worker.respawn.failed:x", 2)])[0]
    branch = repair_branch_name(req)
    assert branch.startswith("self-repair/")
    assert ":" not in branch and branch.endswith("-a2")


def test_briefing_carries_scope_verification_and_skill():
    req = pending_repair_dispatches([_req_ev("fp", 1)])[0]
    briefing = build_repair_briefing(req)
    assert "zf-self-repair" in briefing
    assert "src/zf/**" in briefing
    assert "pytest tests/test_x.py" in briefing
    assert "never merge on red" in briefing.lower()
    assert "do not push" in briefing.lower()
