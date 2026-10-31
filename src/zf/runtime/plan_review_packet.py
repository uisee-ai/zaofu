"""Structured, read-only review packet for Plan approval."""

from __future__ import annotations

from collections import Counter
import json
import re
from pathlib import Path
from typing import Any

from zf.runtime.plan_preview_artifacts import read_preview_artifact
from zf.runtime.plan_review_decisions import decision_counts


def build_review_packet(
    state_dir: Path,
    *,
    project_root: Path | None,
    payload: dict[str, Any],
    artifacts: list[dict[str, str]],
    authority_refs: list[str],
    markdown: str,
    task_map_summary: dict[str, Any],
    decision_items: list[dict[str, Any]],
    preview_bundle: dict[str, Any],
    decision_register_available: bool = False,
) -> dict[str, Any]:
    """Build an explicit, read-only contract for human Plan approval.

    The packet is intentionally derived only from refs already bound to the
    approval event. A missing port or an unrepresented source identifier is a
    review finding, never an implicit PASS.
    """

    artifact_map = {str(item.get("name") or ""): item for item in artifacts}
    json_values: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    bodies: dict[str, str] = {}
    for name, item in artifact_map.items():
        try:
            body = read_preview_artifact(
                item["ref"], state_dir=state_dir, project_root=project_root,
                digest=item.get("sha256", ""),
            )
        except (OSError, ValueError, UnicodeError) as exc:
            diagnostics.append({
                "code": "review_artifact_unreadable",
                "severity": "error",
                "message": f"{name}: {exc}",
                "evidence": [item["ref"]],
            })
            continue
        bodies[name] = body
        if name.endswith((".json", "_matrix", "_ledger", "_spec", "_index")) or name in {
            "task_map", "source_index", "coverage_ledger", "acceptance_matrix",
            "test_matrix", "product_acceptance_spec", "real_e2e_matrix", "decision_register", "planning_result",
        }:
            try:
                value = json.loads(body)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                json_values[name] = value

    refs_by_name = {
        name: [str(item.get("ref") or "")]
        for name, item in artifact_map.items()
        if item.get("ref")
    }
    source_index = json_values.get("source_index")
    coverage_ledger = json_values.get("coverage_ledger")
    # Accept the explicit row representation, never infer the source universe
    # from coverage or turn task-level excerpts into requirement completeness.
    trace_rows = _review_rows(source_index, "traceability")
    coverage_rows = _review_rows(coverage_ledger, "entries")
    task_source_rows = _json_list(source_index, "tasks")
    # Identifiers are authored by the Planner and bound through existing ports.
    # Their spelling and the applicable requirement categories are project-owned.
    source_ids = {"requirements": _trace_ids(trace_rows)}
    plan_ids = {"requirements": _trace_ids(coverage_rows)}
    acceptance_rows = _json_list(json_values.get("acceptance_matrix"), "acceptance")
    test_rows = _json_list(json_values.get("test_matrix"), "commands")
    journeys = _json_list(json_values.get("product_acceptance_spec"), "user_journeys")

    declared_acceptance = _declared_acceptance_count(markdown)
    if declared_acceptance is not None and len(acceptance_rows) != declared_acceptance:
        diagnostics.append({
            "code": "acceptance_count_mismatch",
            "severity": "warning",
            "message": f"Plan 声明 {declared_acceptance} 个任务 AC，但 acceptance_matrix 实际绑定 {len(acceptance_rows)} 条。",
            "evidence": [refs_by_name.get("acceptance_matrix", [""])[0]],
        })

    # Run and PDD identities have different lifecycles and are not aliases.
    lineage_by_kind: dict[str, dict[str, str]] = {
        "workflow_run_id": {}, "pdd_id": {},
    }
    lineage_values: dict[str, list[str]] = {}
    for name, value in {
        "preview_event": payload, "preview_bundle": preview_bundle, **json_values,
    }.items():
        metadata = value.get("metadata")
        sources = [(name, value)]
        if isinstance(metadata, dict):
            sources.append((f"{name}.metadata", metadata))
        for source, body in sources:
            for kind, identities in lineage_by_kind.items():
                identity = str(body.get(kind) or "").strip()
                if identity:
                    identities[source] = identity
                    lineage_values.setdefault(name, []).append(f"{kind}={identity}")
    lineage_ids = sorted({item for values in lineage_values.values() for item in values if item})
    for kind, identities in lineage_by_kind.items():
        if len(set(identities.values())) > 1:
            diagnostics.append({
                "code": "artifact_lineage_mismatch",
                "severity": "error",
                "message": f"Plan Preview 绑定的事件、bundle 或 port 使用了不同 {kind} 标识。",
                "evidence": [f"{name}.{kind}={value}" for name, value in identities.items()],
            })

    def section(
        section_id: str,
        title: str,
        status: str,
        *,
        counts: dict[str, int] | None = None,
        names: list[str] | None = None,
        refs: list[str] | None = None,
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": section_id,
            "title": title,
            "status": status,
            "counts": counts or {},
            "artifact_names": names or [],
            "refs": [ref for ref in (refs or []) if ref],
            "notes": notes or [],
        }

    from zf.runtime.plan_decision_register import decision_readiness
    decisions_ready, pending = decision_readiness(bodies, payload, decision_items, markdown)
    if "decision_register" in artifact_map and "decision_register" not in bodies:
        decisions_ready = False
    blocking = sum(item.get("blocking") is True for item in pending)
    sections = [
        section(
            "prd", "PRD", "ready" if markdown and authority_refs else "partial",
            counts={"authority_refs": len(authority_refs), "plan_chars": len(markdown)},
            names=[name for name in artifact_map if name.startswith("requirement_")],
            refs=authority_refs + [refs_by_name.get("accepted_plan", [""])[0]],
            notes=["canonical Plan 与已绑定权威输入可供阅读。"] if markdown else ["canonical Plan 正文不可读。"],
        ),
        section(
            "sdd", "SDD", "ready" if task_map_summary.get("ok") and json_values.get("product_acceptance_spec") else "partial",
            counts={"tasks": int(task_map_summary.get("task_count") or 0), "journeys": len(journeys)},
            names=[name for name in ("task_map", "product_acceptance_spec") if name in artifact_map],
            refs=[refs_by_name.get(name, [""])[0] for name in ("task_map", "product_acceptance_spec")],
            notes=["任务边界、入口和用户旅程可复核。" if task_map_summary.get("ok") else "缺少可读 Task Map。"],
        ),
        section(
            "tdd", "TDD", "ready" if acceptance_rows and test_rows else "partial",
            counts={"acceptance": len(acceptance_rows), "commands": len(test_rows)},
            names=[name for name in ("acceptance_matrix", "test_matrix", "real_e2e_matrix") if name in artifact_map],
            refs=[refs_by_name.get(name, [""])[0] for name in ("acceptance_matrix", "test_matrix", "real_e2e_matrix")],
            notes=["验收、命令和证据状态以 NOT_RUN/BLOCKED 原样呈现。" if acceptance_rows and test_rows else "缺少 acceptance/test port。"],
        ),
        section(
            "traceability", "Traceability", "ready" if trace_rows and coverage_rows else "partial",
            counts={"source_rows": len(trace_rows), "coverage_rows": len(coverage_rows),
                    "task_source_rows": len(task_source_rows),
                    "uncovered": len(_json_list(coverage_ledger, "uncovered")),
                    "pending_decision": sum(row.get("disposition") == "pending_decision" for row in coverage_rows)},
            names=[name for name in ("source_index", "coverage_ledger", "source_inventory") if name in artifact_map],
            refs=[refs_by_name.get(name, [""])[0] for name in ("source_index", "coverage_ledger", "source_inventory")],
            notes=[
                "source -> capability -> task -> test -> evidence 链可逐项查看。"
                if trace_rows and coverage_rows else
                f"已绑定 {len(task_source_rows)} 个任务来源；缺少逐条来源索引或显式绑定的覆盖账本，不能据此声明完整覆盖。"
            ],
        ),
        section(
            "task_map", "Task Map", "ready" if task_map_summary.get("ok") else "missing",
            counts={"tasks": int(task_map_summary.get("task_count") or 0)},
            names=["task_map"] if "task_map" in artifact_map else [],
            refs=[refs_by_name.get("task_map", [""])[0]],
            notes=["包含 owner、wave、依赖、路径、验收和验证命令。" if task_map_summary.get("ok") else str(task_map_summary.get("reason") or "task_map 缺失。")],
        ),
        _coverage_section("source_coverage", "来源条款映射", source_ids["requirements"], plan_ids["requirements"], artifact_map, refs_by_name),
        section(
            "decisions", "Owner Decisions", "ready" if decisions_ready else "partial",
            counts=(decision_counts(decision_items) if decision_register_available
                    else {"pending": len(pending), "blocking": blocking}),
            names=[name for name in ("decision_register", "planning_result") if name in artifact_map],
            refs=[refs_by_name.get(name, [""])[0] for name in ("decision_register", "planning_result")],
            notes=[("决定登记与本次审批问题分别展示；按受影响阶段处理 blocking decision。" if pending else "已绑定有效决策登记；当前无待决项。")
                   if decisions_ready else "未绑定有效结构化 decision register，请核对 Plan。"],
        ),
        section(
            "provenance", "Provenance", "error" if any(item["code"] == "artifact_lineage_mismatch" for item in diagnostics) else "ready",
            counts={"lineage_ids": len(lineage_ids)},
            names=list(lineage_values),
            refs=[refs_by_name.get(name, [""])[0] for name in lineage_values],
            notes=["; ".join(lineage_ids) if lineage_ids else "未提供 workflow/pdd 血缘标识。"],
        ),
    ]
    missing = [item["id"] for item in sections if item["status"] in {"missing", "partial", "error"}]
    overall = "blocked" if any(item["severity"] == "error" for item in diagnostics) else ("incomplete" if missing else "ready")
    return {
        "schema_version": "plan-review-packet.v1",
        "status": overall,
        "sections": sections,
        "diagnostics": diagnostics,
        "source_ids": source_ids,
        "plan_ids": plan_ids,
        "owner_decision_items": pending,
        "missing_sections": missing,
        "policy": {"read_only": True, "missing_is_not_pass": True},
    }


def _coverage_section(
    section_id: str,
    title: str,
    source_ids: list[str],
    plan_ids: list[str],
    artifact_map: dict[str, dict[str, str]],
    refs_by_name: dict[str, list[str]],
) -> dict[str, Any]:
    covered = sorted(set(source_ids) & set(plan_ids))
    missing = sorted(set(source_ids) - set(plan_ids))
    port_names = [name for name in ("source_index", "coverage_ledger") if name in artifact_map]
    status = "ready" if source_ids and not missing else ("partial" if covered else "missing")
    notes = [f"来源条款 {len(source_ids)}，覆盖账本包含 {len(covered)}；映射存在不代表验收通过。"]
    if missing:
        notes.append("未覆盖: " + ", ".join(missing[:40]))
    return {
        "id": section_id,
        "title": title,
        "status": status,
        "counts": {"source": len(source_ids), "covered": len(covered), "missing": len(missing)},
        "artifact_names": port_names,
        "refs": [ref for name in port_names for ref in refs_by_name.get(name, []) if ref],
        "notes": notes,
    }


def _trace_ids(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({
        str(row.get("trace_id") or row.get("source_id") or row.get("id") or "").strip()
        for row in rows
    } - {""})


def _json_list(value: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    raw = value.get(key)
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _review_rows(value: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    """Support authored rows while preserving an explicit canonical field."""
    if isinstance(value, dict) and key not in value:
        return _json_list(value, "rows")
    return _json_list(value, key)


def _declared_acceptance_count(markdown: str) -> int | None:
    match = re.search(r"(\d+)\s*个任务\s*AC", markdown)
    return int(match.group(1)) if match else None




def render_human_review_dossier(
    state_dir: Path,
    *,
    project_root: Path | None,
    payload: dict[str, Any],
    artifacts: list[dict[str, str]],
    authority_refs: list[str],
    task_map_summary: dict[str, Any],
    decision_items: list[dict[str, Any]],
    status: str,
    review_packet: dict[str, Any],
) -> str:
    """Build the short SDD/TDD approval guide from bound plan ports.

    The canonical Plan remains unchanged. This dossier is a read-only review
    surface that gives an owner the decision context before the long trace and
    Task Map details.
    """

    def load_json(name: str) -> dict[str, Any]:
        item = next((item for item in artifacts if item.get("name") == name), None)
        if not item:
            return {}
        try:
            value = json.loads(read_preview_artifact(
                item["ref"], state_dir=state_dir, project_root=project_root,
                digest=item.get("sha256", ""),
            ))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    requirement = load_json("requirement_spec") or load_json("issue_spec")
    product = load_json("product_acceptance_spec")
    tests = load_json("test_matrix")
    acceptance = load_json("acceptance_matrix")
    tasks = task_map_summary.get("tasks", []) if task_map_summary.get("ok") else []
    journeys = product.get("user_journeys", [])
    assertions = [
        item for journey in journeys if isinstance(journey, dict)
        for item in journey.get("assertions", [])
        if isinstance(item, dict)
    ]
    commands = [item for item in tests.get("commands", []) if isinstance(item, dict)]
    trace_rows = acceptance.get("traceability", [])
    trace_status = Counter(
        str(item.get("status") or "UNKNOWN") for item in trace_rows
        if isinstance(item, dict)
    )
    prerequisites = [
        str(item) for item in product.get("external_prerequisite_ids", [])
        if str(item).strip()
    ]
    objective = str(requirement.get("objective") or "").strip()
    if not objective:
        objective = "绑定需求未提供结构化目标，请核对来源正文。"

    lines = [
        "# 人工审批阅读摘要",
        "",
        "> 这是 Plan Approval 前的阅读入口。它把 PRD、SDD、TDD 和风险决策放在一起；后面的 canonical Plan、逐项 trace 和 Task Map 是证据正文。审批不会把未运行证据变成 PASS，也不会在本次审批前启动 impl/verify。",
        "",
        "## 1. 本次要审批什么",
        "",
        f"- **计划状态**：`{status}`；Plan ID：`{payload.get('plan_id') or ''}`；阶段：`{payload.get('stage_id') or '未声明'}`。",
        f"- **目标**：{objective}",
        f"- **权威输入**：{', '.join(f'`{ref}`' for ref in authority_refs) or '`未绑定`'}。",
        f"- **决定登记**：`{len(decision_items)}` 项；待决定 `{decision_counts(decision_items)['pending']}`，"
        f"明确阻塞 `{decision_counts(decision_items)['blocking']}`，已授权待落实 `{decision_counts(decision_items)['follow_up']}`。"
        "未来决定仅在其受影响动作前关闭；Plan 批准不自动关闭决定。",
        "- **审批边界**：以当前审批请求为准；未决方案及尚未取得的验证结果仍需独立确认。",
        "",
        "## 1.1 审核包状态",
        "",
        f"- **总体状态**：`{review_packet.get('status') or 'unknown'}`；`missing_is_not_pass=true`。",
        "| 分区 | 状态 | 计数 | 需要人工确认 |",
        "|---|---|---|---|",
    ]
    for item in review_packet.get("sections", []):
        counts = ", ".join(f"{key}={value}" for key, value in (item.get("counts") or {}).items()) or "-"
        notes = "；".join(str(note) for note in item.get("notes", [])[:2]) or "-"
        lines.append(f"| {item.get('title') or item.get('id') or '-'} | `{item.get('status') or '-'}` | {counts} | {notes} |")
    if review_packet.get("diagnostics"):
        lines.extend(["", "**一致性诊断**", ""])
        for diagnostic in review_packet["diagnostics"]:
            lines.append(
                f"- `{diagnostic.get('severity') or 'warning'}` `{diagnostic.get('code') or '-'}`：{diagnostic.get('message') or '-'}"
            )
    lines.extend([
        "",
        "## 2. PRD：用户和产品结果",
        "",
    ])
    if assertions:
        lines.append("下面是用户旅程的可观察结果，按产品行为阅读，而不是按 worker 阅读：")
        lines.append("")
        for assertion in assertions:
            lines.append(
                f"- **{assertion.get('assertion_id') or 'ASSERT'}**（{assertion.get('producer') or '-'}，{assertion.get('tier') or '-'}）：{assertion.get('statement') or '-'}"
            )
    else:
        lines.append("- 当前 plan port 未提供结构化 user journey；请以 canonical Plan 的产品验收章节为准。")
    lines.extend(["", "## 3. SDD：系统边界和集成合同", ""])
    lines.append("各任务的写入边界和集成责任以下方绑定合同为准。")
    lines.append("")
    lines.append("| Slice | Owner | Depends on | Architecture responsibility |")
    lines.append("|---|---|---|---|")
    for task in tasks:
        lines.append(
            f"| {task.get('task_id') or '-'} {task.get('title') or '-'} | {task.get('owner_role') or '-'} | {task.get('blocked_by') or '-'} | {task.get('source_excerpt') or '-'} |"
        )
    lines.extend(["", "**SDD boundary evidence**", ""])
    for task in tasks:
        paths = ", ".join(f"`{path}`" for path in task.get("allowed_paths", [])) or "未声明"
        exclusive = ", ".join(f"`{path}`" for path in task.get("exclusive_files", [])) or "未声明"
        lines.append(
            f"- **{task.get('task_id') or '-'}**：allowed paths {paths}；exclusive files {exclusive}；source `{task.get('source_key') or task.get('source_ref') or '-'}`"
        )
    if product.get("entrypoints"):
        lines.extend(["", "**Entrypoints / integration contract**", ""])
        for entry in product["entrypoints"]:
            if isinstance(entry, dict):
                lines.append(
                    f"- `{entry.get('entrypoint_id') or '-'}`：start `{entry.get('start_command') or '-'}`；health `{entry.get('health_check') or '-'}`；owner `{entry.get('owner') or '-'}`"
                )
    lines.extend(["", "## 4. TDD：验证合同和当前证据状态", ""])
    lines.append(
        f"- 验收追踪：`{len(trace_rows)}` 条；状态统计：`NOT_RUN={trace_status.get('NOT_RUN', 0)}`、`BLOCKED={trace_status.get('BLOCKED', 0)}`、`PASS={trace_status.get('PASS', 0)}`。"
    )
    lines.append(f"- 验证命令：`{len(commands)}` 条；每条命令绑定 owner、tier 和 acceptance IDs。")
    lines.append("")
    assertion_by_command: dict[str, list[dict[str, Any]]] = {}
    for journey in journeys:
        if not isinstance(journey, dict):
            continue
        for assertion in journey.get("assertions", []):
            if not isinstance(assertion, dict):
                continue
            assertion_by_command.setdefault(str(assertion.get("command_id") or ""), []).append(assertion)
    lines.append("| Command | Tier | Owner | Acceptance | Expected result | Evidence |")
    lines.append("|---|---|---|---|---|---|")
    for command in commands:
        command_assertions = assertion_by_command.get(str(command.get("id") or ""), [])
        expected = "；".join(
            str(item.get("statement") or "")
            for item in command_assertions
            if item.get("statement")
        ) or "未声明；由测试断言定义"
        evidence = ", ".join(dict.fromkeys(
            str(ref)
            for item in command_assertions
            for ref in item.get("evidence_refs", [])
            if str(ref).strip()
        )) or "未声明"
        lines.append(
            f"| `{command.get('id') or '-'}` `{command.get('command') or '-'}` | {command.get('tier') or '-'} | {command.get('owner') or '-'} | {', '.join(str(item) for item in command.get('acceptance_ids', [])) or '-'} | {expected} | {evidence} |"
        )
    lines.extend(["", "## 5. 风险、前置条件和审批问题", ""])
    if prerequisites:
        lines.append("**外部前置条件**")
        lines.extend(f"- `{item}`：未关闭时只能输出 BLOCKED/UNKNOWN，不能宣称正式通过。" for item in prerequisites)
        lines.append("")
    if decision_items:
        lines.append("**Owner decisions**")
        for decision in decision_items:
            if isinstance(decision, dict):
                impact = decision.get("blocking_impact") or "影响后续实现/验证边界"
                lines.append(f"- **{decision.get('decision_id') or '-'}**：{decision.get('question') or '-'}（`PENDING_APPROVAL`；{impact}）")
    else:
        lines.append("- 未绑定结构化待决事项；请核对 canonical Plan，不代表所有问题均已关闭。")
    lines.extend([
        "",
        "## 6. Approve 前检查清单",
        "",
        "- [ ] 产品目标、用户旅程和非目标符合原生 Markdown。",
        "- [ ] SDD 的任务边界、依赖、唯一写入 owner 和集成责任可接受。",
        "- [ ] TDD 命令、验收 ID、证据路径和 NOT_RUN/BLOCKED 规则可执行。",
        "- [ ] 绑定的待决事项、外部依赖及其对实现和验收的影响已确认。",
        "- [ ] 通过 Chat 提出的修改已反映在新的 Plan candidate 后，再执行 approve。",
        "",
        "---",
        "",
        "以下继续显示 canonical Plan 正文、完整 requirement trace 和 Task Map 详情。",
        "",
    ])
    return "\n".join(lines)
