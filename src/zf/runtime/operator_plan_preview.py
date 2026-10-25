"""Read-only plan preview projection for operator approval cards."""

from __future__ import annotations

import json
import hashlib
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from zf.runtime.plan_review_packet import build_review_packet, render_human_review_dossier
from zf.runtime.plan_preview_artifacts import (
    PLAN_REVIEW_REF_FIELDS,
    preview_artifact_path,
    preview_package_artifacts,
    read_plan_preview_bundle,
    read_preview_artifact,
)


PLAN_APPROVAL_REQUESTED = "plan.approval.requested"
PLAN_APPROVED = "plan.approved"
PLAN_REJECTED = "plan.rejected"
PLAN_PREVIEW_SCHEMA_VERSION = "operator-plan-preview.v1"


def build_plan_preview(
    state_dir: Path,
    events: Iterable[Any],
    *,
    plan_id: str,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Return a full-screen-ready markdown preview for one plan approval."""

    events = list(events)
    requested: Any | None = None
    resolved: Any | None = None
    for event in events:
        payload = _payload(event)
        if (
            _etype(event) == "approval.resolved"
            and requested is not None
            and str(payload.get("approval_ref") or "")
            == str(_payload(requested).get("approval_ref") or "")
        ):
            resolved = event
            continue
        event_plan_id = str(
            payload.get("plan_id")
            or (
                payload.get("source_event_id")
                if str(payload.get("owner_route") or "")
                == "plan_synth_owner_decision"
                else ""
            )
            or ""
        )
        if event_plan_id != plan_id:
            continue
        is_owner_preview = (
            _etype(event) == "approval.requested"
            and str(payload.get("owner_route") or "")
            == "plan_synth_owner_decision"
        )
        if _etype(event) == PLAN_APPROVAL_REQUESTED or is_owner_preview:
            requested = event
            resolved = None
        elif _etype(event) in {PLAN_APPROVED, PLAN_REJECTED}:
            resolved = event
    if requested is None and resolved is None:
        return {
            "schema_version": PLAN_PREVIEW_SCHEMA_VERSION,
            "ok": False,
            "status": "not_found",
            "plan_id": plan_id,
            "reason": "plan_id not found",
        }

    source_event = requested or resolved
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
               for key in ("task_map_ref", "pdd_id", "trace_id", "workflow_run_id")):
            bound_source = None
        else:
            for key in PLAN_REVIEW_REF_FIELDS:
                if not payload.get(key) and original.get(key):
                    payload[key] = original[key]
    resolved_payload = _payload(resolved) if resolved is not None else {}
    status = "pending"
    if resolved is not None:
        status = (
            "approved"
            if _etype(resolved) == PLAN_APPROVED
            or str(_payload(resolved).get("resolution") or _payload(resolved).get("status") or "")
            in {"approved", "approve"}
            else "rejected"
        )

    digest_ref = str(payload.get("digest_ref") or "")
    task_map_ref = str(payload.get("task_map_ref") or "")
    plan_ref = str(
        payload.get("plan_artifact_ref")
        or payload.get("plan_ref")
        or payload.get("artifact_ref")
        or ""
    )
    artifacts, artifacts_error = preview_package_artifacts(
        payload, state_dir=state_dir, project_root=project_root,
    )
    preview_bundle, preview_bundle_error = read_plan_preview_bundle(
        payload,
        state_dir=state_dir,
        project_root=project_root,
    )
    if preview_bundle_error:
        artifacts_error = artifacts_error or preview_bundle_error
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
    # A pure plan critic may carry the planner's markdown only as an explicit
    # evidence ref while retaining its own prefilled, workdir-local plan path.
    # Reuse that bound evidence ref when the declared path is unavailable;
    # never scan directories or infer an artifact from unrelated events.
    if not markdown and bound_source is not None:
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
    # Evidence fallback may recover the canonical Markdown after the package
    # was written without an `accepted_plan` port. Bind that recovered body to
    # the preview too, so the same human-readable Plan can be opened from the
    # artifact list and is protected by its digest.
    if markdown:
        # A package port named accepted_plan must never continue pointing at a
        # Decision Register after the canonical Plan has been recovered.
        artifacts = [
            item for item in artifacts
            if str(item.get("name") or "") != "accepted_plan"
            or str(item.get("ref") or "") == markdown_ref
        ]
    if markdown and plan_ref and not artifacts_error and not any(
        str(item.get("name") or "") == "accepted_plan" for item in artifacts
    ):
        artifacts.append({
            "name": "accepted_plan",
            "ref": plan_ref,
            "sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        })
    content_kind = ("full_plan" if plan_ref else "digest_only") if markdown else "unavailable"
    digest_markdown, _, _ = _plan_markdown(state_dir, project_root=project_root, refs=[digest_ref])
    task_map_digest = next((p["sha256"] for p in artifacts if p["name"] == "task_map"), "")
    task_map_summary = _task_map_summary(
        state_dir,
        project_root=project_root,
        task_map_ref=task_map_ref,
        digest=task_map_digest,
        plan_id=plan_id,
    )
    decision_items = _decision_items(payload, markdown)
    decision_register_ref = str(
        preview_bundle.get("decision_register_ref")
        or planning_result.get("decision_register_ref")
        or ""
    )
    if decision_register_ref:
        for ref in _bound_plan_refs(decision_register_ref, fanout_id=fanout_id):
            try:
                decision_register = read_preview_artifact(
                    ref, state_dir=state_dir, project_root=project_root,
                )
            except (OSError, ValueError):
                continue
            decision_items = _decision_items(
                {"owner_decision_items": decision_items}, decision_register,
            )
            if decision_items:
                break
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
    requirement_artifacts = [
        item for item in artifacts
        if str(item.get("name") or "").startswith("requirement_")
    ]
    for index, ref in enumerate(authority_refs, start=1):
        if any(str(item.get("ref") or "") == ref for item in requirement_artifacts):
            continue
        try:
            body = read_preview_artifact(
                ref, state_dir=state_dir, project_root=project_root,
            )
        except (OSError, ValueError):
            continue
        item = {
            "name": f"requirement_{index}_{Path(ref).name}",
            "ref": ref,
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        }
        artifacts.append(item)
        requirement_artifacts.append(item)
    review_packet = build_review_packet(
        state_dir,
        project_root=project_root,
        payload=payload,
        artifacts=artifacts,
        authority_refs=authority_refs,
        markdown=markdown,
        task_map_summary=task_map_summary,
        decision_items=decision_items,
        preview_bundle=preview_bundle,
    )
    review_dossier = render_human_review_dossier(
        state_dir,
        project_root=project_root,
        payload=payload,
        artifacts=artifacts,
        authority_refs=authority_refs,
        task_map_summary=task_map_summary,
        decision_items=decision_items,
        status=status,
        review_packet=review_packet,
    )
    digest_source = "stored_digest"
    if task_map_digest:
        digest_markdown = task_map_summary.pop("digest_markdown", "")
        digest_source = "bound_task_map" if digest_markdown else "unavailable"
        if not task_map_summary.get("ok"):
            artifacts_error = artifacts_error or str(task_map_summary.get("reason") or "task map unavailable")
    if not markdown:
        markdown = _fallback_markdown(
            plan_id=plan_id,
            status=status,
            payload=payload,
            task_map_summary=task_map_summary,
            markdown_error=markdown_error,
        )
        markdown_ref = ""

    return {
        "schema_version": PLAN_PREVIEW_SCHEMA_VERSION,
        "ok": True,
        "plan_id": plan_id,
        "status": status,
        "requested_event_id": _event_id(requested) if requested is not None else "",
        "requested_ts": _event_ts(requested) if requested is not None else "",
        "resolved_event_id": _event_id(resolved) if resolved is not None else "",
        "resolved_ts": _event_ts(resolved) if resolved is not None else "",
        "reject_reason": str(resolved_payload.get("reason") or ""),
        "stage_id": str(payload.get("stage_id") or ""),
        "trace_id": str(payload.get("trace_id") or _correlation_id(source_event) or ""),
        "pdd_id": str(payload.get("pdd_id") or ""),
        "task_count": _int_or_none(payload.get("task_count")),
        "refs": {
            "digest_ref": digest_ref,
            "task_map_ref": task_map_ref,
            "plan_ref": plan_ref,
            "markdown_ref": markdown_ref,
            "plan_artifact_digest": str(payload.get("plan_artifact_digest") or ""),
            "source_index_ref": str(payload.get("source_index_ref") or ""),
            "requirement_spec_ref": str(payload.get("requirement_spec_ref") or ""),
            "plan_preview_bundle_ref": str(payload.get("plan_preview_bundle_ref") or ""),
            "plan_preview_bundle_digest": str(payload.get("plan_preview_bundle_digest") or ""),
        },
        "markdown": markdown,
        "review_dossier_markdown": review_dossier,
        "review_packet": review_packet,
        "content_kind": content_kind,
        "content_error": markdown_error,
        "digest_markdown": digest_markdown,
        "digest_source": digest_source,
        "source_event_id": _event_id(bound_source) or _event_id(source_event),
        "artifacts": artifacts,
        "artifacts_error": artifacts_error,
        "content_profile": str(
            preview_bundle.get("content_profile")
            or payload.get("plan_content_profile")
            or ("derived_plan_full" if markdown else "unknown")
        ),
        "authority_refs": authority_refs,
        "requirement_artifacts": requirement_artifacts,
        "task_map_markdown": _render_task_map_markdown(task_map_summary),
        "task_map_summary": task_map_summary,
        "actions": {
            "approve": (
                "replan-approve"
                if str(payload.get("owner_route") or "")
                == "plan_synth_owner_decision"
                else "plan-approve"
            ),
            "reject": (
                "replan-reject"
                if str(payload.get("owner_route") or "")
                == "plan_synth_owner_decision"
                else "plan-reject"
            ),
            "repair_chat": "chat-orchestrator",
        },
        "owner_decision_items": decision_items,
        "action_payload": {key: payload[key] for key in (
            "proposal_ref", "eval_ref", "candidate_task_map_ref", "approval_ref",
            "checkpoint_id", "workflow_run_id", "fanout_id", "source_event_id",
        ) if payload.get(key)},
        "decision_count": len(decision_items),
        "policy": {
            "mutation_path": "controlled-action",
            "agent_can_propose_plan_approve": False,
            "repair_owner": "orchestrator",
        },
    }


def plan_preview_available(
    state_dir: Path,
    *,
    project_root: Path | None,
    refs: list[str],
) -> bool:
    return _first_existing_ref(state_dir, project_root=project_root, refs=refs) is not None


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


def _task_map_summary(
    state_dir: Path,
    *,
    project_root: Path | None,
    task_map_ref: str,
    digest: str = "",
    plan_id: str = "",
) -> dict[str, Any]:
    if not task_map_ref:
        return {"ok": False, "task_count": 0, "tasks": [], "reason": "task_map_ref missing"}
    path = _first_existing_ref(state_dir, project_root=project_root, refs=[task_map_ref])
    if path is None:
        return {"ok": False, "task_count": 0, "tasks": [], "reason": "task_map_ref unreadable"}
    try:
        data = json.loads(read_preview_artifact(
            task_map_ref, state_dir=state_dir, project_root=project_root, digest=digest,
        ))
    except (OSError, ValueError) as exc:
        return {"ok": False, "task_count": 0, "tasks": [], "reason": str(exc)}
    raw_tasks = data.get("tasks") if isinstance(data, dict) else []
    tasks = raw_tasks if isinstance(raw_tasks, list) else []
    rows: list[dict[str, Any]] = []
    for task in tasks[:50]:
        if not isinstance(task, dict):
            continue
        validation = task.get("validation")
        validation = validation if isinstance(validation, dict) else {}
        commands = validation.get("commands")
        commands = commands if isinstance(commands, list) else []
        verification = str(task.get("verification") or "")
        if not verification:
            verification = "\n".join(
                str(command.get("command") or "")
                for command in commands
                if isinstance(command, dict) and command.get("command")
            )
        rows.append({
            "task_id": str(task.get("task_id") or task.get("id") or ""),
            "title": str(task.get("title") or task.get("summary") or ""),
            "root_owner_class": str(task.get("root_owner_class") or ""),
            "owner_role": str(task.get("owner_role") or ""),
            "wave": str(task.get("wave") or ""),
            "blocked_by": ", ".join(
                str(item)
                for item in task.get("blocked_by", [])
                if str(item).strip()
            ) if isinstance(task.get("blocked_by"), list) else str(task.get("blocked_by") or ""),
            "allowed_paths": [str(item) for item in task.get("allowed_paths", [])],
            "exclusive_files": [str(item) for item in task.get("exclusive_files", [])],
            "source_key": str(task.get("source_key") or ""),
            "source_ref": str(task.get("source_ref") or ""),
            "source_excerpt": str(task.get("source_excerpt") or ""),
            "acceptance_criteria": [
                item for item in task.get("acceptance_criteria", [])
                if isinstance(item, dict)
            ],
            "owner_decision_ids": [
                str(item) for item in task.get("owner_decision_ids", [])
            ],
            "external_prerequisite_ids": [
                str(item) for item in task.get("external_prerequisite_ids", [])
            ],
            "explicit_non_goals": [
                str(item) for item in task.get("explicit_non_goals", [])
            ],
            "validation": validation,
            "verification": verification,
        })
    from zf.runtime.plan_digest import render_plan_digest

    return {
        "ok": True,
        "task_count": len(tasks),
        "tasks": rows,
        "path": str(path),
        "truncated": len(tasks) > len(rows),
        **({"digest_markdown": render_plan_digest(
            tasks, plan_id=plan_id, task_map_ref=task_map_ref,
        )} if digest else {}),
    }


def _source_index_authority_refs(
    state_dir: Path,
    *,
    project_root: Path | None,
    source_index_ref: str,
    source_index_digest: str = "",
) -> list[str]:
    """Read authority refs from the already-bound source index only."""

    if not source_index_ref:
        return []
    try:
        body = read_preview_artifact(
            source_index_ref,
            state_dir=state_dir,
            project_root=project_root,
            digest=source_index_digest,
        )
        data = json.loads(body)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return []
    refs = data.get("authority_refs") if isinstance(data, dict) else []
    if not isinstance(refs, list):
        return []
    return [str(item) for item in refs if str(item).strip()]


def _render_task_map_markdown(summary: dict[str, Any]) -> str:
    """Render the structured Task Map as a human review surface."""

    if not summary.get("ok"):
        return ""
    lines = [
        "## Task Map（完整拆分）",
        "",
        "下面内容来自绑定的 `task_map.json`；Plan Preview 只读展示，不会直接修改任务状态。",
        "",
    ]
    for task in summary.get("tasks", []):
        task_id = str(task.get("task_id") or "")
        lines.extend([
            f"### {task_id} {task.get('title') or ''}".rstrip(),
            "",
            f"- owner: `{task.get('owner_role') or '-'}`",
            f"- root owner class: `{task.get('root_owner_class') or '-'}`",
            f"- wave: `{task.get('wave') or '-'}`",
            f"- blocked_by: `{task.get('blocked_by') or '-'}`",
            f"- source: `{task.get('source_ref') or '-'}`",
        ])
        if task.get("source_excerpt"):
            lines.append(f"- source excerpt: {task['source_excerpt']}")
        paths = task.get("allowed_paths") or []
        if paths:
            lines.append("- allowed paths:")
            lines.extend(f"  - `{path}`" for path in paths)
        exclusive_files = task.get("exclusive_files") or []
        if exclusive_files:
            lines.append("- exclusive files:")
            lines.extend(f"  - `{path}`" for path in exclusive_files)
        criteria = task.get("acceptance_criteria") or []
        if criteria:
            lines.append("- acceptance:")
            for criterion in criteria:
                criterion_id = criterion.get("id") or "AC"
                statement = criterion.get("statement") or ""
                lines.append(f"  - **{criterion_id}** {statement}")
        validation = task.get("validation") or {}
        commands = validation.get("commands") if isinstance(validation, dict) else []
        if commands:
            lines.append("- verification:")
            for command in commands:
                if isinstance(command, dict):
                    lines.append(
                        f"  - `{command.get('id') or 'command'}`: "
                        f"`{command.get('command') or ''}`"
                    )
        for label, key in (
            ("owner decisions", "owner_decision_ids"),
            ("external prerequisites", "external_prerequisite_ids"),
            ("explicit non-goals", "explicit_non_goals"),
        ):
            values = task.get(key) or []
            if values:
                lines.append(f"- {label}: " + ", ".join(f"`{value}`" for value in values))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _decision_items(payload: dict[str, Any], markdown: str) -> list[dict[str, Any]]:
    """Return one decision register from explicit payload or bound Plan text.

    The fallback is scoped to the Plan's named decision-register table. It
    keeps older plans reviewable without scanning unrelated files and never
    invents a decision absent from the bound Plan.
    """

    items: dict[str, dict[str, Any]] = {}
    raw = payload.get("owner_decision_items")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            decision_id = str(item.get("decision_id") or item.get("id") or "").strip()
            if decision_id:
                items[decision_id] = dict(item)
    lines = markdown.splitlines()
    start = next(
        (index for index, line in enumerate(lines)
         if line.startswith("#") and "decision register" in line.lstrip("#").strip().lower()),
        None,
    )
    if start is None:
        start = next(
            (index for index, line in enumerate(lines)
             if line.startswith("#") and "owner 决策项" in line.lstrip("#").strip().lower()),
            None,
        )
    if start is None:
        return list(items.values())
    heading = lines[start].lstrip()
    heading_level = len(heading) - len(heading.lstrip("#"))
    end = next(
        (
            index for index in range(start + 1, len(lines))
            if lines[index].startswith("#")
            and len(lines[index]) - len(lines[index].lstrip("#")) <= heading_level
        ),
        len(lines),
    )
    header: list[str] = []
    for line in lines[start + 1:end]:
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] in {"ID", "id", "---"} or set(cells[0]) <= {"-", ":"}:
            continue
        if not header and cells[0].lower() in {"id", "decision id"}:
            header = [cell.lower() for cell in cells]
            continue
        decision_id = cells[0].strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*", decision_id):
            continue
        status_index = next(
            (index for index, cell in enumerate(header) if "status" in cell),
            None,
        )
        if status_index is not None and status_index < len(cells):
            status = cells[status_index]
            if status and status.upper() not in {"PENDING_APPROVAL", "PENDING", "OPEN", "NOT_RUN"}:
                continue
        item = items.setdefault(decision_id, {"decision_id": decision_id})
        item.setdefault("question", cells[1])
        if len(cells) > 2:
            item.setdefault("options", cells[2])
        if len(cells) > 3:
            item.setdefault("recommended", cells[3])
        if len(cells) > 4:
            item.setdefault("blocking_impact", cells[4])
        if status_index is not None and status_index < len(cells):
            item.setdefault("status", cells[status_index])
    return list(items.values())


def _fallback_markdown(
    *,
    plan_id: str,
    status: str,
    payload: dict[str, Any],
    task_map_summary: dict[str, Any],
    markdown_error: str,
) -> str:
    lines = [
        "# Plan Preview",
        "",
        f"- plan_id: `{plan_id}`",
        f"- status: `{status}`",
        f"- stage_id: `{payload.get('stage_id') or ''}`",
        f"- task_map_ref: `{payload.get('task_map_ref') or ''}`",
    ]
    if markdown_error:
        lines.append(f"- preview_note: `{markdown_error}`")
    lines.extend(["", "## Task Map Summary", ""])
    if task_map_summary.get("ok"):
        lines.append(f"task_count: `{task_map_summary.get('task_count')}`")
        for task in task_map_summary.get("tasks", [])[:20]:
            lines.append(f"- `{task.get('task_id')}` {task.get('title')}")
    else:
        lines.append(str(task_map_summary.get("reason") or "task map unavailable"))
    return "\n".join(lines).rstrip() + "\n"


def _first_existing_ref(
    state_dir: Path,
    *,
    project_root: Path | None,
    refs: list[str],
) -> Path | None:
    for ref in refs:
        if not ref:
            continue
        for path in _candidate_paths(ref, state_dir=state_dir, project_root=project_root):
            try:
                if path.is_file():
                    return path
            except OSError:
                continue
    return None


def _candidate_paths(ref: str, *, state_dir: Path, project_root: Path | None) -> list[Path]:
    try:
        return [preview_artifact_path(ref, project_root=project_root, state_dir=state_dir)]
    except (OSError, ValueError):
        return []


def read_plan_preview_attachment(
    state_dir: Path, events: Iterable[Any], *, plan_id: str, name: str,
    project_root: Path | None = None,
) -> str:
    preview = build_plan_preview(state_dir, events, plan_id=plan_id, project_root=project_root)
    artifact = next((p for p in preview.get("artifacts", []) if p["name"] == name), None)
    if artifact is None:
        raise ValueError("artifact is not bound to this plan")
    return read_preview_artifact(artifact["ref"], state_dir=state_dir, project_root=project_root, digest=artifact["sha256"])


def _etype(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("type") or "")
    return str(getattr(event, "type", "") or "")


def _payload(event: Any) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event, dict) else getattr(event, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _event_id(event: Any) -> str:
    if event is None:
        return ""
    if isinstance(event, dict):
        return str(event.get("id") or "")
    return str(getattr(event, "id", "") or "")


def _event_ts(event: Any) -> str:
    if event is None:
        return ""
    if isinstance(event, dict):
        return str(event.get("ts") or "")
    return str(getattr(event, "ts", "") or "")


def _correlation_id(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("correlation_id") or "")
    return str(getattr(event, "correlation_id", "") or "")


def _string_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


__all__ = [
    "PLAN_APPROVAL_REQUESTED",
    "PLAN_APPROVED",
    "PLAN_PREVIEW_SCHEMA_VERSION",
    "PLAN_REJECTED",
    "build_plan_preview",
    "plan_preview_available",
]
