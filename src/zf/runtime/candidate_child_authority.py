"""Shared frozen child authority checks for Candidate Verify and Thin Judge."""
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping

from zf.core.task.store import TaskStore
from zf.runtime.task_contract_snapshot import TaskContractSnapshotError, current_task_contract_identity


def candidate_child_authority_issues(state_dir: Path, events: list, current_candidate,
                                    children: list, *, require_all: bool = False) -> list[dict[str, str]]:
    from zf.runtime.call_result_authority import _currentness_issue

    issues: list[dict[str, str]] = []
    task_store = TaskStore(state_dir / "kanban.json")
    reused = {}
    if any(child.get("completion_reuse_ref") or child.get("retained_task_evidence") for child in children
           if isinstance(child, Mapping)):
        from zf.runtime.candidate_retained_tasks import candidate_child_proofs

        if current_candidate is None:
            return [_currentness_issue("control_result.contract_snapshot_ref", "stale_task_authority",
                "retained Task proof requires a current frozen Candidate")]
        try:
            reused = candidate_child_proofs(SimpleNamespace(state_dir=state_dir, task_store=task_store),
                                    list(events), current_candidate)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return [_currentness_issue("control_result.contract_snapshot_ref", "stale_task_authority", str(exc))]
    for child in children:
        if not isinstance(child, Mapping):
            continue
        authority_revision = str(
            child.get("contract_authority_revision") or ""
        )
        if not require_all and not authority_revision and not child.get("completion_reuse_ref") and not child.get("retained_task_evidence"):
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
        if child.get("completion_reuse_ref") or child.get("retained_task_evidence"):
            proof = reused.get(child_task_id)
            if (not proof or proof.get("completion_reuse_ref") != child.get("completion_reuse_ref")
                    or proof.get("retained_task_evidence") != child.get("retained_task_evidence")
                    or proof.get("completion_restore_ref") != child.get("completion_restore_ref")
                    or proof["verification_event_id"] != child.get("verification_event_id")):
                issues.append(_currentness_issue("control_result.contract_snapshot_ref", "stale_task_authority",
                    f"candidate child {child_task_id!r} lacks its exact reviewed completion proof"))
                continue
            # current_proofs checks the current complete contract and identity;
            # the retained native Verify still carries its reviewed old identity.
            current_identity = proof["prior_identity"]
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


def goal_child_authority_issues(state_dir: Path, events: list, *, snapshot: Mapping,
                               result: Mapping) -> list[dict[str, str]]:
    from zf.runtime.call_result_authority import _currentness_issue
    from zf.runtime.candidate_result_binding import same_task_map_generation
    from zf.runtime.candidate_verification_authority import _latest_frozen_candidate

    children = snapshot.get("child_authorities") or []
    candidate = None
    if any(child.get("completion_reuse_ref") or child.get("retained_task_evidence")
           for child in children if isinstance(child, Mapping)):
        try:
            candidate = _latest_frozen_candidate(events, str(snapshot.get("workflow_run_id") or ""))
        except ValueError as exc:
            return [_currentness_issue("control_result.contract_snapshot_ref", "stale_task_authority", str(exc))]
        body = candidate.payload if candidate else {}
        if (not candidate
                or result.get("workflow_run_id") != snapshot.get("workflow_run_id")
                or result.get("target_commit") != body.get("candidate_head_commit")
                or not same_task_map_generation(str(snapshot.get("task_map_generation") or ""),
                                                str(body.get("task_map_generation") or ""))
                or any(body.get(key) and snapshot.get(key) != body[key] for key in (
                    "plan_artifact_package_id", "plan_artifact_package_ref", "plan_artifact_package_digest"))):
            return [_currentness_issue("control_result.contract_snapshot_ref", "stale_task_authority",
                "Goal closure retained Task proof does not bind the current frozen Candidate")]
        if not _goal_pins_candidate_verify(state_dir, events, snapshot, result, candidate):
            return [_currentness_issue("control_result.contract_snapshot_ref", "stale_task_authority",
                "Goal closure lacks its exact admitted Candidate Verify freeze binding")]
    return candidate_child_authority_issues(state_dir, events, candidate, children, require_all=True)


def _goal_pins_candidate_verify(state_dir: Path, events: list, snapshot: Mapping,
                               result: Mapping, candidate) -> bool:
    from zf.runtime.candidate_verification_authority import _latest_frozen_candidate
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    if not result.get("closure_fact_ref") or not result.get("closure_fact_digest"):
        return False
    pin = next((event for event in reversed(events)
        if event.origin == "kernel" and event.type in {"flow.goal.closed", "module.parity.closed"}
        and event.payload.get("workflow_run_id") == snapshot.get("workflow_run_id")
        and event.payload.get("closure_fact_ref") == result["closure_fact_ref"]
        and event.payload.get("closure_fact_digest") == result["closure_fact_digest"]
        and event.payload.get("contract_snapshot_ref") == result.get("contract_snapshot_ref")
        and event.payload.get("contract_snapshot_digest") == result.get("contract_snapshot_digest")), None)
    if pin is None:
        return False
    before = events[:events.index(pin)]
    try:
        pinned = _latest_frozen_candidate(before, str(snapshot.get("workflow_run_id") or ""))
    except ValueError:
        return False
    if pinned is None or pinned.id != candidate.id:
        return False
    # The closure pin can include older results. Require the actual admitted
    # Candidate Verify contract, not just the freeze latest at closure time.
    refs = set(pin.payload.get("input_result_refs") or [])
    children = sorted(snapshot.get("child_authorities") or [], key=lambda row: row["task_id"])
    for event in reversed(before):
        body = event.payload
        descriptor = body.get("envelope_ref") or {}
        if (event.origin != "kernel" or event.type != "workflow.call.result.admitted"
                or body.get("control_result_schema") != "verification-result.v1"
                or body.get("workflow_run_id") != snapshot.get("workflow_run_id")
                or not isinstance(descriptor, Mapping) or descriptor.get("ref") not in refs):
            continue
        try:
            identity = hydrate_sidecar_ref(state_dir, descriptor).payload["identity"]
            contract = hydrate_sidecar_ref(state_dir, {"ref": identity["contract_snapshot_ref"],
                "sha256": identity["contract_snapshot_digest"]}).payload
            if (contract.get("authority_scope") == "candidate"
                    and contract.get("candidate_event_id") == candidate.id
                    and identity.get("workflow_run_id") == snapshot.get("workflow_run_id")
                    and identity.get("target_commit") == result.get("target_commit")
                    and contract.get("source_refs", {}).get("freeze_receipt_ref")
                        == candidate.payload.get("freeze_receipt_ref", {}).get("ref")
                    and contract.get("source_refs", {}).get("freeze_receipt_digest")
                        == candidate.payload.get("freeze_receipt_digest")
                    and sorted(contract.get("child_authorities") or [], key=lambda row: row["task_id"]) == children):
                return True
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return False
