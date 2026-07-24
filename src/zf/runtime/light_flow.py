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


def light_flow_metadata(config: Any, *, flow_kind: str = "") -> dict[str, Any] | None:
    from zf.core.workflow.flow_metadata import flow_metadata_for

    metadata = flow_metadata_for(config, flow_kind)
    if not metadata:
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
    verification_commands: list[str] | None = None,
    flow_kind: str = "prd",
    objective_ref: str = "",
) -> dict[str, Any]:
    root = (target_root or ".").strip().rstrip("/") or "."
    task_id = f"{pdd_id.upper()}-{LIGHT_TASK_SUFFIX}" if pdd_id else LIGHT_TASK_SUFFIX
    requirement_ref = objective_ref or prd_ref
    objective_text = objective.strip() or f"Deliver the work described by {requirement_ref}"
    refs = dict(workflow_refs or {})
    matrix_refs = _matrix_refs(refs)
    source_refs = refs.get("source_refs") if isinstance(refs.get("source_refs"), dict) else {}
    artifact_refs = refs.get("artifact_refs") if isinstance(refs.get("artifact_refs"), list) else []
    path_prefix = "" if root == "." else f"{root}/"
    flow_label = _flow_label(flow_kind)
    task = {
        "task_id": task_id,
        "title": objective_text[:120],
        "description": (
            f"{objective_text}\n\nSource requirement: {requirement_ref}. "
            "Single-lane light flow: you own the entire deliverable; "
            f"follow the {flow_label} acceptance criteria as the contract."
        ),
        "wave": 1,
        "allowed_paths": [f"{path_prefix}**", "README.md"],
        "allowed_paths_reason": "light flow single-lane deliverable owns the target root",
        "workflow_input_manifest_ref": str(refs.get("workflow_input_manifest_ref") or ""),
        "workflow_prompt_ref": str(refs.get("workflow_prompt_ref") or ""),
        "source_refs": source_refs,
        "artifact_refs": artifact_refs,
        **matrix_refs,
        "acceptance": [objective_text],
        "acceptance_criteria": [
            f"All acceptance criteria in {requirement_ref} are met on the current tree.",
            "Read and satisfy every referenced acceptance/test/real-e2e matrix before completion.",
            "Slice tests pass; runtime evidence regenerated and committed.",
        ],
    }
    commands = _dedupe_strings(verification_commands or [])
    if commands:
        task["verification"] = commands[0]
        task["validation"] = {
            "commands": [
                {
                    "id": f"light-verification-{index}",
                    "command": command,
                    "acceptance_ids": [],
                    "owner": "impl_self_check",
                    "tier": "task_non_smoke",
                    "deterministic": True,
                    "reusable": True,
                    "timeout_seconds": 900,
                }
                for index, command in enumerate(commands, start=1)
            ]
        }
    return {
        "schema_version": "task-map.v1",
        "workflow_input_manifest_ref": str(refs.get("workflow_input_manifest_ref") or ""),
        "workflow_prompt_ref": str(refs.get("workflow_prompt_ref") or ""),
        "source_refs": source_refs,
        "artifact_refs": artifact_refs,
        **matrix_refs,
        "shared_conventions": {
            "test_path_prefix": f"{path_prefix}tests",
        },
        "tasks": [task],
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
    flow_kind = str(payload.get("kind") or metadata.get("flow_kind") or "prd")
    pdd_id = str(payload.get("pdd_id") or payload.get("request_id") or f"{flow_kind}-default")
    objective = str(payload.get("objective") or payload.get("reason") or "")
    workflow_refs = _workflow_refs_from_payload(payload, state_dir=state_dir)
    objective_ref = str(
        payload.get("objective_ref")
        or payload.get("prd_ref")
        or payload.get("issue_ref")
        or metadata.get("objective_ref")
        or metadata.get("prd_ref")
        or metadata.get("issue_ref")
        or ""
    )
    task_map = synthesize_light_task_map(
        pdd_id=pdd_id,
        objective=objective,
        prd_ref=str(
            payload.get("prd_ref") or metadata.get("prd_ref") or ""
        ),
        objective_ref=objective_ref,
        target_root=str(
            payload.get("target_root") or metadata.get("target_root") or ""
        ),
        workflow_refs=workflow_refs,
        verification_commands=_light_verification_commands(
            config=config,
            workflow_refs=workflow_refs,
            state_dir=state_dir,
        ),
        flow_kind=flow_kind,
    )
    # light goal 终态闭环(2026-07-08):最简配置只开 goal.enabled、无人发
    # run.goal.started → goal 投影永远是 loop.started 兜底(run_id 空),
    # run_goal_completion_event 的 run_id 守卫正确拒发完成事件 → light 没有
    # goal 终态/停机语义。入口合成即补发真 goal(幂等),judge.passed 后由
    # _maybe_complete_run_goal 自动闭环 run.goal.completed。
    goal_enabled = bool(getattr(getattr(config, "goal", None), "enabled", False))
    if goal_enabled and not any(
        prior.type == "run.goal.started" for prior in events
    ):
        event_writer.append(ZfEvent(
            type="run.goal.started",
            actor="zf-cli",
            payload={
                "run_id": f"run-light-{pdd_id}-{event.id}",
                "objective": objective,
                "pdd_id": pdd_id,
                "source": "light_flow_kernel",
                "reason": "light topology: goal minted at entry synthesis",
            },
            causation_id=event.id,
            correlation_id=event.correlation_id or event.id,
        ))
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


def _light_verification_commands(
    *,
    config: Any,
    workflow_refs: dict[str, Any],
    state_dir: Path,
) -> list[str]:
    matrix_ref = str(workflow_refs.get("test_matrix_ref") or "").strip()
    matrix = _load_manifest_ref(matrix_ref, state_dir=state_dir)
    commands: list[str] = []
    tests = matrix.get("tests") if isinstance(matrix.get("tests"), list) else []
    for test in tests:
        if not isinstance(test, dict):
            continue
        raw = test.get("commands")
        if isinstance(raw, list):
            commands.extend(str(item).strip() for item in raw)
        elif isinstance(raw, str):
            commands.append(raw.strip())
    commands = _dedupe_strings(commands)
    if commands:
        return commands

    for gate in getattr(config, "quality_gates", {}).values():
        if not getattr(gate, "enabled", True):
            continue
        commands.extend(getattr(gate, "required_checks", []) or [])
    return _dedupe_strings(commands)


def _dedupe_strings(items: list[Any]) -> list[str]:
    return list(dict.fromkeys(
        str(item).strip() for item in items if str(item or "").strip()
    ))


def _workflow_refs_from_payload(payload: dict[str, Any], *, state_dir: Path) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    for key in (
        "workflow_input_manifest_ref",
        "workflow_prompt_ref",
        "workflow_run_id",
        "requirement_spec_ref",
        "requirement_spec_digest",
        "objective_ref",
        "prd_ref",
        "issue_ref",
    ):
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
    if (
        refs.get("source_refs")
        or refs.get("artifact_refs")
        or refs.get("workflow_input_manifest_ref")
    ):
        return refs
    return {}


def _flow_label(flow_kind: str) -> str:
    value = str(flow_kind or "").strip().lower()
    if value == "issue":
        return "issue fix"
    if value == "refactor":
        return "refactor follow-up"
    if value == "feat":
        return "feature"
    return "product"


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
