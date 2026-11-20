"""Explicitly reviewed reuse of integrated Tasks after a Plan-only revision.

This is an operator recovery, not a heuristic semantic-equivalence verdict.
The preview contains both contracts; its exact digest must be reviewed. Old
Task evidence retains its generation and cannot grant a Candidate verdict.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidate_continuation import _git_commit, _json, _latest, digest
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.plan_output_lineage import assert_package_task_map_matches
from zf.runtime.task_pipeline_contexts import latest_task_pipeline_generation_context
from zf.runtime.workflow_git_readiness import git_environment

SCHEMA = "plan-completion-reuse.v1"
SOURCE = "plan_completion_reuse"


def preview(service: Any, source: ZfEvent) -> dict:
    from zf.runtime.candidate_verification_authority import prepare_candidate_verification_authority
    from zf.runtime.task_contract_snapshot import current_task_contract_identity

    events = service.writer.event_log.read_all()
    if source.type != "task_map.ready" or source.origin != "kernel":
        raise ValueError("completion reuse requires a kernel Plan source")
    run_id = str(source.payload.get("workflow_run_id") or source.correlation_id or "")
    if any(_latest(events, kind, run_id) for kind in ("run.cancelled", "run.failed", "run.completed")):
        raise ValueError("completion reuse cannot revive a terminal Run")
    context = latest_task_pipeline_generation_context(events, workflow_run_id=run_id)
    if not context or context.get("trigger_event_id") != source.id:
        raise ValueError("completion reuse requires the current admitted Plan generation")
    root = Path(service.project_root or service.state_dir.parent)
    package_ref = {"ref": context["plan_artifact_package_ref"],
                   "sha256": context["plan_artifact_package_digest"]}
    package = hydrate_plan_artifact_package(service.state_dir, package_ref)
    if package.get("workflow_run_id") != run_id or package.get("task_map_generation") != context["task_map_generation"]:
        raise ValueError("completion reuse package identity differs")
    assert_package_task_map_matches(service.state_dir, root, package, context)
    admitted = _latest(events, "plan.artifact_package.admitted", run_id)
    if not admitted or admitted.payload.get("package_digest") != package_ref["sha256"]:
        raise ValueError("completion reuse Plan package is no longer current")
    approval = next((e for e in reversed(events) if e.type in {"plan.approved", "plan.rejected"}
                     and e.payload.get("plan_id") == source.id and not e.payload.get("semantic_control")), None)
    if approval is None or approval.type != "plan.approved":
        raise ValueError("completion reuse requires exact-version Owner Plan approval")
    candidate = _latest(events, "candidate.ready", run_id)
    if candidate is None or events.index(candidate) >= events.index(source):
        raise ValueError("completion reuse requires a previous frozen Candidate")
    body = candidate.payload
    if body.get("candidate_continuation_ref") or body.get("status") != "frozen":
        raise ValueError("completion reuse requires a native frozen Candidate")
    freeze = _json(service.state_dir, body.get("freeze_receipt_ref") or {})
    for key in ("workflow_run_id", "task_map_generation", "candidate_head_commit", "completed_task_ids", "freeze_id"):
        if not body.get(key) or freeze.get(key) != body[key]:
            raise ValueError("completion reuse freeze mismatch: " + key)
    head = body["candidate_head_commit"]
    if _git_commit(root, body["candidate_ref"]) != head or context["dispatch_base_commit"] != head:
        raise ValueError("completion reuse target moved or Plan targets another commit")
    task_ids = sorted(context["task_ids"])
    if task_ids != sorted(body["completed_task_ids"]) or task_ids != sorted(body["task_ids"]):
        raise ValueError("completion reuse must cover the full unchanged Task set")
    store = TaskStore(service.state_dir / "kanban.json")
    historical = events[:events.index(source)]
    previous_context = latest_task_pipeline_generation_context(
        events[:events.index(candidate) + 1], workflow_run_id=run_id)
    if not previous_context or previous_context["task_map_generation"] != body["task_map_generation"]:
        raise ValueError("completion reuse source generation differs")
    archived_tasks = store.list_all_with_archive()
    old_tasks = {}
    rows = []
    for task_id in task_ids:
        current = store.get(task_id)
        if current is None or current.status not in {"backlog", "done"} or current.active_dispatch_id:
            raise ValueError("completion reuse requires inactive Tasks: " + task_id)
        done = next((e for e in reversed(historical) if e.type == "task.done" and e.origin == "kernel"
                     and e.task_id == task_id and e.payload.get("workflow_run_id") == run_id
                     and e.payload.get("task_map_generation") == body["task_map_generation"]), None)
        verify = next((e for e in reversed(historical) if e.type == "task.pipeline.verify.completed"
                       and e.task_id == task_id and e.payload.get("workflow_run_id") == run_id
                       and e.payload.get("task_map_generation") == body["task_map_generation"]), None)
        if done is None or verify is None:
            raise ValueError("completion reuse lacks native completion/Verify: " + task_id)
        old = next((t for t in reversed(archived_tasks) if t.id == task_id and t.status == "done"
                    and t.contract_authority_revision == verify.payload.get("contract_authority_revision")), None)
        if old is None:
            raise ValueError("completion reuse lacks archived Task authority: " + task_id)
        from zf.runtime.plan_output_recovery import _acceptance_contract

        if _acceptance_contract(old.contract) != _acceptance_contract(current.contract):
            raise ValueError("completion reuse cannot change acceptance/execution contract: " + task_id)
        _native_proof(service.state_dir, events, done, verify)
        _settled_redispatches(root, events, source, task_id, head)
        refs = current.contract.evidence_contract.get("source_refs", {})
        if refs.get("task_map_generation") != context["task_map_generation"] or refs.get("plan_artifact_package_digest") != package_ref["sha256"]:
            raise ValueError("completion reuse Task is not bound to the current Plan: " + task_id)
        old_tasks[task_id] = old
        rows.append({"task_id": task_id, "completion_event_id": done.id,
                     "verification_event_id": verify.id, "integration_receipt_ref": done.payload["integration_receipt_ref"],
                     "prior_identity": current_task_contract_identity(old),
                     "current_identity": current_task_contract_identity(current),
                     "prior_contract": asdict(old.contract), "current_contract": asdict(current.contract),
                     "completed_at": old.completed_at})
    # Historical validation is a read view, never a rewrite of the event ledger.
    historic_runtime = SimpleNamespace(state_dir=service.state_dir, project_root=root, config=service.config,
        event_log=SimpleNamespace(read_all=lambda: historical), task_store=SimpleNamespace(get=old_tasks.get))
    authority = prepare_candidate_verification_authority(historic_runtime, payload={}, workflow_run_id=run_id,
                                                        task_id=str(body.get("pdd_id") or ""))
    if not authority:
        raise ValueError("completion reuse lacks frozen Task verification authority")
    manifest = {"schema_version": SCHEMA, "workflow_run_id": run_id, "source_event_id": source.id,
                "generation_admitted_event_id": context["generation_admitted_event_id"],
                "task_map_generation": context["task_map_generation"], "plan_package_ref": package_ref,
                "plan_approval_event_id": approval.id, "candidate_event_id": candidate.id,
                "candidate_head_commit": head, "candidate_ref": body["candidate_ref"],
                "candidate_base_commit": body["candidate_base_commit"],
                "prior_generation_event_id": previous_context["generation_admitted_event_id"],
                "prior_task_map_generation": previous_context["task_map_generation"], "tasks": rows,
                "scope": "Task completion evidence only; fresh Candidate/Judge/Goal required"}
    return {**manifest, "manifest_digest": digest(manifest)}


def _native_proof(state: Path, events: list[ZfEvent], done: ZfEvent, verify: ZfEvent) -> None:
    v = verify.payload
    admission = next((e for e in events if e.id == v.get("semantic_submit_admission_event_id")), None)
    if (admission is None or admission.type != "workflow.call.result.admitted" or admission.origin != "kernel"
            or admission.payload.get("semantic_verdict") != "passed"
            or any(admission.payload.get(k) != v.get(k) for k in (
                "workflow_run_id", "operation_id", "request_hash", "control_result_ref"))):
        raise ValueError("completion reuse requires independent admitted Verify")
    if _json(state, v.get("control_result_ref") or {}).get("verdict") != "passed":
        raise ValueError("completion reuse Verify did not pass")
    _json(state, v.get("call_result_envelope_ref") or {})
    receipt = _json(state, done.payload.get("integration_receipt_ref") or {})
    if (receipt.get("status") != "integrated" or receipt.get("task_id") != done.task_id
            or receipt.get("workflow_run_id") != v.get("workflow_run_id")
            or receipt.get("task_map_generation") != v.get("task_map_generation")
            or receipt.get("task_commit") != v.get("target_commit")
            or receipt.get("contract_revision") != v.get("contract_revision")
            or receipt.get("new_candidate_head") != done.payload.get("candidate_head")):
        raise ValueError("completion reuse integration identity differs")


def _settled_redispatches(root: Path, events: list[ZfEvent], source: ZfEvent, task_id: str, head: str) -> None:
    for event in events[events.index(source) + 1:]:
        if event.type != "task.pipeline.stage.dispatched" or event.task_id != task_id:
            continue
        p = event.payload
        lifecycle = next((e for e in reversed(events) if e.type in {"loop.started", "loop.stopped"}), None)
        if lifecycle is None or lifecycle.type != "loop.stopped" or events.index(lifecycle) < events.index(event):
            raise ValueError("completion reuse requires stopped runtime after redispatch")
        from zf.runtime.workflow_operation import reduce_workflow_operations

        operation = reduce_workflow_operations(events).get(p.get("operation_id"), {})
        if operation.get("status") not in {"suspended", "cancelled", "settled", "failed"}:
            raise ValueError("completion reuse has a live execution attempt: " + task_id)
        workdir = Path(p.get("workdir") or "")
        if not workdir.is_dir() or _git_commit(workdir, "HEAD") != head:
            raise ValueError("completion reuse redispatched worktree changed: " + task_id)
        result = subprocess.run(["git", "-C", str(workdir), "status", "--porcelain", "--untracked-files=normal"],
                                env=git_environment(), capture_output=True, text=True, timeout=20, check=False)
        if result.returncode or result.stdout.strip():
            raise ValueError("completion reuse redispatched worktree is dirty: " + task_id)


def apply(service: Any, source: ZfEvent, payload: dict, requested: ZfEvent) -> dict:
    manifest = preview(service, source)
    if (not str(payload.get("approval_ref") or "").strip()
            or not str(payload.get("reuse_rationale") or "").strip()
            or payload.get("reviewed_digest") != manifest["manifest_digest"]):
        raise ValueError("completion reuse requires a reviewed digest, semantic rationale and approval_ref")
    receipt = {"schema_version": SCHEMA, "manifest": manifest,
               "approval_ref": payload["approval_ref"], "rationale": payload["reuse_rationale"]}
    descriptor = write_immutable_json_sidecar(service.state_dir, receipt, root="plan-completion-reuse",
        kind="plan_completion_reuse", schema_version=SCHEMA, created_by="controlled-plan-recovery")
    from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations
    from zf.runtime.task_attempt_operation_settlement import settle_terminal_operation_attempt

    operations = WorkflowOperationService(state_dir=service.state_dir, event_log=service.writer.event_log,
                                          event_writer=service.writer)
    runtime = SimpleNamespace(state_dir=service.state_dir, event_log=service.writer.event_log,
                              event_writer=service.writer, config=service.config,
                              project_root=service.project_root or service.state_dir.parent)
    for operation in reduce_workflow_operations(service.writer.event_log.read_all()).values():
        if (operation.get("workflow_run_id") == manifest["workflow_run_id"]
                and operation.get("task_map_generation") == manifest["task_map_generation"]
                and operation.get("task_pipeline_stage") and operation.get("status") == "suspended"):
            cancelled = operations.cancel(operation_id=operation["operation_id"],
                request_hash=operation["request_hash"], workflow_run_id=manifest["workflow_run_id"],
                task_id=operation["task_id"], reason=SOURCE, causation_id=requested.id)
            if cancelled:
                settle_terminal_operation_attempt(runtime, cancelled)
    store = TaskStore(service.state_dir / "kanban.json")
    from zf.runtime.task_contract_snapshot import current_task_contract_identity

    for row in manifest["tasks"]:
        task = store.get(row["task_id"])
        if task is None or task.status not in {"backlog", "done"} or task.active_dispatch_id:
            raise ValueError("completion reuse Task no longer inactive")
        if current_task_contract_identity(task) != row["current_identity"]:
            raise ValueError("Task changed during completion reuse: " + task.id)
        if task.status == "done":
            if task.completed_at != row["completed_at"]:
                raise ValueError("Task completion changed during reuse: " + task.id)
        else:
            _, applied = store.compare_and_update_fields(task.id,
                expected_authority_revision=row["current_identity"]["contract_authority_revision"],
                expected_status="backlog", updates={"status": "done", "completed_at": row["completed_at"],
                                                    "assigned_to": None, "active_dispatch_id": ""})
            if not applied:
                raise ValueError("Task changed during completion reuse: " + task.id)
        restored = service.writer.append(ZfEvent(type="task.status_changed", origin="kernel", actor=service.actor,
            task_id=task.id, correlation_id=manifest["workflow_run_id"], causation_id=requested.id,
            payload={"from": task.status, "to": "done", "status": "done", "source": SOURCE,
                "workflow_run_id": manifest["workflow_run_id"],
                "generation_admitted_event_id": manifest["generation_admitted_event_id"],
                "prior_generation_event_id": manifest["prior_generation_event_id"],
                "completion_event_id": row["completion_event_id"], "completion_reuse_ref": descriptor}))
        from zf.core.state.role_sessions import RoleSessionRegistry
        from zf.runtime.task_pipeline_terminal import _archive_settled_task_sessions

        registry = RoleSessionRegistry(service.state_dir / "role_sessions.yaml", project_root=str(runtime.project_root))
        context = {"workflow_run_id": manifest["workflow_run_id"], "task_map_generation": manifest["task_map_generation"]}
        bindings = [b for b in registry.task_stage_bindings().values()
                    if b.get("task_id") == task.id and b.get("workflow_run_id") == manifest["workflow_run_id"]
                    and str(b.get("rework_affinity_id") or "").startswith(manifest["task_map_generation"] + ":")]
        if bindings and any(b.get("status") != "archived" for b in bindings):
            if not _archive_settled_task_sessions(runtime, task_id=task.id, context=context,
                    integration_event=restored, registry=registry, events=service.writer.event_log.read_all()):
                raise ValueError("completion reuse cannot release stopped Task sessions: " + task.id)
    return {**source.payload, "completion_reuse_ref": descriptor}


def current_proofs(runtime: Any, events: list[ZfEvent], candidate: ZfEvent) -> dict:
    """Recheck exact current identity before accepting historical child authority."""
    from zf.runtime.task_contract_snapshot import current_task_contract_identity

    generation = candidate.payload.get("task_map_generation")
    run_id = candidate.payload.get("workflow_run_id")
    proofs = {}
    for event in events:
        if event.type != "task.status_changed" or event.origin != "kernel" or event.payload.get("source") != SOURCE:
            continue
        if event.payload.get("workflow_run_id") != run_id:
            continue
        receipt = _json(runtime.state_dir, event.payload.get("completion_reuse_ref") or {})
        manifest = receipt.get("manifest") or {}
        if manifest.get("task_map_generation") != generation:
            continue
        if (receipt.get("schema_version") != SCHEMA or not receipt.get("approval_ref")
                or not receipt.get("rationale") or manifest.get("manifest_digest") != digest({
                    k: v for k, v in manifest.items() if k != "manifest_digest"})
                or manifest.get("candidate_head_commit") != candidate.payload.get("candidate_head_commit")
                or manifest.get("candidate_base_commit") != candidate.payload.get("candidate_base_commit")
                or manifest.get("plan_package_ref", {}).get("sha256") != candidate.payload.get("plan_artifact_package_digest")):
            raise ValueError("completion reuse receipt no longer binds the Candidate")
        row = next((r for r in manifest["tasks"] if r["task_id"] == event.task_id), None)
        task = runtime.task_store.get(event.task_id)
        if (not row or task is None or task.status != "done"
                or row["current_identity"] != current_task_contract_identity(task)
                or row["current_contract"] != asdict(task.contract)):
            raise ValueError("completion reuse Task authority changed")
        proofs[event.task_id] = {**row, "task_map_generation": manifest["prior_task_map_generation"],
                                 "completion_reuse_ref": event.payload["completion_reuse_ref"]}
    return proofs
