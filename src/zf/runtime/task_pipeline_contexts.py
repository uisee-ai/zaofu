"""Replay Task Pipeline generation ownership from canonical events."""

from __future__ import annotations

from typing import Any, Iterable
import re

from zf.core.events.model import ZfEvent


TASK_PIPELINE_GENERATION_SCHEMA = "task-pipeline-generation.v1"
TASK_PIPELINE_GENERATION_ADMITTED = "task.pipeline.generation.admitted"
CANDIDATE_FREEZE_RECEIPT_SCHEMA = "candidate-freeze-receipt.v1"


def latest_task_pipeline_generation_context(
    events: Iterable[ZfEvent],
    *,
    workflow_run_id: str,
) -> dict[str, Any] | None:
    """Return the latest admitted Task Pipeline generation for one Run."""

    run_id = str(workflow_run_id or "").strip()
    if not run_id:
        return None
    latest: dict[str, Any] | None = None
    for event in events:
        if event.type != TASK_PIPELINE_GENERATION_ADMITTED:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            str(payload.get("schema_version") or "")
            != TASK_PIPELINE_GENERATION_SCHEMA
        ):
            continue
        admitted_run_id = str(
            payload.get("workflow_run_id") or event.correlation_id or ""
        ).strip()
        if admitted_run_id != run_id:
            continue
        latest = {
            **payload,
            "generation_admitted_event_id": event.id,
            "generation_admitted_at": event.ts,
        }
    return latest


def task_pipeline_generation_is_current(
    events: Iterable[ZfEvent],
    *,
    workflow_run_id: str,
    generation_id: str,
) -> bool:
    """Check exact generation currentness within one Workflow Run."""

    claimed_generation_id = str(generation_id or "").strip()
    if not claimed_generation_id:
        return False
    latest = latest_task_pipeline_generation_context(
        events,
        workflow_run_id=workflow_run_id,
    )
    return bool(
        latest
        and str(latest.get("generation_id") or "") == claimed_generation_id
    )


def task_pipeline_generation_contexts(
    events: Iterable[ZfEvent],
) -> dict[str, dict[str, Any]]:
    """Project the latest admitted generation context for each Task."""

    contexts: dict[str, dict[str, Any]] = {}
    admitted: dict[str, dict[str, Any]] = {}
    for event in events:
        if (event.type == "task.status_changed" and event.origin == "kernel"
                and event.payload.get("source") == "plan_completion_reuse"):
            current = contexts.get(str(event.task_id or ""), {})
            previous = admitted.get(str(event.payload.get("prior_generation_event_id") or ""))
            if (previous and event.task_id in previous.get("task_ids", [])
                    and current.get("generation_admitted_event_id") == event.payload.get("generation_admitted_event_id")
                    and previous.get("workflow_run_id") == current.get("workflow_run_id")
                    and event.payload.get("completion_reuse_ref")):
                restored = dict(previous)
                restored["completion_reuse_ref"] = event.payload["completion_reuse_ref"]
                if previous.get("task_dispatch_base_commits"):
                    restored["dispatch_base_commit"] = previous["task_dispatch_base_commits"][event.task_id]
                contexts[event.task_id] = restored
            continue
        if event.type != TASK_PIPELINE_GENERATION_ADMITTED:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("schema_version") or "") != TASK_PIPELINE_GENERATION_SCHEMA:
            continue
        context = {
            **payload,
            "generation_admitted_event_id": event.id,
            "generation_admitted_at": event.ts,
        }
        admitted[event.id] = context
        bases = payload.get("task_dispatch_base_commits")
        if bases is not None and (
            not isinstance(bases, dict)
            or set(bases) != set(payload.get("task_ids") or [])
            or any(not isinstance(base, str) or not re.fullmatch(
                r"[0-9a-f]{40}|[0-9a-f]{64}", base
            ) for base in bases.values())
        ):
            raise ValueError("invalid admitted task dispatch bases")
        for raw_task_id in payload.get("task_ids") or []:
            task_id = str(raw_task_id or "").strip()
            if task_id:
                contexts[task_id] = dict(context)
                # Compiled from the admitted Task Map, not a new task contract.
                # Legacy events retain their generation-wide baseline.
                if bases is not None:
                    contexts[task_id]["dispatch_base_commit"] = bases[task_id]
    return contexts


def task_pipeline_managed_task_ids(runtime: Any) -> set[str]:
    """Return Tasks fenced away from the legacy backlog dispatcher."""

    return set(task_pipeline_generation_contexts(runtime.event_log.read_all()))


__all__ = [
    "CANDIDATE_FREEZE_RECEIPT_SCHEMA",
    "TASK_PIPELINE_GENERATION_ADMITTED",
    "TASK_PIPELINE_GENERATION_SCHEMA",
    "latest_task_pipeline_generation_context",
    "task_pipeline_generation_contexts",
    "task_pipeline_generation_is_current",
    "task_pipeline_managed_task_ids",
]
