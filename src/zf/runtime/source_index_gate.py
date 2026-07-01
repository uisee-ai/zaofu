"""Source-index 双向锚门(B4,doc 91 P1/§3.1;1345 record 实测断裂)。

正向锚:每个 task 须有 source_key/source_ref(锚回 plan/scan 段落),
否则 plan synth 压缩丢失无从对账(R25:24 findings → 6 tasks,
source-index 全空,per-task 回溯链断)。
反向审计:每条 scan finding 须被某 task 认领 —— 没人认领 = 拆分丢失。

分级(doc 91 §3.1):baseline 缺锚 → 合成 degraded source_index +
证据事件,不挡;strict/release → fail-closed 拒绝进入 impl。
纯函数模块:评估与合成不做 IO,事件由调用方(admission 接线层)发。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEGRADED_SCHEMA = "source-index.v1"
_ANCHOR_KEYS = (
    "source_key",
    "source_keys",
    "source_ref",
    "source_refs",
    "source_excerpt",
)
_FAIL_CLOSED_PROFILES = {"strict", "release"}


@dataclass(frozen=True)
class SourceIndexGateResult:
    passed: bool
    mode: str  # "ok" | "degraded" | "fail_closed"
    missing_anchor_task_ids: list[str] = field(default_factory=list)
    unclaimed_finding_ids: list[str] = field(default_factory=list)
    degraded_index: dict[str, Any] | None = None
    note: str = ""


def _has_anchor_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_anchor_value(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_anchor_value(v) for v in value)
    return False


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_string_values(item))
        return out
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_string_values(item))
        return out
    text = str(value or "").strip()
    return [text] if text else []


def _task_has_anchor(item: dict[str, Any], index_tasks: dict[str, dict]) -> bool:
    for key in _ANCHOR_KEYS:
        if _has_anchor_value(item.get(key)):
            return True
    mapped = index_tasks.get(str(item.get("task_id") or ""))
    if isinstance(mapped, dict):
        for key in _ANCHOR_KEYS:
            if _has_anchor_value(mapped.get(key)):
                return True
    return False


def _index_tasks(source_index: dict[str, Any] | None) -> dict[str, dict]:
    if not isinstance(source_index, dict):
        return {}
    out: dict[str, dict] = {}
    for entry in source_index.get("tasks") or []:
        if isinstance(entry, dict) and entry.get("task_id"):
            out[str(entry["task_id"])] = dict(entry)
    for entry in source_index.get("task_sources") or []:
        if isinstance(entry, dict) and entry.get("task_id"):
            task_id = str(entry["task_id"])
            merged = out.setdefault(task_id, {"task_id": task_id})
            _merge_anchor_fields(merged, entry)
    for fact in source_index.get("source_facts") or []:
        if not isinstance(fact, dict):
            continue
        task_ids = _string_values(fact.get("task_ids"))
        if not task_ids:
            continue
        for task_id in task_ids:
            merged = out.setdefault(task_id, {"task_id": task_id})
            _merge_anchor_fields(merged, fact)
    return out


def _merge_anchor_fields(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ("source_key", "source_ref", "source_excerpt"):
        value = source.get(key)
        if _has_anchor_value(value) and not _has_anchor_value(target.get(key)):
            target[key] = value
    for key in ("source_keys", "source_refs"):
        values = [
            *(_string_values(target.get(key))),
            *(_string_values(source.get(key))),
        ]
        if values:
            target[key] = _dedupe(values)


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _legacy_single_task_sources_cover(
    *,
    task_items: list[dict[str, Any]],
    source_index: dict[str, Any] | None,
) -> bool:
    """Accept legacy ``sources[]`` only for a single-task task_map.

    Issue fanout historically emitted global source lists with no per-task
    mapping. That is enough provenance for one dispatchable task, but too weak
    for multi-task writer admission.
    """
    if len(task_items) != 1 or not isinstance(source_index, dict):
        return False
    sources = source_index.get("sources")
    if not isinstance(sources, list) or not sources:
        return False
    for source in sources:
        if not isinstance(source, dict):
            continue
        if _has_anchor_value(
            source.get("source_ref")
            or source.get("ref")
            or source.get("path")
            or source.get("facts")
        ):
            return True
    return False


def _claimed_refs(task_items: list[dict[str, Any]], index_tasks: dict[str, dict]) -> set[str]:
    claimed: set[str] = set()
    for item in task_items:
        sources: list[Any] = []
        for key in ("source_key", "source_keys", "source_ref", "source_refs"):
            sources.append(item.get(key))
        mapped = index_tasks.get(str(item.get("task_id") or ""))
        if isinstance(mapped, dict):
            for key in ("source_key", "source_keys", "source_ref", "source_refs"):
                sources.append(mapped.get(key))
        for src in sources:
            for text in _string_values(src):
                claimed.add(text)
    return claimed


def evaluate_source_index_gate(
    *,
    task_items: list[dict[str, Any]],
    source_index: dict[str, Any] | None,
    findings: list[dict[str, Any]] | None,
    harness_profile: str,
) -> SourceIndexGateResult:
    """正向锚 + 反向认领评估。``findings=None`` → 跳过反向审计。"""
    index_tasks = _index_tasks(source_index)
    missing = [
        str(item.get("task_id") or "")
        for item in task_items
        if not _task_has_anchor(item, index_tasks)
    ]
    if missing and _legacy_single_task_sources_cover(
        task_items=task_items,
        source_index=source_index,
    ):
        missing = []

    unclaimed: list[str] = []
    if findings:
        claimed = _claimed_refs(task_items, index_tasks)
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            fid = str(
                finding.get("id")
                or finding.get("finding_id")
                or finding.get("key")
                or ""
            ).strip()
            refs = {fid} | {
                str(finding.get(k) or "").strip()
                for k in ("path", "source_ref", "anchor")
            }
            refs.discard("")
            if refs and not (refs & claimed):
                unclaimed.append(fid or str(sorted(refs))[:60])

    if not missing:
        return SourceIndexGateResult(
            passed=True,
            mode="ok",
            unclaimed_finding_ids=unclaimed,
            note="" if not unclaimed else (
                f"{len(unclaimed)} finding(s) unclaimed by any task"
            ),
        )

    if str(harness_profile or "").strip() in _FAIL_CLOSED_PROFILES:
        return SourceIndexGateResult(
            passed=False,
            mode="fail_closed",
            missing_anchor_task_ids=missing,
            unclaimed_finding_ids=unclaimed,
            note=(
                f"{len(missing)}/{len(task_items)} task(s) carry no source "
                "anchor; strict/release admission is fail-closed (doc 91 P1)"
            ),
        )

    degraded = synthesize_degraded_source_index(
        task_items,
        reason="missing per-task source anchors at admission (baseline)",
    )
    return SourceIndexGateResult(
        passed=True,
        mode="degraded",
        missing_anchor_task_ids=missing,
        unclaimed_finding_ids=unclaimed,
        degraded_index=degraded,
        note=f"synthesized degraded source-index for {len(missing)} task(s)",
    )


def synthesize_degraded_source_index(
    task_items: list[dict[str, Any]],
    *,
    reason: str,
) -> dict[str, Any]:
    """baseline 兜底:从 task_map 自身合成显式 degraded 的 source-index。

    每项带 ``degraded: true`` 与 reason —— 不假装有锚,只保证下游消费者
    (capsule/Web 投影)拿到结构化的"缺源"事实而非空文件(doc 91 §3.1)。
    """
    return {
        "schema_version": DEGRADED_SCHEMA,
        "degraded": True,
        "degraded_reason": reason,
        "tasks": [
            {
                "task_id": str(item.get("task_id") or ""),
                "source_key": str(item.get("source_key") or ""),
                "source_ref": str(item.get("source_ref") or ""),
                "source_keys": _string_values(item.get("source_keys")),
                "source_refs": _string_values(item.get("source_refs")),
                "degraded": True,
                "degraded_reason": reason,
            }
            for item in task_items
        ],
    }
