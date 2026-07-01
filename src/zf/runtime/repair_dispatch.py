"""Consumer-side planning for the authorized self-repair loop (backlog 0820, block B last mile).

The autoresearch reactor emits ``autoresearch.repair.dispatch_requested`` (gated
by ``ZF_AUTORESEARCH_AUTO_REPAIR=authorized`` + a per-fingerprint cap). The
repair targets the HARNESS's own code (``src/zf``), so it must run in the ZAOFU
repo — not the project worktree the orchestrator is driving. This module + the
``zf self-repair`` CLI are that zaofu-side consumer: prepare an isolated zaofu
worktree + a briefing pointing the agent at the ``zf-self-repair`` skill; the
skill-equipped agent then runs the tracked playbook (backlog → fix → verify →
done). Pure functions here; the CLI does the git worktree + agent spawn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DISPATCH_REQUESTED = "autoresearch.repair.dispatch_requested"
DISPATCHED = "autoresearch.repair.dispatched"


@dataclass(frozen=True)
class RepairRequest:
    fingerprint: str
    attempt: int
    candidate_id: str
    candidate_path: str
    repair_task_payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = ""


def _key(payload: dict[str, Any]) -> tuple[str, int]:
    try:
        attempt = int(payload.get("attempt") or 0)
    except (TypeError, ValueError):
        attempt = 0
    return str(payload.get("fingerprint") or ""), attempt


def pending_repair_dispatches(
    events,
    *,
    request_types: tuple[str, ...] = (DISPATCH_REQUESTED,),
) -> list[RepairRequest]:
    """Repair request events that don't yet have a matching dispatched."""
    accepted_request_types = set(request_types)
    dispatched: set[tuple[str, int]] = set()
    requests: dict[tuple[str, int], RepairRequest] = {}
    for event in events:
        etype = getattr(event, "type", "")
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        key = _key(payload)
        if etype == DISPATCHED:
            dispatched.add(key)
        elif etype in accepted_request_types:
            repair = payload.get("repair_task_payload")
            requests[key] = RepairRequest(
                fingerprint=key[0],
                attempt=key[1],
                candidate_id=str(payload.get("candidate_id") or ""),
                candidate_path=str(payload.get("candidate_path") or ""),
                repair_task_payload=repair if isinstance(repair, dict) else {},
                event_id=str(getattr(event, "id", "")),
            )
    return [req for key, req in requests.items() if key not in dispatched]


def repair_branch_name(req: RepairRequest) -> str:
    short = (req.fingerprint.replace(":", "-").replace("/", "-") or "unknown")[:48]
    return f"self-repair/{short}-a{req.attempt}"


def build_repair_briefing(req: RepairRequest) -> str:
    contract = req.repair_task_payload.get("contract")
    contract = contract if isinstance(contract, dict) else {}
    scope = contract.get("scope") or ["src/zf/**", "tests/**"]
    if not isinstance(scope, list):
        scope = [str(scope)]
    verification = str(
        contract.get("verification")
        or "run the focused pytest target + relevant regression"
    )
    hypothesis = str(
        contract.get("behavior") or req.repair_task_payload.get("title") or ""
    )
    return (
        f"# Authorized self-repair — {req.candidate_id}\n\n"
        "Follow the **zf-self-repair** skill exactly: write backlog → fix → "
        "verify → done. You are on an isolated zaofu worktree branch.\n\n"
        f"- fingerprint: {req.fingerprint}  (attempt {req.attempt})\n"
        f"- hypothesis: {hypothesis}\n"
        f"- scope (do NOT exceed): {', '.join(str(s) for s in scope)}\n"
        f"- verification (HARD gate — never merge on red): {verification}\n"
        f"- candidate artifact: {req.candidate_path}\n\n"
        "Steps: ① write the backlog FIRST (`> 状态: active`) ② make the surgical "
        "fix within scope ③ run verification ④ on GREEN commit + mark the backlog "
        "`done` with the commit hash; on RED or over the attempt cap leave it "
        "un-merged, mark `blocked`, escalate. Never touch runtime truth "
        "(events.jsonl/kanban.json/...) or credentials. Do not push to any remote.\n"
    )


def dispatched_event_payload(req: RepairRequest, *, branch: str, worktree: str, briefing_path: str) -> dict[str, Any]:
    return {
        "fingerprint": req.fingerprint,
        "attempt": req.attempt,
        "candidate_id": req.candidate_id,
        "branch": branch,
        "worktree": worktree,
        "briefing_path": briefing_path,
        "skill": "zf-self-repair",
    }
