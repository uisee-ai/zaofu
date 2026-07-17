"""Run contract snapshot and drift diagnostics.

The run contract is a compact, deterministic launch snapshot. It does not
replace ``zf.yaml`` or runtime state; it records the config/input/skill/artifact
refs that a run was launched with so restart/resume can detect drift.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


RUN_CONTRACT_SCHEMA = "run-contract.v1"
STRICT_RUN_CONTRACT_VALUES = frozenset({
    "strict",
    "full-parity",
    "full_parity",
    "release",
    "release_candidate",
})


def build_run_contract(
    config: Any,
    *,
    config_path: Path,
    project_root: Path | None = None,
    state_dir: Path | None = None,
    workflow_input_manifest_ref: str = "",
    skill_adapter_plan_ref: str = "",
    run_tag: str = "",
) -> dict[str, Any]:
    """Build a deterministic run contract preview for a loaded config."""

    config_path = config_path.expanduser().resolve(strict=False)
    project_root = (project_root or config_path.parent).expanduser().resolve(strict=False)
    if state_dir is None:
        state_raw = str(getattr(getattr(config, "project", None), "state_dir", "") or ".zf")
        state_dir = Path(state_raw).expanduser()
        if not state_dir.is_absolute():
            state_dir = project_root / state_dir
    state_dir = state_dir.expanduser().resolve(strict=False)
    from zf.core.workflow.flow_metadata import flow_metadata_for

    metadata = flow_metadata_for(config)
    manifest_ref = workflow_input_manifest_ref or str(
        metadata.get("workflow_input_manifest_ref") or ""
    )
    manifest = _load_json_ref(manifest_ref, project_root=project_root)
    metadata = flow_metadata_for(
        config,
        str(manifest.get("kind") or manifest.get("request_kind") or ""),
    )
    if not skill_adapter_plan_ref:
        skill_adapter_plan_ref = str(
            manifest.get("skill_adapter_plan_ref")
            or metadata.get("skill_adapter_plan_ref")
            or ""
        )
    refs = _collect_contract_refs(
        metadata=metadata,
        manifest=manifest,
        workflow_input_manifest_ref=manifest_ref,
        skill_adapter_plan_ref=skill_adapter_plan_ref,
    )
    result_protocol = (
        dict(metadata.get("result_protocol") or {})
        if isinstance(metadata.get("result_protocol"), Mapping)
        else {}
    )
    from zf.runtime.call_result_admission import CALL_RESULT_ADAPTER_VERSION
    from zf.runtime.call_result_envelope import CALL_RESULT_CANONICALIZATION
    from zf.runtime.workflow_operation import WORKFLOW_OPERATION_CANONICALIZATION

    required_operation_ids = _string_list(
        result_protocol.get("required_operation_ids")
        or metadata.get("required_operation_ids")
    )
    contract: dict[str, Any] = {
        "schema_version": RUN_CONTRACT_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_tag": run_tag,
        "project": {
            "name": str(getattr(getattr(config, "project", None), "name", "") or ""),
            "state_dir": str(state_dir),
            "root": str(project_root),
        },
        "workflow": {
            "kind": str(metadata.get("flow_kind") or ""),
            "schema_profile": str(getattr(getattr(config, "workflow", None), "schema_profile", "") or ""),
            "quality_floor": str(metadata.get("quality_floor") or ""),
            "strictness": str(manifest.get("strictness") or metadata.get("strictness") or ""),
            "gap_loop": str(metadata.get("gap_loop") or ""),
            "post_verify_discovery": str(metadata.get("post_verify_discovery") or ""),
            "completion_threshold": str(metadata.get("completion_threshold") or ""),
        },
        "config": {
            "path": str(config_path),
            "sha256": _file_sha256(config_path),
        },
        "refs": refs,
        "digests": _ref_digests(refs, project_root=project_root),
        "required_delivery_artifacts": required_delivery_artifacts(
            str(metadata.get("flow_kind") or manifest.get("kind") or "")
        ),
        "protocols": {
            "result_protocol": {
                "schema_version": "call-result-envelope.v1",
                "mode": str(
                    result_protocol.get("mode")
                    or metadata.get("result_protocol_mode")
                    or "shadow"
                ),
                "adapter_version": str(
                    result_protocol.get("adapter_version")
                    or CALL_RESULT_ADAPTER_VERSION
                ),
                "canonicalization_version": str(
                    result_protocol.get("canonicalization_version")
                    or CALL_RESULT_CANONICALIZATION
                ),
            },
            "workflow_operation": {
                "schema_version": "workflow-operation.v1",
                "canonicalization_version": str(
                    result_protocol.get("operation_canonicalization_version")
                    or WORKFLOW_OPERATION_CANONICALIZATION
                ),
                "required_operation_ids": required_operation_ids,
            },
            "required_read": {
                "schema_version": "input-consumption-policy.v1",
                "policy_ref": str(result_protocol.get("read_policy_ref") or ""),
                "policy_digest": str(result_protocol.get("read_policy_digest") or ""),
            },
            "goal_closure": {
                "schema_version": "goal-closure-protocol.v1",
                "authority": "admitted_thin_judge",
                "delivery_policy": str(metadata.get("delivery_policy") or "report_only"),
                "approval_policy": str(metadata.get("approval_policy") or ""),
                "target_ref": str(
                    getattr(getattr(getattr(config, "runtime", None), "git", None), "ship_target_branch", "")
                    or ""
                ),
                "claim_set_binding": "goal.claim_set.pinned",
                "terminal_event": "run.goal.completed",
            },
        },
    }
    contract["contract_digest"] = stable_json_sha256(_stable_contract_body(contract))
    return contract


def write_run_contract(
    state_dir: Path,
    contract: Mapping[str, Any],
) -> Path:
    path = state_dir.expanduser() / "config" / "run-contract.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(contract), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def load_run_contract(state_dir: Path) -> dict[str, Any] | None:
    path = state_dir.expanduser() / "config" / "run-contract.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def run_contract_drift_diagnostics(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    *,
    strict: bool = False,
) -> list[dict[str, Any]]:
    if not previous:
        return []
    diagnostics: list[dict[str, Any]] = []
    for field in ("contract_digest",):
        old = str(previous.get(field) or "")
        new = str(current.get(field) or "")
        if old and new and old != new:
            diagnostics.append(_drift(field, old, new, strict=strict))
    prev_digests = previous.get("digests") if isinstance(previous.get("digests"), dict) else {}
    curr_digests = current.get("digests") if isinstance(current.get("digests"), dict) else {}
    for key in sorted(set(prev_digests) | set(curr_digests)):
        old = str(prev_digests.get(key) or "")
        new = str(curr_digests.get(key) or "")
        if old and new and old != new:
            diagnostics.append(_drift(f"digests.{key}", old, new, strict=strict))
    return diagnostics


def is_strict_run_contract(contract: Mapping[str, Any] | None) -> bool:
    """Whether drift for this contract must fail closed.

    The check accepts both old and current snapshots. Restart/resume must not
    downgrade a previously strict run just because the new config omitted the
    metadata field.
    """
    if not contract:
        return False
    workflow = contract.get("workflow")
    workflow = workflow if isinstance(workflow, Mapping) else {}
    strictness = str(workflow.get("strictness") or "").strip().lower()
    if strictness in STRICT_RUN_CONTRACT_VALUES:
        return True
    quality_floor = str(workflow.get("quality_floor") or "").strip().lower()
    completion_threshold = str(workflow.get("completion_threshold") or "").strip().lower()
    return (
        quality_floor in {"release", "full-parity", "full_parity"}
        or completion_threshold in {"100%", "1.0", "full", "complete"}
    )


def strict_run_contract_drift(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    *,
    strict: bool = False,
) -> bool:
    return bool(strict or is_strict_run_contract(previous) or is_strict_run_contract(current))


def evaluate_run_contract_resume_policy(
    config: Any,
    *,
    config_path: Path,
    project_root: Path,
    state_dir: Path,
    strict: bool = False,
) -> dict[str, Any]:
    """Preview restart/resume safety without overwriting the pinned contract."""
    current = build_run_contract(
        config,
        config_path=config_path,
        project_root=project_root,
        state_dir=state_dir,
    )
    previous = load_run_contract(state_dir)
    effective_strict = strict_run_contract_drift(previous, current, strict=strict)
    diagnostics = run_contract_drift_diagnostics(
        previous,
        current,
        strict=effective_strict,
    )
    return {
        "schema_version": "run-contract.resume-policy.v1",
        "status": "STOP" if diagnostics and effective_strict else "WARN" if diagnostics else "PASS",
        "strict": effective_strict,
        "diagnostics": diagnostics,
        "previous_digest": str((previous or {}).get("contract_digest") or ""),
        "current_digest": str(current.get("contract_digest") or ""),
        "run_contract_ref": str(state_dir / "config" / "run-contract.json"),
    }


def required_delivery_artifacts(flow_kind: str) -> list[dict[str, str]]:
    kind = str(flow_kind or "").strip().lower()
    common = [
        {"name": "skill_adapter_plan", "required_for": "strict"},
        {"name": "acceptance_matrix", "required_for": "strict"},
        {"name": "test_matrix", "required_for": "strict"},
        {"name": "task_map", "required_for": "strict"},
    ]
    if kind == "refactor":
        return [
            {"name": "source_inventory", "required_for": "strict"},
            {"name": "capability_matrix", "required_for": "strict"},
            *common,
            {"name": "real_e2e_matrix", "required_for": "full-parity"},
        ]
    if kind == "prd":
        return [
            {"name": "product_spec", "required_for": "strict"},
            {"name": "capability_matrix", "required_for": "strict"},
            *common,
            {"name": "demo_evidence", "required_for": "release"},
        ]
    if kind == "issue":
        return [
            {"name": "issue_ref", "required_for": "standard"},
            {"name": "regression_test_matrix", "required_for": "strict"},
            *common,
        ]
    return common


def stable_json_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_contract_body(contract: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in contract.items()
        if key not in {"created_at", "contract_digest"}
    }


def _collect_contract_refs(
    *,
    metadata: Mapping[str, Any],
    manifest: Mapping[str, Any],
    workflow_input_manifest_ref: str,
    skill_adapter_plan_ref: str,
) -> dict[str, list[str]]:
    fields = {
        "workflow_input_manifest": [workflow_input_manifest_ref],
        "skill_adapter_plan": [skill_adapter_plan_ref],
        "source_inventory": _ref_list(manifest, metadata, "source_inventory_ref", "source_inventory_refs"),
        "capability_matrix": _ref_list(manifest, metadata, "capability_matrix_ref", "capability_matrix_refs"),
        "acceptance_matrix": _ref_list(manifest, metadata, "acceptance_matrix_ref", "acceptance_matrix_refs"),
        "test_matrix": _ref_list(manifest, metadata, "test_matrix_ref", "test_matrix_refs"),
        "task_map": _ref_list(manifest, metadata, "task_map_ref", "task_map_refs"),
        "real_e2e_matrix": _ref_list(manifest, metadata, "real_e2e_matrix_ref", "real_e2e_matrix_refs"),
    }
    artifact_refs = _string_list(manifest.get("artifact_refs")) + _string_list(metadata.get("artifact_refs"))
    if artifact_refs:
        fields["artifact_refs"] = artifact_refs
    return {
        key: list(dict.fromkeys(value for value in refs if str(value).strip()))
        for key, refs in fields.items()
    }


def _ref_list(
    manifest: Mapping[str, Any],
    metadata: Mapping[str, Any],
    *keys: str,
) -> list[str]:
    out: list[str] = []
    for key in keys:
        out.extend(_string_list(manifest.get(key)))
        out.extend(_string_list(metadata.get(key)))
    return out


def _ref_digests(refs: Mapping[str, list[str]], *, project_root: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for group, values in refs.items():
        for index, ref in enumerate(values):
            path = _resolve_ref(ref, project_root=project_root)
            if path is None or not path.exists() or not path.is_file():
                continue
            digests[f"{group}[{index}]"] = _file_sha256(path)
    return digests


def _load_json_ref(ref: str, *, project_root: Path) -> dict[str, Any]:
    path = _resolve_ref(ref, project_root=project_root)
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_ref(ref: str, *, project_root: Path) -> Path | None:
    if not str(ref or "").strip():
        return None
    path = Path(str(ref)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve(strict=False)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _drift(field: str, old: str, new: str, *, strict: bool) -> dict[str, Any]:
    return {
        "severity": "STOP" if strict else "WARN",
        "kind": "run_contract_drift",
        "field": field,
        "previous": old,
        "current": new,
        "message": f"run contract drift on {field}: {old[:12]} -> {new[:12]}",
        "safe_auto_fix": False,
    }


__all__ = [
    "RUN_CONTRACT_SCHEMA",
    "build_run_contract",
    "evaluate_run_contract_resume_policy",
    "load_run_contract",
    "required_delivery_artifacts",
    "run_contract_drift_diagnostics",
    "stable_json_sha256",
    "write_run_contract",
]
