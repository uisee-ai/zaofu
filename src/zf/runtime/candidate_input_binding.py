"""Prove a live frozen-target Reader's input binding without granting execution."""
from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.candidate_currentness import candidate_currentness_issues
from zf.runtime.candidate_verification_authority import _latest_frozen_candidate
from zf.runtime.scoped_candidate_verify import candidate_bound_task_verify
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.reader_input_dispatch import reader_input_dispatch_matches
from zf.runtime.workflow_operation import operation_request_hash, reduce_workflow_operations


def current_candidate_input_binding(
    state_dir: Path, manifest: Mapping[str, Any], events: Sequence[ZfEvent],
) -> bool:
    authority = manifest.get("handoff_authority_contract") or {}
    if (authority.get("attempt_domain") != "task"
            or authority.get("operation_type") != "fanout_reader_child"):
        return False
    rows = [row for row in reduce_workflow_operations(events).values()
            if row.get("active_attempt_id") == manifest.get("attempt_id")]
    if len(rows) != 1:
        return False
    row = rows[0]
    if (row.get("status") != "running" or not row.get("lease_id")
            or row.get("operation_type") != "fanout_reader_child"
            or any(not manifest.get(key) or row.get(key) != manifest[key] for key in (
                "task_id", "workflow_run_id", "task_map_generation"))
            or not reader_input_dispatch_matches(row, manifest, events)):
        return False
    body = hydrate_sidecar_ref(state_dir, row.get("request_ref") or {}).payload
    if operation_request_hash(body) != row.get("request_hash"):
        return False
    request = body.get("request") or {}
    identity = request.get("result_identity") or {}
    profile = request.get("output_profile_id")
    scoped_task_reader = candidate_bound_task_verify(request)
    contract_reader = profile == "candidate-verify"
    workflow_reader = (profile == "workflow-read"
                       and not manifest.get("contract_revision")
                       and not identity.get("contract_snapshot_ref"))
    goal_reader = profile == "thin-judge-goal-closure" and not manifest.get("contract_revision")
    if (not (scoped_task_reader or contract_reader or workflow_reader or goal_reader)
            or request.get("source_manifest_digest") != canonical_json_sha256(manifest)
            or request.get("handoff_authority_contract") != authority
            or any(body.get(key) != row.get(key) for key in (
                "task_id", "workflow_run_id", "operation_id", "active_attempt_id", "role_instance", "lease_id"))
            or any(not manifest.get(key) or identity.get(key) != manifest[key] for key in (
                "task_id", "workflow_run_id", "task_map_generation",
                "target_commit", "plan_artifact_package_ref", "plan_artifact_package_digest"))):
        return False
    fanout = request.get("fanout_id")
    if not fanout or any(e.type == "fanout.cancelled"
            and e.payload.get("workflow_run_id") == manifest["workflow_run_id"]
            and e.payload.get("fanout_id") == fanout for e in events):
        return False
    if scoped_task_reader:
        return _scoped_task_reader_current(state_dir, manifest, events, identity)
    if contract_reader and (not manifest.get("contract_revision")
            or identity.get("contract_revision") != manifest["contract_revision"]
            or candidate_currentness_issues(state_dir, events, identity=identity,
            contract_ref=str(identity.get("contract_snapshot_ref") or ""),
            contract_digest=str(identity.get("contract_snapshot_digest") or ""))):
        return False
    if goal_reader:
        from zf.runtime.goal_closure_identity import (
            closure_is_current, validate_goal_closure_dispatch_snapshots,
        )

        target = hydrate_sidecar_ref(state_dir, {
            "ref": identity.get("target_snapshot_ref"),
            "sha256": identity.get("target_snapshot_digest"),
        }).payload
        validate_goal_closure_dispatch_snapshots(state_dir, {
            **identity, "closure_identity": target.get("closure_identity"),
        })
        if not closure_is_current(list(events), target):
            return False
    frozen = _latest_frozen_candidate(list(events), str(manifest["workflow_run_id"]))
    if frozen is None:
        return False
    if any(frozen.payload.get(freeze_key) != manifest.get(key) for key, freeze_key in (
        ("target_commit", "candidate_head_commit"),
        ("task_map_generation", "task_map_generation"),
        ("plan_artifact_package_ref", "plan_artifact_package_ref"),
        ("plan_artifact_package_digest", "plan_artifact_package_digest"),
    )):
        return False
    hydrate_sidecar_ref(state_dir, frozen.payload.get("freeze_receipt_ref") or {})
    return True


def _scoped_task_reader_current(
    state_dir: Path, manifest: Mapping[str, Any], events: Sequence[ZfEvent], identity: Mapping[str, Any],
) -> bool:
    """Partitioned Verify owns a reader lease, while its Task still names the writer."""
    from zf.core.task.store import TaskStore
    from zf.runtime.artifact_query.handoff import CanonicalHandoffResolver
    from zf.runtime.task_contract_snapshot import current_task_contract_identity

    task = TaskStore(state_dir / "kanban.json").get(str(manifest["task_id"]))
    if task is None or task.status not in {"in_progress", "done"}:
        return False
    current = current_task_contract_identity(task)
    if (not manifest.get("contract_revision") or identity.get("contract_revision") != manifest["contract_revision"]
            or any(identity.get(key) != value for key, value in current.items() if value)):
        return False
    # Reuse dispatch's snapshot/lineage validation against the latest candidate,
    # supplying no historical event override. Reading never transfers the Task lease.
    resolver = CanonicalHandoffResolver(state_dir=state_dir, project_root=state_dir.parent, config=None)
    resolver._validate_task_snapshots(payload=identity, workflow_run_id=manifest["workflow_run_id"],
        task_id=task.id, profile="task-verify", currentness=identity)
    from zf.runtime.run_scope import events_for_run
    candidates = [event for event in events_for_run(list(events), run_id=manifest["workflow_run_id"])
                  if event.type == "candidate.ready"]
    if not candidates:
        return False
    latest = candidates[-1]
    if latest.payload.get("source") == "workflow_resume_batch":
        producer = next((e for e in reversed(candidates) if e.payload.get("source") != "workflow_resume_batch"), None)
        if (producer is None or latest.origin != "kernel" or producer.origin != "kernel"
                or latest.payload.get("candidate_event_id") != producer.id
                or not latest.payload.get("resume_checkpoint_ref") or latest.payload.get("candidate_snapshot_event_id")
                or any(not producer.payload.get(key) or latest.payload.get(key) != producer.payload[key] for key in (
                    "workflow_run_id", "fanout_id", "integration_attempt_id", "candidate_head_commit", "completed_task_ids",
                    "task_map_generation", "plan_artifact_package_digest", "candidate_base_commit", "candidate_ref"))):
            return False
        # Dispatch can run synchronously before the final applied receipt is written.
        # The kernel checkpoint already binds its failed stage and writer source.
        if not any(e.type == "workflow.resume.checkpoint" and e.origin == "kernel"
                   and e.payload.get("safe_resume_action") == "reemit_candidate_ready"
                   and e.payload.get("checkpoint_id") == latest.payload["resume_checkpoint_ref"]
                   and e.payload.get("workflow_run_id") == manifest["workflow_run_id"]
                   and e.payload.get("source_event_id") == latest.causation_id
                   and e.payload.get("candidate_snapshot_event_id") == producer.id
                   for e in events[:events.index(latest)]):
            return False
        if manifest.get("candidate_event_id") == producer.id:
            latest = producer
    if manifest.get("candidate_event_id") != latest.id:
        return False
    snapshot = resolver._validate_candidate_authority(
        payload={"output_profile_id": "task-verify", "task_id": task.id, "candidate_event_id": latest.id,
                 "handoff_authority_contract": manifest["handoff_authority_contract"]},
        workflow_run_id=manifest["workflow_run_id"], currentness=dict(identity), events=events,
    )
    return bool(snapshot and snapshot["candidate_event_id"] == manifest.get("candidate_event_id")
                and snapshot["identity_digest"] == manifest.get("candidate_snapshot_digest"))
