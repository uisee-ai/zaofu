"""Structured, read-only review packet for Plan approval."""

from __future__ import annotations

from collections import Counter
import json
import re
from pathlib import Path
from typing import Any

from zf.runtime.plan_preview_artifacts import read_preview_artifact


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
) -> dict[str, Any]:
    """Build an explicit, read-only contract for human Plan approval.

    The packet is intentionally derived only from refs already bound to the
    approval event. A missing port or an unrepresented source identifier is a
    review finding, never an implicit PASS.
    """

    artifact_map = {str(item.get("name") or ""): item for item in artifacts}
    json_values: dict[str, dict[str, Any]] = {}
    text_values: dict[str, str] = {}
    for name, item in artifact_map.items():
        try:
            body = read_preview_artifact(
                item["ref"], state_dir=state_dir, project_root=project_root,
                digest=item.get("sha256", ""),
            )
        except (OSError, ValueError, UnicodeError):
            continue
        text_values[name] = body
        if name.endswith((".json", "_matrix", "_ledger", "_spec", "_index")) or name in {
            "task_map", "source_index", "coverage_ledger", "acceptance_matrix",
            "test_matrix", "product_acceptance_spec", "real_e2e_matrix",
        }:
            try:
                value = json.loads(body)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                json_values[name] = value

    requirement_text = "\n".join(
        body for name, body in text_values.items()
        if name.startswith("requirement_")
    )
    id_patterns = {
        "requirements": r"\b(?:R\d{3}|T14-\d{3}|AT\d{2}|T12-\d{2})\b",
        "metrics": r"\bM14-\d{2}\b",
        "ui_states": r"\bU14-\d{2}\b",
        "configs": r"\b(?:import|clean|align|scene|sample|annotate|train|sim)_config\.ya?ml\b",
    }
    source_ids = {
        key: sorted(set(re.findall(pattern, requirement_text, flags=re.IGNORECASE)))
        for key, pattern in id_patterns.items()
    }
    plan_ids = {
        key: sorted(set(re.findall(pattern, markdown, flags=re.IGNORECASE)))
        for key, pattern in id_patterns.items()
    }
    refs_by_name = {
        name: [str(item.get("ref") or "")]
        for name, item in artifact_map.items()
        if item.get("ref")
    }
    trace_rows = _json_list(json_values.get("source_index"), "traceability")
    coverage_rows = _json_list(json_values.get("coverage_ledger"), "entries")
    acceptance_rows = _json_list(json_values.get("acceptance_matrix"), "acceptance")
    test_rows = _json_list(json_values.get("test_matrix"), "commands")
    journeys = _json_list(json_values.get("product_acceptance_spec"), "user_journeys")

    diagnostics: list[dict[str, Any]] = []
    declared_acceptance = _declared_acceptance_count(markdown)
    if declared_acceptance is not None and len(acceptance_rows) != declared_acceptance:
        diagnostics.append({
            "code": "acceptance_count_mismatch",
            "severity": "warning",
            "message": f"Plan 声明 {declared_acceptance} 个任务 AC，但 acceptance_matrix 实际绑定 {len(acceptance_rows)} 条。",
            "evidence": [refs_by_name.get("acceptance_matrix", [""])[0]],
        })

    lineage_values: dict[str, list[str]] = {}
    preview_workflow = str(payload.get("workflow_run_id") or "").strip()
    if preview_workflow:
        lineage_values["preview_event"] = [preview_workflow]
    bundle_workflow = str(preview_bundle.get("workflow_run_id") or "").strip()
    if bundle_workflow:
        lineage_values["preview_bundle"] = [bundle_workflow]
    for name, value in json_values.items():
        metadata = value.get("metadata")
        if isinstance(metadata, dict) and metadata.get("workflow_run_id"):
            lineage_values[name] = [str(metadata["workflow_run_id"])]
        if name == "task_map" and value.get("pdd_id"):
            lineage_values[name] = [str(value["pdd_id"])]
    lineage_ids = sorted({item for values in lineage_values.values() for item in values if item})
    if len(lineage_ids) > 1:
        diagnostics.append({
            "code": "artifact_lineage_mismatch",
            "severity": "error",
            "message": "Plan Preview 绑定的事件、bundle 或 port 使用了不同 workflow/pdd 标识。",
            "evidence": [f"{name}={value}" for name, values in lineage_values.items() for value in values],
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
            counts={"source_rows": len(trace_rows), "coverage_rows": len(coverage_rows), "uncovered": len(_json_list(json_values.get("coverage_ledger"), "uncovered"))},
            names=[name for name in ("source_index", "coverage_ledger", "source_inventory") if name in artifact_map],
            refs=[refs_by_name.get(name, [""])[0] for name in ("source_index", "coverage_ledger", "source_inventory")],
            notes=["source -> capability -> task -> test -> evidence 链可逐项查看。" if trace_rows and coverage_rows else "缺少 source index 或 coverage ledger。"],
        ),
        section(
            "task_map", "Task Map", "ready" if task_map_summary.get("ok") else "missing",
            counts={"tasks": int(task_map_summary.get("task_count") or 0)},
            names=["task_map"] if "task_map" in artifact_map else [],
            refs=[refs_by_name.get("task_map", [""])[0]],
            notes=["包含 owner、wave、依赖、路径、验收和验证命令。" if task_map_summary.get("ok") else str(task_map_summary.get("reason") or "task_map 缺失。")],
        ),
        _coverage_section("metrics", "指标 M14", source_ids["metrics"], plan_ids["metrics"], artifact_map, refs_by_name),
        _coverage_section("ui_states", "UI 状态 U14", source_ids["ui_states"], plan_ids["ui_states"], artifact_map, refs_by_name),
        _coverage_section("config_contracts", "配置与合同", source_ids["configs"], plan_ids["configs"], artifact_map, refs_by_name),
        section(
            "decisions", "Owner Decisions", "ready" if decision_items else "partial",
            counts={"pending": len(decision_items)},
            names=[name for name in ("decision_register", "planning_result") if name in artifact_map],
            refs=[refs_by_name.get(name, [""])[0] for name in ("decision_register", "planning_result")],
            notes=["审批前必须逐项处理 blocking decision。" if decision_items else "未绑定结构化 decision register，请核对 Plan。"],
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
    port_names = [name for name in artifact_map if any(token in name.lower() for token in (section_id, "metric", "ui", "config", "contract"))]
    status = "ready" if source_ids and not missing else ("partial" if covered else "missing")
    notes = [f"source {len(source_ids)}，Plan 显式覆盖 {len(covered)}。"]
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


def _json_list(value: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    raw = value.get(key)
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


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
        objective = "基于两份原生 v1.4 权威 Markdown 形成可人工审批的 PRD、架构边界、测试合同和任务拆分。"

    lines = [
        "# 人工审批阅读摘要",
        "",
        "> 这是 Plan Approval 前的阅读入口。它把 PRD、SDD、TDD 和风险决策放在一起；后面的 canonical Plan、逐项 trace 和 Task Map 是证据正文。审批不会把未运行证据变成 PASS，也不会在本次审批前启动 impl/verify。",
        "",
        "## 1. 本次要审批什么",
        "",
        f"- **计划状态**：`{status}`；Plan ID：`{payload.get('plan_id') or ''}`；阶段：`{payload.get('stage_id') or 'prd-lanes-impl'}`。",
        f"- **目标**：{objective}",
        f"- **权威输入**：{', '.join(f'`{ref}`' for ref in authority_refs) or '`未绑定`'}。",
        f"- **待决事项**：`{len(decision_items)}` 项；任何 blocking decision 未关闭前不得启动 impl/verify。",
        "- **审批边界**：只批准后续实现/验证计划及其责任边界；不批准未决 CR/Q14 方案，不批准真实媒体、模型、仿真或 provider 结果。",
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
    lines.append("每个任务拥有自己的写入边界；T10 只负责总装配和真实入口，不替代上游任务实现。")
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
        lines.append("- 当前没有额外 owner decision；请仍检查 canonical Plan 中的 CR/Q14 阻断项。")
    lines.extend([
        "",
        "## 6. Approve 前检查清单",
        "",
        "- [ ] 产品目标、用户旅程和非目标符合原生 Markdown。",
        "- [ ] SDD 的任务边界、依赖、唯一写入 owner 和 T10 集成责任可接受。",
        "- [ ] TDD 命令、验收 ID、证据路径和 NOT_RUN/BLOCKED 规则可执行。",
        "- [ ] CR/Q14、真实数据、模型、仿真、通知和外部 runner 的未决状态已知晓。",
        "- [ ] 通过 Chat 提出的修改已反映在新的 Plan candidate 后，再执行 approve。",
        "",
        "---",
        "",
        "以下继续显示 canonical Plan 正文、完整 requirement trace 和 Task Map 详情。",
        "",
    ])
    return "\n".join(lines)
