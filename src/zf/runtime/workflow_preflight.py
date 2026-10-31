"""Workflow delivery preflight application helpers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.render import build_config_inspection_report
from zf.runtime.preflight import preflight_ok, run_preflight_checks
from zf.runtime.run_contract import (
    build_run_contract,
    evaluate_run_contract_submit_binding,
    load_run_contract,
    required_delivery_artifacts,
)
from zf.runtime.workflow_intake import _project_root_from_intake_path
from zf.runtime.workflow_refactor_preflight import (
    git_is_work_tree as _git_is_work_tree,
    git_source_fingerprint as _git_source_fingerprint,
    is_relative_to as _is_relative_to,
    is_flow_template_placeholder as _is_flow_template_placeholder,
    refactor_safety_report as _refactor_safety_report,
    resolve_declared_root as _resolve_declared_root,
    target_git_root as _target_git_root,
)

def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []

def build_flow_preflight_report(
    config_path: Path,
    *,
    flow_kind: str = "",
    intake_path: Path | None = None,
    workflow_input_manifest_path: Path | None = None,
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
    # Match zf start before an owner approves a configuration that cannot boot.
    from zf.core.skills import validate_skill_sources
    diagnostics.extend({
        "severity": "STOP" if config.runtime.skills.strict else "WARN",
        "kind": "skill_binding_invalid",
        "title": "角色技能绑定无法通过启动校验",
        "message": problem,
        "fix_it": "选择存在且适用于该角色和阶段的技能后重新预览。",
        "safe_auto_fix": False,
    } for problem in validate_skill_sources(config=config, project_root=project_root))
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
    intake_report = _intake_preflight_report(
        intake_path,
        workflow_input_manifest_path=workflow_input_manifest_path,
    )
    diagnostics.extend(intake_report.get("diagnostics", []))
    effective_kind = str(
        flow_kind
        or intake_report.get("kind")
        or _flow_kind(config)
        or ""
    )
    diagnostics.extend(_workflow_route_readiness_diagnostics(
        config,
        flow_kind=effective_kind,
    ))
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

    configured_metadata = flow_metadata_for(config, effective_kind)
    metadata = _effective_flow_metadata(
        configured_metadata,
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
        configured_metadata=configured_metadata,
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


def _workflow_route_readiness_diagnostics(
    config: Any,
    *,
    flow_kind: str,
) -> list[dict[str, Any]]:
    from zf.runtime.workflow_route_catalog import (
        delivery_route_contracts_for_kind,
    )

    diagnostics: list[dict[str, Any]] = []
    for contract in delivery_route_contracts_for_kind(config, flow_kind):
        if contract["ok"]:
            continue
        diagnostics.append({
            "severity": "STOP",
            "kind": "workflow_route_entry_invalid",
            "title": "workflow route 入口契约无效",
            "message": contract["error"],
            "why_it_matters": (
                "入口 topology 与 start adapter 不匹配时，运行会被接受却无法形成 "
                "TaskAttempt、lease 和 transport dispatch。"
            ),
            "fix_it": (
                "blocking/full route 指向 DAG external_triggers 中声明的 "
                "fanout_reader；light route 保持生成的 light adapter 入口。"
            ),
            "safe_auto_fix": False,
        })
    return diagnostics

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
    rotation = _terminal_run_contract_rotation_context(
        state_dir=state_dir,
        previous=previous,
        current_request_id=str(intake_report.get("request_id") or ""),
    )
    binding = evaluate_run_contract_submit_binding(
        previous,
        contract,
        bootstrap=bootstrap,
        strict=strict,
        prior_terminal_rotation=bool(rotation),
    )
    diagnostics = list(binding.get("diagnostics") or [])
    return {
        "schema_version": "run-contract-preflight.v1",
        "status": str(binding.get("status") or "PASS"),
        "preview": contract,
        "previous_ref": str(state_dir / "config" / "run-contract.json") if previous else "",
        "initial_binding": bool(binding.get("initial_binding")),
        "prior_terminal_rotation": bool(binding.get("prior_terminal_rotation")),
        "prior_run_id": str(rotation.get("prior_run_id") or ""),
        "prior_terminal_event_id": str(
            rotation.get("prior_terminal_event_id") or ""
        ),
        "comparison_basis": str(binding.get("comparison_basis") or "current"),
        "diagnostics": diagnostics,
    }


def _terminal_run_contract_rotation_context(
    *,
    state_dir: Path,
    previous: dict[str, Any] | None,
    current_request_id: str,
) -> dict[str, str]:
    """Prove that the active compatibility contract belongs to a closed Run."""

    previous_digest = str((previous or {}).get("contract_digest") or "").strip()
    request_id = str(current_request_id or "").strip()
    if not previous_digest or not request_id:
        return {}

    from zf.core.events.log import EventLog
    from zf.runtime.run_admission import build_run_admission_projection

    events = EventLog(state_dir / "events.jsonl").read_all()
    binding = next(
        (
            event
            for event in reversed(events)
            if event.type == "config.run_contract.request_bound"
            and str((event.payload or {}).get("contract_digest") or "").strip()
            == previous_digest
        ),
        None,
    )
    if binding is None:
        return {}
    payload = binding.payload if isinstance(binding.payload, dict) else {}
    prior_run_id = str(
        payload.get("run_id") or binding.correlation_id or ""
    ).strip()
    if not prior_run_id or prior_run_id == request_id:
        return {}

    projection = build_run_admission_projection(events)
    prior = projection.runs.get(prior_run_id)
    if prior is None or not prior.terminal or projection.active_run_ids:
        return {}
    return {
        "prior_run_id": prior_run_id,
        "prior_terminal_event_id": str(prior.terminal_event_id or ""),
        "prior_terminal_type": str(prior.terminal_type or ""),
    }

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

def _environment_readiness_diagnostics(
    metadata: dict[str, Any],
    *,
    allow_missing_env: bool,
) -> list[dict[str, Any]]:
    if str(metadata.get("environment_policy") or "") != "real_env_required":
        return []
    missing: list[str] = []
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
        "why_it_matters": "real_env_required 需要 Docker 承载真实 Web/Playwright 验证。",
        "fix_it": "安装并启动 Docker；或显式使用 --allow-missing-env 只做 dry-run。",
        "safe_auto_fix": False,
    }]

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

def _intake_preflight_report(
    intake_path: Path | None,
    *,
    workflow_input_manifest_path: Path | None = None,
) -> dict[str, Any]:
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
    manifest_path = (
        workflow_input_manifest_path.expanduser()
        if workflow_input_manifest_path is not None
        else None
    )
    manifest = _load_json(manifest_path) if manifest_path is not None else {}
    if manifest_path is None:
        manifest_path, manifest = _load_manifest_for_intake(path)
    if manifest_path is None or not manifest:
        diagnostics.append({
            "severity": "STOP",
            "kind": "workflow_input_manifest_missing",
            "title": "workflow input manifest 缺失",
            "message": (
                f"workflow input manifest is missing or invalid: "
                f"{manifest_path or path}"
            ),
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
