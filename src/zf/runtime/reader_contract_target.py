"""Reader contract evidence hydration without overwriting rescan plan authority."""

from __future__ import annotations

from typing import Any

from zf.runtime.task_contract_snapshot import (
    TaskContractSnapshotError,
    build_target_snapshot,
    contract_snapshot_identity_fields,
    current_task_contract_identity,
    descriptor_from_payload as contract_descriptor_from_payload,
    hydrate_task_contract_snapshot,
    hydrate_target_snapshot,
    target_descriptor_from_payload,
    target_payload_fields,
    write_target_snapshot,
)


def prepare_reader_contract_target(runtime: Any, child: Any) -> None:
    payload = child.payload if isinstance(child.payload, dict) else {}
    if not (
        str(payload.get("contract_snapshot_ref") or "").strip()
        or str(payload.get("contract_snapshot_digest") or "").strip()
    ):
        return
    if (
        str(payload.get("closure_identity") or "").strip()
        and str(payload.get("goal_claim_set_ref") or "").strip()
    ):
        from zf.runtime.goal_closure_identity import (
            validate_goal_closure_dispatch_snapshots,
        )

        validate_goal_closure_dispatch_snapshots(runtime.state_dir, payload)
        return
    from zf.runtime.rescan_contract_authority import uses_rescan_authority, prepare_rescan_contract

    rescan = uses_rescan_authority(payload)
    descriptor = contract_descriptor_from_payload(payload)
    snapshot = hydrate_task_contract_snapshot(runtime.state_dir, descriptor)
    if snapshot.get("authority_scope") == "candidate":
        from zf.runtime.candidate_currentness import candidate_currentness_issues

        # The workflow anchor is lineage, not the aggregate's Task authority.
        # Reuse the same frozen-candidate checks as input/result admission;
        # never replace a retry's target or contract to make it current.
        issues = candidate_currentness_issues(
            runtime.state_dir, runtime.event_log.read_all(), identity=payload,
            contract_ref=str(payload.get("contract_snapshot_ref") or descriptor["ref"]),
            contract_digest=str(payload.get("contract_snapshot_digest") or descriptor["sha256"]),
        )
        if issues:
            raise TaskContractSnapshotError("; ".join(
                f"{issue['code']}: {issue['message']}" for issue in issues
            ))
        return
    task_id = str(payload.get("task_id") or "")
    task = runtime.task_store.get(task_id) if task_id else None
    expected = {"task_id": task_id}
    if task is not None and not rescan:
        expected.update(current_task_contract_identity(
            task,
            task_map_ref=str(payload.get("task_map_ref") or ""),
        ))
    task_ref_entry = runtime._task_ref_entry(task_id)
    expected_task_ref = str(
        task_ref_entry.get("task_ref")
        or payload.get("task_ref")
        or ""
    )
    if expected_task_ref:
        expected["task_ref"] = expected_task_ref
    snapshot = hydrate_task_contract_snapshot(
        runtime.state_dir,
        descriptor,
        expected=expected,
    )
    if rescan:
        snapshot, descriptor = prepare_rescan_contract(runtime, payload, snapshot, descriptor)
    target = build_target_snapshot(
        descriptor,
        target_commit=str(payload.get("target_commit") or child.target_ref or ""),
        contract_snapshot=snapshot,
    )
    try:
        target_descriptor = target_descriptor_from_payload(payload)
        target = hydrate_target_snapshot(
            runtime.state_dir,
            target_descriptor,
            expected={
                "contract_snapshot_ref": descriptor["ref"],
                "contract_snapshot_digest": descriptor["sha256"],
                "target_commit": str(target["target_commit"]),
            },
        )
    except TaskContractSnapshotError:
        target_descriptor = write_target_snapshot(
            runtime.state_dir,
            target,
            source_event_id=str(payload.get("trigger_event_id") or ""),
        )
    snapshot_identity = contract_snapshot_identity_fields(snapshot)
    payload.update({
        **snapshot_identity,
        "target_snapshot": target,
        "target_commit": str(target["target_commit"]),
        **target_payload_fields(target_descriptor),
    })
