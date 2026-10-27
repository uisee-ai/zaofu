"""Recover an approved Plan only from a matching failed-writer resume lineage."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from zf.runtime.plan_preview_artifacts import read_preview_artifact


def resumed_plan_refs(
    payload: Mapping[str, Any], events: Iterable[Any], *, state_dir: Path,
    project_root: Path | None,
) -> dict[str, str]:
    if payload.get("source") != "workflow_resume_batch" or payload.get("resume_scope") != "failed_children_only":
        return {}
    run = str(payload.get("workflow_run_id") or "")
    fanout_id = str(payload.get("upstream_fanout_id") or "")
    failure_id = str(payload.get("rework_of") or "")
    keys = ("task_map_ref", "task_map_generation", "plan_artifact_package_digest", "requirement_spec_digest")
    if not run or not fanout_id or not failure_id or any(not payload.get(k) for k in keys):
        return {}

    events = list(events)
    def body(event: Any) -> dict:
        value = event.get("payload") if isinstance(event, Mapping) else getattr(event, "payload", None)
        return value if isinstance(value, dict) else {}
    def field(event: Any, name: str) -> str:
        return str(event.get(name) or "") if isinstance(event, Mapping) else str(getattr(event, name, "") or "")
    def matches(candidate: Mapping[str, Any], fields: tuple[str, ...]) -> bool:
        return all(str(candidate.get(k) or "") == str(payload.get(k) or "") for k in fields)

    failure = next((e for e in events if field(e, "id") == failure_id), None)
    if failure is None:
        return {}
    failed = body(failure)
    if (str(failed.get("fanout_id") or "") != fanout_id
        or str(failed.get("workflow_run_id") or failed.get("trace_id") or "") != run
        or not matches(failed, ("task_map_ref", "plan_artifact_package_digest", "requirement_spec_digest"))):
        return {}
    fanout = next((e for e in reversed(events) if field(e, "type") == "fanout.started"
                   and str(body(e).get("fanout_id") or "") == fanout_id), None)
    if fanout is None or str(body(fanout).get("workflow_run_id") or body(fanout).get("trace_id") or "") != run:
        return {}
    if not matches(body(fanout), ("task_map_ref",)):
        return {}
    origin_id = str(body(fanout).get("trigger_event_id") or "")
    origin = next((e for e in events if field(e, "id") == origin_id and field(e, "type") == "task_map.ready"), None)
    if origin is None or str(body(origin).get("workflow_run_id") or body(origin).get("trace_id") or "") != run or not matches(body(origin), keys):
        return {}
    requests = [e for e in events if field(e, "type") == "plan.approval.requested" and body(e).get("plan_id") == origin_id]
    if not requests:
        return {}
    request = requests[-1]
    prior = body(request)
    if (str(prior.get("workflow_run_id") or prior.get("trace_id") or "") != run
        or not matches(prior, ("task_map_ref", "plan_artifact_package_digest", "requirement_spec_ref"))):
        return {}
    resolutions = [e for e in events[events.index(request) + 1:]
                   if field(e, "type") in {"plan.approved", "plan.rejected"} and body(e).get("plan_id") == origin_id]
    if not resolutions or field(resolutions[-1], "type") != "plan.approved":
        return {}
    if not (events.index(origin) < events.index(request) < events.index(resolutions[-1])
            < events.index(fanout) < events.index(failure)):
        return {}
    ref = str(prior.get("plan_artifact_ref") or "")
    digest = str(prior.get("plan_artifact_digest") or "")
    task_map_digest = str(body(origin).get("task_map_digest") or "")
    if not ref or not digest or not task_map_digest or str(body(origin).get("plan_artifact_ref") or "") != ref:
        return {}
    try:
        read_preview_artifact(ref, state_dir=state_dir, project_root=project_root, digest=digest)
        read_preview_artifact(str(payload["task_map_ref"]), state_dir=state_dir,
                              project_root=project_root, digest=task_map_digest)
    except (OSError, ValueError, UnicodeError):
        return {}
    return {"plan_artifact_ref": ref, "plan_artifact_digest": digest}
