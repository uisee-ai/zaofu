"""Choose a reader checkout from its immutable verification target."""

from pathlib import Path

from zf.runtime.task_contract_snapshot import (
    hydrate_target_snapshot, target_descriptor_from_payload,
)


def reader_checkout_ref(state_dir: Path, payload: dict, fallback: str) -> str:
    if payload.get("closure_identity") and payload.get("goal_claim_set_ref"):
        # Goal closure has a different target schema and its own authority gate.
        return fallback
    if not (payload.get("target_snapshot_ref") or payload.get("target_snapshot_digest")):
        return fallback
    target = hydrate_target_snapshot(
        state_dir, target_descriptor_from_payload(payload),
        expected={key: payload.get(key) for key in (
            "target_commit", "task_id", "workflow_run_id", "task_map_generation",
            "contract_snapshot_ref", "contract_snapshot_digest",
        )},
    )
    candidate = str(payload.get("candidate_head_commit") or "")
    if candidate and candidate != target["target_commit"]:
        # Candidate integration carries prior Task Verify targets until its
        # aggregate authority is prepared. They are not the candidate checkout.
        return fallback
    return str(target["target_commit"])
