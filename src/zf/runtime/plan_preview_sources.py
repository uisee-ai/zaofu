"""Shared Plan source binding, independent of heavy review rendering."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.runtime.plan_preview_artifacts import PLAN_REVIEW_REF_FIELDS


class PlanSourceError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


def bound_plan_sources(state_dir: Path, events: list, *, plan_id: str,
                       project_root: Path | None) -> tuple[dict, list[dict], dict]:
    """Resolve immutable catalog bindings without rendering any review body."""
    from zf.runtime.operator_plan_preview import _plan_events
    from zf.runtime.plan_preview_artifacts import preview_package_artifacts, read_plan_preview_bundle

    requested, resolved = _plan_events(events, plan_id)
    if requested is None and resolved is None:
        raise PlanSourceError(404, "plan not found")
    payload, _ = plan_source_payload(state_dir, events, plan_id=plan_id,
        source_event=requested or resolved, project_root=project_root)
    ports, error = preview_package_artifacts(payload, state_dir=state_dir, project_root=project_root)
    bundle, bundle_error = read_plan_preview_bundle(payload, state_dir=state_dir, project_root=project_root)
    if error or bundle_error:
        raise PlanSourceError(409, "plan source binding changed or is unavailable")
    return payload, ports, bundle


def bound_plan_source(state_dir: Path, events: list, *, plan_id: str, name: str,
                      project_root: Path | None) -> dict:
    payload, ports, bundle = bound_plan_sources(state_dir, events, plan_id=plan_id, project_root=project_root)
    matches = [row for row in ports if row["name"] == name]
    if len(matches) > 1:
        raise PlanSourceError(409, "ambiguous plan source binding")
    item = matches[0] if matches else None
    if name == "accepted_plan":
        # Legacy canonical-Plan recovery remains owned by the full projection.
        from zf.runtime.operator_plan_preview import build_plan_preview
        preview = build_plan_preview(state_dir, events, plan_id=plan_id, project_root=project_root)
        item = next((row for row in preview.get("artifacts", []) if row["name"] == name), None)
    elif item is None:
        authority = plan_authority_refs(state_dir, payload=payload, artifacts=ports,
            preview_bundle=bundle, project_root=project_root)
        for index, ref in enumerate(authority, 1):
            if name == f"requirement_{index}_{Path(ref).name}":
                item = {"name": name, "ref": ref, "sha256": ""}
                break
    if item is None:
        raise PlanSourceError(404, "source is not bound to this plan")
    return item


def plan_source_payload(state_dir: Path, events: list, *, plan_id: str, source_event: Any,
                        project_root: Path | None) -> tuple[dict, Any]:
    from zf.runtime.operator_plan_preview import _payload, _event_id, _etype

    payload = dict(_payload(source_event))
    if str(payload.get("owner_route") or "") == "plan_synth_owner_decision":
        payload.setdefault("plan_id", str(payload.get("source_event_id") or plan_id))
    if (
        str(payload.get("owner_route") or "") == "plan_synth_owner_decision"
        and (not payload.get("plan_artifact_ref") or not payload.get("task_map_ref"))
    ):
        source_id = str(payload.get("source_event_id") or "").strip()
        candidate = next(
            (
                event for event in events
                if _event_id(event) == source_id
                and isinstance(_payload(event), dict)
            ),
            None,
        )
        if candidate is not None:
            from zf.runtime.plan_synth_owner_checkpoint import plan_preview_refs

            refs = plan_preview_refs(
                _payload(candidate),
                state_dir=state_dir,
                project_root=project_root,
            )
            payload.update({
                key: value for key, value in refs.items()
                if value and not payload.get(key)
            })
    bound_source = next((event for event in events if _event_id(event) == plan_id
                         and _etype(event) == "task_map.ready"), None)
    if bound_source is not None:
        original = _payload(bound_source)
        if any(payload.get(key) and original.get(key) and payload[key] != original[key]
               for key in ("task_map_ref", "pdd_id", "trace_id", "workflow_run_id",
                           "plan_artifact_package_digest", "requirement_spec_ref")):
            bound_source = None
        else:
            for key in (*PLAN_REVIEW_REF_FIELDS, "task_map_ref", "digest_ref"):
                if not payload.get(key) and original.get(key):
                    payload[key] = original[key]
            # Semantic approval events may omit the PRD identity fields while
            # task_map.ready remains the bound source of truth.
            for key in ("stage_id", "flow_kind", "request_kind", "target_ref", "pdd_id", "task_count"):
                if not payload.get(key) and original.get(key):
                    payload[key] = original[key]
            if not payload.get("plan_artifact_ref"):
                from zf.runtime.resume_plan_preview import resumed_plan_refs

                payload.update(resumed_plan_refs(
                    original, events[:events.index(bound_source) + 1],
                    state_dir=state_dir, project_root=project_root,
                ))
    return payload, bound_source


def plan_authority_refs(state_dir: Path, *, payload: dict, artifacts: list[dict],
                        preview_bundle: dict, project_root: Path | None) -> list[str]:
    from zf.runtime.operator_plan_preview import _source_index_authority_refs

    authority_refs = [
        str(item) for item in (
            preview_bundle.get("authority_refs")
            or payload.get("authority_refs")
            or []
        ) if str(item).strip()
    ]
    if not authority_refs:
        authority_refs = _source_index_authority_refs(
            state_dir,
            project_root=project_root,
            source_index_ref=str(payload.get("source_index_ref") or ""),
            source_index_digest=next(
                (p["sha256"] for p in artifacts if p["name"] == "source_index"),
                "",
            ),
        )
    if not authority_refs:
        # The admitted Package already binds these authority ports and digests;
        # source_index need not repeat the same refs as optional metadata.
        authority_refs = list(dict.fromkeys(
            item["ref"] for item in artifacts
            if item["name"] in {"requirement_spec", "issue_spec"}
        ))
    return authority_refs
