"""Durable, queryable artifacts for completed Research workflows."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.provider_operation_summary import (
    prepare_provider_operation_summary,
)
from zf.runtime.sidecar_refs import write_sidecar_text


RESEARCH_REPORT_SCHEMA_VERSION = "research-report.v1"
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


def materialize_research_fanout_artifact(
    state_dir: Path,
    *,
    manifest: dict[str, Any],
    synth_event: ZfEvent,
) -> dict[str, Any]:
    """Write one complete Research body and return a normalized descriptor."""

    stage_id = str(manifest.get("stage_id") or "").strip()
    if not _is_research_stage(stage_id):
        return {}

    synth_raw = (
        dict(synth_event.payload)
        if isinstance(synth_event.payload, dict)
        else {}
    )
    report_raw = (
        dict(synth_raw.get("report"))
        if isinstance(synth_raw.get("report"), dict)
        else {}
    )
    summary = str(
        synth_raw.get("research_summary")
        or synth_raw.get("summary")
        or report_raw.get("summary")
        or ""
    ).strip()
    if not summary:
        return {}

    trigger = (
        dict(manifest.get("trigger_payload"))
        if isinstance(manifest.get("trigger_payload"), dict)
        else {}
    )
    source_refs = (
        dict(trigger.get("source_refs"))
        if isinstance(trigger.get("source_refs"), dict)
        else {}
    )
    task_id = str(
        trigger.get("task_id")
        or manifest.get("task_id")
        or synth_event.task_id
        or "unbound"
    ).strip()
    fanout_id = str(
        manifest.get("fanout_id")
        or synth_raw.get("fanout_id")
        or synth_event.id
    ).strip()
    workflow_run_id = str(
        trigger.get("workflow_run_id")
        or manifest.get("workflow_run_id")
        or ""
    ).strip()
    topic = str(
        source_refs.get("topic")
        or trigger.get("reason")
        or trigger.get("expected_output")
        or stage_id
    ).strip()
    children = _child_reports(manifest)
    provider_summary_ref, provider_summary_issues = _provider_summary_ref(
        Path(state_dir),
        synth_raw=synth_raw,
        report_raw=report_raw,
        children=children,
        workflow_run_id=workflow_run_id,
        operation_id=str(
            trigger.get("workflow_operation_id")
            or manifest.get("workflow_operation_id")
            or ""
        ).strip(),
        fanout_id=fanout_id,
        role_instance=str(
            synth_raw.get("role_instance")
            or synth_event.actor
            or ""
        ).strip(),
        source_event_id=synth_event.id,
        max_parallel_agents=_native_parallel_limit(manifest),
    )
    evidence_refs = _string_refs(
        synth_raw.get("evidence_refs"),
        report_raw.get("evidence_refs"),
    )
    open_questions = _strings(
        _first_present(
            synth_raw,
            report_raw,
            keys=("open_questions",),
            default=[],
        )
    )
    canonical_payload = redact_obj({
        "schema_version": RESEARCH_REPORT_SCHEMA_VERSION,
        "identity": {
            "task_id": task_id,
            "stage_id": stage_id,
            "fanout_id": fanout_id,
            "workflow_run_id": workflow_run_id,
            "request_id": str(
                trigger.get("request_id")
                or manifest.get("request_id")
                or ""
            ),
            "request_revision": (
                trigger.get("request_revision")
                or manifest.get("request_revision")
                or ""
            ),
            "result_event_id": synth_event.id,
            "synth_event_id": (
                synth_event.id
                if synth_event.type == "fanout.synth.completed"
                else ""
            ),
            "root_result_event_id": (
                synth_event.id
                if "adaptive" in stage_id.lower()
                else ""
            ),
            "template_id": str(source_refs.get("template_id") or ""),
            "research_rollout": str(
                source_refs.get("research_rollout") or ""
            ),
        },
        "topic": topic,
        "summary": summary,
        "findings": _first_present(
            synth_raw,
            report_raw,
            keys=("findings",),
            default=[],
        ),
        "architecture": _first_present(
            synth_raw,
            report_raw,
            keys=("architecture", "technical_architecture"),
            default={},
        ),
        "acceptance_matrix": _first_present(
            synth_raw,
            report_raw,
            keys=("acceptance_matrix", "acceptance_criteria"),
            default=[],
        ),
        "test_matrix": _first_present(
            synth_raw,
            report_raw,
            keys=("test_matrix", "verification_matrix"),
            default=[],
        ),
        "task_map": _first_present(
            synth_raw,
            report_raw,
            keys=("task_map", "tasks"),
            default=[],
        ),
        "evidence_refs": evidence_refs,
        "open_questions": open_questions,
        "prd_prompt_input": _first_present(
            synth_raw,
            report_raw,
            keys=("prd_prompt_input", "prd_input"),
            default="",
        ),
        "refactor_prompt_input": _first_present(
            synth_raw,
            report_raw,
            keys=("refactor_prompt_input", "refactor_input"),
            default="",
        ),
        "provider_operation_summary_ref": provider_summary_ref or {},
        "provider_operation_summary_issues": provider_summary_issues,
        "children": children,
        # Preserve future semantic fields without teaching this projection
        # every Provider-specific report shape.
        "canonical_result_payload": synth_raw,
        # Kept for fixed-fanout consumers while they migrate to the neutral
        # result field above.
        "canonical_synthesis_payload": synth_raw,
    })
    body = _render_markdown(canonical_payload)
    relative_ref = (
        Path("artifacts")
        / "research"
        / _safe_id(task_id)
        / f"{_safe_id(fanout_id)}.md"
    )
    descriptor = write_sidecar_text(
        Path(state_dir),
        relative_ref,
        body,
        kind="research_report",
        schema_version=RESEARCH_REPORT_SCHEMA_VERSION,
        created_by="research-fanout-artifact",
        source_event_id=synth_event.id,
        access_scope={
            "visibility": "project",
        },
        required=True,
        content_type="text/markdown",
        preview=summary[:240],
    )
    descriptor.update({
        "artifact_id": f"research-{_safe_id(fanout_id)}",
        "name": relative_ref.name,
        "path": relative_ref.as_posix(),
        "hash": f"sha256:{descriptor['sha256']}",
        "summary": summary,
        "task_id": task_id,
        "stage_id": stage_id,
        "fanout_id": fanout_id,
        "workflow_run_id": workflow_run_id,
        "request_id": str(
            trigger.get("request_id")
            or manifest.get("request_id")
            or ""
        ),
        "request_revision": (
            trigger.get("request_revision")
            or manifest.get("request_revision")
            or ""
        ),
        "result_event_id": synth_event.id,
        "synth_event_id": (
            synth_event.id
            if synth_event.type == "fanout.synth.completed"
            else ""
        ),
        "root_result_event_id": (
            synth_event.id
            if "adaptive" in stage_id.lower()
            else ""
        ),
        "provider_operation_summary_status": (
            "available"
            if provider_summary_ref
            else "missing_or_invalid"
            if "adaptive" in stage_id.lower()
            else "not_applicable"
        ),
        "provider_operation_summary_ref": provider_summary_ref or {},
    })
    return descriptor


def merge_research_artifact_payload(
    payload: dict[str, Any],
    descriptor: dict[str, Any],
) -> dict[str, Any]:
    if not descriptor:
        return payload
    refs = list(payload.get("artifact_refs") or [])
    provider_ref = descriptor.get("provider_operation_summary_ref")
    if isinstance(provider_ref, dict) and provider_ref.get("ref"):
        _append_descriptor_once(refs, provider_ref)
    _append_descriptor_once(refs, descriptor)
    return {
        **payload,
        "artifact_refs": refs,
        "research_artifact_ref": str(descriptor.get("ref") or ""),
        "research_artifact_digest": str(
            descriptor.get("sha256") or ""
        ),
        "research_summary": str(descriptor.get("summary") or ""),
        "provider_operation_summary_ref": (
            dict(provider_ref)
            if isinstance(provider_ref, dict)
            else {}
        ),
        "provider_operation_summary_status": str(
            descriptor.get("provider_operation_summary_status") or ""
        ),
    }


def _provider_summary_ref(
    state_dir: Path,
    *,
    synth_raw: dict[str, Any],
    report_raw: dict[str, Any],
    children: list[dict[str, Any]],
    workflow_run_id: str,
    operation_id: str,
    fanout_id: str,
    role_instance: str,
    source_event_id: str,
    max_parallel_agents: int = 4,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    raw = _first_present(
        synth_raw,
        report_raw,
        *[
            child.get("report")
            for child in children
            if isinstance(child.get("report"), dict)
        ],
        keys=("provider_operation_summary",),
        default=None,
    )
    if not isinstance(raw, Mapping):
        return None, [{
            "field": "provider_operation_summary",
            "code": "missing_object",
        }]
    normalized = dict(raw)
    normalized.setdefault("workflow_run_id", workflow_run_id)
    normalized.setdefault("operation_id", operation_id or fanout_id)
    normalized.setdefault(
        "provider_session_id",
        _provider_session_id(
            state_dir,
            role_instance=role_instance,
        ),
    )
    return prepare_provider_operation_summary(
        state_dir=state_dir,
        source_payload={"provider_operation_summary": normalized},
        workflow_run_id=workflow_run_id,
        operation_id=operation_id,
        max_parallel_agents=max_parallel_agents,
        source_event_id=source_event_id,
    )


def _native_parallel_limit(manifest: Mapping[str, Any]) -> int:
    """Read the admitted research root's limit, not its result's claim."""
    children = manifest.get("children") or []
    if len(children) == 1 and isinstance(children[0], Mapping):
        payload = children[0].get("payload") or {}
        value = payload.get("provider_native_max_children") if isinstance(payload, Mapping) else None
        if type(value) is int and value >= 0:
            return value
    return 4


def _provider_session_id(
    state_dir: Path,
    *,
    role_instance: str,
) -> str:
    if not role_instance:
        return ""
    try:
        session_id = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            str(state_dir.parent),
        ).get(role_instance)
    except (OSError, ValueError):
        return ""
    return str(session_id or "")


def _render_markdown(payload: Mapping[str, Any]) -> str:
    identity = (
        dict(payload.get("identity"))
        if isinstance(payload.get("identity"), Mapping)
        else {}
    )
    lines = [
        f"# Research synthesis: {payload.get('topic') or 'Research'}",
        "",
        "## Identity",
        "",
    ]
    for key in (
        "task_id",
        "stage_id",
        "fanout_id",
        "workflow_run_id",
        "request_id",
        "request_revision",
        "synth_event_id",
        "template_id",
        "research_rollout",
    ):
        lines.append(f"- {key}: `{identity.get(key, '')}`")
    lines.extend([
        "",
        "## Summary",
        "",
        str(payload.get("summary") or ""),
    ])
    for title, key in (
        ("Findings", "findings"),
        ("Architecture", "architecture"),
        ("Acceptance Matrix", "acceptance_matrix"),
        ("Test Matrix", "test_matrix"),
        ("Task Map", "task_map"),
        ("Evidence Refs", "evidence_refs"),
        ("Open Questions", "open_questions"),
        ("PRD Prompt Input", "prd_prompt_input"),
        ("Refactor Prompt Input", "refactor_prompt_input"),
        ("Provider Operation Summary Ref", "provider_operation_summary_ref"),
        ("Provider Operation Summary Issues", "provider_operation_summary_issues"),
        ("Child Provenance", "children"),
    ):
        _append_section(lines, title, payload.get(key))
    lines.extend([
        "",
        "## Canonical Research Payload",
        "",
        "```json",
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        ),
        "```",
    ])
    return "\n".join(lines).rstrip() + "\n"


def _append_section(
    lines: list[str],
    title: str,
    value: Any,
) -> None:
    lines.extend(["", f"## {title}", ""])
    if value in (None, "", [], {}):
        lines.append("- None.")
        return
    if isinstance(value, str):
        lines.append(value)
        return
    lines.extend([
        "```json",
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        ),
        "```",
    ])


def _child_reports(
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for child in manifest.get("children") or []:
        if not isinstance(child, dict):
            continue
        payload = (
            dict(child.get("payload"))
            if isinstance(child.get("payload"), dict)
            else {}
        )
        report = (
            dict(child.get("report"))
            if isinstance(child.get("report"), dict)
            else dict(payload.get("report"))
            if isinstance(payload.get("report"), dict)
            else {}
        )
        summary = str(
            child.get("summary")
            or report.get("summary")
            or payload.get("summary")
            or ""
        ).strip()
        reports.append(redact_obj({
            "child_id": str(
                child.get("child_id")
                or child.get("role_instance")
                or "child"
            ),
            "role_instance": str(child.get("role_instance") or ""),
            "status": str(
                child.get("status")
                or payload.get("status")
                or ""
            ),
            "result_event_id": str(
                child.get("result_event_id")
                or payload.get("result_event_id")
                or child.get("last_event_id")
                or ""
            ),
            "summary": summary or "No summary supplied.",
            "report": report,
            "artifact_refs": payload.get("artifact_refs") or [],
            "evidence_refs": payload.get("evidence_refs")
            or report.get("evidence_refs")
            or [],
            "admitted_call_result_ref": (
                child.get("admitted_call_result_ref") or {}
            ),
        }))
    return reports


def _first_present(
    *sources: Any,
    keys: tuple[str, ...],
    default: Any,
) -> Any:
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for key in keys:
            if key in source and source[key] is not None:
                return source[key]
    return default


def _append_descriptor_once(
    refs: list[Any],
    descriptor: Mapping[str, Any],
) -> None:
    digest = str(
        descriptor.get("sha256")
        or descriptor.get("hash")
        or ""
    ).removeprefix("sha256:")
    ref = str(descriptor.get("ref") or descriptor.get("path") or "")
    if any(
        isinstance(item, Mapping)
        and str(item.get("ref") or item.get("path") or "") == ref
        and str(
            item.get("sha256")
            or item.get("hash")
            or ""
        ).removeprefix("sha256:")
        == digest
        for item in refs
    ):
        return
    refs.append(dict(descriptor))


def _string_refs(*values: Any) -> list[str]:
    out: list[str] = []
    for value in values:
        for item in _strings(value):
            if item not in out:
                out.append(item)
    return out


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                text = str(
                    item.get("ref")
                    or item.get("path")
                    or item.get("uri")
                    or json.dumps(
                        item,
                        sort_keys=True,
                        ensure_ascii=False,
                    )
                ).strip()
            else:
                text = str(item or "").strip()
            if text:
                out.append(text)
        return out
    text = str(value).strip()
    return [text] if text else []


def _safe_id(value: str) -> str:
    return (
        _SAFE_ID_RE.sub("-", str(value or "")).strip("-._")
        or "research"
    )


def _is_research_stage(stage_id: str) -> bool:
    lowered = stage_id.lower()
    return "research" in lowered or "autoresearch" in lowered


__all__ = [
    "RESEARCH_REPORT_SCHEMA_VERSION",
    "materialize_research_fanout_artifact",
    "merge_research_artifact_payload",
]
