"""task-context-manifest.v1 — 动作分面的 agent reading surface(X15)。

implement/check 阅读清单形:把"agent 该读什么"
从 prompt 拿出来,变成 Task Capsule/contract 的 **projection**(不是
第二 task schema)。kernel/物化期写,worker 只读;missing required ref
按 harness_profile 分级(baseline WARN / strict STOP),但 dispatch 物化路径
保持 observe-first:它发事件给 Supervisor/gate 接管,不在 best-effort 投影函数
里伪装成同步阻塞。

四面:implement(改代码前读)/ check(验收谓词与 spec)/
research(task-scoped 证据)/ closeout(沉淀入口)。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "task-context-manifest.v1"


def _entry(kind: str, path: str, *, required: bool, reason: str) -> dict:
    return {"kind": kind, "path": path, "required": required, "reason": reason}


def build_task_context_manifest(
    *,
    task: Any,
    dispatch_id: str,
    state_dir: Path,
    payload: dict | None = None,
) -> dict[str, Any]:
    """从 task contract + capsule 布局 + dispatch payload 派生四面清单。

    纯函数:只读输入,产 dict。required 项缺失的判定留给
    ``missing_required_refs``(分级在调用方)。
    """
    payload = payload or {}
    task_id = str(getattr(task, "id", "") or "")
    contract = getattr(task, "contract", None)
    docs_dir = state_dir / "task_docs" / task_id

    implement: list[dict] = []
    source_md = docs_dir / "source.md"
    implement.append(_entry(
        "source_doc", str(source_md), required=source_md.exists(),
        reason="task 语义源头(Task Capsule)",
    ))
    for ref_key in ("instruction_ref", "criteria_ref"):
        ref = str(payload.get(ref_key) or "").strip()
        if ref:
            implement.append(_entry(
                "payload_ref", ref, required=True,
                reason=f"dispatch payload 显式 {ref_key}",
            ))
    for scope_path in list(getattr(contract, "scope", []) or [])[:8]:
        implement.append(_entry(
            "scope", str(scope_path), required=False,
            reason="contract.scope 声明的工作区",
        ))

    check: list[dict] = []
    task_md = docs_dir / "task.md"
    check.append(_entry(
        "task_doc", str(task_md), required=task_md.exists(),
        reason="验收谓词与完成边界(Task Capsule)",
    ))
    behavior = str(getattr(contract, "behavior", "") or "")
    if behavior:
        check.append(_entry(
            "inline", f"contract.behavior: {behavior[:120]}",
            required=False, reason="行为级验收(内联,无文件)",
        ))

    research_dir = docs_dir / "research"
    research = [
        _entry("research", str(p), required=False,
               reason="task-scoped research artifact")
        for p in sorted(research_dir.glob("*")) if research_dir.exists()
    ][:8]

    closeout = [
        _entry("evidence", str(docs_dir / "evidence.md"),
               required=False,
               reason="closeout 沉淀入口(decision evidence 写此/X16)"),
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "dispatch_id": dispatch_id,
        "contexts": {
            "implement": implement,
            "check": check,
            "research": research,
            "closeout": closeout,
        },
    }


def missing_required_refs(manifest: dict[str, Any]) -> list[str]:
    """required 且 path 形(非 inline)且文件不存在的项。"""
    out: list[str] = []
    for facet, entries in (manifest.get("contexts") or {}).items():
        for entry in entries or []:
            if not entry.get("required"):
                continue
            path = str(entry.get("path") or "")
            if entry.get("kind") == "inline" or not path:
                continue
            if not Path(path).exists():
                out.append(f"{facet}:{path}")
    return sorted(out)


def read_receipt_gaps(
    manifest: dict[str, Any],
    payload: Any,
) -> list[str]:
    """B19(读取回执闭环): required 项中无回执的 ``facet:path``。

    完成 payload 可带 ``read_receipts``: ``[{"path":..,"digest":..}, ...]``
    或 ``[str, ...]``。无该字段 → 全部 required 计 gap(没回执=没证据;
    与 missing_required_refs 同样豁免 inline 类)。
    """
    receipts: set[str] = set()
    if isinstance(payload, dict):
        for entry in payload.get("read_receipts") or []:
            if isinstance(entry, dict):
                path = str(entry.get("path") or "").strip()
            else:
                path = str(entry or "").strip()
            if path:
                receipts.add(path)
    out: list[str] = []
    for facet, entries in (manifest.get("contexts") or {}).items():
        for entry in entries or []:
            if not entry.get("required"):
                continue
            path = str(entry.get("path") or "")
            if entry.get("kind") == "inline" or not path:
                continue
            if path not in receipts:
                out.append(f"{facet}:{path}")
    return sorted(out)


def write_task_context_manifest(
    manifest: dict[str, Any], *, briefing_dir: Path,
) -> Path:
    briefing_dir.mkdir(parents=True, exist_ok=True)
    path = briefing_dir / "task_context_manifest.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def read_task_context_manifest(briefing_dir: Path) -> dict[str, Any] | None:
    path = briefing_dir / "task_context_manifest.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
