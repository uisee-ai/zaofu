"""Workflow input manifests for Channel/Squad -> Workflow handoff.

Channel agents may propose artifacts, but workflow workers should receive
deterministic refs through kernel-managed runtime state instead of direct
writes into worker workdirs.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.safety.path_guard import PathGuard, PathGuardError
from zf.core.state.atomic_io import atomic_write_text
from zf.core.security.redaction import redact_obj
from zf.runtime.sidecar_refs import write_sidecar_text
from zf.runtime.workflow_request_concurrency import WorkflowRequestError


_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_ADOPTED_RESEARCH_SOURCE = "workflow.research.adopted"


def workflow_run_id_for(*, event_id: str, task_id: str, pattern_id: str) -> str:
    raw = f"{event_id}:{task_id}:{pattern_id}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    safe_pattern = _safe_id(pattern_id)[:32] or "workflow"
    return f"wf-{safe_pattern}-{digest}"


def workflow_input_manifest_ref(workflow_run_id: str) -> str:
    return f"workflow-inputs/{_safe_id(workflow_run_id)}/manifest.json"


def hydrate_request_workflow_input_manifest(
    state_dir: Path,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Read the immutable effective input manifest bound to one Request."""

    ref = str(request.get("workflow_input_manifest_ref") or "").strip()
    expected_digest = str(
        request.get("workflow_input_manifest_digest") or ""
    ).strip()
    if not ref or not expected_digest:
        raise WorkflowRequestError(
            "workflow request has no immutable input manifest identity"
        )
    state_root = Path(state_dir).expanduser().resolve()
    path = Path(ref).expanduser()
    if not path.is_absolute():
        path = state_root / path
    if path.is_symlink():
        raise WorkflowRequestError("workflow input manifest ref is a symlink")
    try:
        path = PathGuard.assert_under(
            path,
            state_root / "workflow-requests",
        ).resolve(strict=True)
    except (OSError, PathGuardError) as exc:
        raise WorkflowRequestError(
            "workflow input manifest ref is outside canonical request state"
        ) from exc
    try:
        raw = path.read_bytes()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowRequestError(
            "workflow input manifest body is unreadable"
        ) from exc
    if hashlib.sha256(raw).hexdigest() != expected_digest:
        raise WorkflowRequestError("workflow input manifest digest mismatch")
    if not isinstance(manifest, dict):
        raise WorkflowRequestError(
            "workflow input manifest body must be an object"
        )
    if str(manifest.get("request_id") or "") != str(
        request.get("request_id") or ""
    ):
        raise WorkflowRequestError(
            "workflow input manifest does not match the request"
        )
    return manifest


def bind_adopted_research_inputs(
    state_dir: Path,
    request: dict[str, Any],
    artifact_refs: list[Any],
) -> list[Any]:
    """Only the Request revision or its frozen Proposal can mark evidence as adopted."""
    # Raw intake or invocation descriptors can carry ordinary references but cannot
    # supply this provenance. Regenerate it from the admitted decision.
    refs = [
        ref for ref in artifact_refs
        if not (isinstance(ref, dict) and ref.get("source_field") == _ADOPTED_RESEARCH_SOURCE)
    ]
    revision = request["revision"]
    adopted = [
        {
            "kind": "research_report",
            "source_field": _ADOPTED_RESEARCH_SOURCE,
            "ref": row["artifact_ref"],
            "sha256": row["sha256"],
            "summary": row["summary"],
            "request_id": request["request_id"],
            "request_revision": revision,
            "result_event_id": row["result_event_id"],
        }
        for row in request.get("research_artifacts") or []
        if row.get("request_revision") == revision
    ]
    if adopted and request.get("proposal_digest") and request.get("status") in {
        "proposed", "approved", "submitted", "running",
    }:
        # A later adoption enriches the Request, not the already frozen Proposal.
        frozen = hydrate_request_workflow_input_manifest(state_dir, request)
        adopted = [row for row in adopted if row in frozen.get("artifact_refs", [])]
    return [*refs, *adopted]


def workflow_prompt_ref(workflow_run_id: str) -> str:
    return f"artifacts/workflow-inputs/{_safe_id(workflow_run_id)}/prompt.md"


def infer_workflow_prompt_kind(pattern_id: str, payload: dict[str, Any]) -> str:
    raw = str(
        payload.get("prompt_kind")
        or payload.get("workflow_prompt_kind")
        or ""
    ).strip().lower()
    aliases = {
        "prd": "prd",
        "product": "prd",
        "product-requirements": "prd",
        "product_requirements": "prd",
        "requirements": "prd",
        "refactor": "refactor",
        "refactoring": "refactor",
        "refactor-plan": "refactor",
        "refactor_plan": "refactor",
        "research": "research",
        "research-fanout": "research",
        "research_fanout": "research",
    }
    if raw in aliases:
        return aliases[raw]
    request_kind = str(payload.get("request_kind") or "").strip().lower()
    route_id = str(payload.get("route_id") or "").strip().lower()
    template_id = str(payload.get("research_template_id") or "").strip().lower()
    if (
        request_kind == "research"
        or route_id.startswith("research:")
        or template_id.startswith("research-")
    ):
        return "research"
    haystack = " ".join([
        str(pattern_id or ""),
        str(payload.get("expected_output") or ""),
        str(payload.get("reason") or ""),
    ]).lower()
    if "refactor" in haystack:
        return "refactor"
    if "prd" in haystack or "product-requirement" in haystack or "requirements" in haystack:
        return "prd"
    return ""


def normalize_source_refs(
    payload: dict[str, Any],
    *,
    task_id: str,
    pattern_id: str,
    workflow_run_id: str,
    workflow_input_manifest_ref: str,
    workflow_invoke_event_id: str,
    requested_event_id: str,
    source: str,
) -> dict[str, str]:
    refs: dict[str, str] = {}
    raw = payload.get("source_refs")
    if isinstance(raw, dict):
        for key, value in raw.items():
            text = str(value or "").strip()
            if text:
                refs[str(key)] = text
    for key in ("channel_id", "thread_id", "synthesis_event_id", "message_id", "route_event_id", "squad_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            refs.setdefault(key, value)
    refs.setdefault("task_id", task_id)
    refs.setdefault("pattern_id", pattern_id)
    refs["workflow_run_id"] = workflow_run_id
    refs["workflow_input_manifest_ref"] = workflow_input_manifest_ref
    refs["workflow_invoke_event_id"] = workflow_invoke_event_id
    refs["requested_event_id"] = requested_event_id
    refs.setdefault("source", source)
    return refs


def normalize_artifact_refs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize explicit artifact/evidence/report refs into small dicts."""
    refs: list[dict[str, Any]] = []
    for field in ("artifact_refs", "evidence_refs", "report_refs", "input_refs"):
        value = payload.get(field)
        if not isinstance(value, list):
            continue
        for item in value:
            normalized = _normalize_ref_item(item, source_field=field)
            if normalized:
                refs.append(normalized)
    raw_refs = payload.get("refs")
    if isinstance(raw_refs, dict):
        for key, value in raw_refs.items():
            if not str(key).endswith("_ref") and str(key) not in {"manifest", "path", "uri"}:
                continue
            normalized = _normalize_ref_item(value, source_field=f"refs.{key}")
            if normalized:
                refs.append(normalized)
    return _dedupe_refs(refs)


def write_workflow_input_manifest(
    state_dir: Path,
    *,
    workflow_run_id: str,
    workflow_invoke_event_id: str,
    task_id: str,
    pattern_id: str,
    source_refs: dict[str, str],
    artifact_refs: list[dict[str, Any]],
    request_payload: dict[str, Any],
) -> str:
    ref = workflow_input_manifest_ref(workflow_run_id)
    manifest = {
        "schema_version": "workflow-input-manifest.v1",
        "workflow_run_id": workflow_run_id,
        "workflow_invoke_event_id": workflow_invoke_event_id,
        "task_id": task_id,
        "pattern_id": pattern_id,
        "request_id": str(request_payload.get("request_id") or ""),
        "request_revision": _int_value(
            request_payload.get("request_revision")
        ),
        "origin_binding": redact_obj(
            dict(request_payload.get("origin_binding") or {})
            if isinstance(request_payload.get("origin_binding"), dict)
            else {}
        ),
        "source_refs": dict(source_refs),
        "artifact_refs": redact_obj(artifact_refs),
        "request": _request_summary(request_payload),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_text(
        Path(state_dir) / ref,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    return ref


def write_workflow_prompt_package(
    state_dir: Path,
    *,
    workflow_run_id: str,
    task_id: str,
    pattern_id: str,
    prompt_kind: str,
    source_refs: dict[str, str],
    artifact_refs: list[dict[str, Any]],
    request_payload: dict[str, Any],
) -> dict[str, Any]:
    """Write a deterministic prompt artifact for workflow entry adapters."""
    ref = workflow_prompt_ref(workflow_run_id)
    prompt_text = _render_workflow_prompt(
        state_dir=state_dir,
        task_id=task_id,
        pattern_id=pattern_id,
        prompt_kind=prompt_kind,
        source_refs=source_refs,
        artifact_refs=artifact_refs,
        request_payload=request_payload,
    )
    descriptor = write_sidecar_text(
        Path(state_dir),
        ref,
        prompt_text,
        kind="workflow_prompt",
        schema_version="workflow-prompt.v1",
        created_by="workflow-inputs",
        required=True,
        content_type="text/markdown",
    )
    return {
        **descriptor,
        "kind": "workflow_prompt",
        "prompt_kind": prompt_kind,
        "ref": ref,
        "path": ref,
        "name": f"{prompt_kind}-workflow-prompt.md",
        "workflow_run_id": workflow_run_id,
        "source_field": "generated_prompt",
        "summary": f"{prompt_kind} workflow prompt generated from channel synthesis and refs",
    }


def workflow_input_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract workflow input refs from an event/child payload."""
    carriers: list[dict[str, Any]] = [payload]
    nested = payload.get("trigger_payload")
    if isinstance(nested, dict):
        carriers.append(nested)
    nested = payload.get("payload")
    if isinstance(nested, dict):
        carriers.append(nested)
    for carrier in carriers:
        manifest_ref = str(carrier.get("workflow_input_manifest_ref") or "").strip()
        source_refs = carrier.get("source_refs") if isinstance(carrier.get("source_refs"), dict) else {}
        artifact_refs = carrier.get("artifact_refs") if isinstance(carrier.get("artifact_refs"), list) else []
        workflow_run_id = str(carrier.get("workflow_run_id") or "").strip()
        if manifest_ref or source_refs or artifact_refs or workflow_run_id:
            return {
                "workflow_run_id": workflow_run_id,
                "workflow_input_manifest_ref": manifest_ref,
                "source_refs": source_refs if isinstance(source_refs, dict) else {},
                "artifact_refs": artifact_refs if isinstance(artifact_refs, list) else [],
                "request_id": str(carrier.get("request_id") or ""),
                "request_revision": _int_value(
                    carrier.get("request_revision")
                ),
                "origin_binding": (
                    dict(carrier.get("origin_binding"))
                    if isinstance(carrier.get("origin_binding"), dict)
                    else {}
                ),
            }
    return {}


def render_workflow_input_briefing_section(payload: dict[str, Any]) -> str:
    extracted = workflow_input_payload(payload)
    if not extracted:
        return ""
    if not any((
        extracted.get("workflow_input_manifest_ref"),
        extracted.get("source_refs"),
        extracted.get("artifact_refs"),
        extracted.get("request_id"),
        extracted.get("request_revision"),
        extracted.get("origin_binding"),
    )):
        return ""
    return (
        "\n\n## Workflow Input Manifest\n"
        "These refs came from a Channel/Squad workflow invocation. Treat the "
        "manifest as read-only input; do not write into another worker's "
        "workdir. If a referenced artifact is missing, emit a blocking event "
        "instead of continuing from stale chat context.\n"
        "```json\n"
        f"{json.dumps(extracted, ensure_ascii=False, indent=2)}\n"
        "```\n"
    )


def compact_workflow_input_payload_for_briefing(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Remove refs duplicated by Thin Judge's exact controlled-read list."""

    if (
        str(payload.get("output_profile_id") or "")
        == "thin-judge-goal-closure"
        and payload.get("required_reads")
    ):
        return {**payload, "artifact_refs": []}
    return payload


def _request_summary(payload: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "channel_id",
        "thread_id",
        "requested_by",
        "reason",
        "source",
        "scope",
        "target_ref",
        "expected_output",
        "risk",
        "open_questions",
        "origin_binding",
        "prompt_kind",
        "request_id",
        "request_revision",
        "workflow_prompt_ref",
        "workflow_generation",
        "effective_config_digest",
        "run_contract_digest",
        "research_template_id",
        "research_route_digest",
        "research_template_digest",
        "research_role_activation_digest",
        "research_prompt_contract_digest",
        "research_generation_contract_ref",
        "restart_boundary",
        "safe_resume_action",
    }
    return redact_obj({key: payload.get(key) for key in keep if key in payload})


def _normalize_ref_item(item: Any, *, source_field: str) -> dict[str, Any]:
    if isinstance(item, dict):
        if item.get("source_field") == _ADOPTED_RESEARCH_SOURCE:
            return {}
        ref = redact_obj(dict(item))
        ref.setdefault("source_field", source_field)
        return ref
    text = str(item or "").strip()
    if not text:
        return {}
    return {"ref": text, "source_field": source_field}


def _dedupe_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for ref in refs:
        key = json.dumps(ref, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def _safe_id(value: str) -> str:
    text = _SAFE_RE.sub("-", str(value or "").strip()).strip("-._")
    return text or "workflow"


def _int_value(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _render_workflow_prompt(
    *,
    state_dir: Path,
    task_id: str,
    pattern_id: str,
    prompt_kind: str,
    source_refs: dict[str, str],
    artifact_refs: list[dict[str, Any]],
    request_payload: dict[str, Any],
) -> str:
    channel = _load_channel(state_dir, source_refs)
    thread_id = str(source_refs.get("thread_id") or request_payload.get("thread_id") or "main")
    synthesis = _select_synthesis(
        channel,
        thread_id=thread_id,
        synthesis_event_id=str(source_refs.get("synthesis_event_id") or request_payload.get("synthesis_event_id") or ""),
    )
    if request_payload.get("request_id") and prompt_kind == "prd":
        # Thread activity can belong to another Request or arrive after this Proposal.
        research = "See the current Request's adopted research in Artifact Refs below."
    else:
        research = _render_state_updates(_select_research_updates(channel, thread_id=thread_id))
    messages = _select_recent_messages(channel, thread_id=thread_id)
    sections = [
        f"# {_prompt_title(prompt_kind)}",
        "",
        "## Runtime Contract",
        f"- task_id: {task_id}",
        f"- pattern_id: {pattern_id}",
        f"- prompt_kind: {prompt_kind}",
        f"- channel_id: {source_refs.get('channel_id', '')}",
        f"- thread_id: {thread_id}",
        "- Treat this prompt package and its refs as read-only workflow input.",
        "- Do not treat chat transcript text as business truth unless it is cited below as a source ref.",
        "",
        "## Requested Outcome",
        str(request_payload.get("expected_output") or request_payload.get("reason") or "Produce the requested workflow artifact.").strip(),
        "",
        "## Channel Synthesis",
        _render_synthesis(synthesis),
        "",
        "## Research And Fanout Results",
        research,
        "",
        "## Recent Discussion",
        _render_messages(messages),
        "",
        "## Source Refs",
        "```json",
        json.dumps(redact_obj(source_refs), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Artifact Refs",
        "```json",
        json.dumps(redact_obj(artifact_refs), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Output Contract",
        _output_contract(prompt_kind),
        "",
    ]
    return "\n".join(sections)


def _load_channel(state_dir: Path, source_refs: dict[str, str]) -> dict[str, Any] | None:
    channel_id = str(source_refs.get("channel_id") or "").strip()
    if not channel_id:
        return None
    try:
        from zf.runtime.channel_projection import project_channel

        return project_channel(state_dir, channel_id)
    except Exception:
        return None


def _select_synthesis(
    channel: dict[str, Any] | None,
    *,
    thread_id: str,
    synthesis_event_id: str,
) -> dict[str, Any]:
    syntheses = [
        item for item in list((channel or {}).get("syntheses") or [])
        if isinstance(item, dict) and str(item.get("thread_id") or "main") == thread_id
    ]
    if synthesis_event_id:
        for item in syntheses:
            if str(item.get("event_id") or "") == synthesis_event_id:
                return item
    return syntheses[-1] if syntheses else {}


def _select_research_updates(channel: dict[str, Any] | None, *, thread_id: str) -> list[dict[str, Any]]:
    updates = []
    for item in list((channel or {}).get("state_updates") or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("thread_id") or "main") != thread_id:
            continue
        status = str(item.get("status") or "").lower()
        if "research" in status or "fanout" in status:
            updates.append(item)
    return updates[-6:]


def _select_recent_messages(channel: dict[str, Any] | None, *, thread_id: str) -> list[dict[str, Any]]:
    messages = [
        item for item in list((channel or {}).get("messages") or (channel or {}).get("recent_messages") or [])
        if isinstance(item, dict) and str(item.get("thread_id") or "main") == thread_id
    ]
    return messages[-8:]


def _render_synthesis(item: dict[str, Any]) -> str:
    if not item:
        return "- No channel synthesis was found. Treat this as incomplete input unless the request explicitly supplies equivalent refs."
    lines = [
        f"- event_id: {item.get('event_id', '')}",
        f"- decision: {item.get('decision', '')}",
        f"- summary: {_excerpt(str(item.get('summary') or ''), 1000)}",
    ]
    open_questions = item.get("open_questions") if isinstance(item.get("open_questions"), list) else []
    if open_questions:
        lines.append("- open_questions:")
        lines.extend(f"  - {question}" for question in open_questions)
    return "\n".join(lines)


def _render_state_updates(items: list[dict[str, Any]]) -> str:
    if not items:
        return "- No research/fanout state update is attached to this channel thread."
    lines: list[str] = []
    for item in items:
        lines.append(
            f"- {item.get('status', '')}: {_excerpt(str(item.get('summary') or ''), 500)} "
            f"(event_id={item.get('event_id', '')})"
        )
        refs = item.get("refs") if isinstance(item.get("refs"), dict) else {}
        if refs:
            lines.append("  refs:")
            lines.append("  ```json")
            lines.append(_indent(json.dumps(redact_obj(refs), ensure_ascii=False, indent=2), "  "))
            lines.append("  ```")
    return "\n".join(lines)


def _render_messages(items: list[dict[str, Any]]) -> str:
    if not items:
        return "- No discussion messages are available in this thread."
    lines: list[str] = []
    for item in items:
        speaker = str(item.get("member_id") or item.get("actor") or "unknown")
        role = str(item.get("role") or item.get("source") or "")
        text = _excerpt(str(item.get("text") or item.get("summary") or ""), 500)
        lines.append(f"- {speaker} ({role}): {text}")
    return "\n".join(lines)


def _prompt_title(prompt_kind: str) -> str:
    if prompt_kind == "prd":
        return "PRD Workflow Prompt"
    if prompt_kind == "refactor":
        return "Refactor Workflow Prompt"
    if prompt_kind == "research":
        return "Research Workflow Prompt"
    return "Workflow Prompt"


def _output_contract(prompt_kind: str) -> str:
    if prompt_kind == "prd":
        return "\n".join([
            "- Produce a PRD with problem, users, goals, non-goals, requirements, acceptance criteria, risks, and open questions.",
            "- Cite research artifacts and channel synthesis refs used for each major decision.",
            "- Keep implementation details separate from product requirements.",
        ])
    if prompt_kind == "refactor":
        return "\n".join([
            "- Produce a refactor plan with current behavior, target shape, scoped files/modules, migration steps, tests, and rollback risks.",
            "- Cite research artifacts, PRD/synthesis refs, and discussion evidence for each risky change.",
            "- Do not mutate code directly from this prompt; emit the workflow's expected planning artifact.",
        ])
    if prompt_kind == "research":
        return "\n".join([
            "- Produce an evidence-backed research synthesis with source refs, findings, disagreements, risks, and open questions.",
            "- Separate observed evidence from inference and bind every major claim to a source or artifact ref.",
            "- Prepare explicit handoff inputs for the requested downstream PRD, refactor, or delivery workflow without mutating product state.",
        ])
    return "- Produce the workflow artifact requested by the execution pattern."


def _excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line if line else prefix for line in text.splitlines())
