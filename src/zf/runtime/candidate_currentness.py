"""Shared read-only frozen-candidate currentness for inputs and result admission."""
from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.candidate_verification_authority import _latest_frozen_candidate
from zf.runtime.candidate_result_binding import same_task_map_generation
from zf.runtime.task_contract_snapshot import (
    TaskContractSnapshotError, current_task_contract_identity,
    hydrate_target_snapshot, hydrate_task_contract_snapshot,
)


def candidate_currentness_issues(
    state_dir: Path, events: Sequence[ZfEvent], *, identity: Mapping[str, Any],
    contract_ref: str, contract_digest: str,
) -> list[dict[str, str]]:
    from zf.runtime.call_result_authority import _currentness_issue

    snapshot_expected = {
        key: identity.get(key)
        for key in (
            "workflow_run_id",
            "task_id",
            "contract_revision",
            "task_map_generation",
            "base_commit",
            "task_ref",
            "plan_artifact_package_id",
            "plan_artifact_package_ref",
            "plan_artifact_package_digest",
        )
        if identity.get(key) not in (None, "")
    }
    try:
        contract_snapshot = hydrate_task_contract_snapshot(
            state_dir,
            {"ref": contract_ref, "sha256": contract_digest},
            expected=snapshot_expected,
        )
    except TaskContractSnapshotError as exc:
        return [_currentness_issue(
            "control_result.contract_snapshot_ref",
            "stale_contract_snapshot",
            str(exc),
        )]
    if str(contract_snapshot.get("authority_scope") or "") != "candidate":
        return [_currentness_issue(
            "control_result.contract_snapshot_ref",
            "stale_contract_snapshot",
            "candidate-verify requires candidate-scoped contract authority",
        )]

    target_ref = str(identity.get("target_snapshot_ref") or "")
    target_digest = str(identity.get("target_snapshot_digest") or "")
    target_commit = str(identity.get("target_commit") or "")
    try:
        target_snapshot = hydrate_target_snapshot(
            state_dir,
            {"ref": target_ref, "sha256": target_digest},
            expected={
                **{
                    key: contract_snapshot.get(key)
                    for key in (
                        "workflow_run_id",
                        "task_id",
                        "contract_revision",
                        "task_map_generation",
                        "base_commit",
                        "task_ref",
                        "plan_artifact_package_id",
                        "plan_artifact_package_ref",
                        "plan_artifact_package_digest",
                    )
                },
                "contract_snapshot_ref": contract_ref,
                "contract_snapshot_digest": contract_digest,
                "target_commit": target_commit,
            },
        )
    except TaskContractSnapshotError as exc:
        return [_currentness_issue(
            "control_result.target_snapshot_ref",
            "stale_target_snapshot",
            str(exc),
        )]
    if str(target_snapshot.get("authority_scope") or "") != "candidate":
        return [_currentness_issue(
            "control_result.target_snapshot_ref",
            "stale_target_snapshot",
            "candidate-verify requires candidate-scoped target authority",
        )]

    workflow_run_id = str(identity.get("workflow_run_id") or "")
    current_candidate = _latest_frozen_candidate(list(events), workflow_run_id)
    if current_candidate is None:
        return [_currentness_issue(
            "control_result.target_commit",
            "stale_target_commit",
            "current candidate.ready is missing",
        )]
    candidate = (
        current_candidate.payload
        if isinstance(current_candidate.payload, Mapping)
        else {}
    )
    issues: list[dict[str, str]] = []
    expected_generation = str(candidate.get("task_map_generation") or "")
    actual_generation = str(identity.get("task_map_generation") or "")
    if expected_generation and not same_task_map_generation(
        actual_generation,
        expected_generation,
    ):
        issues.append(_currentness_issue(
            "control_result.task_map_generation",
            "stale_task_map_generation",
            f"current candidate expects {expected_generation}",
        ))
    expected_target = str(
        candidate.get("candidate_head_commit")
        or candidate.get("candidate_head")
        or ""
    )
    if expected_target and target_commit != expected_target:
        issues.append(_currentness_issue(
            "control_result.target_commit",
            "stale_target_commit",
            f"current frozen candidate targets {expected_target}, got {target_commit}",
        ))
    for field, candidate_field, code in (
        ("base_commit", "candidate_base_commit", "stale_contract_snapshot"),
        ("task_ref", "candidate_ref", "stale_contract_snapshot"),
        (
            "plan_artifact_package_id",
            "plan_artifact_package_id",
            "stale_plan_artifact_package",
        ),
        (
            "plan_artifact_package_ref",
            "plan_artifact_package_ref",
            "stale_plan_artifact_package",
        ),
        (
            "plan_artifact_package_digest",
            "plan_artifact_package_digest",
            "stale_plan_artifact_package",
        ),
    ):
        expected = str(candidate.get(candidate_field) or "")
        actual = str(contract_snapshot.get(field) or "")
        if expected and actual != expected:
            issues.append(_currentness_issue(
                f"control_result.{field}",
                code,
                f"current candidate expects {expected}, got {actual or '<missing>'}",
            ))

    contract_event_id = str(contract_snapshot.get("candidate_event_id") or "")
    if contract_event_id != current_candidate.id:
        issues.append(_currentness_issue(
            "control_result.contract_snapshot_ref",
            "stale_contract_snapshot",
            "candidate contract was not derived from current candidate.ready",
        ))
    freeze_descriptor = candidate.get("freeze_receipt_ref")
    freeze_descriptor = (
        freeze_descriptor if isinstance(freeze_descriptor, Mapping) else {}
    )
    source_refs = (
        contract_snapshot.get("source_refs")
        if isinstance(contract_snapshot.get("source_refs"), Mapping)
        else {}
    )
    for field, actual, expected in (
        (
            "freeze_receipt_ref",
            str(source_refs.get("freeze_receipt_ref") or ""),
            str(freeze_descriptor.get("ref") or ""),
        ),
        (
            "freeze_receipt_digest",
            str(source_refs.get("freeze_receipt_digest") or ""),
            str(candidate.get("freeze_receipt_digest") or ""),
        ),
    ):
        if expected and actual != expected:
            issues.append(_currentness_issue(
                "control_result.contract_snapshot_ref",
                "stale_contract_snapshot",
                f"current candidate {field} expects {expected}, got {actual}",
            ))
    expected_tasks = sorted(
        str(item) for item in candidate.get("completed_task_ids") or []
        if str(item)
    )
    actual_tasks = sorted(
        str(item) for item in contract_snapshot.get("completed_task_ids") or []
        if str(item)
    )
    if expected_tasks and actual_tasks != expected_tasks:
        from zf.runtime.candidate_replacement_authority import verified_predecessors

        events = list(events)
        children = contract_snapshot.get("child_authorities") or []
        try:
            replaced = verified_predecessors(
                state_dir, current_candidate, events[: events.index(current_candidate)], children,
            )
        except (KeyError, ValueError, TypeError, OSError) as exc:
            return [_currentness_issue("control_result.contract_snapshot_ref", "stale_contract_snapshot", str(exc))]
        if (sorted(child.get("task_id", "") for child in children) != actual_tasks
                or sorted(set(expected_tasks) - replaced) != actual_tasks):
            issues.append(_currentness_issue(
                "control_result.contract_snapshot_ref",
                "stale_contract_snapshot",
                "candidate contract completed_task_ids do not match current freeze",
            ))
    task_store = TaskStore(state_dir / "kanban.json")
    for child in contract_snapshot.get("child_authorities") or []:
        if not isinstance(child, Mapping):
            continue
        authority_revision = str(
            child.get("contract_authority_revision") or ""
        )
        if not authority_revision:
            continue
        child_task_id = str(child.get("task_id") or "")
        current_task = task_store.get(child_task_id)
        if current_task is None:
            issues.append(_currentness_issue(
                "control_result.contract_snapshot_ref",
                "stale_task_authority",
                f"candidate child {child_task_id!r} is absent from TaskStore",
            ))
            continue
        try:
            current_identity = current_task_contract_identity(current_task)
        except TaskContractSnapshotError as exc:
            issues.append(_currentness_issue(
                "control_result.contract_snapshot_ref",
                "stale_task_authority",
                str(exc),
            ))
            continue
        for field, expected in current_identity.items():
            actual = str(child.get(field) or "")
            if str(expected) != actual:
                issues.append(_currentness_issue(
                    f"control_result.child_authorities.{field}",
                    "stale_task_authority",
                    (
                        f"candidate child {child_task_id!r} expects "
                        f"{expected}, got {actual or '<missing>'}"
                    ),
                ))
    return issues
