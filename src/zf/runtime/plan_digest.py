"""B-93-03 (doc 93 §4): plan-digest 投影 —— task_map → 人读审核摘要。

纯函数(task_items in → markdown out),CLI/Web/Feishu 三方共用同一份
digest,避免各自重算/漂移。落盘与事件由调用方做。

digest = 任务表(task_id/wave/affinity/根owner/allowed_paths 摘要/
verification 一行式)+ 机械 checklist(R25 监工预检制度化:根 owner 唯一?
assembly 在位?verification 可执行形?scope 重叠?)。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from zf.runtime.task_map import normalize_verification_command


def _is_assembly(item: dict) -> bool:
    if str(item.get("root_owner_class") or "") == "assembly":
        return True
    return "ASSEMBLY" in str(item.get("task_id") or "").upper()


def _root_paths(item: dict) -> list[str]:
    """根级路径 = allowed_paths 里不含 '/' 的条目(scaffolding 标志)。"""
    out = []
    for path in item.get("allowed_paths", []) or []:
        p = str(path).strip()
        if p and "/" not in p.rstrip("/"):
            out.append(p)
    return out


def _is_simple_serial(
    items: list[dict],
    *,
    missing_verify: list[str],
    overlaps: list[str],
) -> bool:
    if len(items) != 1 or missing_verify or overlaps:
        return False
    item = items[0]
    paths = [str(path).strip() for path in item.get("allowed_paths", []) or []]
    return bool(paths) and not _is_assembly(item) and not _root_paths(item)


def plan_digest_checklist(
    task_items: list[dict],
    *,
    allowed_owner_roles: list[str] | None = None,
    require_unique_owner_roles: bool = False,
) -> list[dict[str, Any]]:
    """机械核结果(每项 {key,label,ok,detail})—— 红绿一眼可见。"""
    items = list(task_items or [])
    assemblies = [i for i in items if _is_assembly(i)]
    root_owners = [i for i in items if _root_paths(i)]
    missing_verify = [
        str(i.get("task_id") or "?")
        for i in items
        if not normalize_verification_command(i.get("verification"))
    ]
    # scope 重叠:任一 allowed_path 被两个 task 同时声明(粗判,精判在 W1 门)
    seen: dict[str, str] = {}
    overlaps: list[str] = []
    for i in items:
        tid = str(i.get("task_id") or "?")
        for path in i.get("allowed_paths", []) or []:
            p = str(path).strip()
            if p in seen and seen[p] != tid:
                overlaps.append(f"{p} ({seen[p]} ∩ {tid})")
            else:
                seen[p] = tid
    simple_serial = _is_simple_serial(
        items,
        missing_verify=missing_verify,
        overlaps=overlaps,
    )
    assembly_ok = len(assemblies) >= 1 or simple_serial
    if assemblies:
        assembly_detail = ", ".join(str(i.get("task_id")) for i in assemblies)
    elif simple_serial:
        assembly_detail = f"simple serial: {items[0].get('task_id')}"
    else:
        assembly_detail = "缺 assembly 任务"
    checks = [
        {"key": "assembly_present", "label": "assembly/根 owner 在位",
         "ok": assembly_ok,
         "detail": assembly_detail},
        {"key": "root_owner_unique", "label": "根文件唯一 owner",
         "ok": len(root_owners) <= 1,
         "detail": "唯一" if len(root_owners) <= 1
                   else "多任务持根路径: " + ", ".join(str(i.get("task_id")) for i in root_owners)},
        {"key": "verification_present", "label": "每任务有 verification",
         "ok": not missing_verify,
         "detail": "齐" if not missing_verify else "缺: " + ", ".join(missing_verify)},
        {"key": "scope_no_overlap", "label": "allowed_paths 无重叠",
         "ok": not overlaps,
         "detail": "无重叠" if not overlaps else "; ".join(overlaps)},
    ]
    if allowed_owner_roles is not None:
        allowed = {str(role).strip() for role in allowed_owner_roles if str(role).strip()}
        missing = []
        unconfigured = []
        owner_to_tasks_by_wave: dict[tuple[str, str], list[str]] = defaultdict(list)
        for i in items:
            tid = str(i.get("task_id") or "?")
            owner = str(i.get("owner_role") or "").strip()
            if not owner:
                missing.append(tid)
                continue
            wave = str(i.get("wave") or "?").strip() or "?"
            owner_to_tasks_by_wave[(owner, wave)].append(tid)
            if owner not in allowed:
                unconfigured.append(f"{tid}:{owner}")
        role_errors = []
        if missing:
            role_errors.append("缺 owner_role: " + ", ".join(missing))
        if unconfigured:
            role_errors.append("未配置 owner_role: " + ", ".join(unconfigured))
        checks.append({
            "key": "owner_role_configured",
            "label": "owner_role 属于 writer stage",
            "ok": not role_errors,
            "detail": "齐" if not role_errors else "; ".join(role_errors),
        })
        if require_unique_owner_roles:
            duplicates = [
                f"{owner}@wave {wave}: {', '.join(task_ids)}"
                for (owner, wave), task_ids in sorted(owner_to_tasks_by_wave.items())
                if owner in allowed and len(task_ids) > 1
            ]
            checks.append({
                "key": "owner_role_unique",
                "label": "owner_role 唯一占用并行 writer",
                "ok": not duplicates,
                "detail": "同 wave 唯一" if not duplicates else "; ".join(duplicates),
            })
    return checks


def render_plan_digest(
    task_items: list[dict],
    *,
    plan_id: str = "",
    task_map_ref: str = "",
    allowed_owner_roles: list[str] | None = None,
    require_unique_owner_roles: bool = False,
) -> str:
    """投影出人读 markdown digest(任务表 + checklist)。"""
    items = list(task_items or [])
    lines: list[str] = [f"# Plan Digest — {plan_id or '(plan)'}", ""]
    if task_map_ref:
        lines += [f"task_map_ref: `{task_map_ref}`", ""]
    lines += [
        "## 任务表",
        "",
        "| task_id | owner_role | wave | affinity | 根owner | allowed_paths | verification |",
        "|---|---|---|---|---|---|---|",
    ]
    for i in items:
        paths = ", ".join(str(p) for p in (i.get("allowed_paths") or [])[:4])
        if len(i.get("allowed_paths") or []) > 4:
            paths += " …"
        verify = normalize_verification_command(i.get("verification")).replace("\n", " ")[:60] or "—"
        lines.append(
            f"| {i.get('task_id','?')} | {i.get('owner_role') or '-'} | "
            f"{i.get('wave','-')} | "
            f"{i.get('affinity_tag') or i.get('affinity') or '-'} | "
            f"{'✓' if _is_assembly(i) or _root_paths(i) else '-'} | "
            f"{paths or '—'} | {verify} |"
        )
    lines += ["", "## Checklist（机械预检）", ""]
    for c in plan_digest_checklist(
        items,
        allowed_owner_roles=allowed_owner_roles,
        require_unique_owner_roles=require_unique_owner_roles,
    ):
        lines.append(f"- {'🟢' if c['ok'] else '🔴'} **{c['label']}** — {c['detail']}")
    lines.append("")
    return "\n".join(lines)
