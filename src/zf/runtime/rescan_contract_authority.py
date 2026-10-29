"""Immutable rescan authority over a current Plan and a historical task contract."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from zf.core.task.store import TaskStore
from zf.runtime.task_contract_snapshot import (
    TaskContractSnapshotError,
    current_task_contract_identity,
    hydrate_task_contract_snapshot,
    hydrate_target_snapshot,
    write_task_contract_snapshot,
    snapshot_payload_fields,
)

PLAN_FIELDS = (
    "task_map_generation",
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
)


def uses_rescan_authority(payload: Mapping[str, Any]) -> bool:
    return payload.get("output_profile_id") == "global-rescan" and all(
        payload.get(k) for k in PLAN_FIELDS
    )


def prepare_rescan_contract(
    runtime: Any, payload: dict, snapshot: dict, descriptor: dict
) -> tuple[dict, dict]:
    if snapshot.get("authority_scope") == "global-rescan":
        descriptor = {
            "ref": snapshot["source_contract_snapshot_ref"],
            "sha256": snapshot["source_contract_snapshot_digest"],
        }
        snapshot = hydrate_task_contract_snapshot(runtime.state_dir, descriptor)
    task = runtime.task_store.get(str(snapshot["task_id"]))
    if task is None or task.status in {"cancelled", "superseded"}:
        raise TaskContractSnapshotError("rescan requires a current source task")
    hydrate_task_contract_snapshot(
        runtime.state_dir,
        descriptor,
        expected={
            **current_task_contract_identity(task),
            "task_id": task.id,
            "workflow_run_id": payload.get("workflow_run_id"),
        },
    )
    seed = {
        **{k: payload[k] for k in PLAN_FIELDS},
        "source_contract_snapshot_ref": descriptor["ref"],
        "source_contract_snapshot_digest": descriptor["sha256"],
    }
    digest = hashlib.sha256(
        json.dumps(seed, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    rescan = {
        **snapshot,
        **seed,
        "authority_scope": "global-rescan",
        "contract_revision": f"rescan-contract-r{digest[:20]}",
    }
    bound = write_task_contract_snapshot(
        runtime.state_dir,
        rescan,
        source_event_id=str(payload.get("trigger_event_id") or ""),
    )
    payload.update({**snapshot_payload_fields(bound), "attempt_domain": "gap"})
    return rescan, bound


def rescan_currentness_issues(
    runtime: Any, envelope: Mapping[str, Any], operation: Mapping[str, Any]
) -> list[dict[str, str]]:
    identity = envelope.get("identity") or {}
    try:
        snapshot = hydrate_task_contract_snapshot(
            runtime.state_dir,
            {
                "ref": identity.get("contract_snapshot_ref"),
                "sha256": identity.get("contract_snapshot_digest"),
            },
            expected={
                k: identity[k]
                for k in (
                    *PLAN_FIELDS,
                    "workflow_run_id",
                    "task_id",
                    "contract_revision",
                    "base_commit",
                    "task_ref",
                )
                if identity.get(k)
            },
        )
        if snapshot.get("authority_scope") != "global-rescan":
            raise TaskContractSnapshotError(
                "global-rescan requires rescan-scoped contract authority"
            )
        source_descriptor = {
            "ref": snapshot["source_contract_snapshot_ref"],
            "sha256": snapshot["source_contract_snapshot_digest"],
        }
        task = TaskStore(runtime.state_dir / "kanban.json").get(
            str(identity.get("task_id") or "")
        )
        if task is None or task.status in {"cancelled", "superseded"}:
            raise TaskContractSnapshotError(
                "rescan source task is absent or superseded"
            )
        hydrate_task_contract_snapshot(
            runtime.state_dir,
            source_descriptor,
            expected={
                **current_task_contract_identity(task),
                "task_id": task.id,
                "workflow_run_id": identity.get("workflow_run_id"),
            },
        )
        hydrate_target_snapshot(
            runtime.state_dir,
            {
                "ref": identity.get("target_snapshot_ref"),
                "sha256": identity.get("target_snapshot_digest"),
            },
            expected={
                **{
                    k: snapshot[k]
                    for k in (
                        *PLAN_FIELDS,
                        "workflow_run_id",
                        "task_id",
                        "contract_revision",
                        "base_commit",
                        "task_ref",
                    )
                    if snapshot.get(k)
                },
                "contract_snapshot_ref": identity.get("contract_snapshot_ref"),
                "contract_snapshot_digest": identity.get("contract_snapshot_digest"),
                "target_commit": identity.get("target_commit"),
            },
        )
    except (TaskContractSnapshotError, KeyError) as exc:
        return [
            {
                "field": "control_result.contract_snapshot_ref",
                "code": "stale_contract_snapshot",
                "message": str(exc),
            }
        ]
    return []
