"""批D:light 拓扑的 kernel 侧——入口触发时机械合成单任务 task_map。

textstat 实弹:小任务走全拓扑,编排固定成本 ≈ 实际工作量 200%,且
plan 分解本身是主要缺陷源(约定分叉/依赖谎言/层级错配)。light 拓扑
把"塞得进单上下文的任务"交给单 lane goal 环:无 scan/plan agent,
task_map 由 kernel 确定性合成(单任务=整个 objective),admission/
候选集成/verify/judge 机械门全保留。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent

LIGHT_TASK_SUFFIX = "DELIVER-001"


def light_flow_metadata(config: Any) -> dict[str, Any] | None:
    workflow = getattr(config, "workflow", None)
    metadata = getattr(workflow, "flow_metadata", None)
    if not isinstance(metadata, dict):
        return None
    if str(metadata.get("topology") or "") != "light":
        return None
    return metadata


def synthesize_light_task_map(
    *,
    pdd_id: str,
    objective: str,
    prd_ref: str,
    target_root: str,
    workflow_refs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = (target_root or ".").strip().rstrip("/") or "."
    task_id = f"{pdd_id.upper()}-{LIGHT_TASK_SUFFIX}" if pdd_id else LIGHT_TASK_SUFFIX
    objective_text = objective.strip() or f"Deliver the product described by {prd_ref}"
    refs = dict(workflow_refs or {})
    matrix_refs = _matrix_refs(refs)
    source_refs = refs.get("source_refs") if isinstance(refs.get("source_refs"), dict) else {}
    artifact_refs = refs.get("artifact_refs") if isinstance(refs.get("artifact_refs"), list) else []
    return {
        "schema_version": "task-map.v1",
        "workflow_input_manifest_ref": str(refs.get("workflow_input_manifest_ref") or ""),
        "workflow_prompt_ref": str(refs.get("workflow_prompt_ref") or ""),
        "source_refs": source_refs,
        "artifact_refs": artifact_refs,
        **matrix_refs,
        "shared_conventions": {
            "test_path_prefix": f"{root}/tests",
        },
        "tasks": [{
            "task_id": task_id,
            "title": objective_text[:120],
            "description": (
                f"{objective_text}\n\nSource requirement: {prd_ref}. "
                "Single-lane light flow: you own the entire deliverable; "
                "follow the PRD acceptance criteria as the contract."
            ),
            "wave": 1,
            "allowed_paths": [f"{root}/**", "README.md"],
            "allowed_paths_reason": "light flow single-lane deliverable owns the target root",
            "workflow_input_manifest_ref": str(refs.get("workflow_input_manifest_ref") or ""),
            "workflow_prompt_ref": str(refs.get("workflow_prompt_ref") or ""),
            "source_refs": source_refs,
            "artifact_refs": artifact_refs,
            **matrix_refs,
            "acceptance": [objective_text],
            "acceptance_criteria": [
                f"All acceptance criteria in {prd_ref} are met on the current tree.",
                "Read and satisfy every referenced acceptance/test/real-e2e matrix before completion.",
                "Slice tests pass; runtime evidence regenerated and committed.",
            ],
            "verification": [
                "Use workflow_input_manifest_ref, acceptance_matrix_ref, test_matrix_ref, and real_e2e_matrix_ref as the verification contract when present.",
                "Run every verification command declared by the PRD or generated matrices before claiming done.",
            ],
        }],
    }


def maybe_synthesize_light_task_map(
    *,
    event: ZfEvent,
    config: Any,
    state_dir: Path,
    event_writer: Any,
    events: list[ZfEvent],
) -> ZfEvent | None:
    """入口触发 → 写 task_map + 发 task_map.ready(幂等)。"""
    metadata = light_flow_metadata(config)
    if metadata is None:
        return None
    entry = str(metadata.get("light_entry_trigger") or "prd.requested")
    if event.type != entry:
        return None
    for prior in events:
        if prior.type != "task_map.ready":
            continue
        payload = prior.payload if isinstance(prior.payload, dict) else {}
        if (
            prior.causation_id == event.id
            or str(payload.get("source") or "") == "light_flow_kernel"
        ):
            return None  # 已合成过(幂等;重入靠 rework_of 通道)
    payload = event.payload if isinstance(event.payload, dict) else {}
    pdd_id = str(payload.get("pdd_id") or "default")
    workflow_refs = _workflow_refs_from_payload(payload, state_dir=state_dir)
    task_map = synthesize_light_task_map(
        pdd_id=pdd_id,
        objective=str(payload.get("objective") or payload.get("reason") or ""),
        prd_ref=str(
            payload.get("prd_ref") or metadata.get("prd_ref") or ""
        ),
        target_root=str(
            payload.get("target_root") or metadata.get("target_root") or ""
        ),
        workflow_refs=workflow_refs,
    )
    target = Path(state_dir) / "artifacts" / pdd_id / "task_map.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(task_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return event_writer.append(ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        payload={
            **workflow_refs,
            "task_map_ref": f".zf/artifacts/{pdd_id}/task_map.json",
            "pdd_id": pdd_id,
            "source": "light_flow_kernel",
            "reason": "light topology: kernel-synthesized single-task map",
        },
        causation_id=event.id,
        correlation_id=event.correlation_id or event.id,
    ))


_MATRIX_REF_KEYS = (
    "source_inventory_ref",
    "capability_matrix_ref",
    "acceptance_matrix_ref",
    "test_matrix_ref",
    "task_map_ref",
    "real_e2e_matrix_ref",
    "skill_adapter_plan_ref",
    "intake_json_ref",
)


def _matrix_refs(payload: dict[str, Any]) -> dict[str, str]:
    return {
        key: str(payload.get(key) or "")
        for key in _MATRIX_REF_KEYS
        if str(payload.get(key) or "").strip()
    }


def _workflow_refs_from_payload(payload: dict[str, Any], *, state_dir: Path) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    for key in ("workflow_input_manifest_ref", "workflow_prompt_ref", "workflow_run_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            refs[key] = value
    source_refs = payload.get("source_refs") if isinstance(payload.get("source_refs"), dict) else {}
    artifact_refs = payload.get("artifact_refs") if isinstance(payload.get("artifact_refs"), list) else []
    refs["source_refs"] = dict(source_refs)
    refs["artifact_refs"] = list(artifact_refs)
    refs.update(_matrix_refs(payload))
    manifest_ref = str(refs.get("workflow_input_manifest_ref") or "").strip()
    manifest = _load_manifest_ref(manifest_ref, state_dir=state_dir)
    if manifest:
        refs.setdefault("workflow_run_id", str(manifest.get("workflow_run_id") or manifest.get("request_id") or ""))
        refs.setdefault("workflow_prompt_ref", str(manifest.get("workflow_prompt_ref") or manifest.get("intake_ref") or ""))
        for key in _MATRIX_REF_KEYS:
            value = str(manifest.get(key) or "").strip()
            if value:
                refs.setdefault(key, value)
        manifest_artifacts = manifest.get("artifact_refs")
        if isinstance(manifest_artifacts, list):
            merged = [*refs.get("artifact_refs", []), *manifest_artifacts]
            refs["artifact_refs"] = _dedupe_artifact_refs(merged)
    if refs.get("source_refs") or refs.get("artifact_refs") or refs.get("workflow_input_manifest_ref"):
        return refs
    return {}


def _load_manifest_ref(ref: str, *, state_dir: Path) -> dict[str, Any]:
    if not ref:
        return {}
    path = Path(ref).expanduser()
    if not path.is_absolute():
        if ref.startswith(".zf/"):
            path = state_dir / ref.removeprefix(".zf/")
        else:
            path = state_dir.parent / ref
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _dedupe_artifact_refs(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    out: list[Any] = []
    for item in items:
        if isinstance(item, dict):
            key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        else:
            key = str(item or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


__all__ = [
    "light_flow_metadata",
    "maybe_synthesize_light_task_map",
    "synthesize_light_task_map",
]
