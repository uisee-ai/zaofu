"""Deterministic artifacts for ZaoFu refactor review/plan fanouts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.hash import sha256_file
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.plan_artifact_ports import canonical_plan_port_name


REVIEW_READY_EVENT = "zaofu.refactor.review.ready"
PLAN_READY_EVENTS = {"zaofu.refactor.plan.ready", "refactor.plan.ready"}
_INHERITED_PLAN_STRUCTURE_FIELDS = frozenset({
    "artifact_refs",
    "evidence_refs",
    "plan_ports",
    "refactor_plan_md",
    "plan_md",
    "plan_intent",
    "task_map",
    "gates",
    "verification_plan",
    "risk_register",
    "backlog_candidates",
    "required_plan_ports",
    "plan_revision",
    "task_map_generation",
})


@dataclass(frozen=True)
class RefactorArtifactProjection:
    status: str
    artifact_dir: str = ""
    artifact_refs: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "completed"


def build_plan_manifest_payload(
    *,
    projection_payload: dict[str, Any],
    feature_id: str,
    source_event_id: str = "",
    is_replan: bool = False,
) -> dict[str, Any]:
    """Build an ``artifact.manifest.published`` payload for a refactor plan so it
    enters the artifact-ledger version chain (doc 78 W3a).

    ``task_map_history`` / ``delivery_trace`` read these manifests to form the
    re-plan supersedes chain — the latest publish for a ``feature_id`` is
    current and earlier ones grey out (publish order is sufficient; no explicit
    supersedes id is required). The refactor flow previously persisted
    refactor-plan.md + task_map.json to disk but never published a manifest, so
    the chain was empty for refactor runs.

    ``role`` is deliberately NOT ``"orchestrator"`` and no ``product_delivery``
    handoff is set, so the reactor's ``_on_artifact_manifest_published``
    early-returns at the ``_is_orchestrator_final_manifest`` gate (actor=zf-cli,
    role!=orchestrator) and neither the plan-only-done nor the
    product-delivery-spine branch fires.
    """
    digests = projection_payload.get("artifact_digests") or {}
    suffix = " (replan)" if is_replan else ""
    refs: list[dict[str, Any]] = []
    for key, kind, summary in (
        ("task_map_ref", "task_map", "refactor task-map"),
        ("plan_artifact_ref", "implementation_plan", "refactor plan"),
    ):
        path = str(projection_payload.get(key) or "")
        if not path:
            continue
        refs.append({
            "kind": kind,
            "path": path,
            "sha256": _manifest_digest(path, digests),
            "summary": summary + suffix,
            "status": "accepted",
            "source_event_id": source_event_id,
        })
    return {
        "role": "refactor-plan",
        "feature_id": feature_id,
        "artifact_refs": refs,
        "handoff_contract": {"source": "refactor_plan_synthesis"},
    }


def project_refactor_artifacts(
    *,
    state_dir: Path,
    manifest: dict[str, Any],
    success_event: str,
    synth_event: ZfEvent | None = None,
) -> RefactorArtifactProjection | None:
    """Project review/plan fanout results into stable runtime artifacts.

    The event log remains the source of truth. These files are rebuildable
    runtime artifacts under ``project.state_dir`` and are used as review/plan
    handoff contracts for later workflow stages.
    """
    if success_event == REVIEW_READY_EVENT:
        return _project_review_artifacts(state_dir=state_dir, manifest=manifest)
    if success_event in PLAN_READY_EVENTS:
        return _project_plan_artifacts(
            state_dir=state_dir,
            manifest=manifest,
            synth_event=synth_event,
        )
    return None


def rebind_refactor_artifact_projection(
    projection: RefactorArtifactProjection,
    *,
    payload: dict[str, Any],
    state_dir: Path,
    project_root: Path,
) -> RefactorArtifactProjection:
    """Bind relocated refs and their digests back to an immutable projection."""

    artifact_refs = list(dict.fromkeys(
        str(ref) for ref in payload.get("artifact_refs", []) or [] if str(ref)
    ))
    artifact_digests: dict[str, str] = {}
    for ref in artifact_refs:
        path = Path(ref)
        candidates = [path] if path.is_absolute() else [
            (
                state_dir.joinpath(*path.parts[1:])
                if path.parts and path.parts[0] == ".zf"
                else state_dir / path
            ),
            project_root / path,
        ]
        resolved = next(
            (
                candidate
                for candidate in candidates
                if candidate.exists() and candidate.is_file()
            ),
            None,
        )
        if resolved is not None:
            artifact_digests[ref] = sha256_file(resolved)
    rebound = {
        **payload,
        "artifact_refs": artifact_refs,
        "artifact_digests": artifact_digests,
    }
    return replace(
        projection,
        artifact_refs=artifact_refs,
        payload=rebound,
    )


def _project_review_artifacts(
    *,
    state_dir: Path,
    manifest: dict[str, Any],
) -> RefactorArtifactProjection:
    fanout_id = _manifest_str(manifest, "fanout_id")
    artifact_dir = _artifact_dir(state_dir, fanout_id)
    child_records = _child_records(state_dir, manifest)
    diagnostics: list[str] = []
    failed_children: list[str] = []
    if not child_records:
        diagnostics.append("review has no child reports")

    coverage: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    uncovered: list[dict[str, Any]] = []
    for record in child_records:
        child_id = str(record.get("child_id") or "")
        report = _record_report(record)
        if not report:
            diagnostics.append(f"{child_id}: missing report")
            failed_children.append(child_id)
            continue
        matrix = _coerce_list(_field(record, "coverage_matrix"))
        if not matrix:
            diagnostics.append(f"{child_id}: missing coverage_matrix")
            failed_children.append(child_id)
        coverage.extend(_tag_items(matrix, child_id=child_id))

        evidence_refs = _coerce_str_list(_field(record, "evidence_refs"))
        if not evidence_refs:
            diagnostics.append(f"{child_id}: missing evidence_refs")
            failed_children.append(child_id)

        findings.extend(_tag_items(_coerce_list(report.get("findings")), child_id=child_id))
        uncovered_value = _field(record, "uncovered")
        if uncovered_value not in (None, "", []):
            uncovered.extend(_tag_items(_coerce_list(uncovered_value), child_id=child_id))

    paths = {
        "review_artifact_ref": artifact_dir / "review.md",
        "coverage_matrix_ref": artifact_dir / "coverage_matrix.json",
        "findings_ref": artifact_dir / "findings.json",
        "uncovered_ref": artifact_dir / "uncovered.md",
    }
    _write_json(paths["coverage_matrix_ref"], coverage)
    _write_json(paths["findings_ref"], findings)
    atomic_write_text(paths["uncovered_ref"], _render_uncovered_md(uncovered))
    atomic_write_text(
        paths["review_artifact_ref"],
        _render_review_md(
            manifest=manifest,
            child_records=child_records,
            coverage=coverage,
            findings=findings,
            uncovered=uncovered,
            diagnostics=diagnostics,
        ),
    )
    artifact_refs = [str(path) for path in paths.values()]
    payload = {
        "artifact_gate": "passed" if not diagnostics else "failed",
        "artifact_kind": "refactor_review",
        "artifact_dir": str(artifact_dir),
        "artifact_refs": artifact_refs,
        **{key: str(path) for key, path in paths.items()},
        "coverage_entry_count": len(coverage),
        "finding_count": len(findings),
        "uncovered_count": len(uncovered),
    }
    if diagnostics:
        if not failed_children:
            failed_children = [
                str(child.get("child_id") or "")
                for child in manifest.get("children", []) or []
                if isinstance(child, dict) and str(child.get("child_id") or "")
            ]
        payload.update(_artifact_gate_failure_payload(
            diagnostics=diagnostics,
            failed_children=failed_children,
        ))
        diagnostics_path = artifact_dir / "artifact-gate-diagnostics.json"
        _write_json(diagnostics_path, diagnostics)
        payload["diagnostics_ref"] = str(diagnostics_path)
        artifact_refs.append(str(diagnostics_path))
        payload["artifact_digests"] = _artifact_digests(artifact_refs)
        return RefactorArtifactProjection(
            status="failed",
            artifact_dir=str(artifact_dir),
            artifact_refs=artifact_refs,
            payload=payload,
            diagnostics=diagnostics,
        )
    payload["artifact_digests"] = _artifact_digests(artifact_refs)
    return RefactorArtifactProjection(
        status="completed",
        artifact_dir=str(artifact_dir),
        artifact_refs=artifact_refs,
        payload=payload,
    )


def _project_plan_artifacts(
    *,
    state_dir: Path,
    manifest: dict[str, Any],
    synth_event: ZfEvent | None,
) -> RefactorArtifactProjection:
    fanout_id = _manifest_str(manifest, "fanout_id")
    artifact_dir = _artifact_dir(state_dir, fanout_id)
    diagnostics: list[str] = []
    source = _plan_source_record(state_dir, manifest, synth_event)
    if not source:
        diagnostics.append("plan has no source report")
        source = {}

    review_artifact_ref = str(_field(source, "review_artifact_ref") or "")
    plan_intent = str(_field(source, "plan_intent") or "")
    plan_md = _extract_plan_markdown(source)
    from zf.runtime.refactor_plan_task_map import resolve_refactor_task_map

    try:
        task_map = resolve_refactor_task_map(
            state_dir, legacy=_extract_task_map(source), ports=_field(source, "plan_ports"),
            reviewed={key: _field(source, key) for key in (
                "fanout_id", "plan_synth_contract_ref", "plan_synth_contract_digest",
                "reviewed_plan_candidate_digest",
            )},
        )
    except (ValueError, OSError) as exc:
        task_map = None
        diagnostics.append(f"task_map port invalid: {exc}")
    gates = _field(source, "gates") or _field(source, "verification_plan")
    risk_register = _field(source, "risk_register") or []
    backlog_candidates = _field(source, "backlog_candidates") or []
    scan_quality_audit_ref = str(
        _field(source, "scan_quality_audit_ref")
        or _find_artifact_ref(source, "scan", "quality", "audit")
        or ""
    )

    if not review_artifact_ref:
        diagnostics.append("plan missing review_artifact_ref")
    if not plan_md.strip():
        diagnostics.append("plan missing refactor_plan_md")
    if not task_map:
        diagnostics.append("plan missing task_map")
    if not gates:
        diagnostics.append("plan missing gates")

    task_map_obj = _normalize_task_map(task_map)
    task_map_metadata = (
        task_map_obj.get("metadata")
        if isinstance(task_map_obj.get("metadata"), dict)
        else {}
    )
    plan_revision = str(
        task_map_obj.get("plan_revision")
        or task_map_metadata.get("plan_revision")
        or _field(source, "plan_revision")
        or ""
    ).strip()
    task_map_generation = str(
        task_map_obj.get("task_map_generation")
        or task_map_metadata.get("task_map_generation")
        or _field(source, "task_map_generation")
        or ""
    ).strip()
    paths = {
        "plan_artifact_ref": artifact_dir / "refactor-plan.md",
        "task_map_ref": artifact_dir / "task_map.json",
        "risk_register_ref": artifact_dir / "risk-register.json",
        "backlog_candidates_ref": artifact_dir / "backlog-candidates.json",
    }
    atomic_write_text(
        paths["plan_artifact_ref"],
        _render_plan_md(
            plan_md=plan_md,
            review_artifact_ref=review_artifact_ref,
            plan_intent=plan_intent,
            diagnostics=diagnostics,
        ),
    )
    _write_json(paths["task_map_ref"], task_map_obj)
    _write_json(paths["risk_register_ref"], risk_register)
    _write_json(paths["backlog_candidates_ref"], backlog_candidates)
    artifact_refs = [str(path) for path in paths.values()]
    if scan_quality_audit_ref:
        artifact_refs.append(scan_quality_audit_ref)
    artifact_refs = list(dict.fromkeys(artifact_refs))
    payload = {
        "artifact_gate": "passed" if not diagnostics else "failed",
        "artifact_kind": "refactor_plan",
        "artifact_dir": str(artifact_dir),
        "artifact_refs": artifact_refs,
        **{key: str(path) for key, path in paths.items()},
        "scan_quality_audit_ref": scan_quality_audit_ref,
        "review_artifact_ref": review_artifact_ref,
        "plan_intent": plan_intent,
        "task_count": len(task_map_obj.get("tasks", [])),
    }
    declared_plan_ports = task_map_obj.get("required_plan_ports")
    if isinstance(declared_plan_ports, list):
        payload["required_plan_ports"] = list(declared_plan_ports)
        for raw_name in declared_plan_ports:
            logical_name = canonical_plan_port_name(str(raw_name or ""))
            if not logical_name:
                continue
            ref_key = f"{logical_name}_ref"
            if payload.get(ref_key):
                continue
            ref_value = str(_field(source, ref_key) or "").strip()
            if ref_value:
                payload[ref_key] = ref_value
                artifact_refs.append(ref_value)
    plan_ports = _field(source, "plan_ports")
    if isinstance(plan_ports, list):
        payload["plan_ports"] = [
            dict(item) for item in plan_ports if isinstance(item, dict)
        ]
    for key in (
        "requirement_spec_ref",
        "requirement_spec_digest",
        "project_adapter_ref",
        "skill_adapter_plan_ref",
    ):
        value = _field(source, key)
        if value not in (None, "", []):
            payload[key] = value
    if plan_revision:
        payload["plan_revision"] = plan_revision
    if task_map_generation:
        payload["task_map_generation"] = task_map_generation
    # The stable projected plan is the planning result consumed by the
    # artifact-package gate. Child-worktree plan refs are not durable here.
    payload["planning_result_ref"] = str(paths["plan_artifact_ref"])
    source_inventory_ref = str(
        _field(source, "source_inventory_ref")
        or _field(source, "hermes_source_inventory_ref")
        or ""
    ).strip()
    scalar_refs = {
        "inventory_ref": str(_field(source, "inventory_ref") or "").strip(),
        "source_inventory_ref": source_inventory_ref,
        "inventory_coverage_matrix_ref": str(
            _field(source, "inventory_coverage_matrix_ref") or ""
        ).strip(),
        "expected_module_parity_report_paths_ref": str(
            _field(source, "expected_module_parity_report_paths_ref") or ""
        ).strip(),
    }
    for key, value in scalar_refs.items():
        if value:
            payload[key] = value
            artifact_refs.append(value)
    inventory_refs = _coerce_str_list(_field(source, "inventory_refs"))
    if inventory_refs:
        payload["inventory_refs"] = inventory_refs
        artifact_refs.extend(inventory_refs)
    artifact_refs = list(dict.fromkeys(artifact_refs))
    payload["artifact_refs"] = artifact_refs
    for key in (
        "rework_of",
        "rework_attempt",
        "rework_source",
        "rework_feedback",
        "rework_categories",
        "rework_summary",
        "replan_classification",
        "replan",
        "orchestrator_decision",
        "supersedes_plan_fanout_id",
        "supersedes_plan_artifact_refs",
    ):
        value = _field(source, key)
        if value not in (None, "", []):
            payload[key] = value
    if diagnostics:
        failed_children = _plan_failure_children(source, manifest)
        payload.update(_artifact_gate_failure_payload(
            diagnostics=diagnostics,
            failed_children=failed_children,
        ))
        diagnostics_path = artifact_dir / "artifact-gate-diagnostics.json"
        _write_json(diagnostics_path, diagnostics)
        payload["diagnostics_ref"] = str(diagnostics_path)
        artifact_refs.append(str(diagnostics_path))
        payload["artifact_digests"] = _artifact_digests(artifact_refs)
        return RefactorArtifactProjection(
            status="failed",
            artifact_dir=str(artifact_dir),
            artifact_refs=artifact_refs,
            payload=payload,
            diagnostics=diagnostics,
        )
    payload["artifact_digests"] = _artifact_digests(artifact_refs)
    return RefactorArtifactProjection(
        status="completed",
        artifact_dir=str(artifact_dir),
        artifact_refs=artifact_refs,
        payload=payload,
    )


def _artifact_dir(state_dir: Path, fanout_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in fanout_id)
    return state_dir / "artifacts" / (safe or "fanout")


def _artifact_digests(artifact_refs: list[str]) -> dict[str, str]:
    digests: dict[str, str] = {}
    for ref in artifact_refs:
        path = Path(ref)
        if path.exists() and path.is_file():
            digests[str(path)] = sha256_file(path)
    return digests


def _manifest_digest(path: str, provided: dict[str, Any]) -> str:
    ref = str(path or "")
    local = Path(ref)
    if local.exists() and local.is_file():
        return sha256_file(local)
    return str(provided.get(ref) or "")


def _artifact_gate_failure_payload(
    *,
    diagnostics: list[str],
    failed_children: list[str],
) -> dict[str, Any]:
    unique_children = list(dict.fromkeys(
        child_id for child_id in failed_children if child_id
    ))
    detail = "; ".join(diagnostics)
    return {
        "failed_children": unique_children,
        "reason": f"artifact gate failed: {detail}"[:1000],
    }


def _plan_failure_children(
    source: dict[str, Any],
    manifest: dict[str, Any],
) -> list[str]:
    child_id = str(_field(source, "child_id") or "").strip()
    if child_id:
        return [child_id]
    return [
        str(child.get("child_id") or "")
        for child in manifest.get("children", []) or []
        if isinstance(child, dict) and str(child.get("child_id") or "")
    ]


def _child_records(state_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    fanout_id = _manifest_str(manifest, "fanout_id")
    for child in manifest.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        child_id = str(child.get("child_id") or "")
        result_payload = _read_child_result_payload(state_dir, fanout_id, child_id)
        records.append({
            "child_id": child_id,
            "manifest_child": child,
            "payload": result_payload,
            "report": (
                result_payload.get("report")
                if isinstance(result_payload.get("report"), dict)
                else child.get("report") if isinstance(child.get("report"), dict) else {}
            ),
        })
    return records


def _read_child_result_payload(
    state_dir: Path,
    fanout_id: str,
    child_id: str,
) -> dict[str, Any]:
    path = state_dir / "fanouts" / fanout_id / "children" / child_id / "result.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    payload = data.get("payload") if isinstance(data, dict) else {}
    return payload if isinstance(payload, dict) else {}


def _plan_source_record(
    state_dir: Path,
    manifest: dict[str, Any],
    synth_event: ZfEvent | None,
) -> dict[str, Any]:
    records = _child_records(state_dir, manifest)
    planner = records[0] if records else {}
    if synth_event is None or not isinstance(synth_event.payload, dict):
        return planner

    synth_payload = dict(synth_event.payload)
    synth_report = (
        dict(synth_payload.get("report") or {})
        if isinstance(synth_payload.get("report"), dict)
        else {}
    )
    if not planner:
        return {"payload": synth_payload, "report": synth_report}

    planner_payload = (
        dict(planner.get("payload") or {})
        if isinstance(planner.get("payload"), dict)
        else {}
    )
    planner_report = _record_report(planner)
    planner_fields = {
        key: value for key, value in planner_payload.items() if key != "report"
    }
    synth_fields = {
        key: value for key, value in synth_payload.items() if key != "report"
    }
    merged_report = _merge_plan_source_fields(planner_report, synth_report)
    merged_payload = _merge_plan_source_fields(
        planner_report,
        planner_fields,
        synth_report,
        synth_fields,
    )
    merged_payload["report"] = merged_report
    return {
        "manifest_child": planner.get("manifest_child"),
        "payload": merged_payload,
        "report": merged_report,
    }


def _merge_plan_source_fields(*sources: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for source in sources:
        for key, value in source.items():
            inherits_when_empty = (
                key in _INHERITED_PLAN_STRUCTURE_FIELDS
                or key.endswith("_ref")
                or key.endswith("_digest")
            )
            if (
                inherits_when_empty
                and value in (None, "", [], {})
                and merged.get(key) not in (None, "", [], {})
            ):
                continue
            merged[key] = value
    return merged


def _record_report(record: dict[str, Any]) -> dict[str, Any]:
    report = record.get("report")
    return report if isinstance(report, dict) else {}


def _field(record: dict[str, Any], key: str) -> Any:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    report = _record_report(record)
    if key in payload:
        return payload.get(key)
    if key in report:
        return report.get(key)
    trigger_payload = payload.get("trigger_payload")
    if isinstance(trigger_payload, dict) and key in trigger_payload:
        return trigger_payload.get(key)
    report_trigger_payload = report.get("trigger_payload")
    if isinstance(report_trigger_payload, dict) and key in report_trigger_payload:
        return report_trigger_payload.get(key)
    manifest_child = record.get("manifest_child")
    if isinstance(manifest_child, dict):
        child_payload = manifest_child.get("payload")
        if isinstance(child_payload, dict):
            child_trigger_payload = child_payload.get("trigger_payload")
            if isinstance(child_trigger_payload, dict) and key in child_trigger_payload:
                return child_trigger_payload.get(key)
    return None


def _find_artifact_ref(record: dict[str, Any], *needles: str) -> str:
    haystack_refs: list[str] = []
    for key in ("artifact_refs", "evidence_refs"):
        haystack_refs.extend(_coerce_str_list(_field(record, key)))
    lowered_needles = [needle.lower() for needle in needles if needle]
    for ref in haystack_refs:
        lowered = ref.lower()
        if all(needle in lowered for needle in lowered_needles):
            return ref
    return ""


def _extract_plan_markdown(record: dict[str, Any]) -> str:
    for key in ("refactor_plan_md", "plan_md", "plan_markdown"):
        value = _field(record, key)
        if isinstance(value, str) and value.strip():
            return value
    value = _field(record, "plan")
    if isinstance(value, str):
        return value
    if value not in (None, "", []):
        return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"
    return ""


def _extract_task_map(record: dict[str, Any]) -> Any:
    for key in ("task_map", "tasks"):
        value = _field(record, key)
        if value not in (None, "", []):
            return value
    return None


def _normalize_task_map(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        tasks = value.get("tasks") or value.get("children") or value.get("order") or []
        if isinstance(tasks, list):
            return {**value, "tasks": tasks}
        return {**value, "tasks": []}
    if isinstance(value, list):
        return {"tasks": value}
    return {"tasks": []}


def _coerce_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, "", {}):
        return []
    return [value]


def _coerce_str_list(value: Any) -> list[str]:
    return [str(item) for item in _coerce_list(value) if str(item)]


def _tag_items(items: list[Any], *, child_id: str) -> list[dict[str, Any]]:
    tagged: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            tagged.append({"child_id": child_id, **item})
        else:
            tagged.append({"child_id": child_id, "value": item})
    return tagged


def _manifest_str(manifest: dict[str, Any], key: str) -> str:
    value = manifest.get(key)
    return str(value) if value not in (None, "") else ""


def _write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _render_review_md(
    *,
    manifest: dict[str, Any],
    child_records: list[dict[str, Any]],
    coverage: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    uncovered: list[dict[str, Any]],
    diagnostics: list[str],
) -> str:
    lines = [
        "# ZaoFu Refactor Review",
        "",
        f"- fanout_id: `{_manifest_str(manifest, 'fanout_id')}`",
        f"- stage_id: `{_manifest_str(manifest, 'stage_id')}`",
        f"- target_ref: `{_manifest_str(manifest, 'target_ref')}`",
        f"- artifact_gate: `{'failed' if diagnostics else 'passed'}`",
        "",
        "## Diagnostics",
        "",
    ]
    lines.extend([f"- {item}" for item in diagnostics] or ["- none"])
    lines.extend(["", "## Child Summaries", ""])
    for record in child_records:
        report = _record_report(record)
        lines.extend([
            f"### {record.get('child_id', '')}",
            "",
            str(report.get("summary") or ""),
            "",
        ])
    lines.extend([
        "## Coverage Matrix",
        "",
        "```json",
        json.dumps(coverage, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Findings",
        "",
        "```json",
        json.dumps(findings, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Uncovered Areas",
        "",
        _render_uncovered_md(uncovered),
    ])
    return "\n".join(lines).rstrip() + "\n"


def _render_uncovered_md(uncovered: list[dict[str, Any]]) -> str:
    if not uncovered:
        return "- none\n"
    lines: list[str] = []
    for item in uncovered:
        child_id = str(item.get("child_id") or "")
        value = item.get("value", item.get("area", item.get("path", item)))
        lines.append(f"- `{child_id}`: {value}")
    return "\n".join(lines) + "\n"


def _render_plan_md(
    *,
    plan_md: str,
    review_artifact_ref: str,
    plan_intent: str,
    diagnostics: list[str],
) -> str:
    header = [
        "# ZaoFu Refactor Plan",
        "",
        f"- review_artifact_ref: `{review_artifact_ref}`",
        f"- plan_intent: {plan_intent or 'unspecified'}",
        f"- artifact_gate: `{'failed' if diagnostics else 'passed'}`",
        "",
    ]
    if diagnostics:
        header.extend(["## Diagnostics", "", *[f"- {item}" for item in diagnostics], ""])
    return "\n".join(header) + plan_md.rstrip() + "\n"
