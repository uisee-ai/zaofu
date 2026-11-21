"""Resolve canonical Plan content without constructing unrelated review bodies."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from zf.runtime.plan_preview_artifacts import read_preview_artifact


def canonical_plan_content(state_dir: Path, events: list[Any], *, payload: dict,
                           bound_source: Any, artifacts: list[dict], preview_bundle: dict,
                           project_root: Path | None) -> tuple[str, str, str, str, dict, str]:
    from zf.runtime.operator_plan_preview import _payload, _event_id, _etype

    digest_ref = str(payload.get("digest_ref") or "")
    plan_ref = str(payload.get("plan_artifact_ref") or payload.get("plan_ref") or payload.get("artifact_ref") or "")
    planning_result = _planning_result_bundle(
        artifacts, state_dir=state_dir, project_root=project_root,
    )
    fanout_id = str(
        payload.get("fanout_id")
        or (bound_source and _payload(bound_source).get("fanout_id"))
        or preview_bundle.get("fanout_id")
        or ""
    )
    # The package may contain a planning-result bundle while the approval
    # event still carries the old logical path (or an accepted_plan port for
    # the Decision Register). Resolve the bundle's canonical Plan first.
    bundle_plan_ref = str(
        preview_bundle.get("plan_ref")
        or planning_result.get("plan_ref")
        or ""
    )
    plan_lookup = bool(bundle_plan_ref or plan_ref)
    plan_refs = _bound_plan_refs(
        bundle_plan_ref or plan_ref,
        fanout_id=fanout_id,
    )
    if bundle_plan_ref and plan_ref and plan_ref != bundle_plan_ref:
        plan_refs.extend(_bound_plan_refs(plan_ref, fanout_id=fanout_id))
    if not plan_refs:
        plan_refs = [plan_ref] if plan_ref else [digest_ref]
    markdown, markdown_ref, markdown_error = _plan_markdown(
        state_dir,
        project_root=project_root,
        refs=plan_refs,
        digest=(
            str(payload.get("plan_artifact_digest") or "")
            if not bundle_plan_ref and plan_ref
            else ""
        ),
    )
    if markdown and markdown_ref and plan_lookup:
        plan_ref = markdown_ref
        payload["plan_artifact_ref"] = markdown_ref
        payload["plan_artifact_digest"] = hashlib.sha256(
            markdown.encode("utf-8")
        ).hexdigest()
    if not markdown and not bundle_plan_ref:
        plan_port = next((p for p in artifacts if p["name"] == "accepted_plan"), None)
        if plan_port:
            plan_lookup = True
            markdown, markdown_ref, markdown_error = _plan_markdown(
                state_dir,
                project_root=project_root,
                refs=[plan_port["ref"]],
                digest=plan_port.get("sha256", ""),
            )
            if markdown and markdown_ref:
                plan_ref = markdown_ref
                payload["plan_artifact_ref"] = markdown_ref
                payload["plan_artifact_digest"] = hashlib.sha256(
                    markdown.encode("utf-8")
                ).hexdigest()
    typed_review = False
    if not markdown and bound_source is not None:
        from zf.runtime.plan_reviewed_markdown import has_reviewed_plan_contract, reviewed_plan_markdown

        typed_review = has_reviewed_plan_contract(events, bound_source)
        reviewed = reviewed_plan_markdown(state_dir, events, bound_source,
            task_map_ref=str(payload.get("task_map_ref") or ""))
        if reviewed is not None:
            markdown, markdown_ref = reviewed
            plan_ref = markdown_ref
            payload["plan_artifact_ref"] = markdown_ref
            payload["plan_artifact_digest"] = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
            markdown_error = ""
    # A pure plan critic may carry the planner's markdown only as an explicit
    # evidence ref while retaining its own prefilled, workdir-local plan path.
    # Reuse that bound evidence ref when the declared path is unavailable;
    # never scan directories or infer an artifact from unrelated events.
    if not markdown and bound_source is not None and not typed_review:
        source_payload = _payload(bound_source)
        synth_id = str(source_payload.get("synth_event_id") or "").strip()
        synth = next((
            event for event in events
            if _etype(event) == "fanout.synth.completed"
            and (
                (synth_id and _event_id(event) == synth_id)
                or (
                    str(_payload(event).get("fanout_id") or "")
                    == str(source_payload.get("fanout_id") or "")
                    and str(_payload(event).get("workflow_run_id") or "")
                    == str(source_payload.get("workflow_run_id") or "")
                )
            )
        ), None)
        explicit_refs = [
            *(_string_refs(source_payload.get("evidence_refs"))),
            *(_string_refs(source_payload.get("artifact_refs"))),
        ]
        if synth is not None:
            explicit_refs.extend([
                *(_string_refs(_payload(synth).get("evidence_refs"))),
                *(_string_refs(_payload(synth).get("artifact_refs"))),
            ])
        if explicit_refs:
            plan_candidates = [
                ref for ref in explicit_refs
                if ref.lower().endswith((".md", ".markdown"))
                and "/plans/" in ref.replace("\\", "/")
            ]
            plan_candidates = sorted(
                dict.fromkeys(plan_candidates),
                key=_plan_candidate_rank,
            )
            if bundle_plan_ref:
                plan_candidates = [
                    *_bound_plan_refs(bundle_plan_ref, fanout_id=fanout_id),
                    *plan_candidates,
                ]
            markdown, markdown_ref, fallback_error = _plan_markdown(
                state_dir,
                project_root=project_root,
                refs=plan_candidates,
            )
            if markdown:
                plan_ref = markdown_ref
                payload["plan_artifact_ref"] = markdown_ref
                payload["plan_artifact_digest"] = hashlib.sha256(
                    markdown.encode("utf-8")
                ).hexdigest()
                markdown_error = ""
            elif fallback_error:
                markdown_error = f"{markdown_error}; {fallback_error}".strip("; ")
    return markdown, markdown_ref, markdown_error, plan_ref, planning_result, fanout_id


def _plan_markdown(
    state_dir: Path,
    *,
    project_root: Path | None,
    refs: list[str],
    digest: str = "",
) -> tuple[str, str, str]:
    diagnostics: list[str] = []
    for ref in refs:
        if not ref:
            continue
        try:
            body = read_preview_artifact(ref, state_dir=state_dir, project_root=project_root, digest=digest)
            if body.strip():
                return body, ref, ""
            diagnostics.append(f"{ref}: artifact is empty")
        except (OSError, ValueError) as exc:
            diagnostics.append(f"{ref}: {exc}")
    return "", "", "; ".join(diagnostics)


def _planning_result_bundle(
    artifacts: list[dict[str, str]], *, state_dir: Path,
    project_root: Path | None,
) -> dict[str, Any]:
    """Read the package's planning-result port when it is a preview bundle."""

    item = next(
        (item for item in artifacts if item.get("name") == "planning_result"),
        None,
    )
    if not item:
        return {}
    try:
        value = json.loads(read_preview_artifact(
            item["ref"], state_dir=state_dir, project_root=project_root,
            digest=item.get("sha256", ""),
        ))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) and value.get("schema_version") == "plan-preview-bundle.v1" else {}


def _bound_plan_refs(ref: str, *, fanout_id: str) -> list[str]:
    """Return explicit refs for a logical planner artifact and its fanout.

    Worker outputs use ``docs/...`` paths, while admitted fanout artifacts are
    materialized below ``state/artifacts/fanouts/<fanout>/planner``. The
    fanout id is already bound by the approval/task-map event, so this is a
    deterministic binding rather than directory discovery.
    """

    ref = str(ref or "").strip()
    if not ref:
        return []
    refs: list[str] = []
    if fanout_id and not Path(ref).is_absolute() and not ref.startswith("artifacts/"):
        refs.append(f"artifacts/fanouts/{fanout_id}/planner/{ref.lstrip('/')}")
    refs.append(ref)
    return list(dict.fromkeys(refs))


def _plan_candidate_rank(ref: str) -> tuple[int, str]:
    """Prefer a canonical planner Plan over PRD summaries or registers."""

    name = Path(ref).name.lower()
    if name in {"prd-plan-planner-plan.md", "prd-plan-synth-plan.md"}:
        rank = 0
    elif name.endswith("-plan.md"):
        rank = 1
    elif name == "prd-v14-preview.md":
        rank = 20
    elif "decision-register" in name:
        rank = 100
    elif name in {"sdd-v14-preview.md", "tdd-v14-preview.md"}:
        rank = 110
    else:
        rank = 50
    return rank, str(ref)


def _string_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
