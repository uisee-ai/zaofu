"""Task terminal and Candidate freeze reconciliation for Task Pipeline v4."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.git_capture import git_env
from zf.runtime.task_pipeline_contexts import (
    CANDIDATE_FREEZE_RECEIPT_SCHEMA,
    task_pipeline_generation_is_current,
)
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


def reconcile_task_pipeline_terminals(
    runtime: Any,
    *,
    generation_contexts: Mapping[str, Mapping[str, Any]],
) -> list[WorkflowRuntimeDecision]:
    """Converge admitted integration receipts into TaskStore terminal state."""

    decisions: list[WorkflowRuntimeDecision] = []
    events = runtime.event_log.read_all()
    archived_session_keys = {
        (
            str(event.payload.get("workflow_run_id") or ""),
            str(event.payload.get("task_map_generation") or ""),
            str(event.task_id or ""),
        )
        for event in events
        if event.type == "task.pipeline.sessions.archived"
        and isinstance(event.payload, dict)
    }
    session_registry: RoleSessionRegistry | None = None
    for task_id, context in sorted(generation_contexts.items()):
        task = runtime.task_store.get(task_id)
        archive_key = (
            str(context.get("workflow_run_id") or ""),
            str(context.get("task_map_generation") or ""),
            task_id,
        )
        if (
            task is not None
            and str(task.status) in {"done", "cancelled"}
            and archive_key in archived_session_keys
        ):
            continue
        if task is not None and str(task.status) == "cancelled":
            superseded = _authorized_supersession_event(
                events,
                task_id=task_id,
                context=context,
            )
            if superseded is not None:
                if session_registry is None:
                    session_registry = RoleSessionRegistry(
                        Path(runtime.state_dir) / "role_sessions.yaml",
                        project_root=str(runtime.project_root),
                    )
                archived = _archive_settled_task_sessions(
                    runtime,
                    task_id=task_id,
                    context=context,
                    integration_event=superseded,
                    registry=session_registry,
                    events=events,
                )
            else:
                archived = False
            if archived:
                archived_session_keys.add(archive_key)
                decisions.append(WorkflowRuntimeDecision(
                    action="task_pipeline_sessions_archived",
                    task_id=task_id,
                    reason="admitted Task Map supersession -> sessions archived",
                ))
            continue
        integrated = _latest_integration_event(
            events,
            task_id=task_id,
            context=context,
        )
        if integrated is None:
            continue
        payload = integrated.payload if isinstance(integrated.payload, dict) else {}
        descriptor = payload.get("receipt_ref")
        if not isinstance(descriptor, Mapping):
            continue
        from zf.runtime.candidate_incremental import (
            hydrate_task_integration_receipt,
        )
        from zf.runtime.candidates import CandidateRebuilder

        rebuilder = CandidateRebuilder(
            state_dir=Path(runtime.state_dir),
            project_root=Path(runtime.project_root),
            config=runtime.config,
            event_log=runtime.event_log,
        )
        try:
            receipt = hydrate_task_integration_receipt(
                rebuilder,
                descriptor,
                task_id=task_id,
                workflow_run_id=str(context.get("workflow_run_id") or ""),
                task_map_generation=str(context.get("task_map_generation") or ""),
            )
        except Exception:
            continue
        task = runtime.task_store.get(task_id)
        if task is None:
            continue
        if str(task.status) != "done":
            runtime.task_store.update(
                task_id,
                status="done",
                assigned_to=None,
                active_dispatch_id="",
                completed_at=_now_iso(),
            )
            decisions.append(WorkflowRuntimeDecision(
                action="task_pipeline_task_done",
                task_id=task_id,
                reason="admitted Task integration receipt -> done",
            ))
        _emit_task_done_once(
            runtime,
            task_id=task_id,
            receipt=receipt,
            receipt_ref=dict(descriptor),
            integration_event=integrated,
        )
        if session_registry is None:
            session_registry = RoleSessionRegistry(
                Path(runtime.state_dir) / "role_sessions.yaml",
                project_root=str(runtime.project_root),
            )
        if _archive_settled_task_sessions(
            runtime,
            task_id=task_id,
            context=context,
            integration_event=integrated,
            registry=session_registry,
            events=events,
        ):
            archived_session_keys.add(archive_key)
            decisions.append(WorkflowRuntimeDecision(
                action="task_pipeline_sessions_archived",
                task_id=task_id,
                reason="settled Task-stage sessions archived",
            ))
    return decisions


def reconcile_task_pipeline_freeze(
    runtime: Any,
    *,
    generation_contexts: Mapping[str, Mapping[str, Any]],
) -> list[WorkflowRuntimeDecision]:
    """Freeze a quiescent generation once all exact Task receipts are current."""

    generations: dict[str, dict[str, Any]] = {}
    for task_id, context in generation_contexts.items():
        generation_id = str(context.get("generation_id") or "")
        if not generation_id:
            continue
        entry = generations.setdefault(
            generation_id,
            {"context": dict(context), "task_ids": []},
        )
        entry["task_ids"].append(task_id)
    decisions: list[WorkflowRuntimeDecision] = []
    for generation_id, entry in sorted(generations.items()):
        context = entry["context"]
        task_ids = sorted(set(entry["task_ids"]))
        frozen = _freeze_generation(
            runtime,
            generation_id=generation_id,
            context=context,
            task_ids=task_ids,
        )
        if frozen:
            decisions.append(WorkflowRuntimeDecision(
                action="task_pipeline_candidate_frozen",
                reason=f"Candidate generation {generation_id} frozen",
            ))
    return decisions


def task_pipeline_workspace_base(
    runtime: Any,
    *,
    task: Any,
    generation_context: Mapping[str, Any],
) -> str:
    """Start newly admitted work from the newest linear Candidate head."""

    fallback = str(generation_context.get("dispatch_base_commit") or "")
    workflow_run_id = str(generation_context.get("workflow_run_id") or "")
    candidate_head = ""
    for event in reversed(runtime.event_log.read_all()):
        if event.type != "candidate.updated":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            payload.get("incremental") is True
            and str(payload.get("workflow_run_id") or "") == workflow_run_id
        ):
            candidate_head = str(payload.get("candidate_head") or "").strip()
            if candidate_head:
                break
    if not candidate_head or candidate_head == fallback:
        return candidate_head or fallback

    project_root = Path(runtime.project_root)
    fallback_before_candidate = _commit_is_ancestor(
        project_root,
        ancestor=fallback,
        descendant=candidate_head,
    )
    if fallback_before_candidate is True:
        return candidate_head
    candidate_before_fallback = _commit_is_ancestor(
        project_root,
        ancestor=candidate_head,
        descendant=fallback,
    )
    if candidate_before_fallback is True:
        return fallback

    from zf.runtime.task_pipeline_runtime import TaskPipelineRuntimeError

    if fallback_before_candidate is None or candidate_before_fallback is None:
        raise TaskPipelineRuntimeError(
            "Task Pipeline workspace base lineage is not resolvable: "
            f"dispatch_base={fallback!r}, candidate_head={candidate_head!r}"
        )
    raise TaskPipelineRuntimeError(
        "Task Pipeline workspace base diverged from the current Candidate: "
        f"dispatch_base={fallback}, candidate_head={candidate_head}"
    )


def _commit_is_ancestor(
    project_root: Path,
    *,
    ancestor: str,
    descendant: str,
) -> bool | None:
    if not ancestor or not descendant:
        return None
    if ancestor == descendant:
        return True
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _freeze_generation(
    runtime: Any,
    *,
    generation_id: str,
    context: Mapping[str, Any],
    task_ids: list[str],
) -> bool:
    events = runtime.event_log.read_all()
    workflow_run_id = str(context.get("workflow_run_id") or "")
    task_map_generation = str(context.get("task_map_generation") or "")
    if not task_pipeline_generation_is_current(
        events,
        workflow_run_id=workflow_run_id,
        generation_id=generation_id,
    ):
        return False
    integrated: list[tuple[ZfEvent, dict[str, Any], dict[str, Any]]] = []
    completed_task_ids: list[str] = []
    superseded: list[ZfEvent] = []
    from zf.runtime.candidate_incremental import hydrate_task_integration_receipt
    from zf.runtime.candidates import CandidateRebuilder

    rebuilder = CandidateRebuilder(
        state_dir=Path(runtime.state_dir),
        project_root=Path(runtime.project_root),
        config=runtime.config,
        event_log=runtime.event_log,
    )
    for task_id in task_ids:
        task = runtime.task_store.get(task_id)
        if task is None:
            return False
        if str(task.status) == "cancelled":
            supersession = _authorized_supersession_event(
                events,
                task_id=task_id,
                context=context,
            )
            if supersession is None:
                return False
            superseded.append(supersession)
            continue
        if str(task.status) != "done":
            return False
        event = _latest_integration_event(
            events,
            task_id=task_id,
            context=context,
        )
        if event is None:
            return False
        payload = event.payload if isinstance(event.payload, dict) else {}
        descriptor = payload.get("receipt_ref")
        if not isinstance(descriptor, Mapping):
            return False
        try:
            receipt = hydrate_task_integration_receipt(
                rebuilder,
                descriptor,
                task_id=task_id,
                workflow_run_id=workflow_run_id,
                task_map_generation=task_map_generation,
            )
        except Exception:
            return False
        integrated.append((event, receipt, dict(descriptor)))
        completed_task_ids.append(task_id)
    if (
        not integrated
        or _generation_has_active_work(runtime, context, task_ids)
        or not _generation_sessions_archived(
            events,
            workflow_run_id=workflow_run_id,
            task_map_generation=task_map_generation,
            task_ids=task_ids,
        )
    ):
        return False

    branches = {str(receipt.get("candidate_branch") or "") for _, receipt, _ in integrated}
    candidate_generations = {
        str(receipt.get("candidate_generation") or "")
        for _, receipt, _ in integrated
    }
    if len(branches) != 1 or len(candidate_generations) != 1:
        return False
    branch = next(iter(branches))
    candidate_generation = next(iter(candidate_generations))
    candidate_head = _git(
        Path(runtime.project_root),
        "rev-parse",
        "--verify",
        f"refs/heads/{branch}^{{commit}}",
    )
    event_order = {event.id: index for index, event in enumerate(events)}
    latest_event, latest_receipt, _ = max(
        integrated,
        key=lambda row: event_order.get(row[0].id, -1),
    )
    latest_receipt_head = str(latest_receipt.get("new_candidate_head") or "")
    if candidate_head != latest_receipt_head:
        return False
    receipt_refs = [descriptor for _, _, descriptor in integrated]
    fanout_id = str(
        context.get("fanout_id")
        or f"task-pipeline-{generation_id}"
    )
    candidate_base_commit = str(
        context.get("dispatch_base_commit") or ""
    )
    flow_kind = str(context.get("flow_kind") or "").strip()
    pdd_id = str(
        context.get("pdd_id")
        or context.get("feature_id")
        or branch.split("/", 1)[-1]
    ).strip()
    feature_id = str(
        context.get("feature_id")
        or context.get("pdd_id")
        or pdd_id
    ).strip()
    ledger_digest = _digest([
        {
            "task_id": event.task_id,
            "event_id": event.id,
            "receipt_digest": descriptor.get("sha256"),
            "candidate_head": receipt.get("new_candidate_head"),
        }
        for event, receipt, descriptor in integrated
    ])
    supersession_ledger_digest = _digest([
        {
            "task_id": event.task_id,
            "event_id": event.id,
            "superseded_by_task_map_ref": (
                event.payload.get("superseded_by_task_map_ref")
                if isinstance(event.payload, dict)
                else ""
            ),
        }
        for event in superseded
    ])
    freeze_id = _digest({
        "workflow_run_id": workflow_run_id,
        "task_map_generation": task_map_generation,
        "candidate_generation": candidate_generation,
        "candidate_head": candidate_head,
        "integration_ledger_digest": ledger_digest,
        "supersession_ledger_digest": supersession_ledger_digest,
    })
    if _candidate_ready_exists(events, freeze_id):
        return False
    receipt = {
        "schema_version": CANDIDATE_FREEZE_RECEIPT_SCHEMA,
        "freeze_id": freeze_id,
        "generation_id": generation_id,
        "workflow_run_id": workflow_run_id,
        "flow_kind": flow_kind,
        "request_kind": str(context.get("request_kind") or flow_kind).strip(),
        "pdd_id": pdd_id,
        "feature_id": feature_id,
        "fanout_id": fanout_id,
        "task_map_generation": task_map_generation,
        "plan_artifact_package_id": str(
            context.get("plan_artifact_package_id") or ""
        ),
        "plan_artifact_package_ref": str(
            context.get("plan_artifact_package_ref") or ""
        ),
        "plan_artifact_package_digest": str(
            context.get("plan_artifact_package_digest") or ""
        ),
        "task_map_ref": str(context.get("task_map_ref") or ""),
        "task_map_digest": str(context.get("task_map_digest") or ""),
        "source_index_ref": str(context.get("source_index_ref") or ""),
        "candidate_generation": candidate_generation,
        "candidate_branch": branch,
        "candidate_ref": f"refs/heads/{branch}",
        "candidate_base_commit": candidate_base_commit,
        "candidate_head": candidate_head,
        "candidate_head_commit": candidate_head,
        "diff_ref": (
            f"{candidate_base_commit}..{candidate_head}"
            if candidate_base_commit
            else f"refs/heads/{branch}"
        ),
        "integration_ledger_digest": ledger_digest,
        "task_ids": task_ids,
        "completed_task_ids": completed_task_ids,
        "superseded_task_ids": sorted(
            str(event.task_id or "") for event in superseded
        ),
        "task_supersession_event_ids": [event.id for event in superseded],
        "supersession_ledger_digest": supersession_ledger_digest,
        "task_integration_receipt_refs": receipt_refs,
        "status": "frozen",
    }
    descriptor = write_immutable_json_sidecar(
        Path(runtime.state_dir),
        receipt,
        root="candidate-freeze-receipts",
        kind="candidate_freeze_receipt",
        schema_version=CANDIDATE_FREEZE_RECEIPT_SCHEMA,
        created_by="task-pipeline-freezer",
        source_event_id=latest_event.id,
    )
    runtime.event_writer.append(ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        origin="kernel",
        payload={
            **receipt,
            "commit": candidate_head,
            "target_commit": candidate_head,
            "freeze_receipt_ref": descriptor,
            "freeze_receipt_digest": str(descriptor.get("sha256") or ""),
        },
        causation_id=latest_event.id,
        correlation_id=workflow_run_id,
    ))
    return True


def _generation_has_active_work(
    runtime: Any,
    context: Mapping[str, Any],
    task_ids: list[str],
) -> bool:
    from zf.runtime.task_attempt_runtime import task_attempt_store
    from zf.runtime.workflow_operation import reduce_workflow_operations

    workflow_run_id = str(context.get("workflow_run_id") or "")
    task_map_generation = str(context.get("task_map_generation") or "")
    active_operations = {
        "requested",
        "reserved",
        "running",
        "suspended",
    }
    for operation in reduce_workflow_operations(runtime.event_log.read_all()).values():
        if (
            str(operation.get("task_id") or "") in task_ids
            and str(operation.get("workflow_run_id") or "") == workflow_run_id
            and str(operation.get("task_map_generation") or "")
            == task_map_generation
            and str(operation.get("status") or "") in active_operations
        ):
            return True
    active_attempts = {"prepared", "delivering", "sent"}
    return any(
        str(row.get("task_id") or "") in task_ids
        and str(row.get("run_id") or "") == workflow_run_id
        and str(row.get("status") or "") in active_attempts
        for row in task_attempt_store(runtime).current_rows()
    )


def _latest_integration_event(
    events: list[ZfEvent],
    *,
    task_id: str,
    context: Mapping[str, Any],
) -> ZfEvent | None:
    for event in reversed(events):
        if event.type != "integration.queue.integrated" or event.task_id != task_id:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            str(payload.get("workflow_run_id") or "")
            == str(context.get("workflow_run_id") or "")
            and str(payload.get("task_map_generation") or "")
            == str(context.get("task_map_generation") or "")
        ):
            return event
    return None


def _authorized_supersession_event(
    events: list[ZfEvent],
    *,
    task_id: str,
    context: Mapping[str, Any],
) -> ZfEvent | None:
    """Return a cancellation backed by a later admitted Task Map amendment."""

    workflow_run_id = str(context.get("workflow_run_id") or "")
    generation_event_id = str(context.get("generation_admitted_event_id") or "")
    event_order = {event.id: index for index, event in enumerate(events)}
    generation_index = event_order.get(generation_event_id, -1)
    for index in range(len(events) - 1, generation_index, -1):
        event = events[index]
        if event.type != "task.superseded" or event.task_id != task_id:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        replacement_ref = str(
            payload.get("superseded_by_task_map_ref") or ""
        ).strip()
        if (
            event.origin != "kernel"
            or str(payload.get("source") or "") != "writer_task_map_adoption"
            or str(payload.get("status") or "") != "cancelled"
            or not replacement_ref
            or (
                workflow_run_id
                and str(event.correlation_id or "") != workflow_run_id
            )
        ):
            continue
        replacement_identity = _artifact_ref_identity(replacement_ref)
        for admitted in events[index + 1:]:
            if admitted.type != "task.pipeline.generation.admitted":
                continue
            admitted_payload = (
                admitted.payload if isinstance(admitted.payload, dict) else {}
            )
            if (
                str(admitted_payload.get("workflow_run_id") or "")
                == workflow_run_id
                and _artifact_ref_identity(
                    str(admitted_payload.get("task_map_ref") or "")
                ) == replacement_identity
            ):
                return event
    return None


def _artifact_ref_identity(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    marker = "artifacts/"
    index = normalized.find(marker)
    return normalized[index:] if index >= 0 else normalized


def _emit_task_done_once(
    runtime: Any,
    *,
    task_id: str,
    receipt: Mapping[str, Any],
    receipt_ref: Mapping[str, Any],
    integration_event: ZfEvent,
) -> None:
    digest = str(receipt_ref.get("sha256") or "")
    for event in reversed(runtime.event_log.read_all()):
        if event.type != "task.done" or event.task_id != task_id:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("integration_receipt_digest") or "") == digest:
            return
    runtime.event_writer.append(ZfEvent(
        type="task.done",
        actor="zf-cli",
        origin="kernel",
        task_id=task_id,
        payload={
            "authority": "task_integration_receipt",
            "workflow_run_id": str(receipt.get("workflow_run_id") or ""),
            "task_map_generation": str(receipt.get("task_map_generation") or ""),
            "candidate_generation": str(receipt.get("candidate_generation") or ""),
            "candidate_head": str(receipt.get("new_candidate_head") or ""),
            "integration_receipt_ref": dict(receipt_ref),
            "integration_receipt_digest": digest,
            "integration_event_id": integration_event.id,
        },
        causation_id=integration_event.id,
        correlation_id=str(receipt.get("workflow_run_id") or "") or None,
    ))


def _archive_settled_task_sessions(
    runtime: Any,
    *,
    task_id: str,
    context: Mapping[str, Any],
    integration_event: ZfEvent,
    registry: RoleSessionRegistry,
    events: list[ZfEvent],
) -> bool:
    """Archive Task-stage transcripts only after all exact work settles."""

    if _generation_has_active_work(runtime, context, [task_id]):
        return False
    workflow_run_id = str(context.get("workflow_run_id") or "")
    task_map_generation = str(context.get("task_map_generation") or "")
    bindings = [
        dict(binding)
        for binding in registry.task_stage_bindings().values()
        if str(binding.get("workflow_run_id") or "") == workflow_run_id
        and str(binding.get("task_id") or "") == task_id
        and str(binding.get("rework_affinity_id") or "").startswith(
            f"{task_map_generation}:"
        )
    ]
    archived: list[dict[str, str]] = []
    for binding in sorted(
        bindings,
        key=lambda row: (
            str(row.get("stage") or ""),
            str(row.get("binding_key") or ""),
        ),
    ):
        identity = {
            "workflow_run_id": workflow_run_id,
            "task_id": task_id,
            "stage": str(binding.get("stage") or ""),
            "rework_affinity_id": str(
                binding.get("rework_affinity_id") or ""
            ),
        }
        current = binding
        if str(current.get("status") or "") != "archived":
            if str(current.get("status") or "") != "sealed":
                current = registry.seal_task_stage_session(**identity) or current
            current = registry.archive_task_stage_session(**identity) or current
        role_instance = str(current.get("current_role_instance") or "")
        binding_key = str(current.get("binding_key") or "")
        if role_instance and binding_key:
            meta = registry.instance_meta().get(role_instance, {})
            if str(meta.get("active_task_stage_binding_key") or "") == binding_key:
                if not _release_archived_task_stage_slot(
                    runtime,
                    registry=registry,
                    role_instance=role_instance,
                    binding_key=binding_key,
                    task_id=task_id,
                ):
                    return False
        archived.append({
            "binding_key": binding_key,
            "session_id": str(current.get("session_id") or ""),
            "stage": str(current.get("stage") or ""),
            "role_instance": role_instance,
            "status": str(current.get("status") or ""),
        })

    closure_id = _digest({
        "workflow_run_id": workflow_run_id,
        "task_map_generation": task_map_generation,
        "task_id": task_id,
        "binding_keys": [row["binding_key"] for row in archived],
    })
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            event.type == "task.pipeline.sessions.archived"
            and str(payload.get("closure_id") or "") == closure_id
        ):
            return False
    runtime.event_writer.append(ZfEvent(
        type="task.pipeline.sessions.archived",
        actor="zf-cli",
        origin="kernel",
        task_id=task_id,
        payload={
            "closure_id": closure_id,
            "workflow_run_id": workflow_run_id,
            "task_map_generation": task_map_generation,
            "bindings": archived,
            "active_binding_count": 0,
        },
        causation_id=integration_event.id,
        correlation_id=workflow_run_id or None,
    ))
    return True


def _release_archived_task_stage_slot(
    runtime: Any,
    *,
    registry: RoleSessionRegistry,
    role_instance: str,
    binding_key: str,
    task_id: str,
) -> bool:
    """Return an idle Task-stage slot to its role-scoped execution context."""

    finder = getattr(runtime, "_find_role_by_instance", None)
    role = finder(role_instance) if callable(finder) else None
    transport = getattr(runtime, "transport", None)
    if role is not None and transport is not None:
        try:
            alive = bool(transport.is_alive(role_instance))
        except Exception:
            alive = False
        if alive:
            try:
                transport.terminate(role_instance)
            except Exception as exc:
                try:
                    still_alive = bool(transport.is_alive(role_instance))
                except Exception:
                    still_alive = True
                if still_alive:
                    runtime.event_writer.append(ZfEvent(
                        type="kernel.housekeeping.failed",
                        actor="orchestrator",
                        origin="kernel",
                        task_id=task_id,
                        payload={
                            "step": "task_pipeline_slot_release",
                            "role_instance": role_instance,
                            "binding_key": binding_key,
                            "exc_type": type(exc).__name__,
                            "exc_repr": repr(exc)[:500],
                        },
                    ))
                    return False

    from zf.runtime.provider_usage_reconciliation import reconcile_provider_usage_tail

    tail = reconcile_provider_usage_tail(
        state_dir=runtime.state_dir, project_root=runtime.project_root,
        config=getattr(runtime, "config", None), event_log=runtime.event_writer.event_log,
        included_roles={role_instance}, capture_phase="session_boundary",
    )
    if tail.failed_roles:
        runtime.event_writer.append(ZfEvent(
            type="kernel.housekeeping.failed", actor="orchestrator", origin="kernel",
            task_id=task_id, payload={"step": "task_pipeline_slot_usage_reconciliation",
                                     "role_instance": role_instance, "binding_key": binding_key,
                                     "exc_type": "ProviderUsageTailError",
                                     "exc_repr": "provider usage tail reconciliation failed"},
        ))
        return False

    released_at = _now_iso()
    registry.update_instance_meta(
        role_instance,
        active_task_stage_binding_key="",
        task_stage_binding_archived_at=released_at,
        lifecycle_state="suspended",
        lifecycle_transition_at=released_at,
        lifecycle_suspended_at=released_at,
        lifecycle_last_error="",
    )
    registry.rotate(role_instance)
    set_state = getattr(runtime, "_set_worker_state", None)
    if callable(set_state):
        set_state(
            role_instance,
            "suspended",
            reason="Task Pipeline stage session archived",
            task_id=task_id,
            force=True,
        )
    emit_lifecycle = getattr(runtime, "_emit_role_lifecycle_event", None)
    if role is not None and callable(emit_lifecycle):
        emit_lifecycle(
            "role.lifecycle.suspended",
            role,
            task_id=task_id,
            payload={
                "from": "active",
                "to": "suspended",
                "reason": "task_stage_session_archived",
                "preserve_session": False,
                "preserve_workdir": True,
            },
        )
    return True


def archive_task_pipeline_stage_binding(
    runtime: Any,
    *,
    binding_key: str,
    task_id: str,
    causation_id: str,
    reason: str,
) -> bool:
    """Archive and release a stage binding that can never be dispatched again."""

    key = str(binding_key or "").strip()
    if not key:
        return False
    registry = RoleSessionRegistry(
        Path(runtime.state_dir) / "role_sessions.yaml",
        project_root=str(runtime.project_root),
    )
    binding = registry.task_stage_bindings().get(key)
    if (
        not isinstance(binding, Mapping)
        or str(binding.get("task_id") or "") != str(task_id or "")
    ):
        return False
    identity = {
        "workflow_run_id": str(binding.get("workflow_run_id") or ""),
        "task_id": str(binding.get("task_id") or ""),
        "stage": str(binding.get("stage") or ""),
        "rework_affinity_id": str(binding.get("rework_affinity_id") or ""),
    }
    current = dict(binding)
    if str(current.get("status") or "") != "archived":
        if str(current.get("status") or "") != "sealed":
            current = registry.seal_task_stage_session(**identity) or current
        current = registry.archive_task_stage_session(**identity) or current
    role_instance = str(current.get("current_role_instance") or "")
    if role_instance:
        meta = registry.instance_meta().get(role_instance, {})
        if str(meta.get("active_task_stage_binding_key") or "") == key:
            if not _release_archived_task_stage_slot(
                runtime,
                registry=registry,
                role_instance=role_instance,
                binding_key=key,
                task_id=str(task_id or ""),
            ):
                return False

    existing = next((
        event
        for event in runtime.event_log.read_all()
        if event.type == "task.pipeline.stage.session.archived"
        and isinstance(event.payload, dict)
        and str(event.payload.get("binding_key") or "") == key
    ), None)
    if existing is not None:
        return False
    runtime.event_writer.append(ZfEvent(
        type="task.pipeline.stage.session.archived",
        actor="orchestrator",
        origin="kernel",
        task_id=str(task_id or "") or None,
        correlation_id=identity["workflow_run_id"] or None,
        causation_id=str(causation_id or "") or None,
        payload={
            "binding_key": key,
            "workflow_run_id": identity["workflow_run_id"],
            "task_id": identity["task_id"],
            "task_pipeline_stage": identity["stage"],
            "rework_affinity_id": identity["rework_affinity_id"],
            "role_instance": role_instance,
            "status": "archived",
            "reason": str(reason or "")[:500],
        },
    ))
    return True


def _generation_sessions_archived(
    events: list[ZfEvent],
    *,
    workflow_run_id: str,
    task_map_generation: str,
    task_ids: list[str],
) -> bool:
    archived = {
        str(event.task_id or "")
        for event in events
        if event.type == "task.pipeline.sessions.archived"
        and isinstance(event.payload, dict)
        and str(event.payload.get("workflow_run_id") or "") == workflow_run_id
        and str(event.payload.get("task_map_generation") or "")
        == task_map_generation
        and int(event.payload.get("active_binding_count") or 0) == 0
    }
    return set(task_ids).issubset(archived)


def _candidate_ready_exists(events: list[ZfEvent], freeze_id: str) -> bool:
    return any(
        event.type == "candidate.ready"
        and isinstance(event.payload, dict)
        and str(event.payload.get("freeze_id") or "") == freeze_id
        for event in events
    )


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=git_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "CANDIDATE_FREEZE_RECEIPT_SCHEMA",
    "archive_task_pipeline_stage_binding",
    "reconcile_task_pipeline_freeze",
    "reconcile_task_pipeline_terminals",
    "task_pipeline_workspace_base",
]
