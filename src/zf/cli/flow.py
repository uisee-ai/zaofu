"""zf flow — draft and preflight short controller workflows."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from zf.core.config.backend_identity import canonical_backend_id
from zf.core.config.loader import ConfigError, load_config
from zf.core.config.render import build_config_inspection_report
from zf.core.events import ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.core.safety.path_guard import PathGuard, PathGuardError
from zf.core.skills import (
    AdapterSkillResolverInput,
    build_project_adapter_skill_plan,
)
from zf.core.workflow.request_policy import (
    default_lanes_for_kind,
    missing_fields_for_kind,
    required_fields_for_kind,
)
from zf.runtime.preflight import preflight_ok, run_preflight_checks
from zf.runtime.run_contract import (
    build_run_contract,
    evaluate_run_contract_submit_binding,
    load_run_contract,
    required_delivery_artifacts,
    write_run_contract,
)


_LLM_ENV_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "DOUBAO_API_KEY",
    "ARK_API_KEY",
    "DEEPSEEK_API_KEY",
    "KIMI_API_KEY",
    "MOONSHOT_API_KEY",
    "QWEN_API_KEY",
    "DASHSCOPE_API_KEY",
    "GLM_API_KEY",
    "ZHIPUAI_API_KEY",
)

_REQUEST_KIND_CHOICES = ["issue", "prd", "refactor", "feat", "auto"]
_FLOW_KIND_CHOICES = ["issue", "prd", "refactor", "feat"]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "flow",
        help="Draft and preflight short IssueFlow/PrdFlow/RefactorFlow specs",
    )
    sub = parser.add_subparsers(dest="flow_cmd")

    intake = sub.add_parser("intake", help="Create a workflow intake artifact")
    intake.add_argument("--kind", required=True, choices=_REQUEST_KIND_CHOICES)
    intake.add_argument("--from", dest="source_ref", default="")
    intake.add_argument("--objective", default="")
    intake.add_argument("--source-root", default="")
    intake.add_argument("--target", "--target-root", dest="target_root", default="")
    intake.add_argument("--backend", default="")
    intake.add_argument("--lanes", type=int, default=0)
    intake.add_argument("--project-id", default="")
    intake.add_argument("--project-name", default="")
    intake.add_argument("--strictness", default="standard")
    intake.add_argument("--parity-scope", default="")
    intake.add_argument("--acceptance", action="append", default=[])
    intake.add_argument("--constraint", action="append", default=[])
    intake.add_argument("--open-question", action="append", default=[])
    intake.add_argument("--request-id", default="")
    intake.add_argument("--output", type=Path, default=None)
    intake.add_argument("--json", action="store_true")
    intake.set_defaults(func=run_intake)

    classify = sub.add_parser("classify", help="Classify a workflow intake artifact")
    classify.add_argument("--intake", type=Path, required=True)
    classify.add_argument("--kind", choices=_REQUEST_KIND_CHOICES, default="auto")
    classify.add_argument("--output", type=Path, default=None)
    classify.add_argument("--json", action="store_true")
    classify.set_defaults(func=run_classify)

    clarify = sub.add_parser(
        "clarify",
        help="Revise and optionally confirm a workflow requirement",
    )
    clarify.add_argument("--config", type=Path, default=Path("zf.yaml"))
    clarify.add_argument("--intake", type=Path, required=True)
    clarify.add_argument("--objective", default=None)
    clarify.add_argument("--source-root", default=None)
    clarify.add_argument("--target", "--target-root", dest="target_root", default=None)
    clarify.add_argument("--acceptance", action="append", default=None)
    clarify.add_argument("--constraint", action="append", default=None)
    clarify.add_argument("--open-question", action="append", default=None)
    clarify.add_argument("--confirm", action="store_true")
    clarify.add_argument("--actor", default="zf-cli")
    clarify.add_argument("--json", action="store_true")
    clarify.set_defaults(func=run_clarify)

    draft = sub.add_parser("draft", help="Draft a short controller flow YAML")
    draft.add_argument("--kind", required=True, choices=_FLOW_KIND_CHOICES)
    draft.add_argument("--from", dest="source_ref", default="")
    draft.add_argument("--source-root", default="")
    draft.add_argument("--target", "--target-root", dest="target_root", default="")
    draft.add_argument("--backend", default="codex")
    draft.add_argument("--lanes", type=int, default=0)
    draft.add_argument("--project-name", default="")
    draft.add_argument("--state-dir", default="")
    draft.add_argument("--strictness", default="standard")
    draft.add_argument("--parity-scope", default="")
    draft.add_argument("--output", type=Path, default=None)
    draft.set_defaults(func=run_draft)

    preflight = sub.add_parser("preflight", help="Check start readiness")
    preflight.add_argument("--config", type=Path, default=Path("zf.yaml"))
    preflight.add_argument("--kind", choices=_FLOW_KIND_CHOICES, default="")
    preflight.add_argument("--intake", type=Path, default=None)
    preflight.add_argument("--json", action="store_true")
    preflight.add_argument(
        "--allow-missing-env",
        action="store_true",
        help="Do not block on real_env_required local env/tool misses",
    )
    preflight.set_defaults(func=run_preflight)

    start = sub.add_parser(
        "start",
        help="Build a safe flow-start proposal; use --dry-run for now",
    )
    start.add_argument("--kind", required=True, choices=_FLOW_KIND_CHOICES)
    start.add_argument("--from", dest="source_ref", default="")
    start.add_argument("--source-root", default="")
    start.add_argument("--target", "--target-root", dest="target_root", default="")
    start.add_argument("--backend", default="codex")
    start.add_argument("--lanes", type=int, default=0)
    start.add_argument("--project-name", default="")
    start.add_argument("--state-dir", default="")
    start.add_argument("--strictness", default="standard")
    start.add_argument("--parity-scope", default="")
    start.add_argument("--output", type=Path, default=None)
    start.add_argument("--json", action="store_true")
    start.add_argument("--dry-run", action="store_true")
    start.add_argument(
        "--allow-missing-env",
        action="store_true",
        help="Do not block dry-run readiness on local env/tool misses",
    )
    start.set_defaults(func=run_start)

    submit = sub.add_parser(
        "submit",
        help="Build a workflow submit event preview; use --dry-run for now",
    )
    submit.add_argument("--config", type=Path, required=True)
    submit.add_argument("--intake", type=Path, required=True)
    submit.add_argument("--kind", choices=_FLOW_KIND_CHOICES, default="")
    submit.add_argument("--task-id", default="")
    submit.add_argument("--pattern-id", default="")
    submit.add_argument("--requested-by", default="zf-cli")
    submit.add_argument("--reason", default="")
    submit.add_argument("--output", type=Path, default=None)
    submit.add_argument("--json", action="store_true")
    submit.add_argument("--dry-run", action="store_true")
    submit.add_argument("--apply", action="store_true")
    submit.add_argument(
        "--allow-missing-env",
        action="store_true",
        help="Do not block dry-run readiness on local env/tool misses",
    )
    submit.set_defaults(func=run_submit)

    parser.set_defaults(func=_no_sub)


def _no_sub(args: argparse.Namespace) -> int:
    print(
        "Error: `zf flow` requires a subcommand: "
        "intake | classify | clarify | draft | preflight | submit | start",
        file=sys.stderr,
    )
    return 2


def run_intake(args: argparse.Namespace) -> int:
    result = build_flow_intake(
        kind=args.kind,
        source_ref=args.source_ref,
        objective=args.objective,
        source_root=args.source_root,
        target_root=args.target_root,
        backend=args.backend,
        lanes=args.lanes,
        project_id=args.project_id,
        project_name=args.project_name,
        strictness=args.strictness,
        parity_scope=_parse_csv(args.parity_scope),
        acceptance=tuple(args.acceptance),
        constraints=tuple(args.constraint),
        open_questions=tuple(args.open_question),
        request_id=args.request_id,
        output=args.output,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"workflow intake: {result['request_id']}")
        print(f"- kind: `{result['request_kind']}`")
        print(f"- intake: `{result['intake_ref']}`")
        print(f"- manifest: `{result['workflow_input_manifest_ref']}`")
        missing = result.get("missing_required_fields") or []
        if missing:
            print(f"- missing: `{', '.join(missing)}`")
    return 0


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
    parity_scope: tuple[str, ...] = (),
    acceptance: tuple[str, ...] = (),
    constraints: tuple[str, ...] = (),
    open_questions: tuple[str, ...] = (),
    request_id: str = "",
    source: str = "cli",
    created_by: str = "zf-cli",
    channel_id: str = "",
    thread_id: str = "",
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    intake_json_path.parent.mkdir(parents=True, exist_ok=True)
    intake_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_dir.mkdir(parents=True, exist_ok=True)

    source_text = _read_text_ref(source_ref)
    objective_text = _compact_text(objective or source_text or source_ref)
    requested_kind = str(kind or "").strip().lower()
    inferred_kind = _infer_request_kind(
        " ".join([kind, objective_text, source_ref, source_root, target_root])
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
        "parity_scope": list(parity_scope),
        "created_by": created_by,
        "channel_id": channel_id,
        "thread_id": thread_id,
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
    artifact_refs = [
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
        "parity_scope": list(parity_scope),
        "channel_id": channel_id,
        "thread_id": thread_id,
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
        "artifact_refs": artifact_refs,
        "created_at": now,
    }
    manifest["workflow_input_manifest_ref"] = str(manifest_path)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    request_projection: dict[str, Any] = {}
    request_projection_ref = ""
    config = _load_project_config(project_root)
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


def run_classify(args: argparse.Namespace) -> int:
    result = build_flow_intent(
        intake_path=args.intake.expanduser(),
        explicit_kind=args.kind,
        output=args.output,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"workflow intent: {result['kind']}")
        print(f"- confidence: `{result['confidence']}`")
        print(f"- next_action: `{result['next_action']}`")
        print(f"- intent: `{result['intent_ref']}`")
        missing = result.get("missing_required_fields") or []
        if missing:
            print(f"- missing: `{', '.join(missing)}`")
    return 0


def build_flow_intent(
    *,
    intake_path: Path,
    explicit_kind: str = "auto",
    output: Path | None = None,
) -> dict[str, Any]:
    if not intake_path.exists():
        raise SystemExit(f"Error: intake file not found: {intake_path}")
    text = intake_path.read_text(encoding="utf-8")
    manifest_path, manifest = _load_manifest_for_intake(intake_path)
    manifest = dict(manifest or {})
    request_id = str(manifest.get("request_id") or _request_id_from_path(intake_path))
    explicit = str(explicit_kind or "auto").strip().lower()
    kind = _normalize_request_kind(explicit) if explicit != "auto" else _infer_request_kind(text)
    if explicit == "auto" and manifest.get("kind") in {"issue", "prd", "refactor"}:
        kind = str(manifest["kind"])
    confidence = "high" if explicit_kind != "auto" or manifest.get("kind") else "medium"
    missing = missing_fields_for_kind(
        kind,
        objective=str(manifest.get("objective") or _compact_text(text)),
        source_ref=str(manifest.get("source_ref") or ""),
        source_root=str(manifest.get("source_root") or ""),
        target_root=str(manifest.get("target_root") or ""),
    )
    workflow_dir = Path(str(manifest.get("workflow_dir") or "") or (
        _project_root_from_intake_path(intake_path) / "artifacts" / "workflow" / request_id
    ))
    workflow_dir.mkdir(parents=True, exist_ok=True)
    intent_path = (output or workflow_dir / "workflow-intent.json").expanduser()
    intent_path.parent.mkdir(parents=True, exist_ok=True)
    next_action = "clarify" if missing else "draft"
    result = {
        "schema_version": "workflow.intent.v1",
        "request_id": request_id,
        "kind": kind,
        "confidence": confidence,
        "reason": _classification_reason(kind, explicit_kind=explicit_kind),
        "missing_required_fields": missing,
        "source_refs": [str(intake_path)],
        "next_action": next_action,
        "intent_ref": str(intent_path),
    }
    intent_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if manifest_path is not None:
        manifest["kind"] = kind
        manifest["intent_ref"] = str(intent_path)
        manifest["missing_required_fields"] = missing
        manifest.setdefault("artifact_refs", [])
        if isinstance(manifest["artifact_refs"], list) and str(intent_path) not in manifest["artifact_refs"]:
            manifest["artifact_refs"].append(str(intent_path))
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return result


def run_clarify(args: argparse.Namespace) -> int:
    from zf.runtime.workflow_requests import revise_workflow_request

    config_path = args.config.expanduser().resolve()
    config = load_config(config_path)
    state_dir = _state_dir_for_config(config_path, config)
    manifest_path, _manifest = _load_manifest_for_intake(args.intake.expanduser())
    if manifest_path is None:
        print("Error: workflow input manifest not found for intake", file=sys.stderr)
        return 1
    writer = EventWriter(event_log_from_project(state_dir, config=config))
    result = revise_workflow_request(
        state_dir,
        manifest_path,
        actor=args.actor,
        objective=args.objective,
        source_root=args.source_root,
        target_root=args.target_root,
        acceptance=args.acceptance,
        constraints=args.constraint,
        open_questions=args.open_question,
        confirm=bool(args.confirm),
        writer=writer,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"workflow request: {result['request_id']}")
        print(f"- status: `{result['status']}`")
        print(f"- revision: `{result['revision']}`")
        print(f"- requirement: `{result['requirement_spec_ref']}`")
        if result.get("missing_required_fields"):
            print(f"- missing: `{', '.join(result['missing_required_fields'])}`")
        if result.get("open_questions"):
            print(f"- open_questions: `{'; '.join(result['open_questions'])}`")
    return 0 if result["status"] != "clarifying" else 1


def run_draft(args: argparse.Namespace) -> int:
    project_root = (
        args.output.expanduser().resolve().parent
        if args.output is not None
        else Path.cwd()
    )
    data = draft_flow_spec(
        kind=args.kind,
        source_ref=args.source_ref,
        source_root=args.source_root,
        target_root=args.target_root,
        backend=args.backend,
        lanes=args.lanes or default_lanes_for_kind(args.kind),
        project_name=args.project_name,
        state_dir=args.state_dir,
        project_root=project_root,
        strictness=args.strictness,
        parity_scope=_parse_csv(args.parity_scope),
    )
    text = yaml.safe_dump_all(data, sort_keys=False, allow_unicode=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


def draft_flow_spec(
    *,
    kind: str,
    source_ref: str = "",
    source_root: str = "",
    target_root: str = "",
    backend: str = "codex",
    lanes: int = 0,
    project_name: str = "",
    state_dir: str = "",
    project_root: Path | None = None,
    strictness: str = "standard",
    parity_scope: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    kind = _normalize_request_kind(kind)
    backend = canonical_backend_id(backend)
    lanes = lanes or default_lanes_for_kind(kind)
    project = project_name or f"{kind}-flow"
    state = state_dir or f".zf-{project}"
    adapter_plan = _build_skill_adapter_plan(
        kind=kind,
        project_root=project_root or Path.cwd(),
        project_id=project,
        state_dir=Path(state),
        strictness=strictness,
        parity_scope=parity_scope,
    )
    role_skill_bundles = _non_empty_mapping(adapter_plan.get("roleSkillBundles"))
    if kind == "issue":
        spec = {
            "lanes": lanes,
            "backend": backend,
            "issueRef": source_ref or "TODO: issue/backlog path",
            "targetRef": "HEAD",
            "qualityFloor": "issue-regression",
            "evidencePolicy": "strict_refs",
            "deliveryPolicy": "report_only",
        }
        flow = {
            "apiVersion": "zaofu.dev/v1",
            "kind": "IssueFlow",
            "metadata": {"name": f"{project}-issue-flow"},
            "spec": spec,
        }
    elif kind == "prd":
        spec = {
            "lanes": lanes,
            "backend": backend,
            "prdRef": source_ref or "TODO: PRD path",
            "targetRef": "HEAD",
            "targetRoot": target_root or "TODO: target app path",
            "qualityFloor": "product-demo",
            "evidencePolicy": "strict_refs",
            "deliveryPolicy": "report_and_demo",
        }
        flow = {
            "apiVersion": "zaofu.dev/v1",
            "kind": "PrdFlow",
            "metadata": {"name": f"{project}-prd-flow"},
            "spec": spec,
        }
    elif kind == "refactor":
        scope = (
            list(parity_scope)
            if parity_scope
            else ["core", "cli", "api", "web", "runtime"]
        )
        spec = {
            "flowProfile": "refactor-flow/v3",
            "lanes": lanes,
            "assembly": "none",
            "roleDefaults": {"backend": backend, "permission_mode": "bypass"},
            "objectiveRef": source_ref or "TODO: refactor prompt path",
            "targetRef": "HEAD",
            "sourceRoot": source_root or "TODO: source project path",
            "targetRoot": target_root or "TODO: target project path",
            "parityScope": scope,
            "gapLoop": "enabled",
            "verifyRescan": "module_parity",
            "completionThreshold": "close_p0_p1",
            "qualityFloor": "refactor-parity-real-env",
            "evidencePolicy": "strict_refs",
            "environmentPolicy": "real_env_required",
            "projectionPolicy": "control_room",
        }
        flow = {
            "apiVersion": "zaofu.dev/v1",
            "kind": "RefactorFlow",
            "metadata": {"name": f"{project}-refactor-flow"},
            "spec": spec,
        }
    else:  # pragma: no cover - argparse guards this.
        raise ValueError(f"unsupported flow kind {kind!r}")
    config = {
        "apiVersion": "zaofu.dev/v1",
        "kind": "ZfConfig",
        "metadata": {"name": project},
        "spec": {
            "version": "1.0",
            "project": {"name": project, "state_dir": state},
            "session": {
                "tmux_session": f"${{ZF_TMUX_SESSION:-{_default_tmux_session(project)}}}",
            },
            **_explicit_orchestrator_spec(backend),
        },
    }
    runtime_profile_name = "flow-draft-runtime/v1"
    runtime_profile = _draft_runtime_profile_doc(
        name=runtime_profile_name,
        backend=backend,
        kind=kind,
        role_skill_bundles=role_skill_bundles,
    )
    config["spec"]["uses"] = [runtime_profile_name]
    skill_sources = _skill_sources_from_adapter_plan(
        adapter_plan,
        project_root=project_root or Path.cwd(),
    )
    if skill_sources:
        config["spec"]["skill_sources"] = skill_sources
    return [flow, runtime_profile, config]


def draft_multi_kind_project_spec(
    *,
    backend: str = "codex",
    lanes: int = 0,
    project_name: str = "",
    state_dir: str = "",
    project_root: Path | None = None,
    strictness: str = "standard",
    parity_scope: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Build one project container that can admit all shipped flow kinds.

    This only assembles configuration.  It intentionally does not create a
    request or emit an entry event: requirement clarification and ignition stay
    explicit request-level operations after project initialization.
    """

    backend = canonical_backend_id(backend)
    project = project_name or "zaofu-project"
    state = state_dir or f".zf-{project}"
    root = project_root or Path.cwd()
    flow_docs: list[dict[str, Any]] = []
    flow_defaults: dict[str, Any] = {}
    skill_sources: dict[tuple[str, str], dict[str, str]] = {}

    for kind in ("issue", "prd", "refactor"):
        docs = draft_flow_spec(
            kind=kind,
            backend=backend,
            lanes=lanes or default_lanes_for_kind(kind),
            project_name=project,
            state_dir=state,
            project_root=root,
            strictness=strictness,
            parity_scope=parity_scope,
        )
        flow_docs.append(docs[0])
        profile_spec = docs[1].get("spec") or {}
        defaults = profile_spec.get("flow_defaults") or {}
        if isinstance(defaults, dict) and isinstance(defaults.get(kind), dict):
            flow_defaults[kind] = dict(defaults[kind])
        config_spec = docs[2].get("spec") or {}
        for source in config_spec.get("skill_sources") or []:
            if not isinstance(source, dict):
                continue
            key = (
                str(source.get("name") or ""),
                str(source.get("path") or ""),
            )
            if all(key):
                skill_sources[key] = dict(source)

    runtime_profile_name = "flow-project-runtime/v1"
    runtime_profile = _draft_runtime_profile_doc(
        name=runtime_profile_name,
        backend=backend,
    )
    if flow_defaults:
        runtime_profile["spec"]["flow_defaults"] = flow_defaults
    config_spec: dict[str, Any] = {
        "version": "1.0",
        "project": {"name": project, "state_dir": state},
        "session": {
            "tmux_session": f"${{ZF_TMUX_SESSION:-{_default_tmux_session(project)}}}",
        },
        **_explicit_orchestrator_spec(backend),
        "uses": [runtime_profile_name],
    }
    if skill_sources:
        config_spec["skill_sources"] = [
            skill_sources[key] for key in sorted(skill_sources)
        ]
    config = {
        "apiVersion": "zaofu.dev/v1",
        "kind": "ZfConfig",
        "metadata": {"name": project},
        "spec": config_spec,
    }
    return [*flow_docs, runtime_profile, config]


def _default_tmux_session(project: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(project or "flow").strip()).strip("-")
    slug = slug.lower()[:48] or "flow"
    return f"zf-{slug}"


def _explicit_orchestrator_spec(backend: str) -> dict[str, Any]:
    return {
        "orchestrator": {
            "backend": backend,
            "wake_min_interval_s": 5,
        },
        "roles": [{
            "name": "orchestrator",
            "instance_id": "orchestrator",
            "role_kind": "reader",
            "backend": backend,
            "permission_mode": "bypass",
            "transport": "tmux",
            "stuck_threshold_seconds": 900,
            "spawn_ready_timeout_seconds": 240,
            "triggers": [
                "dispatch.silent_stall",
                "orchestrator.rework.triage.requested",
            ],
            "publishes": ["orchestrator.rework.triage.recorded"],
            "skills": [
                "zf-yoke-orchestrator-role-context",
                "zf-harness-state-sync",
            ],
        }],
    }


def _draft_runtime_profile_doc(
    *,
    name: str,
    backend: str,
    kind: str = "",
    role_skill_bundles: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Return the executable runtime profile embedded in portable flow drafts."""

    run_manager_backend = backend if backend in {"codex", "claude-code"} else ""
    resident_enabled = bool(run_manager_backend)
    spec: dict[str, Any] = {
        "runtime": {
            "workdirs": {
                "enabled": True,
                "mode": "worktree",
            },
            "run_manager": {
                "backend": run_manager_backend,
                "resident_agent": {
                    "enabled": resident_enabled,
                    "session_mode": "dedicated",
                },
            },
            "autoresearch_resident": {
                "enabled": resident_enabled,
                "interval_seconds": 10,
                "max_actions_per_tick": 1,
            },
            "feishu_inbound": {
                "enabled": "${ZF_FEISHU_INBOUND_ENABLED:-false}",
                "mode": "bridge",
                "require_routing": True,
            },
        },
        "workflow": {
            "candidate_quality_source": "task_contract_required",
            "rework_routing": (
                {"static_gate.failed": "fix-lane-0"}
                if kind == "issue"
                else {"static_gate.failed": "dev-lane-0"}
                if kind in {"prd", "refactor"}
                else {}
            ),
            "work_units": {
                "enabled": True,
                "split_quality": {
                    "mode": "blocking",
                    "max_acceptance_criteria": 8,
                },
            },
        },
    }
    if kind in {"issue", "prd", "refactor"} and role_skill_bundles:
        spec["flow_defaults"] = {
            kind: {
                "roleSkillBundles": role_skill_bundles,
            },
        }
    return {
        "apiVersion": "zaofu.dev/v1",
        "kind": "ConfigProfile",
        "metadata": {"name": name},
        "spec": spec,
    }


def run_start(args: argparse.Namespace) -> int:
    if not args.dry_run:
        print(
            "Error: `zf flow start` currently requires --dry-run; "
            "start/apply remains owned by `zf start`.",
            file=sys.stderr,
        )
        return 2
    proposal = build_flow_start_proposal(
        kind=args.kind,
        source_ref=args.source_ref,
        source_root=args.source_root,
        target_root=args.target_root,
        backend=args.backend,
        lanes=args.lanes or default_lanes_for_kind(args.kind),
        project_name=args.project_name,
        state_dir=args.state_dir,
        strictness=args.strictness,
        parity_scope=_parse_csv(args.parity_scope),
        output=args.output,
        allow_missing_env=bool(args.allow_missing_env),
    )
    if args.json:
        print(json.dumps(proposal, ensure_ascii=False, indent=2))
    else:
        print(f"flow start proposal: {proposal['status']}")
        print(f"- kind: `{proposal['kind']}`")
        print(f"- project: `{proposal['project']['name']}`")
        print(f"- state_dir: `{proposal['project']['state_dir']}`")
        print(f"- config: `{proposal['config_path']}`")
        print(f"- backend: `{proposal['backend']}`")
        print(f"- lanes: `{proposal['lanes']}`")
        summary = proposal.get("summary", {})
        print(f"- roles/stages/pipelines: `{summary.get('roles', 0)}`/"
              f"`{summary.get('stages', 0)}`/`{summary.get('pipelines', 0)}`")
        policies = proposal.get("policies", {})
        for key in ("quality_floor", "evidence_policy", "environment_policy",
                    "delivery_policy", "projection_policy"):
            value = policies.get(key)
            if value:
                print(f"- {key}: `{value}`")
        for item in proposal.get("diagnostics", []):
            print(
                f"- [{item.get('severity', 'INFO')}] "
                f"{item.get('title') or item.get('kind')}: {item.get('message', '')}"
            )
            if item.get("fix_it"):
                print(f"  fix-it: {item['fix_it']}")
    return 0 if proposal["status"] != "STOP" else 1


def build_flow_start_proposal(
    *,
    kind: str,
    source_ref: str = "",
    source_root: str = "",
    target_root: str = "",
    backend: str = "codex",
    lanes: int = 0,
    project_name: str = "",
    state_dir: str = "",
    strictness: str = "standard",
    parity_scope: tuple[str, ...] = (),
    output: Path | None = None,
    allow_missing_env: bool = False,
) -> dict[str, Any]:
    kind = _normalize_request_kind(kind)
    backend = canonical_backend_id(backend)
    project = project_name or _unique_project_name(kind)
    state = state_dir or f".zf-{project}"
    config_path = output or Path.cwd() / f"zf-{project}.yaml"
    config_path = config_path.expanduser()
    docs = draft_flow_spec(
        kind=kind,
        source_ref=source_ref,
        source_root=source_root,
        target_root=target_root,
        backend=backend,
        lanes=lanes or default_lanes_for_kind(kind),
        project_name=project,
        state_dir=state,
        project_root=config_path.parent,
        strictness=strictness,
        parity_scope=parity_scope,
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    report = build_flow_preflight_report(
        config_path.resolve(),
        flow_kind=kind,
        allow_missing_env=allow_missing_env,
    )
    metadata = _flow_metadata_from_report(report)
    return {
        "schema_version": "flow-start-proposal.v1",
        "status": report["status"],
        "kind": kind,
        "backend": backend,
        "lanes": lanes or default_lanes_for_kind(kind),
        "config_path": str(config_path),
        "project": {
            "name": project,
            "state_dir": state,
        },
        "summary": report.get("summary", {}),
        "policies": metadata,
        "diagnostics": report.get("diagnostics", []),
        "next": {
            "render": f"zf config render --config {config_path}",
            "start": (
                "zf init/register + zf start remains explicit until "
                "the operator approves this proposal"
            ),
        },
    }


def _unique_project_name(kind: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return f"{kind}-{stamp}"


def _flow_metadata_from_report(report: dict[str, Any]) -> dict[str, Any]:
    generated = report.get("generated") or {}
    if not isinstance(generated, dict):
        return {}
    metadata = generated.get("flow_metadata") or {}
    if not isinstance(metadata, dict):
        return {}
    return dict(metadata)


def _parse_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def _normalize_request_kind(kind: str) -> str:
    value = str(kind or "").strip().lower()
    if value == "feat":
        return "prd"
    return value


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _non_empty_mapping(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, item in value.items():
        if not isinstance(item, list):
            continue
        values = [str(entry) for entry in item if str(entry).strip()]
        if values:
            out[str(key)] = values
    return out


def _skill_sources_from_adapter_plan(
    adapter_plan: dict[str, Any],
    *,
    project_root: Path,
) -> list[dict[str, str]]:
    loaded = adapter_plan.get("loaded_skills")
    if not isinstance(loaded, list):
        return []
    source_paths: dict[str, Path] = {}
    for item in loaded:
        if not isinstance(item, dict):
            continue
        source_name = str(item.get("source_name") or "")
        if source_name in {"", "project", "state", "yoke"}:
            continue
        source_ref = str(item.get("source_ref") or "")
        if not source_ref:
            continue
        skill_root = Path(source_ref).expanduser().parent.parent
        if source_name in {"zaofu", "skill-source:zaofu"}:
            config_name = "zaofu-skills"
        else:
            config_name = source_name.removeprefix("skill-source:")
        source_paths[config_name] = skill_root
    out: list[dict[str, str]] = []
    for name, root in sorted(source_paths.items()):
        out.append({
            "name": name,
            "path": _display_or_relative_path(root, project_root),
            "mode": "readonly",
        })
    return out


def _display_or_relative_path(path: Path, project_root: Path) -> str:
    root = project_root.expanduser().resolve()
    target = path.expanduser().resolve(strict=False)
    try:
        return os.path.relpath(target, root)
    except ValueError:
        return str(target)


def run_submit(args: argparse.Namespace) -> int:
    if args.dry_run and args.apply:
        print("Error: choose exactly one of --dry-run or --apply", file=sys.stderr)
        return 2
    if not args.dry_run and not args.apply:
        print(
            "Error: `zf flow submit` requires --dry-run or --apply.",
            file=sys.stderr,
        )
        return 2
    kwargs = {
        "config_path": args.config.expanduser(),
        "intake_path": args.intake.expanduser(),
        "flow_kind": args.kind,
        "task_id": args.task_id,
        "pattern_id": args.pattern_id,
        "requested_by": args.requested_by,
        "reason": args.reason,
        "output": args.output,
        "allow_missing_env": bool(args.allow_missing_env),
    }
    result = build_flow_submit_preview(**kwargs) if args.dry_run else apply_flow_submit(**kwargs)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        label = "workflow submit preview" if args.dry_run else "workflow submit apply"
        print(f"{label}: {result['status']}")
        print(f"- event_type: `{result['event_type']}`")
        if result.get("submit_preview_ref"):
            print(f"- preview: `{result['submit_preview_ref']}`")
        if result.get("preflight_ref"):
            print(f"- preflight: `{result['preflight_ref']}`")
        if result.get("event_ids"):
            print(f"- events: `{', '.join(result['event_ids'])}`")
        if result.get("workflow_invoke_status"):
            print(f"- workflow_invoke_status: `{result['workflow_invoke_status']}`")
        if result.get("next_action"):
            print(f"- next_action: {result['next_action']}")
    return 0 if result["status"] != "STOP" else 1


def build_flow_submit_preview(
    *,
    config_path: Path,
    intake_path: Path,
    flow_kind: str = "",
    task_id: str = "",
    pattern_id: str = "",
    requested_by: str = "zf-cli",
    reason: str = "",
    output: Path | None = None,
    allow_missing_env: bool = False,
) -> dict[str, Any]:
    manifest_path, manifest = _load_manifest_for_intake(intake_path)
    if manifest_path is None:
        manifest = {
            "request_id": _request_id_from_path(intake_path),
            "kind": flow_kind,
            "intake_ref": str(intake_path),
            "artifact_refs": [str(intake_path)],
        }
    request_id = str(manifest.get("request_id") or _request_id_from_path(intake_path))
    # Intake/matrix artifacts are durable request inputs and may intentionally
    # live in the project tree.  Submit preflight and preview are runtime
    # projections: rewriting them on every submit must not dirty a project and
    # block its subsequent ship.  Keep explicit --output as the caller's
    # deliberate escape hatch, otherwise place them under the configured state.
    config = load_config(config_path)
    state_dir = _state_dir_for_config(config_path, config)
    projection_dir = state_dir / "artifacts" / "workflow" / request_id
    projection_dir.mkdir(parents=True, exist_ok=True)
    preflight_path = projection_dir / "workflow-preflight.json"
    preview_path = (output or projection_dir / "workflow-submit-preview.json").expanduser()
    report = build_flow_preflight_report(
        config_path.resolve(),
        flow_kind=flow_kind or str(manifest.get("kind") or ""),
        intake_path=intake_path,
        allow_missing_env=allow_missing_env,
    )
    request_projection: dict[str, Any] = {}
    request_blockers: list[dict[str, Any]] = []
    if manifest_path is not None:
        from zf.runtime.workflow_requests import (
            register_workflow_intake,
            request_readiness_blockers,
            workflow_request_path,
        )

        state_dir.mkdir(parents=True, exist_ok=True)
        request_writer = EventWriter(event_log_from_project(state_dir, config=config))
        request_projection = register_workflow_intake(
            state_dir,
            manifest_path,
            actor=requested_by or "zf-cli",
            writer=request_writer,
        )
        effective_manifest_ref = str(
            request_projection.get("workflow_input_manifest_ref") or ""
        )
        effective_manifest = (
            _load_json(Path(effective_manifest_ref))
            if effective_manifest_ref
            else {}
        )
        if effective_manifest:
            manifest = effective_manifest
        request_blockers = request_readiness_blockers(request_projection)
        manifest["request_projection_ref"] = str(
            workflow_request_path(state_dir, request_id)
        )
        manifest["requirement_spec_ref"] = str(
            request_projection.get("requirement_spec_ref") or ""
        )
        manifest["requirement_spec_digest"] = str(
            request_projection.get("requirement_spec_digest") or ""
        )
        manifest.setdefault("artifact_refs", [])
        if (
            manifest["requirement_spec_ref"]
            and manifest["requirement_spec_ref"] not in manifest["artifact_refs"]
        ):
            manifest["artifact_refs"].append(manifest["requirement_spec_ref"])
    resolved_kind = _normalize_request_kind(
        flow_kind or str(manifest.get("kind") or report.get("flow_kind") or "")
    )
    source_ref_blockers = _submit_source_ref_blockers(
        config_path=config_path,
        source_ref=str(manifest.get("source_ref") or ""),
        flow_kind=resolved_kind,
    )
    resolved_task_id = _resolve_submit_task_id(task_id, request_id=request_id, kind=resolved_kind)
    workflow_tier = str(
        manifest.get("workflow_tier")
        or manifest.get("tier")
        or ""
    ).strip().lower()
    route_blockers: list[dict[str, Any]] = []
    try:
        resolved_pattern_id = _resolve_submit_pattern_id(
            config_path=config_path,
            pattern_id=pattern_id,
            kind=resolved_kind,
            workflow_tier=workflow_tier,
        )
    except ConfigError as exc:
        resolved_pattern_id = ""
        route_blockers.append({
            "severity": "STOP",
            "kind": "workflow_route_unresolved",
            "title": "workflow route 无法确定",
            "message": str(exc),
            "why_it_matters": (
                "同一 canonical zf.yaml 承载多个 request kind 时,submit "
                "必须确定性选择 stage,不能猜第一个 stage。"
            ),
            "fix_it": (
                "在 workflow.kind_routes 中声明 kind -> pattern_id,或显式传 "
                "--pattern-id。"
            ),
            "safe_auto_fix": False,
        })
    preflight_path.write_text(
        json.dumps(_public_preflight_report(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    effective_manifest_path = Path(
        str(request_projection.get("workflow_input_manifest_ref") or "")
    ) if request_projection.get("workflow_input_manifest_ref") else manifest_path
    manifest_artifact_refs = _workflow_manifest_artifact_refs(
        manifest,
        manifest_path=effective_manifest_path,
        intake_path=intake_path,
        preflight_path=preflight_path,
    )
    matrix_ref_payload = {
        key: str(manifest.get(key) or "")
        for key in _WORKFLOW_MATRIX_REF_KEYS
        if str(manifest.get(key) or "").strip()
    }
    canonical_intake_ref = str(manifest.get("intake_json_ref") or intake_path)
    display_intake_ref = str(
        manifest.get("intake_markdown_ref")
        or manifest.get("intake_ref")
        or intake_path
    )
    submit_payload = {
        "schema_version": "workflow.submit.requested.v1",
        "request_id": request_id,
        "run_id": request_id,
        "kind": resolved_kind,
        "request_kind": str(manifest.get("request_kind") or resolved_kind),
        "workflow_tier": workflow_tier,
        "task_id": resolved_task_id,
        "pattern_id": resolved_pattern_id,
        "config_ref": str(config_path),
        "workflow_prompt_ref": canonical_intake_ref,
        "workflow_input_manifest_ref": str(effective_manifest_path or ""),
        "workflow_preflight_ref": str(preflight_path),
        "workflow_request_ref": str(manifest.get("request_projection_ref") or ""),
        "requirement_spec_ref": str(request_projection.get("requirement_spec_ref") or ""),
        "requirement_spec_digest": str(request_projection.get("requirement_spec_digest") or ""),
        "request_revision": int(request_projection.get("revision") or 0),
        "requested_by": requested_by or "zf-cli",
        "reason": reason or f"workflow submit {request_id}",
        # E2(prd-goal e2e):objective 曾不入 submit payload,G0 铸造
        # 落到 reason(操作员备注被当成了 run 目标)。真源=manifest。
        "objective": str(manifest.get("objective") or ""),
        **matrix_ref_payload,
        "source_refs": {
            "source_ref": str(manifest.get("source_ref") or ""),
            "source_root": str(manifest.get("source_root") or ""),
            "target_root": str(manifest.get("target_root") or ""),
            "intake_ref": canonical_intake_ref,
            "intake_markdown_ref": display_intake_ref,
            "workflow_input_manifest_ref": str(effective_manifest_path or ""),
            **matrix_ref_payload,
        },
        "artifact_refs": manifest_artifact_refs,
    }
    blockers = [
        *(report.get("blockers") or []),
        *source_ref_blockers,
        *route_blockers,
        *request_blockers,
    ]
    status = (
        "STOP"
        if source_ref_blockers or route_blockers or request_blockers
        else report["status"]
    )
    if (
        status != "STOP"
        and request_projection
        and str(request_projection.get("status") or "") in {"draft", "ready", "proposed"}
    ):
        from zf.runtime.workflow_requests import mark_workflow_request

        request_projection = mark_workflow_request(
            state_dir,
            request_id,
            status="proposed",
            actor=requested_by or "zf-cli",
            writer=request_writer,
            event_type="workflow.request.proposed",
        )
    result = {
        "schema_version": "workflow.submit.preview.v1",
        "status": status,
        "dry_run": True,
        "event_type": "workflow.submit.requested",
        "payload": submit_payload,
        "submit_preview_ref": str(preview_path),
        "preflight_ref": str(preflight_path),
        "blockers": blockers,
        "request": request_projection,
        "next": {
            "apply": "run `zf flow submit --apply ...` after operator approval",
        },
    }
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


_WORKFLOW_MATRIX_REF_KEYS = (
    "source_inventory_ref",
    "capability_matrix_ref",
    "acceptance_matrix_ref",
    "test_matrix_ref",
    "task_map_ref",
    "real_e2e_matrix_ref",
    "skill_adapter_plan_ref",
    "intake_json_ref",
)


def _submit_source_ref_blockers(
    *,
    config_path: Path,
    source_ref: str,
    flow_kind: str,
) -> list[dict[str, Any]]:
    """Mirror reader target admission before a workflow is submitted."""

    ref = str(source_ref or "").strip()
    if flow_kind not in {"issue", "prd"} or not ref:
        return []
    project_root = config_path.resolve().parent
    candidate = Path(ref).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(project_root)
        if resolved.exists():
            return []
    except (OSError, RuntimeError, ValueError):
        pass
    git_ref = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if git_ref.returncode == 0:
        return []
    return [{
        "severity": "STOP",
        "kind": "workflow_source_ref_unresolvable",
        "title": "workflow source_ref 无法作为 reader target 使用",
        "message": (
            f"source_ref `{ref}` 既不是项目内现存路径，也不是可解析 Git ref"
        ),
        "why_it_matters": (
            "reader worktree 会 fail-closed；若继续 submit，只会消耗有界 rework "
            "而不会启动 scan agent。"
        ),
        "fix_it": (
            "将 Issue/PRD 输入复制到项目内并更新 workflow input manifest，"
            "或改用当前项目可解析的 Git ref。"
        ),
        "safe_auto_fix": False,
    }]


def _workflow_manifest_artifact_refs(
    manifest: dict[str, Any],
    *,
    manifest_path: Path | None,
    intake_path: Path,
    preflight_path: Path,
) -> list[str]:
    refs: list[str] = [str(intake_path), str(manifest_path or ""), str(preflight_path)]
    for item in manifest.get("artifact_refs") or []:
        if isinstance(item, dict):
            refs.extend(
                str(item.get(key) or "")
                for key in ("path", "ref", "uri")
            )
        else:
            refs.append(str(item or ""))
    for key in _WORKFLOW_MATRIX_REF_KEYS:
        refs.append(str(manifest.get(key) or ""))
    refs.append(str(manifest.get("intent_ref") or ""))
    return [ref for ref in dict.fromkeys(ref.strip() for ref in refs) if ref]


def apply_flow_submit(
    *,
    config_path: Path,
    intake_path: Path,
    flow_kind: str = "",
    task_id: str = "",
    pattern_id: str = "",
    requested_by: str = "zf-cli",
    reason: str = "",
    output: Path | None = None,
    allow_missing_env: bool = False,
) -> dict[str, Any]:
    config = load_config(config_path)
    state_dir = _state_dir_for_config(config_path, config)
    state_dir.mkdir(parents=True, exist_ok=True)
    manifest_path, _manifest = _load_manifest_for_intake(intake_path)
    if manifest_path is not None:
        from zf.runtime.workflow_requests import (
            register_workflow_intake,
            revise_workflow_request,
        )

        request_writer = EventWriter(event_log_from_project(state_dir, config=config))
        projection = register_workflow_intake(
            state_dir,
            manifest_path,
            actor=requested_by or "zf-cli",
            writer=request_writer,
        )
        if str(projection.get("status") or "") in {"submitted", "running"}:
            return _submitted_request_replay_result(
                config=config,
                state_dir=state_dir,
                projection=projection,
                events=request_writer.event_log.read_all(),
            )
        if not bool(projection.get("confirmed")):
            projection = revise_workflow_request(
                state_dir,
                manifest_path,
                actor=requested_by or "zf-cli",
                confirm=True,
                writer=request_writer,
            )
    preview = build_flow_submit_preview(
        config_path=config_path,
        intake_path=intake_path,
        flow_kind=flow_kind,
        task_id=task_id,
        pattern_id=pattern_id,
        requested_by=requested_by,
        reason=reason,
        output=output,
        allow_missing_env=allow_missing_env,
    )
    writer = EventWriter(event_log_from_project(state_dir, config=config))
    payload = dict(preview.get("payload") or {})
    correlation_id = str(payload.get("request_id") or "")
    task = str(payload.get("task_id") or "")
    if preview["status"] != "STOP":
        _pin_submitted_run_contract(
            state_dir=state_dir,
            preview=preview,
            writer=writer,
            correlation_id=correlation_id,
            task_id=task,
        )
    if preview["status"] != "STOP" and correlation_id:
        from zf.runtime.workflow_requests import mark_workflow_request

        mark_workflow_request(
            state_dir,
            correlation_id,
            status="approved",
            actor=str(payload.get("requested_by") or "zf-cli"),
            writer=writer,
            run_id=str(payload.get("run_id") or correlation_id),
            event_type="workflow.request.approved",
        )
    submit_requested = writer.append(ZfEvent(
        type="workflow.submit.requested",
        actor=str(payload.get("requested_by") or "zf-cli"),
        task_id=task,
        correlation_id=correlation_id,
        payload={**payload, "dry_run": False, "preflight_status": preview.get("status")},
    ))
    event_ids = [submit_requested.id]
    if preview["status"] == "STOP":
        rejected = writer.append(ZfEvent(
            type="workflow.submit.rejected",
            actor="zf-cli",
            task_id=task,
            causation_id=submit_requested.id,
            correlation_id=correlation_id,
            payload={
                "request_id": correlation_id,
                "source_event_id": submit_requested.id,
                "reason": "preflight failed",
                "preflight_ref": preview.get("preflight_ref", ""),
                "blockers": preview.get("blockers") or [],
            },
        ))
        event_ids.append(rejected.id)
        return {
            **preview,
            "schema_version": "workflow.submit.apply.v1",
            "dry_run": False,
            "status": "STOP",
            "workflow_invoke_status": "not_requested",
            "next_action": "fix flow preflight blockers before workflow invoke",
            "event_ids": event_ids,
            "state_dir": str(state_dir),
        }
    accepted = writer.append(ZfEvent(
        type="workflow.submit.accepted",
        actor="zf-cli",
        task_id=task,
        causation_id=submit_requested.id,
        correlation_id=correlation_id,
        payload={
            "request_id": correlation_id,
            "run_id": str(payload.get("run_id") or correlation_id),
            "kind": str(payload.get("kind") or ""),
            "request_kind": str(payload.get("request_kind") or payload.get("kind") or ""),
            "workflow_tier": str(payload.get("workflow_tier") or ""),
            "source_event_id": submit_requested.id,
            "workflow_preflight_ref": payload.get("workflow_preflight_ref", ""),
            "workflow_input_manifest_ref": payload.get("workflow_input_manifest_ref", ""),
            "workflow_prompt_ref": payload.get("workflow_prompt_ref", ""),
            "config_ref": payload.get("config_ref", ""),
            "workflow_request_ref": payload.get("workflow_request_ref", ""),
            "requirement_spec_ref": payload.get("requirement_spec_ref", ""),
            "requirement_spec_digest": payload.get("requirement_spec_digest", ""),
            "request_revision": int(payload.get("request_revision") or 0),
        },
    ))
    event_ids.append(accepted.id)
    if correlation_id:
        from zf.runtime.workflow_requests import mark_workflow_request

        mark_workflow_request(
            state_dir,
            correlation_id,
            status="submitted",
            actor=str(payload.get("requested_by") or "zf-cli"),
            writer=writer,
            run_id=str(payload.get("run_id") or correlation_id),
            event_type="workflow.request.submitted",
        )
    # G0(133):goal 铸造——submit accepted 即 kernel 发 run.goal.started
    # (投影 build_run_goal_projection 已在等这个事件;灰度 goal.enabled)。
    goal_started: ZfEvent | None = None
    if bool(getattr(getattr(config, "goal", None), "enabled", False)):
        objective = str(
            payload.get("objective")
            or payload.get("summary")
            or payload.get("reason")
            or f"deliver workflow submit {correlation_id or task}"
        )
        goal_started = writer.append(ZfEvent(
            type="run.goal.started",
            actor="zf-cli",
            task_id=task,
            causation_id=accepted.id,
            correlation_id=correlation_id,
            payload={
                "objective": objective,
                "run_id": correlation_id or accepted.id,
                "source_refs": [
                    ref for ref in (
                        payload.get("workflow_input_manifest_ref"),
                        payload.get("workflow_prompt_ref"),
                        payload.get("config_ref"),
                    ) if ref
                ],
            },
        ))
        event_ids.append(goal_started.id)
    # LB-2: light topology is driven by its entry trigger and kernel task-map
    # synthesis, not by the bootstrap invoke. Submit and entry share one
    # transaction so their run/correlation identity cannot diverge.
    from zf.runtime.light_flow import light_flow_metadata
    light_metadata = light_flow_metadata(
        config,
        flow_kind=str(payload.get("kind") or ""),
    )
    if light_metadata is not None:
        entry_trigger = str(light_metadata.get("light_entry_trigger") or "prd.requested")
        source_refs = (
            dict(payload.get("source_refs") or {})
            if isinstance(payload.get("source_refs"), dict)
            else {}
        )
        source_ref = str(source_refs.get("source_ref") or "")
        kind = str(payload.get("kind") or "prd")
        entry_payload = {
            **payload,
            "pdd_id": correlation_id,
            "feature_id": correlation_id,
            "workflow_run_id": correlation_id,
            "trace_id": correlation_id,
            "flow_kind": kind,
            "objective_ref": source_ref,
            "target_root": str(source_refs.get("target_root") or ""),
            "source": "workflow-submit-light",
        }
        if kind == "issue":
            entry_payload["issue_ref"] = source_ref
        else:
            entry_payload["prd_ref"] = source_ref
        entry = writer.append(ZfEvent(
            type=entry_trigger,
            actor=str(payload.get("requested_by") or "zf-cli"),
            task_id=task,
            causation_id=(goal_started.id if goal_started is not None else accepted.id),
            correlation_id=correlation_id,
            payload=entry_payload,
        ))
        event_ids.append(entry.id)
        if correlation_id:
            from zf.runtime.workflow_requests import mark_workflow_request

            mark_workflow_request(
                state_dir,
                correlation_id,
                status="running",
                actor=str(payload.get("requested_by") or "zf-cli"),
                writer=writer,
                run_id=str(payload.get("run_id") or correlation_id),
                event_type="workflow.request.running",
            )
        return {
            **preview,
            "schema_version": "workflow.submit.apply.v1",
            "dry_run": False,
            "status": "accepted",
            "event_type": "workflow.submit.accepted",
            "workflow_invoke_status": "light_entry_requested",
            "workflow_entry_event_id": entry.id,
            "next_action": "light topology entry accepted; monitor the running request",
            "event_ids": event_ids,
            "state_dir": str(state_dir),
        }
    invoke_payload = _submit_payload_to_workflow_invoke(payload)
    invoked = writer.append(ZfEvent(
        type="workflow.invoke.requested",
        actor=str(payload.get("requested_by") or "zf-cli"),
        task_id=task,
        causation_id=accepted.id,
        correlation_id=correlation_id,
        payload=invoke_payload,
    ))
    event_ids.append(invoked.id)
    if correlation_id:
        from zf.runtime.workflow_requests import mark_workflow_request

        mark_workflow_request(
            state_dir,
            correlation_id,
            status="running",
            actor=str(payload.get("requested_by") or "zf-cli"),
            writer=writer,
            run_id=str(payload.get("run_id") or correlation_id),
            event_type="workflow.request.running",
        )
    invoke_visibility = _workflow_invoke_visibility(
        writer.event_log.read_all(),
        source_event_id=invoked.id,
    )
    return {
        **preview,
        "schema_version": "workflow.submit.apply.v1",
        "dry_run": False,
        "status": "accepted",
        "event_type": "workflow.submit.accepted",
        "workflow_invoke_event_id": invoked.id,
        "workflow_invoke_status": invoke_visibility["status"],
        "next_action": invoke_visibility["next_action"],
        "event_ids": event_ids,
        "state_dir": str(state_dir),
    }


def _submitted_request_replay_result(
    *,
    config: Any,
    state_dir: Path,
    projection: dict[str, Any],
    events: list[ZfEvent],
) -> dict[str, Any]:
    """Return an idempotent submit result without emitting a second Run."""

    request_id = str(projection.get("request_id") or "")
    from zf.runtime.light_flow import light_flow_metadata

    light_metadata = light_flow_metadata(
        config,
        flow_kind=str(projection.get("kind") or ""),
    )
    entry_trigger = (
        str(light_metadata.get("light_entry_trigger") or "prd.requested")
        if light_metadata is not None else ""
    )
    related_types = {"workflow.submit.accepted", "workflow.invoke.requested"}
    if entry_trigger:
        related_types.add(entry_trigger)
    related = [
        event for event in events
        if str(event.correlation_id or "") == request_id
        and event.type in related_types
    ]
    invoked = next(
        (event for event in reversed(related) if event.type == "workflow.invoke.requested"),
        None,
    )
    light_entry = next(
        (event for event in reversed(related) if event.type == entry_trigger),
        None,
    )
    if invoked is not None:
        invoke_status = "already_requested"
        next_action = "workflow request is already running"
    elif light_entry is not None:
        invoke_status = "already_requested"
        next_action = "light topology entry is already running"
    else:
        invoke_status = "already_submitted"
        next_action = "inspect/resume the submitted request; duplicate ignition was suppressed"
    return {
        "schema_version": "workflow.submit.apply.v1",
        "status": "accepted",
        "dry_run": False,
        "event_type": "workflow.submit.accepted",
        "payload": {
            "request_id": request_id,
            "run_id": str(projection.get("run_id") or request_id),
            "kind": str(projection.get("kind") or ""),
        },
        "request": projection,
        "idempotent_replay": True,
        "workflow_invoke_event_id": str(invoked.id if invoked is not None else ""),
        "workflow_entry_event_id": str(light_entry.id if light_entry is not None else ""),
        "workflow_invoke_status": invoke_status,
        "next_action": next_action,
        "event_ids": [event.id for event in related],
        "state_dir": str(state_dir),
        "blockers": [],
    }


def run_preflight(args: argparse.Namespace) -> int:
    config_path = args.config.expanduser().resolve()
    report = build_flow_preflight_report(
        config_path,
        flow_kind=args.kind,
        intake_path=args.intake.expanduser() if args.intake is not None else None,
        allow_missing_env=bool(args.allow_missing_env),
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"flow preflight: {report['status']}")
        for item in report["diagnostics"]:
            print(
                f"- [{item.get('severity', 'INFO')}] "
                f"{item.get('title') or item.get('kind')}: {item.get('message', '')}"
            )
            if item.get("fix_it"):
                print(f"  fix-it: {item['fix_it']}")
    return 0 if report["status"] != "STOP" else 1


def build_flow_preflight_report(
    config_path: Path,
    *,
    flow_kind: str = "",
    intake_path: Path | None = None,
    allow_missing_env: bool = False,
) -> dict[str, Any]:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        return {
            "schema_version": "flow-start-readiness.v1",
            "status": "STOP",
            "config": str(config_path),
            "diagnostics": [{
                "severity": "STOP",
                "kind": "config_load_failed",
                "title": "配置无法加载",
                "message": str(exc),
                "why_it_matters": "配置加载失败时不能安全启动 workflow。",
                "fix_it": "先修复 YAML/schema/profile_sources，再重新 preflight。",
                "safe_auto_fix": False,
            }],
        }

    project_root = config_path.parent
    state_dir = Path(config.project.state_dir)
    if not state_dir.is_absolute():
        state_dir = project_root / state_dir
    inspect_report = build_config_inspection_report(
        config,
        config_path=config_path,
        project_root=project_root,
        state_dir=state_dir.resolve(),
    )
    diagnostics = list(inspect_report.get("diagnostics") or [])
    static_results = run_preflight_checks(
        config,
        check_provider_auth=not allow_missing_env,
    )
    for result in static_results:
        if result.ok:
            continue
        diagnostics.append({
            "severity": "STOP",
            "kind": f"static_preflight_{result.name}",
            "title": "静态启动检查失败",
            "message": result.detail,
            "why_it_matters": "调度链或 backend 基础能力不满足时，启动后会静默卡住。",
            "fix_it": "按 preflight detail 修复角色/backend/dispatch 配置。",
            "safe_auto_fix": False,
        })
    intake_report = _intake_preflight_report(intake_path)
    diagnostics.extend(intake_report.get("diagnostics", []))
    effective_kind = str(
        flow_kind
        or intake_report.get("kind")
        or _flow_kind(config)
        or ""
    )
    from zf.core.config.candidate_gate import combined_candidate_gate_gap

    candidate_gate_gap = combined_candidate_gate_gap(
        config,
        flow_kind=effective_kind,
    )
    if candidate_gate_gap:
        diagnostics.append({
            "severity": "STOP",
            "kind": "combined_candidate_gate_missing",
            "title": "当前 workflow 缺少合并候选树质量门",
            "message": candidate_gate_gap,
            "why_it_matters": "多 lane 合并后必须重新验证集成候选树。",
            "fix_it": (
                "配置 workflow.candidate_quality_source=task_contract_required "
                "并由 Task Map 声明 verification，或配置显式 legacy "
                "quality_gates；仅观测运行才使用豁免。"
            ),
            "safe_auto_fix": False,
        })
    from zf.core.workflow.flow_metadata import flow_metadata_for

    metadata = _effective_flow_metadata(
        flow_metadata_for(config, effective_kind),
        intake_report=intake_report,
    )
    diagnostics.extend(_project_setup_readiness_diagnostics(
        config=config,
        project_root=project_root,
        metadata=metadata,
    ))
    diagnostics.extend(_git_delivery_baseline_diagnostics(
        project_root=project_root,
        metadata=metadata,
    ))
    diagnostics.extend(_environment_readiness_diagnostics(
        metadata,
        allow_missing_env=allow_missing_env,
    ))
    skill_report = _skill_adapter_preflight_report(intake_report)
    diagnostics.extend(skill_report.get("diagnostics", []))
    delivery_report = _delivery_launch_coverage_report(
        project_root=project_root,
        metadata=metadata,
        flow_kind=effective_kind,
        intake_report=intake_report,
        skill_report=skill_report,
    )
    diagnostics.extend(delivery_report.get("diagnostics", []))
    refactor_report = _refactor_safety_report(
        project_root=project_root,
        metadata=metadata,
        flow_kind=effective_kind,
        intake_report=intake_report,
    )
    diagnostics.extend(refactor_report.get("diagnostics", []))
    run_contract_report = _run_contract_preflight_report(
        config=config,
        config_path=config_path,
        project_root=project_root,
        state_dir=state_dir,
        intake_report=intake_report,
        strict=_contract_is_strict(
            str(delivery_report.get("strictness") or ""),
        ),
    )
    diagnostics.extend(run_contract_report.get("diagnostics", []))
    stop = any(str(item.get("severity") or "").upper() == "STOP" for item in diagnostics)
    warn = any(str(item.get("severity") or "").upper() == "WARN" for item in diagnostics)
    return {
        "schema_version": "flow-start-readiness.v1",
        "status": "STOP" if stop else "WARN" if warn else "GO",
        "config": str(config_path),
        "flow_kind": effective_kind,
        "project": inspect_report.get("project", {}),
        "summary": inspect_report.get("summary", {}),
        "generated": inspect_report.get("generated", {}),
        "effective_flow_metadata": metadata,
        "preflight": {
            "static_dispatch": "PASS" if preflight_ok(static_results) else "FAIL",
            "profile_sources_locked": bool(
                (inspect_report.get("source") or {}).get("profiles")
            ),
        },
        "intake": intake_report,
        "skill_adapter": skill_report,
        "delivery_contract": delivery_report,
        "refactor_safety": refactor_report,
        "run_contract": run_contract_report,
        "diagnostics": diagnostics,
        "blockers": [
            item for item in diagnostics
            if str(item.get("severity") or "").upper() == "STOP"
        ],
    }


def _project_setup_readiness_diagnostics(
    *,
    config: Any,
    project_root: Path,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    if str(metadata.get("delivery_policy") or "report_only") not in {
        "ship",
        "ship_candidate",
        "candidate_ship",
        "code_merge",
        "merge",
    }:
        return []
    target_root = _resolve_declared_root(
        str(metadata.get("target_root") or ""),
        project_root,
    ) or project_root
    setup_script = str(getattr(config.project, "setup_script", "") or "").strip()
    node_checks = [
        str(command).strip()
        for gate in (getattr(config, "quality_gates", {}) or {}).values()
        if getattr(gate, "enabled", True)
        for command in (getattr(gate, "required_checks", []) or [])
        if re.search(
            r"(?:^|&&|\|\||;)\s*(?:cd\s+\S+\s+&&\s*)?"
            r"(?:npm|npx|pnpm|yarn|bun|bunx)\b",
            str(command).strip(),
        )
    ]
    has_node_manifest = any(
        (target_root / name).exists()
        for name in (
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "bun.lockb",
        )
    )
    if has_node_manifest and node_checks and not setup_script:
        return [{
            "severity": "STOP",
            "kind": "project_setup_missing",
            "title": "候选 worktree 缺少依赖准备声明",
            "message": (
                "Node quality gates require project.scripts.setup before "
                "candidate integration"
            ),
            "why_it_matters": "干净 worktree 不继承 node_modules,质量门会误报产品失败。",
            "fix_it": "在 zf.yaml 配置 project.scripts.setup（例如 npm ci）。",
            "safe_auto_fix": False,
            "quality_commands": node_checks,
        }]
    if not setup_script:
        return []
    syntax = subprocess.run(
        ["sh", "-n", "-c", setup_script],
        cwd=target_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if syntax.returncode == 0:
        return []
    return [{
        "severity": "STOP",
        "kind": "project_setup_invalid",
        "title": "project.scripts.setup 无法解析",
        "message": (syntax.stderr or syntax.stdout or "invalid shell syntax").strip(),
        "why_it_matters": "候选 worktree setup 无法执行时所有后续质量门都不可信。",
        "fix_it": "修复 project.scripts.setup 的 shell 语法。",
        "safe_auto_fix": False,
    }]


def _git_delivery_baseline_diagnostics(
    *,
    project_root: Path,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    if str(metadata.get("delivery_policy") or "report_only") not in {
        "ship",
        "ship_candidate",
        "candidate_ship",
        "code_merge",
        "merge",
    }:
        return []
    target_root = _resolve_declared_root(
        str(metadata.get("target_root") or ""),
        project_root,
    ) or project_root
    delivery_git_root = _target_git_root(target_root, project_root)
    if delivery_git_root is None:
        return []
    try:
        target_pathspec = os.path.relpath(target_root, delivery_git_root)
    except ValueError:
        target_pathspec = "."
    status = subprocess.run(
        [
            "git", "status", "--porcelain", "--untracked-files=no",
            "--", target_pathspec,
        ],
        cwd=delivery_git_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode != 0 or not status.stdout.strip():
        return []
    paths = [line[3:] for line in status.stdout.splitlines() if len(line) > 3]
    return [{
        "severity": "STOP",
        "kind": "git_delivery_baseline_dirty",
        "title": "交付基线包含未提交的 tracked 改动",
        "message": ", ".join(paths[:10]),
        "why_it_matters": "candidate/ship 不能区分既有脏改动与本次 workflow 交付。",
        "fix_it": "先提交、暂存到独立 worktree，或恢复这些 tracked 改动后再启动。",
        "safe_auto_fix": False,
    }]


def _delivery_launch_coverage_report(
    *,
    project_root: Path,
    metadata: dict[str, Any],
    flow_kind: str,
    intake_report: dict[str, Any],
    skill_report: dict[str, Any],
) -> dict[str, Any]:
    kind = str(flow_kind or "").strip().lower()
    if kind not in {"issue", "prd", "refactor"}:
        return {"status": "not_applicable", "diagnostics": []}
    manifest_ref = str(intake_report.get("workflow_input_manifest_ref") or "")
    manifest = _load_json(Path(manifest_ref)) if manifest_ref else {}
    strictness = str(manifest.get("strictness") or metadata.get("strictness") or "standard")
    diagnostics: list[dict[str, Any]] = []
    workflow_dir = Path(str(manifest.get("workflow_dir") or "") or "")
    if workflow_dir and not workflow_dir.is_absolute():
        workflow_dir = project_root / workflow_dir
    if not manifest:
        diagnostics.append({
            "severity": "WARN",
            "kind": "delivery_contract_manifest_missing",
            "title": "delivery contract manifest 缺失",
            "message": "未提供 workflow-input-manifest.json,只能做基础启动检查。",
            "why_it_matters": "没有 manifest 就无法证明 scan/plan/verify 需要的矩阵产物存在。",
            "fix_it": "先运行 `zf flow intake ...` 并把 --intake 传给 preflight/submit。",
            "safe_auto_fix": True,
        })
    present: dict[str, list[str]] = {}
    missing: list[str] = []
    for item in required_delivery_artifacts(kind):
        name = item["name"]
        refs = _delivery_refs_for_name(
            name,
            manifest=manifest,
            metadata=metadata,
            workflow_dir=workflow_dir,
        )
        if refs:
            missing_refs = [
                ref for ref in refs
                if _local_artifact_ref_missing(ref, project_root=project_root)
            ]
            if missing_refs:
                severity = (
                    "STOP"
                    if _contract_requires_stop(strictness, str(item.get("required_for") or "strict"))
                    else "WARN"
                )
                diagnostics.append({
                    "severity": severity,
                    "kind": "delivery_contract_artifact_missing",
                    "title": "delivery contract ref 指向的产物不存在",
                    "message": f"{name} refs are missing: {', '.join(missing_refs[:5])}",
                    "why_it_matters": (
                        "One-run delivery cannot resume or hydrate workers from "
                        "artifact refs that do not exist on disk."
                    ),
                    "fix_it": "重新生成 artifact,或修正 workflow-input-manifest.json 中的 ref。",
                    "safe_auto_fix": False,
                    "artifact_name": name,
                    "missing_refs": missing_refs,
                    "required_for": item.get("required_for", ""),
                })
                missing.append(name)
                continue
            present[name] = refs
            continue
        if name == "skill_adapter_plan" and skill_report.get("status") in {"PASS", "WARN"}:
            ref = str(skill_report.get("skill_adapter_plan_ref") or "")
            if ref:
                present[name] = [ref]
                continue
        missing.append(name)
        severity = (
            "STOP"
            if _contract_requires_stop(strictness, str(item.get("required_for") or "strict"))
            else "WARN"
        )
        diagnostics.append({
            "severity": severity,
            "kind": "delivery_contract_artifact_missing",
            "title": "delivery contract 关键产物缺失",
            "message": f"{name} is missing for {kind} workflow",
            "why_it_matters": (
                "One-run delivery requires source/capability/task/test/evidence "
                "artifacts before dispatching long-horizon workers."
            ),
            "fix_it": "让 scan/plan skill 生成对应 artifact ref,或在 manifest 中声明已存在 refs。",
            "safe_auto_fix": False,
            "artifact_name": name,
            "required_for": item.get("required_for", ""),
        })
    stop = any(d["severity"] == "STOP" for d in diagnostics)
    warn = any(d["severity"] == "WARN" for d in diagnostics)
    return {
        "schema_version": "delivery-launch-coverage.v1",
        "status": "STOP" if stop else "WARN" if warn else "PASS",
        "flow_kind": kind,
        "strictness": strictness,
        "present": present,
        "missing": missing,
        "diagnostics": diagnostics,
    }


def _run_contract_preflight_report(
    *,
    config: Any,
    config_path: Path,
    project_root: Path,
    state_dir: Path,
    intake_report: dict[str, Any],
    strict: bool,
) -> dict[str, Any]:
    manifest_ref = str(intake_report.get("workflow_input_manifest_ref") or "")
    contract = build_run_contract(
        config,
        config_path=config_path,
        project_root=project_root,
        state_dir=state_dir,
        workflow_input_manifest_ref=manifest_ref,
    )
    previous = load_run_contract(state_dir)
    bootstrap = build_run_contract(
        config,
        config_path=config_path,
        project_root=project_root,
        state_dir=state_dir,
    )
    binding = evaluate_run_contract_submit_binding(
        previous,
        contract,
        bootstrap=bootstrap,
        strict=strict,
    )
    diagnostics = list(binding.get("diagnostics") or [])
    return {
        "schema_version": "run-contract-preflight.v1",
        "status": str(binding.get("status") or "PASS"),
        "preview": contract,
        "previous_ref": str(state_dir / "config" / "run-contract.json") if previous else "",
        "initial_binding": bool(binding.get("initial_binding")),
        "comparison_basis": str(binding.get("comparison_basis") or "current"),
        "diagnostics": diagnostics,
    }


def _pin_submitted_run_contract(
    *,
    state_dir: Path,
    preview: dict[str, Any],
    writer: EventWriter,
    correlation_id: str,
    task_id: str,
) -> None:
    preflight_ref = str(preview.get("preflight_ref") or "")
    report = _load_json(Path(preflight_ref)) if preflight_ref else {}
    run_contract = report.get("run_contract")
    run_contract = run_contract if isinstance(run_contract, dict) else {}
    contract = run_contract.get("preview")
    if not isinstance(contract, dict) or not str(contract.get("contract_digest") or ""):
        raise RuntimeError("accepted workflow submit is missing its run contract preview")
    previous = load_run_contract(state_dir)
    from zf.runtime.run_contract import write_run_contract_snapshot

    snapshot = write_run_contract_snapshot(state_dir, contract)
    path = write_run_contract(state_dir, contract)
    if str((previous or {}).get("contract_digest") or "") == str(
        contract.get("contract_digest") or ""
    ):
        return
    refs = contract.get("refs")
    refs = refs if isinstance(refs, dict) else {}
    manifest_refs = refs.get("workflow_input_manifest")
    manifest_refs = manifest_refs if isinstance(manifest_refs, list) else []
    writer.append(ZfEvent(
        type="config.run_contract.request_bound",
        actor="zf-cli",
        task_id=task_id,
        correlation_id=correlation_id,
        payload={
            "run_id": correlation_id,
            "run_contract_ref": str(snapshot.get("ref") or ""),
            "run_contract_sha256": str(snapshot.get("sha256") or ""),
            "contract_digest": str(contract.get("contract_digest") or ""),
            "active_run_contract_ref": str(path),
            "workflow_input_manifest_ref": str(manifest_refs[0] if manifest_refs else ""),
            "initial_binding": bool(run_contract.get("initial_binding")),
        },
    ))


def _delivery_refs_for_name(
    name: str,
    *,
    manifest: dict[str, Any],
    metadata: dict[str, Any],
    workflow_dir: Path,
) -> list[str]:
    key_aliases = {
        "source_inventory": ("source_inventory_ref", "source_inventory_refs"),
        "capability_matrix": ("capability_matrix_ref", "capability_matrix_refs"),
        "acceptance_matrix": ("acceptance_matrix_ref", "acceptance_matrix_refs"),
        "test_matrix": ("test_matrix_ref", "test_matrix_refs"),
        "regression_test_matrix": ("test_matrix_ref", "test_matrix_refs", "regression_test_matrix_ref"),
        "task_map": ("task_map_ref", "task_map_refs"),
        "real_e2e_matrix": ("real_e2e_matrix_ref", "real_e2e_matrix_refs"),
        "product_spec": ("prd_ref", "product_spec_ref", "spec_ref"),
        "demo_evidence": ("demo_evidence_ref", "demo_evidence_refs"),
        "issue_ref": ("issue_ref", "source_ref", "intake_ref"),
        "skill_adapter_plan": ("skill_adapter_plan_ref",),
    }
    refs: list[str] = []
    for key in key_aliases.get(name, (f"{name}_ref", f"{name}_refs")):
        refs.extend(_string_list(manifest.get(key)))
        refs.extend(_string_list(metadata.get(key)))
    if refs:
        return list(dict.fromkeys(refs))
    default_names = {
        "source_inventory": "source-inventory.json",
        "capability_matrix": "capability-matrix.json",
        "acceptance_matrix": "acceptance-matrix.json",
        "test_matrix": "test-matrix.json",
        "regression_test_matrix": "test-matrix.json",
        "task_map": "task-map.json",
        "real_e2e_matrix": "real-e2e-matrix.json",
        "demo_evidence": "demo-evidence.json",
    }
    filename = default_names.get(name)
    if filename and workflow_dir:
        candidate = workflow_dir / filename
        if candidate.exists():
            return [str(candidate)]
    return []


def _local_artifact_ref_missing(ref: str, *, project_root: Path) -> bool:
    text = str(ref or "").strip()
    if not text or "://" in text or text.startswith("#"):
        return False
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return not path.exists()


def _contract_requires_stop(strictness: str, required_for: str) -> bool:
    strictness = str(strictness or "").strip().lower()
    required_for = str(required_for or "").strip().lower()
    if strictness in {"strict", "full-parity", "full_parity", "release", "release_candidate"}:
        if required_for in {"strict", "standard"}:
            return True
    if strictness in {"full-parity", "full_parity", "release", "release_candidate"}:
        if required_for in {"full-parity", "full_parity", "release"}:
            return True
    return False


def _contract_is_strict(strictness: str) -> bool:
    return str(strictness or "").strip().lower() in {
        "strict",
        "full-parity",
        "full_parity",
        "release",
        "release_candidate",
    }


def _flow_kind(config: Any) -> str:
    from zf.core.workflow.flow_metadata import flow_metadata_for

    metadata = flow_metadata_for(config)
    return str(metadata.get("flow_kind") or (
        "refactor" if metadata.get("gap_loop") or metadata.get("verify_rescan") else ""
    ))


def _effective_flow_metadata(
    metadata: dict[str, Any],
    *,
    intake_report: dict[str, Any],
) -> dict[str, Any]:
    effective = dict(metadata)
    if not intake_report or intake_report.get("status") == "not_requested":
        return effective
    for key in ("source_root", "target_root"):
        configured = str(effective.get(key) or "").strip()
        if configured and not _is_flow_template_placeholder(configured):
            continue
        value = str(intake_report.get(key) or "").strip()
        if value:
            effective[key] = value
    return effective


def _is_flow_template_placeholder(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized.startswith(("todo:", "todo ", "<todo", "${todo"))


def _git_is_work_tree(root: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10,
        )
    except OSError:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _git_source_fingerprint(root: Path) -> dict[str, str]:
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True, text=True, timeout=30,
    )
    return {
        "head": head.stdout.strip() if head.returncode == 0 else "",
        "status_sha256": hashlib.sha256(
            (status.stdout if status.returncode == 0 else "").encode("utf-8")
        ).hexdigest(),
    }


def _resolve_declared_root(raw: str, project_root: Path) -> Path | None:
    if not raw or raw.startswith("TODO"):
        return None
    root = Path(raw).expanduser()
    return root if root.is_absolute() else (project_root / root)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _target_git_root(target_root: Path, project_root: Path) -> Path | None:
    if _git_is_work_tree(target_root):
        return target_root
    if _is_relative_to(target_root, project_root) and _git_is_work_tree(project_root):
        return project_root
    return None


def _refactor_safety_report(
    *,
    project_root: Path,
    metadata: dict[str, Any],
    flow_kind: str,
    intake_report: dict[str, Any],
) -> dict[str, Any]:
    """Mechanical refactor prechecks (doc 125 §6): disjoint source/target,
    target must be git (r10 target_ref lesson), source baseline must not move
    within one request (r6 write_violation class). Handwritten configs without
    flow_metadata get WARN, profile-driven refactor flows fail closed."""
    if flow_kind != "refactor":
        return {"status": "not_applicable", "diagnostics": []}
    diagnostics: list[dict[str, Any]] = []
    source_raw = str(metadata.get("source_root") or "")
    target_raw = str(metadata.get("target_root") or "")
    source_root = _resolve_declared_root(source_raw, project_root)
    target_root = _resolve_declared_root(target_raw, project_root) or project_root
    if source_root is None:
        severity = "STOP" if metadata else "WARN"
        diagnostics.append({
            "severity": severity,
            "kind": "workflow_source_root_undeclared",
            "title": "refactor source_root 未声明",
            "message": source_raw or "flow_metadata 无 source_root",
            "why_it_matters": "没有 source_root 就无法做 source/target 隔离与基线保护。",
            "fix_it": "在 FlowSpec/intake 中声明真实 sourceRoot(手写配置至少在 prompt 中锚定)。",
            "safe_auto_fix": False,
        })
    elif not source_root.exists():
        diagnostics.append({
            "severity": "STOP",
            "kind": "workflow_source_root_not_found",
            "title": "refactor source_root 不存在",
            "message": str(source_root),
            "why_it_matters": "source 路径无效时 scan/parity 全部建立在空分母上。",
            "fix_it": "修正 sourceRoot 路径。",
            "safe_auto_fix": False,
        })
    else:
        try:
            PathGuard.assert_disjoint(source_root, target_root)
        except PathGuardError as exc:
            diagnostics.append({
                "severity": "STOP",
                "kind": "workflow_source_target_overlap",
                "title": "source_root 与 target 重叠",
                "message": str(exc),
                "why_it_matters": "重叠时 candidate 写入会直接篡改 source(r6 write_violation 类事故)。",
                "fix_it": "让 sourceRoot 与 targetRoot 完全互斥。",
                "safe_auto_fix": False,
            })
    target_git_root = _target_git_root(target_root, project_root)
    if target_git_root is None:
        diagnostics.append({
            "severity": "STOP",
            "kind": "workflow_target_not_git",
            "title": "refactor target 不是 git 仓库",
            "message": str(target_root),
            "why_it_matters": "candidate/worktree 机制需要一个 git 承载根; target 子目录可不存在,但必须在 git project root 内。",
            "fix_it": "在项目根运行 git init,或使用 `zf project init --kind refactor --git-init`。",
            "safe_auto_fix": True,
        })
    report: dict[str, Any] = {
        "source_root": str(source_root or ""),
        "target_root": str(target_root),
        "target_git_root": str(target_git_root or ""),
    }
    if source_root is not None and source_root.exists():
        if not _git_is_work_tree(source_root):
            diagnostics.append({
                "severity": "WARN",
                "kind": "workflow_source_not_git",
                "title": "source_root 不是 git 仓库",
                "message": str(source_root),
                "why_it_matters": "无法建立 source 基线快照,运行中 source 被改动将不可检测。",
                "fix_it": "优先使用 git 管理的 source;否则自行保证 source 只读。",
                "safe_auto_fix": False,
            })
        else:
            fingerprint = _git_source_fingerprint(source_root)
            manifest_ref = str(intake_report.get("workflow_input_manifest_ref") or "")
            if manifest_ref:
                baseline_path = Path(manifest_ref).parent / "source-baseline.json"
                baseline = _load_json(baseline_path)
                if baseline:
                    if (
                        baseline.get("head") != fingerprint["head"]
                        or baseline.get("status_sha256") != fingerprint["status_sha256"]
                    ):
                        diagnostics.append({
                            "severity": "STOP",
                            "kind": "workflow_source_root_modified",
                            "title": "source_root 相对基线被改动",
                            "message": (
                                f"baseline head {baseline.get('head', '')[:12]} -> "
                                f"{fingerprint['head'][:12]}"
                            ),
                            "why_it_matters": "同一 request 内 source 变动会让 parity 分母漂移,结论不可信。",
                            "fix_it": "恢复 source 到基线,或显式开启新 request 重建基线。",
                            "safe_auto_fix": False,
                        })
                else:
                    baseline_path.write_text(
                        json.dumps({
                            "schema_version": "workflow.source_baseline.v1",
                            "source_root": str(source_root),
                            **fingerprint,
                            "created_at": _now_iso(),
                        }, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                report["source_baseline_ref"] = str(baseline_path)
            report["source_fingerprint"] = fingerprint
    stop = any(d["severity"] == "STOP" for d in diagnostics)
    warn = any(d["severity"] == "WARN" for d in diagnostics)
    report["status"] = "STOP" if stop else "WARN" if warn else "PASS"
    report["diagnostics"] = diagnostics
    return report


def _environment_readiness_diagnostics(
    metadata: dict[str, Any],
    *,
    allow_missing_env: bool,
) -> list[dict[str, Any]]:
    if str(metadata.get("environment_policy") or "") != "real_env_required":
        return []
    missing: list[str] = []
    if not any(os.environ.get(key) for key in _LLM_ENV_KEYS):
        missing.append("LLM_API_KEY")
    if shutil.which("docker") is None:
        missing.append("docker")
    if not missing:
        return []
    severity = "WARN" if allow_missing_env else "STOP"
    return [{
        "severity": severity,
        "kind": "environment_readiness_missing",
        "title": "真实环境依赖未就绪",
        "message": "缺少: " + ", ".join(missing),
        "why_it_matters": "real_env_required 需要真实 LLM/Web/Playwright 环境，否则 verify 不能证明产品可用。",
        "fix_it": "配置至少一个 LLM API key，并确保 Docker 可用；或显式使用 --allow-missing-env 只做 dry-run。",
        "safe_auto_fix": False,
    }]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unique_request_id(kind: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    safe = "".join(ch if ch.isalnum() else "-" for ch in str(kind or "auto").lower())
    return f"wfint-{safe}-{stamp}"


def _request_id_from_path(path: Path) -> str:
    stem = path.expanduser().name
    if stem.endswith((".md", ".json")):
        stem = Path(stem).stem
    return stem or _unique_request_id("auto")


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
    if any(term in lowered for term in refactor_terms):
        return "refactor"
    if any(term in lowered for term in prd_terms):
        return "prd"
    if any(term in lowered for term in issue_terms):
        return "issue"
    return "issue"


def _classification_reason(kind: str, *, explicit_kind: str) -> str:
    if explicit_kind != "auto":
        return f"explicit kind {kind!r} supplied by operator"
    return "classified from intake text and manifest hints"


def _build_skill_adapter_plan(
    *,
    kind: str,
    project_root: Path,
    project_id: str = "",
    state_dir: Path | None = None,
    strictness: str = "standard",
    parity_scope: tuple[str, ...] = (),
) -> dict[str, Any]:
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
    tests = []
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
        tests.append({
            "id": test_id,
            "test_id": test_id,
            "capability_id": cap_id,
            "acceptance_id": "",
            "tier": "real-e2e" if _surface_needs_real_e2e(surface) else "integration",
            "commands": related_commands,
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
    for row in tests:
        cap_id = str(row["capability_id"])
        row["acceptance_id"] = (
            first_acceptance_by_capability.get(cap_id)
            or (str(acceptances[0]["acceptance_id"]) if acceptances else "")
        )
    adapter_skills = skill_plan.get("loaded_skills")
    if not isinstance(adapter_skills, list):
        adapter_skills = []
    metadata = {
        "objective": objective,
        "adapter_skills": adapter_skills,
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
    _write_matrix_json(refs["test_matrix_ref"], "test-matrix.v1", "tests", tests, metadata)
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
            "test_matrix must include deterministic verification commands or evidence refs.",
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


def _extract_verification_commands(source_text: str) -> list[str]:
    commands: list[str] = []
    for raw_line in (source_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        inline = re.findall(r"`([^`]+)`", raw_line)
        inline_commands = [
            item.strip()
            for item in inline
            if _COMMAND_START_RE.match(item.strip())
        ]
        if inline_commands:
            commands.extend(inline_commands)
            continue
        line = re.sub(r"^[-*+]\s+", "", line).strip()
        line = re.sub(r"^\d+[.)]\s+", "", line).strip()
        line = line.strip("`")
        if line.startswith("$ "):
            line = line[2:].strip()
        if _COMMAND_START_RE.match(line):
            commands.append(line)
            continue
    return list(dict.fromkeys(commands))


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


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_manifest_for_intake(intake_path: Path) -> tuple[Path | None, dict[str, Any]]:
    project_root = _project_root_from_intake_path(intake_path)
    if not (project_root / "artifacts" / "workflow").exists():
        return None, {}
    for manifest_path in (project_root / "artifacts" / "workflow").glob(
        "*/workflow-input-manifest.json"
    ):
        manifest = _load_json(manifest_path)
        candidates = [
            str(manifest.get("intake_ref") or ""),
            str(manifest.get("intake_json_ref") or ""),
            str(manifest.get("intake_markdown_ref") or ""),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            candidate_path = Path(candidate).expanduser()
            if candidate_path == intake_path:
                return manifest_path, manifest
            try:
                if candidate_path.resolve() == intake_path.resolve():
                    return manifest_path, manifest
            except OSError:
                continue
    return None, {}


def _state_dir_for_config(config_path: Path, config: Any) -> Path:
    state_raw = str(getattr(getattr(config, "project", None), "state_dir", "") or ".zf")
    state = Path(state_raw).expanduser()
    if not state.is_absolute():
        state = config_path.expanduser().resolve().parent / state
    return state.resolve()


def _resolve_submit_task_id(task_id: str, *, request_id: str, kind: str) -> str:
    value = str(task_id or "").strip()
    if value:
        return value
    prefix = {"issue": "ISSUE", "prd": "PRD", "refactor": "REFACTOR"}.get(kind, "FLOW")
    safe = "".join(ch if ch.isalnum() else "-" for ch in request_id.upper()).strip("-")
    return f"{prefix}-{safe or 'REQUEST'}"


def _resolve_submit_pattern_id(
    *,
    config_path: Path,
    pattern_id: str,
    kind: str = "",
    workflow_tier: str = "",
) -> str:
    value = str(pattern_id or "").strip()
    if value:
        return value
    config = load_config(config_path)
    stages = list(getattr(getattr(config, "workflow", None), "stages", []) or [])
    stage_ids = [str(getattr(stage, "id", "") or "").strip() for stage in stages]
    stage_ids = [sid for sid in stage_ids if sid]
    route = _workflow_kind_route(config, kind)
    if route is not None:
        tier = str(workflow_tier or getattr(route, "default_tier", "") or "").strip().lower()
        tier_routes = dict(getattr(route, "tier_routes", {}) or {})
        if tier and tier in tier_routes and str(tier_routes[tier] or "").strip():
            return str(tier_routes[tier]).strip()
        if str(getattr(route, "pattern_id", "") or "").strip():
            return str(route.pattern_id).strip()
        raise ConfigError(
            f"workflow.kind_routes.{kind or 'unknown'} resolved but has no pattern_id"
        )
    metadata_kind = _normalize_request_kind(_flow_kind(config))
    requested_kind = _normalize_request_kind(kind)
    if stage_ids and metadata_kind and (
        not requested_kind or requested_kind == metadata_kind
    ):
        return stage_ids[0]
    if len(stage_ids) > 1:
        raise ConfigError(
            f"multiple workflow stages declared ({', '.join(stage_ids[:8])}); "
            "submit requires workflow.kind_routes or explicit --pattern-id"
        )
    for stage in stages:
        sid = str(getattr(stage, "id", "") or "").strip()
        if sid:
            return sid
    return ""


def _workflow_kind_route(config: Any, kind: str) -> Any | None:
    routes = dict(getattr(getattr(config, "workflow", None), "kind_routes", {}) or {})
    requested = _normalize_request_kind(kind)
    route = routes.get(requested)
    seen: set[str] = set()
    while route is not None and str(getattr(route, "alias", "") or "").strip():
        if requested in seen:
            raise ConfigError(f"workflow.kind_routes alias cycle at {requested!r}")
        seen.add(requested)
        requested = _normalize_request_kind(str(route.alias))
        route = routes.get(requested)
    return route


def _submit_payload_to_workflow_invoke(payload: dict[str, Any]) -> dict[str, Any]:
    source_refs = payload.get("source_refs") if isinstance(payload.get("source_refs"), dict) else {}
    artifact_refs = payload.get("artifact_refs") if isinstance(payload.get("artifact_refs"), list) else []
    return {
        "task_id": str(payload.get("task_id") or ""),
        "request_id": str(payload.get("request_id") or ""),
        "run_id": str(payload.get("run_id") or payload.get("request_id") or ""),
        "workflow_run_id": str(
            payload.get("run_id") or payload.get("request_id") or ""
        ),
        "kind": str(payload.get("kind") or ""),
        "flow_kind": str(payload.get("kind") or ""),
        "request_kind": str(payload.get("request_kind") or payload.get("kind") or ""),
        "workflow_tier": str(payload.get("workflow_tier") or ""),
        "pattern_id": str(payload.get("pattern_id") or ""),
        "requested_by": str(payload.get("requested_by") or "zf-cli"),
        "reason": str(payload.get("reason") or "workflow submit accepted"),
        "source": "workflow-submit",
        "source_refs": dict(source_refs),
        "workflow_input_manifest_ref": str(payload.get("workflow_input_manifest_ref") or ""),
        "workflow_prompt_ref": str(payload.get("workflow_prompt_ref") or ""),
        "workflow_request_ref": str(payload.get("workflow_request_ref") or ""),
        "requirement_spec_ref": str(payload.get("requirement_spec_ref") or ""),
        "requirement_spec_digest": str(payload.get("requirement_spec_digest") or ""),
        "request_revision": int(payload.get("request_revision") or 0),
        "prompt_kind": str(payload.get("kind") or ""),
        "artifact_refs": [{"path": str(ref)} for ref in artifact_refs if str(ref).strip()],
        "expected_output": f"execute {payload.get('kind') or 'workflow'} workflow",
    }


def _workflow_invoke_visibility(
    events: list[ZfEvent],
    *,
    source_event_id: str,
) -> dict[str, str]:
    for event in reversed(events):
        if event.type not in {"workflow.invoke.accepted", "workflow.invoke.rejected"}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("source_event_id") or "") != source_event_id:
            continue
        if event.type == "workflow.invoke.accepted":
            return {
                "status": "accepted",
                "next_action": "watch fanout/task events; workflow invoke was consumed by the orchestrator",
            }
        return {
            "status": "rejected",
            "next_action": "inspect workflow.invoke.rejected reason and resubmit after correction",
        }
    return {
        "status": "pending_consumer",
        "next_action": "ensure `zf start` watcher is running so workflow.invoke.requested is consumed",
    }


def _public_preflight_report(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if not str(key).startswith("_")}


def _skill_adapter_preflight_report(intake_report: dict[str, Any]) -> dict[str, Any]:
    if not intake_report or intake_report.get("status") == "not_requested":
        return {"status": "not_requested", "diagnostics": []}
    manifest_ref = str(intake_report.get("workflow_input_manifest_ref") or "")
    if not manifest_ref:
        return {"status": "not_requested", "diagnostics": []}
    manifest = _load_json(Path(manifest_ref))
    plan_ref = str(manifest.get("skill_adapter_plan_ref") or "")
    if not plan_ref:
        return {
            "status": "WARN",
            "diagnostics": [{
                "severity": "WARN",
                "kind": "workflow_skill_adapter_plan_missing",
                "title": "skill adapter plan 缺失",
                "message": "workflow-input-manifest.json 未声明 skill_adapter_plan_ref",
                "why_it_matters": "缺少 skill plan 时入口无法审计项目 adapter skill 覆盖。",
                "fix_it": "重新运行 `zf flow intake ...` 生成 skill-adapter-plan.json。",
                "safe_auto_fix": True,
            }],
        }
    plan = _load_json(Path(plan_ref))
    missing = [str(item) for item in plan.get("missing_skills") or [] if str(item).strip()]
    diagnostics = [
        dict(item) for item in plan.get("diagnostics") or []
        if isinstance(item, dict)
    ]
    if missing and not diagnostics:
        diagnostics.append({
            "severity": "WARN",
            "kind": "workflow_skill_adapter_missing",
            "title": "部分 workflow adapter skills 缺失",
            "message": ", ".join(missing),
            "why_it_matters": "缺少项目/阶段 skill 会增加 plan/verify 反复 replan 的概率。",
            "fix_it": "生成项目 adapter skill,或在 proposal 中显式接受 generic fallback。",
            "safe_auto_fix": False,
        })
    stop = any(str(item.get("severity") or "").upper() == "STOP" for item in diagnostics)
    warn = any(str(item.get("severity") or "").upper() == "WARN" for item in diagnostics)
    return {
        "status": "STOP" if stop else "WARN" if warn else "PASS",
        "skill_adapter_plan_ref": plan_ref,
        "strictness": plan.get("strictness", ""),
        "missing_skills": missing,
        "missing_required_skills": plan.get("missing_required_skills", []),
        "missing_recommended_skills": plan.get("missing_recommended_skills", []),
        "loaded_skills": plan.get("loaded_skills") if isinstance(plan.get("loaded_skills"), list) else [],
        "roleSkillBundles": plan.get("roleSkillBundles") if isinstance(plan.get("roleSkillBundles"), dict) else {},
        "proposed_skill_backlogs": (
            plan.get("proposed_skill_backlogs")
            if isinstance(plan.get("proposed_skill_backlogs"), list) else []
        ),
        "diagnostics": diagnostics,
    }


def _intake_preflight_report(intake_path: Path | None) -> dict[str, Any]:
    if intake_path is None:
        return {
            "status": "not_requested",
            "diagnostics": [],
        }
    path = intake_path.expanduser()
    diagnostics: list[dict[str, Any]] = []
    if not path.exists():
        diagnostics.append({
            "severity": "STOP",
            "kind": "workflow_intake_missing",
            "title": "workflow intake 不存在",
            "message": str(path),
            "why_it_matters": "workflow 启动前必须有可审计 intake artifact。",
            "fix_it": "先运行 `zf flow intake ...` 生成 intake。",
            "safe_auto_fix": False,
        })
        return {"status": "STOP", "intake_ref": str(path), "diagnostics": diagnostics}
    manifest_path, manifest = _load_manifest_for_intake(path)
    if manifest_path is None:
        diagnostics.append({
            "severity": "STOP",
            "kind": "workflow_input_manifest_missing",
            "title": "workflow input manifest 缺失",
            "message": f"no workflow-input-manifest.json references {path}",
            "why_it_matters": "后续 worker 需要稳定 manifest refs,不能只依赖聊天或 markdown。",
            "fix_it": "使用 `zf flow intake` 重新生成 intake + manifest。",
            "safe_auto_fix": False,
        })
        return {"status": "STOP", "intake_ref": str(path), "diagnostics": diagnostics}
    missing = [
        str(item) for item in manifest.get("missing_required_fields") or []
        if str(item).strip()
    ]
    if missing:
        diagnostics.append({
            "severity": "STOP",
            "kind": "workflow_intake_required_fields_missing",
            "title": "workflow intake 必填字段缺失",
            "message": ", ".join(missing),
            "why_it_matters": "缺少最小需求信息时启动 workflow 会导致后续 agent 猜测。",
            "fix_it": "重跑 `zf flow intake` 并带缺失字段的 flags(如 --target-root);直接编辑 intake md 不生效(真源=manifest JSON)。补齐后重新 submit。",
            "safe_auto_fix": False,
        })
    return {
        "status": "STOP" if diagnostics else "PASS",
        "intake_ref": str(path),
        "workflow_input_manifest_ref": str(manifest_path),
        "request_id": str(manifest.get("request_id") or ""),
        "kind": str(manifest.get("kind") or ""),
        "source_root": str(manifest.get("source_root") or ""),
        "target_root": str(manifest.get("target_root") or ""),
        "missing_required_fields": missing,
        "diagnostics": diagnostics,
    }
