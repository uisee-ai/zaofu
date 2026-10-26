"""zf flow — draft and preflight short controller workflows."""

from __future__ import annotations

import argparse
import hashlib
import json
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
from zf.cli.flow_draft_support import (
    default_tmux_session as _default_tmux_session,
    draft_runtime_profile_doc as _draft_runtime_profile_doc,
    explicit_orchestrator_spec as _explicit_orchestrator_spec,
    non_empty_mapping as _non_empty_mapping,
    orchestration_spec as _orchestration_spec,
    skill_sources_from_adapter_plan as _skill_sources_from_adapter_plan,
)
from zf.runtime.preflight import preflight_ok, run_preflight_checks
from zf.runtime.run_contract import (
    build_run_contract,
    evaluate_run_contract_submit_binding,
    load_run_contract,
    required_delivery_artifacts,
    write_run_contract,
)


from zf.runtime.workflow_intake import (
    _build_skill_adapter_plan,
    _capability_for_acceptance,
    _compact_text,
    _delivery_matrix_enrichment_contract,
    _delivery_surfaces_for_kind,
    _extract_acceptance_criteria,
    _extract_verification_commands,
    _infer_prd_surfaces,
    _infer_request_kind,
    _is_declared_verification_command,
    _load_project_config,
    _normalize_request_kind,
    _now_iso,
    _project_default_backend,
    _project_root_from_intake_path,
    _read_text_ref,
    _render_intake_markdown,
    _resolve_intake_backend,
    _safe_matrix_id,
    _state_dir_for_config,
    _surface_needs_real_e2e,
    _unique_request_id,
    _write_delivery_matrix_drafts,
    _write_matrix_json,
    build_flow_intake,
)
from zf.runtime.workflow_preflight import (
    _contract_is_strict,
    _contract_requires_stop,
    _delivery_launch_coverage_report,
    _delivery_refs_for_name,
    _effective_flow_metadata,
    _environment_readiness_diagnostics,
    _flow_kind,
    _git_delivery_baseline_diagnostics,
    _git_is_work_tree,
    _git_source_fingerprint,
    _intake_preflight_report,
    _is_flow_template_placeholder,
    _is_relative_to,
    _load_json,
    _load_manifest_for_intake,
    _local_artifact_ref_missing,
    _project_setup_readiness_diagnostics,
    _refactor_safety_report,
    _resolve_declared_root,
    _run_contract_preflight_report,
    _skill_adapter_preflight_report,
    _string_list,
    _target_git_root,
    build_flow_preflight_report,
)
from zf.runtime.workflow_delivery import (
    _pin_submitted_run_contract,
    _public_preflight_report,
    _request_id_from_path,
    _resolve_submit_pattern_id,
    _resolve_submit_task_id,
    _submit_payload_to_workflow_invoke,
    _submit_source_ref_blockers,
    _submitted_request_replay_result,
    _workflow_invoke_visibility,
    _flow_kind_route,
    _workflow_manifest_artifact_refs,
    apply_flow_submit,
    build_flow_submit_preview,
)

_REQUEST_KIND_CHOICES = [
    "issue",
    "prd",
    "refactor",
    "feat",
    "workflow",
    "auto",
]
_CONTROLLER_KIND_CHOICES = ["issue", "prd", "refactor", "feat"]
_FLOW_KIND_CHOICES = [*_CONTROLLER_KIND_CHOICES, "workflow"]


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
    draft.add_argument(
        "--kind",
        required=True,
        choices=_CONTROLLER_KIND_CHOICES,
    )
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
    start.add_argument(
        "--kind",
        required=True,
        choices=_CONTROLLER_KIND_CHOICES,
    )
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
    kind = _normalize_request_kind(explicit) if explicit != "auto" else "auto"
    if explicit == "auto" and manifest.get("kind") in {
        "issue",
        "prd",
        "refactor",
        "workflow",
    }:
        kind = str(manifest["kind"])
    current: dict[str, Any] = {}
    if manifest_path is not None and explicit != "auto":
        from zf.runtime.workflow_requests import load_workflow_request, revise_workflow_request

        config_path = _project_root_from_intake_path(intake_path) / "zf.yaml"
        if config_path.is_file():
            config = load_config(config_path)
            state_dir = _state_dir_for_config(config_path, config)
            current = load_workflow_request(state_dir, request_id)
            if current.get("kind") != kind and current.get("status") in {"submitted", "running"}:
                raise ValueError("active Workflow Request is immutable; use controlled recovery/replan")
    confidence = "pending" if kind == "auto" else "high"
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
    next_action = "clarify" if missing or kind == "auto" else "draft"
    result = {
        "schema_version": "workflow.intent.v1",
        "request_id": request_id,
        "kind": kind,
        "candidate_kind": _infer_request_kind(text),
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
        if current and current.get("kind") != kind:
            revise_workflow_request(
                state_dir, manifest_path, actor="zf-cli", kind=kind,
                expected_revision=current["revision"],
                expected_requirement_digest=current["requirement_spec_digest"],
                writer=EventWriter(event_log_from_project(state_dir, config=config)),
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
    verify_backend: str = "",
    lanes: int = 0,
    project_name: str = "",
    project_description: str = "",
    state_dir: str = "",
    project_root: Path | None = None,
    strictness: str = "standard",
    parity_scope: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    kind = _normalize_request_kind(kind)
    backend = canonical_backend_id(backend)
    verify_backend = canonical_backend_id(verify_backend)
    if verify_backend == backend:
        verify_backend = ""
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
        if verify_backend:
            spec["verifyBackend"] = verify_backend
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
        if verify_backend:
            spec["verifyBackend"] = verify_backend
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
        if verify_backend:
            spec["verifyBackend"] = verify_backend
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
            "project": {
                "name": project,
                **(
                    {"description": project_description.strip()}
                    if project_description.strip() else {}
                ),
                "state_dir": state,
            },
            "session": {
                "tmux_session": f"${{ZF_TMUX_SESSION:-{_default_tmux_session(project)}}}",
            },
            **_explicit_orchestrator_spec(
                backend,
                semantic_control=kind in {"prd", "refactor"},
            ),
            **_orchestration_spec(
                tier="light" if kind == "issue" else "full",
            ),
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
    verify_backend: str = "",
    lanes: int = 0,
    project_name: str = "",
    project_description: str = "",
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
            verify_backend=verify_backend,
            lanes=lanes or default_lanes_for_kind(kind),
            project_name=project,
            project_description=project_description,
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
        "project": {
            "name": project,
            **(
                {"description": project_description.strip()}
                if project_description.strip() else {}
            ),
            "state_dir": state,
        },
        "session": {
            "tmux_session": f"${{ZF_TMUX_SESSION:-{_default_tmux_session(project)}}}",
        },
        **_explicit_orchestrator_spec(backend, semantic_control=True),
        **_orchestration_spec(tier="multi"),
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


def _classification_reason(kind: str, *, explicit_kind: str) -> str:
    if kind == "auto":
        return "semantic selection required; choose an explicit kind or use workflow synthesis"
    if explicit_kind != "auto":
        return f"explicit kind {kind!r} supplied by operator"
    return "classified from intake text and manifest hints"
