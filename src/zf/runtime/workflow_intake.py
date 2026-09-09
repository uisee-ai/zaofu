"""Workflow intake artifact application helpers."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.config.backend_identity import canonical_backend_id
from zf.core.config.loader import ConfigError, load_config
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.core.skills import (
    AdapterSkillResolverInput,
    build_project_adapter_skill_plan,
)
from zf.core.workflow.request_policy import (
    default_lanes_for_kind,
    missing_fields_for_kind,
    required_fields_for_kind,
)
from zf.runtime.workflow_origin import (
    WorkflowOriginError,
    assert_same_workflow_origin,
    build_workflow_origin_binding,
    normalize_workflow_origin_binding,
    workflow_origin_from_request,
)
from zf.runtime.workflow_task_input_artifacts import (
    dedupe_artifact_refs,
    materialize_task_workflow_input,
)

def build_flow_intake(
    *,
    kind: str,
    source_ref: str = "",
    objective: str = "",
    source_root: str = "",
    target_root: str = "",
    backend: str = "",
    lanes: int = 0,
    project_id: str = "",
    project_name: str = "",
    strictness: str = "standard",
    scope: tuple[str, ...] = (),
    parity_scope: tuple[str, ...] = (),
    acceptance: tuple[str, ...] = (),
    constraints: tuple[str, ...] = (),
    open_questions: tuple[str, ...] = (),
    request_id: str = "",
    source: str = "cli",
    created_by: str = "zf-cli",
    channel_id: str = "",
    thread_id: str = "",
    conversation_id: str = "",
    thread_key: str = "",
    origin_binding: dict[str, Any] | None = None,
    source_refs: dict[str, str] | None = None,
    artifact_refs: tuple[Any, ...] = (),
    task_input_binding: dict[str, Any] | None = None,
    task_input_contract: dict[str, Any] | None = None,
    output: Path | None = None,
) -> dict[str, Any]:
    request_id = request_id or _unique_request_id(kind)
    output_path = (output or Path("docs") / "intake" / f"{request_id}.md").expanduser()
    project_root = _project_root_from_intake_path(output_path)
    backend = _resolve_intake_backend(project_root, backend)
    workflow_dir = project_root / "artifacts" / "workflow" / request_id
    output_is_json = output_path.suffix.lower() == ".json"
    intake_json_path = output_path if output_is_json else (
        project_root / "artifacts" / "intake" / f"{request_id}.json"
    )
    intake_markdown_path = (
        output_path.with_suffix(".md") if output_is_json else output_path
    )
    manifest_path = workflow_dir / "workflow-input-manifest.json"
    skill_plan_path = workflow_dir / "skill-adapter-plan.json"
    normalized_origin_binding = (
        normalize_workflow_origin_binding(origin_binding)
        if origin_binding is not None
        else build_workflow_origin_binding(
            source=source,
            project_id=project_id or project_name,
            channel_id=channel_id,
            thread_id=thread_id,
            conversation_id=conversation_id,
            thread_key=thread_key,
        )
    )
    config = _load_project_config(project_root)
    state_dir: Path | None = None
    if config is not None:
        from zf.runtime.workflow_requests import (
            WorkflowRequestError,
            load_workflow_request,
        )

        state_dir = _state_dir_for_config(project_root / "zf.yaml", config)
        existing = load_workflow_request(state_dir, request_id)
        if existing:
            try:
                assert_same_workflow_origin(
                    workflow_origin_from_request(existing),
                    normalized_origin_binding,
                )
            except WorkflowOriginError as exc:
                raise WorkflowRequestError(str(exc)) from exc
    output_path.parent.mkdir(parents=True, exist_ok=True)
    intake_json_path.parent.mkdir(parents=True, exist_ok=True)
    intake_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_dir.mkdir(parents=True, exist_ok=True)

    source_text = _read_text_ref(source_ref)
    objective_text = _compact_text(objective or source_text or source_ref)
    requested_kind = str(kind or "").strip().lower()
    # Parent checkout names are transport context, not request semantics.
    source_hint = Path(source_ref).name if source_ref else ""
    inferred_kind = _infer_request_kind(
        " ".join([kind, objective_text, source_hint])
    )
    effective_kind = (
        inferred_kind if requested_kind == "auto"
        else _normalize_request_kind(requested_kind)
    )
    lanes = lanes or default_lanes_for_kind(effective_kind)
    missing = missing_fields_for_kind(
        effective_kind,
        objective=objective_text,
        source_ref=source_ref,
        source_root=source_root,
        target_root=target_root,
    )
    now = _now_iso()
    normalized_source_refs = {
        str(key): str(value)
        for key, value in (source_refs or {}).items()
        if str(key).strip() and str(value).strip()
    }
    task_input = materialize_task_workflow_input(
        workflow_dir=workflow_dir,
        project_root=project_root,
        source_ref=source_ref,
        source_refs=normalized_source_refs,
        artifact_refs=artifact_refs,
        acceptance=acceptance,
        constraints=constraints,
        scope=scope,
        binding=task_input_binding or {},
        input_contract=task_input_contract or {},
        reference_roots=(state_dir,) if state_dir is not None else (),
    )
    normalized_source_refs = task_input.source_refs
    intake_payload = {
        "schema_version": "workflow.intake.v1",
        "request_id": request_id,
        "source": source,
        "project_id": project_id or project_name,
        "request_kind": requested_kind,
        "inferred_kind": inferred_kind,
        "effective_kind": effective_kind,
        "objective": objective_text,
        "source_root": source_root,
        "target_root": target_root,
        "refs": [source_ref] if source_ref else [],
        "constraints": list(constraints),
        "acceptance": list(acceptance),
        "open_questions": list(open_questions),
        "requested_backend": backend,
        "requested_lanes": lanes,
        "strictness": strictness,
        "scope": list(scope),
        "parity_scope": list(parity_scope),
        "created_by": created_by,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "origin_binding": normalized_origin_binding,
        "source_refs": normalized_source_refs,
        "task_input_binding": dict(task_input_binding or {}),
        "task_input_contract_ref": task_input.contract_ref,
        "task_input_contract_digest": task_input.contract_digest,
        "created_at": now,
    }
    intake_json_path.write_text(
        json.dumps(intake_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    intake_markdown_path.write_text(
        _render_intake_markdown(intake_payload, source_text=source_text),
        encoding="utf-8",
    )
    skill_plan = _build_skill_adapter_plan(
        kind=effective_kind,
        project_root=project_root,
        project_id=project_id or project_name,
        strictness=strictness,
        parity_scope=parity_scope,
    )
    skill_plan_path.write_text(
        json.dumps(skill_plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    matrix_refs = _write_delivery_matrix_drafts(
        workflow_dir=workflow_dir,
        kind=effective_kind,
        objective=objective_text,
        source_ref=source_ref,
        source_root=source_root,
        target_root=target_root,
        lanes=lanes,
        parity_scope=parity_scope,
        skill_plan=skill_plan,
        created_at=now,
    )
    manifest_artifact_refs = [
        *artifact_refs,
        *([source_ref] if source_ref else []),
        *([task_input.contract_ref] if task_input.contract_ref else []),
        str(intake_json_path),
        str(intake_markdown_path),
        str(skill_plan_path),
        *matrix_refs.values(),
    ]
    manifest = {
        "schema_version": "workflow.input_manifest.v1",
        "request_id": request_id,
        "kind": effective_kind,
        "request_kind": requested_kind,
        "source": source,
        "project_id": project_id or project_name,
        "objective": objective_text,
        "source_ref": source_ref,
        "source_root": source_root,
        "target_root": target_root,
        "requested_backend": backend,
        "requested_lanes": lanes,
        "strictness": strictness,
        "scope": list(scope),
        "parity_scope": list(parity_scope),
        "channel_id": channel_id,
        "thread_id": thread_id,
        "origin_binding": normalized_origin_binding,
        "source_refs": normalized_source_refs,
        "task_input_binding": dict(task_input_binding or {}),
        "task_input_contract_ref": task_input.contract_ref,
        "task_input_contract_digest": task_input.contract_digest,
        "intake_ref": str(output_path),
        "intake_json_ref": str(intake_json_path),
        "intake_markdown_ref": str(intake_markdown_path),
        "skill_adapter_plan_ref": str(skill_plan_path),
        **matrix_refs,
        "workflow_dir": str(workflow_dir),
        "required_fields": required_fields_for_kind(effective_kind),
        "missing_required_fields": missing,
        "acceptance": list(acceptance),
        "constraints": list(constraints),
        "open_questions": list(open_questions),
        "artifact_refs": dedupe_artifact_refs(manifest_artifact_refs),
        "created_at": now,
    }
    manifest["workflow_input_manifest_ref"] = str(manifest_path)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    request_projection: dict[str, Any] = {}
    request_projection_ref = ""
    if config is not None:
        from zf.runtime.workflow_requests import (
            register_workflow_intake,
            workflow_request_path,
        )

        state_dir = _state_dir_for_config(project_root / "zf.yaml", config)
        state_dir.mkdir(parents=True, exist_ok=True)
        writer = EventWriter(event_log_from_project(state_dir, config=config))
        request_projection = register_workflow_intake(
            state_dir,
            manifest_path,
            actor=created_by,
            writer=writer,
        )
        request_projection_ref = str(workflow_request_path(state_dir, request_id))
    return {
        "schema_version": "workflow.intake.result.v1",
        "request_id": request_id,
        "request_kind": requested_kind,
        "effective_kind": effective_kind,
        "intake_ref": str(output_path),
        "intake_json_ref": str(intake_json_path),
        "intake_markdown_ref": str(intake_markdown_path),
        "workflow_input_manifest_ref": str(manifest_path),
        "skill_adapter_plan_ref": str(skill_plan_path),
        **matrix_refs,
        "missing_required_fields": missing,
        "request_status": str(request_projection.get("status") or (
            "clarifying" if missing or open_questions else "draft"
        )),
        "request_projection_ref": request_projection_ref,
    }


def _normalize_request_kind(kind: str) -> str:
    value = str(kind or "").strip().lower()
    if value == "feat":
        return "prd"
    return value

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _unique_request_id(kind: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    safe = "".join(ch if ch.isalnum() else "-" for ch in str(kind or "auto").lower())
    return f"wfint-{safe}-{stamp}"

def _project_root_from_intake_path(path: Path) -> Path:
    expanded = path.expanduser()
    parent = expanded.parent
    if parent.name == "intake" and parent.parent.name == "docs":
        return parent.parent.parent
    if parent.name == "intake" and parent.parent.name == "artifacts":
        return parent.parent.parent
    return Path.cwd()

def _resolve_intake_backend(project_root: Path, backend: str) -> str:
    explicit = str(backend or "").strip()
    if explicit:
        return canonical_backend_id(explicit)
    configured = _project_default_backend(project_root)
    return canonical_backend_id(configured or "codex")

def _project_default_backend(project_root: Path) -> str:
    config_path = Path(project_root) / "zf.yaml"
    if not config_path.exists():
        return ""
    try:
        config = load_config(config_path)
    except ConfigError:
        return ""
    for role in getattr(config, "roles", []) or []:
        for backend in list(getattr(role, "backends", []) or []):
            text = str(backend or "").strip()
            if text and text != "python":
                return canonical_backend_id(text)
        text = str(getattr(role, "backend", "") or "").strip()
        if text and text != "python":
            return canonical_backend_id(text)
    return ""

def _read_text_ref(source_ref: str) -> str:
    if not source_ref:
        return ""
    path = Path(source_ref).expanduser()
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")

def _compact_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())

def _render_intake_markdown(payload: dict[str, Any], *, source_text: str = "") -> str:
    lines = [
        f"# Workflow Intake: {payload['request_id']}",
        "",
        "> ⚠️ 本文件是**展示副本**;submit 读取的真源是同名 JSON manifest。",
        "> 修改任何字段(如 target_root)必须**重跑 `zf flow intake`** 带",
        "> 对应 flags(--target-root/--source-root/--objective)——直接编辑",
        "> 本 md 不会生效(prd-goal e2e 实弹教训)。",
        "",
        f"- schema_version: `{payload['schema_version']}`",
        f"- request_kind: `{payload['request_kind']}`",
        f"- inferred_kind: `{payload['inferred_kind']}`",
        f"- source: `{payload['source']}`",
        f"- project_id: `{payload.get('project_id') or ''}`",
        f"- source_root: `{payload.get('source_root') or ''}`",
        f"- target_root: `{payload.get('target_root') or ''}`",
        f"- requested_backend: `{payload.get('requested_backend') or ''}`",
        f"- requested_lanes: `{payload.get('requested_lanes') or 0}`",
        "",
        "## Objective",
        "",
        str(payload.get("objective") or ""),
        "",
        "## Refs",
        "",
    ]
    refs = payload.get("refs") if isinstance(payload.get("refs"), list) else []
    lines.extend([f"- {ref}" for ref in refs] or ["- none"])
    if source_text:
        lines.extend([
            "",
            "## Source Excerpt",
            "",
            "```text",
            source_text[:8000],
            "```",
        ])
    return "\n".join(lines).rstrip() + "\n"

def _infer_request_kind(text: str) -> str:
    lowered = str(text or "").lower()
    workflow_terms = (
        "research",
        "evidence synthesis",
        "literature review",
        "调研",
        "研究",
        "证据综合",
    )
    refactor_terms = (
        "refactor", "rewrite", "migrate", "parity", "复刻", "重构",
        "迁移", "替代", "对齐旧项目",
    )
    prd_terms = (
        "prd", "product", "build", "new app", "新产品", "从0", "从 0",
        "需求", "产品", "构建",
    )
    issue_terms = (
        "bug", "fix", "issue", "regression", "报错", "修复", "问题",
        "失败", "异常",
    )
    if any(term in lowered for term in workflow_terms):
        return "workflow"
    if any(term in lowered for term in refactor_terms):
        return "refactor"
    if any(term in lowered for term in prd_terms):
        return "prd"
    if any(term in lowered for term in issue_terms):
        return "issue"
    return "issue"

def _build_skill_adapter_plan(
    *,
    kind: str,
    project_root: Path,
    project_id: str = "",
    state_dir: Path | None = None,
    strictness: str = "standard",
    parity_scope: tuple[str, ...] = (),
) -> dict[str, Any]:
    if kind == "workflow":
        catalog = {
            "kind": "registered-generic-workflow-catalog",
            "version": "generic-workflow.v1",
        }
        return {
            "schema_version": "skill.adapter.plan.v2",
            "kind": kind,
            "project_id": project_id,
            "project_key": project_id,
            "strictness": strictness,
            "parity_scope": [],
            "status": "PASS",
            "required_skills": [],
            "recommended_skills": [],
            "loaded_skills": [],
            "missing_required_skills": [],
            "missing_recommended_skills": [],
            "missing_skills": [],
            "discovered_project_skills": [],
            "roleSkillBundles": {},
            "role_skill_bundles_patch": {},
            "diagnostics": [],
            "policy": {
                "source_ref": "registered-generic-workflow-catalog",
                "sha256": hashlib.sha256(
                    json.dumps(
                        catalog,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "fallback": "registered_template_only",
                "strictness_stop_values": [],
            },
            "proposed_skill_backlogs": [],
            "created_at": _now_iso(),
        }
    config = _load_project_config(project_root)
    return build_project_adapter_skill_plan(AdapterSkillResolverInput(
        kind=kind,
        project_root=project_root,
        project_id=project_id,
        state_dir=state_dir,
        config=config,
        strictness=strictness,
        parity_scope=parity_scope,
    ))

def _write_delivery_matrix_drafts(
    *,
    workflow_dir: Path,
    kind: str,
    objective: str,
    source_ref: str,
    source_root: str,
    target_root: str,
    lanes: int,
    parity_scope: tuple[str, ...],
    skill_plan: dict[str, Any],
    created_at: str,
) -> dict[str, str]:
    if kind == "workflow":
        return {}
    source_text = _read_text_ref(source_ref)
    extracted_acceptance = _extract_acceptance_criteria(source_text)
    extracted_commands = _extract_verification_commands(source_text)
    surfaces = _delivery_surfaces_for_kind(
        kind,
        parity_scope=parity_scope,
        source_text=source_text,
        target_root=target_root,
    )
    lane_count = max(int(lanes or 1), 1)
    capabilities = []
    acceptances = []
    test_commands = []
    tasks = []
    real_e2e = []
    inventory = []
    for index, surface in enumerate(surfaces):
        cap_id = _safe_matrix_id(f"{kind}-{surface}")
        capability = {
            "id": cap_id,
            "capability_id": cap_id,
            "name": surface,
            "kind": kind,
            "surface": surface,
            "priority": "p0" if index == 0 else "p1",
            "status": "planned",
            "source_ref": source_ref,
            "source_root": source_root,
            "target_root": target_root,
            "source_evidence": {
                "extracted_acceptance_count": len(extracted_acceptance),
                "extracted_command_count": len(extracted_commands),
            },
        }
        inventory.append({
            "id": cap_id,
            "capability_id": cap_id,
            "name": surface,
            "priority": capability["priority"],
            "source_ref": source_ref or source_root,
            "status": "draft",
        })
        test_id = f"test-{cap_id}"
        task_id = f"TASK-{cap_id.upper()}"
        capabilities.append(capability)
        related_commands = (
            extracted_commands
            if surface == "cli"
            else []
        )
        test_commands.append({
            "id": test_id,
            "command_id": test_id,
            "capability_id": cap_id,
            "acceptance_ids": [],
            "tier": "real-e2e" if _surface_needs_real_e2e(surface) else "integration",
            "command": " && ".join(related_commands),
            "command_source": "source_prd" if related_commands else "project-adapter-skill",
            "status": "planned",
            "evidence_required": True,
        })
        tasks.append({
            "id": task_id,
            "task_id": task_id,
            "capability_id": cap_id,
            "title": f"Implement and verify {surface}",
            "lane_id": f"lane-{index % lane_count}",
            "role": "dev",
            "status": "planned",
        })
        if _surface_needs_real_e2e(surface):
            command = ""
            command_source = "project-adapter-skill"
            command_hint = (
                "Replace with a project-specific real command such as CLI smoke, "
                "Docker Playwright, live LLM provider probe, or gateway webhook drill."
            )
            if related_commands:
                command = " && ".join(related_commands)
                command_source = "source_prd"
                command_hint = "Extracted from the source PRD acceptance/test instructions."
            real_e2e.append({
                "id": f"e2e-{cap_id}",
                "surface": surface,
                "capability_id": cap_id,
                "status": "planned",
                "command": command,
                "command_required": True,
                "command_source": command_source,
                "command_hint": command_hint,
                "evidence_refs": [],
                "required": True,
            })
    capability_by_surface = {
        str(row["surface"]): str(row["capability_id"])
        for row in capabilities
    }
    default_capability_id = (
        capability_by_surface.get("product")
        or (str(capabilities[0]["capability_id"]) if capabilities else _safe_matrix_id(kind))
    )
    if extracted_acceptance:
        for criteria_index, criteria in enumerate(extracted_acceptance, start=1):
            cap_id = _capability_for_acceptance(
                criteria,
                capability_by_surface,
                default_capability_id=default_capability_id,
            )
            acceptance_id = f"accept-{_safe_matrix_id(f'{cap_id}-{criteria_index}')}"
            acceptances.append({
                "id": acceptance_id,
                "acceptance_id": acceptance_id,
                "capability_id": cap_id,
                "criteria": criteria,
                "source": "source_prd",
                "status": "planned",
                "evidence_required": True,
            })
    else:
        for row in capabilities:
            cap_id = str(row["capability_id"])
            surface = str(row["surface"])
            acceptance_id = f"accept-{cap_id}"
            acceptances.append({
                "id": acceptance_id,
                "acceptance_id": acceptance_id,
                "capability_id": cap_id,
                "criteria": f"{surface} capability satisfies the workflow objective",
                "source": "portable_draft",
                "status": "planned",
                "evidence_required": True,
            })
    first_acceptance_by_capability: dict[str, str] = {}
    for row in acceptances:
        first_acceptance_by_capability.setdefault(
            str(row["capability_id"]),
            str(row["acceptance_id"]),
        )
    for row in test_commands:
        cap_id = str(row["capability_id"])
        acceptance_id = (
            first_acceptance_by_capability.get(cap_id)
            or (str(acceptances[0]["acceptance_id"]) if acceptances else "")
        )
        row["acceptance_ids"] = [acceptance_id] if acceptance_id else []
    loaded_skills = skill_plan.get("loaded_skills")
    if not isinstance(loaded_skills, list):
        loaded_skills = []
    adapter_skills = [
        {
            "name": str(item.get("name") or ""),
            "sha256": str(item.get("sha256") or ""),
        }
        for item in loaded_skills
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    adapter_skill_plan_path = workflow_dir / "skill-adapter-plan.json"
    adapter_skill_plan_digest = (
        hashlib.sha256(adapter_skill_plan_path.read_bytes()).hexdigest()
        if adapter_skill_plan_path.exists()
        else hashlib.sha256(
            json.dumps(
                skill_plan,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )
    metadata = {
        "objective": objective,
        "adapter_skills": adapter_skills,
        "adapter_skill_plan_ref": str(adapter_skill_plan_path),
        "adapter_skill_plan_digest": adapter_skill_plan_digest,
        "created_at": created_at,
        "source": "zf-flow-intake",
        "enrichment_contract": _delivery_matrix_enrichment_contract(
            kind,
            parity_scope=tuple(parity_scope),
        ),
    }
    refs = {
        "source_inventory_ref": workflow_dir / "source-inventory.json",
        "capability_matrix_ref": workflow_dir / "capability-matrix.json",
        "acceptance_matrix_ref": workflow_dir / "acceptance-matrix.json",
        "test_matrix_ref": workflow_dir / "test-matrix.json",
        "task_map_ref": workflow_dir / "task-map.json",
        "real_e2e_matrix_ref": workflow_dir / "real-e2e-matrix.json",
    }
    _write_matrix_json(refs["source_inventory_ref"], "source-inventory.v1", "items", inventory, metadata)
    _write_matrix_json(refs["capability_matrix_ref"], "capability-matrix.v1", "capabilities", capabilities, metadata)
    _write_matrix_json(refs["acceptance_matrix_ref"], "acceptance-matrix.v1", "acceptance", acceptances, metadata)
    _write_matrix_json(
        refs["test_matrix_ref"],
        "test-matrix.v1",
        "commands",
        test_commands,
        metadata,
    )
    _write_matrix_json(refs["task_map_ref"], "task-map.v1", "tasks", tasks, metadata)
    _write_matrix_json(refs["real_e2e_matrix_ref"], "real-e2e-matrix.v1", "rows", real_e2e, metadata)
    return {key: str(value) for key, value in refs.items()}

def _write_matrix_json(
    path: Path,
    schema_version: str,
    row_key: str,
    rows: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> None:
    payload = {
        "schema_version": schema_version,
        "status": "draft",
        "metadata": metadata,
        row_key: rows,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

def _delivery_matrix_enrichment_contract(
    kind: str,
    *,
    parity_scope: tuple[str, ...],
) -> dict[str, Any]:
    surfaces = _delivery_surfaces_for_kind(kind, parity_scope=parity_scope)
    return {
        "schema_version": "delivery-matrix-enrichment-contract.v1",
        "status": "requires_scan_plan_enrichment",
        "owner": "project-adapter-skill",
        "principle": (
            "Runtime generated a portable draft only. Scan/plan skills must "
            "replace placeholders with project facts before final judge."
        ),
        "required_updates": [
            "source_inventory must cite concrete source files/modules.",
            "capability_matrix must map source behavior to target behavior.",
            "acceptance_matrix must state user-visible acceptance criteria.",
            "test_matrix.commands[] is the only command registry and must include deterministic verification commands or evidence refs.",
            "task_map must assign each blocking capability to a lane/role.",
            "real_e2e_matrix must declare real command/evidence for surfaces that need live validation.",
        ],
        "command_policy": {
            "mode": "declared_only",
            "runtime_behavior": "real E2E runner executes only commands declared in real_e2e_matrix rows.",
            "adapter_requirement": (
                "For each required real-e2e row, scan/plan/verify skills must replace empty command "
                "placeholders with project-specific commands or attach passing evidence_refs with a "
                "clear reason."
            ),
            "forbidden": [
                "Do not hard-code project commands in runtime.",
                "Do not mark a required real-e2e row passed without command output or evidence_refs.",
                "Do not use mock-only commands for release/full-parity validation unless objective explicitly says mock.",
            ],
        },
        "flow_kind": kind,
        "surfaces": surfaces,
        "adapter_skill_phases": ["scan", "plan", "verify", "real_e2e"],
    }

def _delivery_surfaces_for_kind(
    kind: str,
    *,
    parity_scope: tuple[str, ...],
    source_text: str = "",
    target_root: str = "",
) -> list[str]:
    explicit = [str(item).strip() for item in parity_scope if str(item).strip()]
    if explicit:
        return list(dict.fromkeys(explicit))
    if kind == "issue":
        return ["regression"]
    if kind == "prd":
        inferred = _infer_prd_surfaces(source_text, target_root=target_root)
        if inferred:
            return inferred
        return ["product", "cli", "web"]
    if kind == "refactor":
        return ["core", "cli", "api", "web", "runtime"]
    return ["core"]

def _infer_prd_surfaces(source_text: str, *, target_root: str = "") -> list[str]:
    text = (source_text or "").lower()
    target = (target_root or "").lower()
    surfaces: list[str] = ["product"]
    cli_terms = (
        "cli", "command", "命令", "terminal", "stdout", "stdin",
        "node ", "npm ", "python ", "uv ", "bin/", "src/index",
    )
    web_terms = (
        "web", "browser", "dashboard", "web ui", "页面", "前端", "react",
        "next.js", "playwright", "http://", "https://",
    )
    api_terms = ("api", "http endpoint", "rest", "graphql", "接口")
    if any(term in text for term in cli_terms) or target.endswith("/cli"):
        surfaces.append("cli")
    if any(term in text for term in web_terms):
        surfaces.append("web")
    if any(term in text for term in api_terms):
        surfaces.append("api")
    return list(dict.fromkeys(surfaces))

_COMMAND_START_RE = re.compile(
    r"^(?:npm|pnpm|yarn|bun|node|python|python3|uv|pytest|npx|docker|curl|go|cargo|deno)\b"
)

_COMMAND_SECTION_TERMS = (
    "acceptance",
    "test",
    "testing",
    "validation",
    "verification",
    "验收",
    "测试",
    "验证",
)

_EXAMPLE_SECTION_TERMS = (
    "usage",
    "synopsis",
    "example",
    "examples",
    "用法",
    "示例",
)

_COMMAND_PLACEHOLDER_RE = re.compile(
    r"(?:<[^>]+>|\{[^}]+\}|\[[^\]]+\]|\.\.\.|"
    r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9_-]*(?:=[A-Z][A-Z0-9_|-]*)?(?![A-Za-z0-9_]))"
)

def _extract_verification_commands(source_text: str) -> list[str]:
    commands: list[str] = []
    trusted_section = False
    example_section = False
    for raw_line in (source_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            header = line.lstrip("#").strip().lower()
            trusted_section = any(term in header for term in _COMMAND_SECTION_TERMS)
            example_section = any(term in header for term in _EXAMPLE_SECTION_TERMS)
            continue
        if example_section:
            continue
        explicit_prefix = bool(
            re.match(r"^(?:run|command|执行|命令)\s*:\s*", line, re.IGNORECASE)
        )
        if explicit_prefix:
            line = re.sub(
                r"^(?:run|command|执行|命令)\s*:\s*",
                "",
                line,
                flags=re.IGNORECASE,
            ).strip()
        explicit_shell = line.startswith("$ ")
        if not trusted_section and not explicit_prefix and not explicit_shell:
            continue
        inline = re.findall(r"`([^`]+)`", raw_line)
        inline_commands = [
            item.strip()
            for item in inline
            if _is_declared_verification_command(item.strip())
        ]
        if inline_commands:
            commands.extend(inline_commands)
            continue
        line = re.sub(r"^[-*+]\s+", "", line).strip()
        line = re.sub(r"^\d+[.)]\s+", "", line).strip()
        line = line.strip("`")
        if line.startswith("$ "):
            line = line[2:].strip()
        if _is_declared_verification_command(line):
            commands.append(line)
            continue
    return list(dict.fromkeys(commands))

def _is_declared_verification_command(value: str) -> bool:
    command = str(value or "").strip()
    if not _COMMAND_START_RE.match(command):
        return False
    return _COMMAND_PLACEHOLDER_RE.search(command) is None

def _extract_acceptance_criteria(source_text: str) -> list[str]:
    criteria: list[str] = []
    in_acceptance = False
    for raw_line in (source_text or "").splitlines():
        stripped = raw_line.strip()
        lowered = stripped.lower()
        if not stripped:
            if in_acceptance and criteria:
                break
            continue
        if stripped.startswith("#"):
            header = stripped.lstrip("#").strip().lower()
            in_acceptance = any(
                term in header
                for term in ("acceptance", "验收", "criteria", "test", "验证")
            )
            continue
        if not in_acceptance:
            continue
        line = re.sub(r"^[-*+]\s+", "", stripped).strip()
        line = re.sub(r"^\d+[.)]\s+", "", line).strip()
        if not line:
            continue
        if lowered.startswith(("```", "---")):
            continue
        criteria.append(line)
    return list(dict.fromkeys(criteria))

def _capability_for_acceptance(
    criteria: str,
    capability_by_surface: dict[str, str],
    *,
    default_capability_id: str,
) -> str:
    text = (criteria or "").lower()
    if any(
        term in text
        for term in (
            "cli", "command", "命令", "stdout", "stdin",
            "node ", "npm ", "python ", "uv ", "bin/", "src/index",
        )
    ) and "cli" in capability_by_surface:
        return capability_by_surface["cli"]
    if any(term in text for term in ("web", "browser", "页面", "playwright")):
        if "web" in capability_by_surface:
            return capability_by_surface["web"]
    if any(term in text for term in ("api", "http", "endpoint", "接口")):
        if "api" in capability_by_surface:
            return capability_by_surface["api"]
    return default_capability_id

def _surface_needs_real_e2e(surface: str) -> bool:
    value = surface.strip().lower()
    return value in {
        "api",
        "browser",
        "cli",
        "dashboard",
        "e2e",
        "gateway",
        "llm",
        "provider",
        "tui",
        "web",
        "webui",
    }

def _safe_matrix_id(value: str) -> str:
    return "-".join(
        chunk for chunk in "".join(
            ch.lower() if ch.isalnum() else "-"
            for ch in value
        ).split("-")
        if chunk
    ) or "capability"

def _load_project_config(project_root: Path) -> Any | None:
    config_path = project_root.expanduser() / "zf.yaml"
    if not config_path.exists():
        return None
    try:
        return load_config(config_path)
    except ConfigError:
        return None

def _state_dir_for_config(config_path: Path, config: Any) -> Path:
    state_raw = str(getattr(getattr(config, "project", None), "state_dir", "") or ".zf")
    state = Path(state_raw).expanduser()
    if not state.is_absolute():
        state = config_path.expanduser().resolve().parent / state
    return state.resolve()
