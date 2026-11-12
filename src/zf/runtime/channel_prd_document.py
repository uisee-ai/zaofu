"""Mechanical validation and rendering of agent-authored PRD evidence."""
from __future__ import annotations

import json
import re
from html import escape
from typing import Any


PRD_SPEC_FORMAT = "prd-spec.v1"

_PRD_SPEC_SECTIONS = {
    "overview": ("overview", "purpose why now", "产品概述"),
    "background": ("background and problem", "background problem", "背景与问题"),
    "goals": (
        "goals",
        "goals non goals and scope",
        "goals non goals scope",
        "goals and non goals",
        "目标与非目标",
    ),
    "users": ("user personas and prioritized scenarios", "user personas and scenarios", "user journeys", "用户与场景"),
    "stories": ("user stories and independent tests", "user stories", "用户故事"),
    "flow": ("user flow and interaction", "user flow", "用户流程与交互"),
    "requirements": ("functional requirements", "requirements", "功能需求"),
    "rules": (
        "business rules permissions and state",
        "business rules permissions state",
        "business rules and state",
        "业务规则与状态",
    ),
    "edges": ("edge cases and error scenarios", "edge cases", "error scenarios", "边界情况", "异常场景"),
    "entities": ("key entities and contracts", "key entities", "entities and contracts", "关键实体"),
    "nfr": ("non functional requirements", "non functional", "非功能需求"),
    "success": ("success metrics", "success criteria", "成功指标", "成功标准"),
    "acceptance": ("acceptance criteria", "acceptance", "验收标准", "验收"),
    "dependencies": (
        "dependencies assumptions risks and open questions",
        "dependencies assumptions risks open questions",
        "dependencies and risks",
        "依赖与风险",
    ),
}

_PRD_VIEW_SECTIONS = {
    "product": ("document control", "overview", "background", "goals", "user personas", "user journeys", "success", "acceptance", "dependencies"),
    "architecture": ("document control", "overview", "goals", "business rules", "entities", "non functional", "dependencies", "rollout", "appendices"),
    "developer": ("document control", "user flow", "functional requirements", "requirements", "business rules", "non functional", "acceptance", "dependencies", "appendices"),
}


def validate_prd_document(body: dict[str, Any]) -> str:
    # Existing persisted contracts remain readable. New provider prompts ask
    # for the full document; the presence of this field opts into its schema.
    if "prd_markdown" not in body:
        return ""
    if not isinstance(body["prd_markdown"], str) or not body["prd_markdown"].strip():
        return "prd_markdown must be a non-empty string"
    spec_format = body.get("spec_format")
    if spec_format is not None:
        if spec_format != PRD_SPEC_FORMAT:
            return f"unsupported spec_format: {spec_format}"
        spec_error = _validate_prd_spec_v1(body)
        if spec_error:
            return spec_error
    rows = body.get("requirement_coverage")
    if not isinstance(rows, list) or not rows:
        return "requirement_coverage must be a non-empty list"
    seen = set()
    acceptance_ids = {str(item.get("id") or "") for item in body.get("acceptance_criteria") or []
                      if isinstance(item, dict)}
    for row in rows:
        if not isinstance(row, dict):
            return "requirement_coverage entries must be objects"
        identity = row.get("id")
        if not isinstance(identity, str) or not identity.strip() or identity in seen:
            return "requirement_coverage id must be non-empty and unique"
        seen.add(identity)
        if row.get("status") not in {"covered", "deferred", "gap"}:
            return "requirement_coverage status must be covered, deferred, or gap"
        for field in ("source_refs", "acceptance_ids"):
            values = row.get(field)
            if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                return f"requirement_coverage {field} must contain string references"
        if not row["source_refs"]:
            return "requirement_coverage requires source_refs"
        if row["status"] == "covered":
            if not isinstance(row.get("section"), str) or not row["section"].strip():
                return "covered requirement requires a PRD section"
            if spec_format == PRD_SPEC_FORMAT:
                headings = {_normalize_heading(title) for _, title in _markdown_headings(body["prd_markdown"])}
                if not _section_matches(headings, row["section"]):
                    return f"covered requirement section not found in prd_markdown: {row['section']}"
            if not row["acceptance_ids"] or not set(row["acceptance_ids"]) <= acceptance_ids:
                return "covered requirement must reference declared acceptance ids"
        elif not isinstance(row.get("reason"), str) or not row["reason"].strip():
            return "deferred or gap requirement requires a reason"
    if not isinstance(body.get("decision_log"), list):
        return "decision_log must be a list"
    return ""


def _validate_prd_spec_v1(body: dict[str, Any]) -> str:
    control = body.get("document_control")
    if not isinstance(control, dict):
        return "prd-spec.v1 requires document_control"
    for field in ("revision", "status", "owner"):
        if not isinstance(control.get(field), str) or not control[field].strip():
            return f"document_control.{field} must be a non-empty string"
    if control.get("profile") not in {"lite", "full"}:
        return "document_control.profile must be lite or full"

    headings = _markdown_headings(body["prd_markdown"])
    normalized_headings = {_normalize_heading(title) for _, title in headings}
    for section, aliases in _PRD_SPEC_SECTIONS.items():
        if not any(_section_matches(normalized_headings, alias) for alias in aliases):
            return f"prd-spec.v1 requires section: {section}"

    requirement_headings = [
        title for level, title in headings
        if level >= 3 and _is_requirement_heading(title)
    ]
    if not requirement_headings:
        return "prd-spec.v1 requires at least one functional requirement heading"
    if not any(_is_scenario_heading(title) for _, title in headings):
        return "prd-spec.v1 requires at least one Scenario heading"
    if not all(re.search(rf"\b{word}\b", body["prd_markdown"], re.IGNORECASE)
               for word in ("GIVEN", "WHEN", "THEN")):
        return "prd-spec.v1 scenarios must include GIVEN, WHEN, and THEN"

    error = validate_prd_measurables(body)
    if error:
        return error
    if control["profile"] == "full":
        error = _validate_full_profile(body, headings, requirement_headings)
        if error:
            return error
    acceptance = body.get("acceptance_criteria")
    if not isinstance(acceptance, list) or not acceptance:
        return "prd-spec.v1 requires acceptance_criteria"
    error = _validate_id_items("acceptance_criteria", acceptance)
    if error:
        return error
    if not isinstance(body.get("open_questions"), list):
        return "prd-spec.v1 open_questions must be a list"
    return ""


def validate_prd_measurables(body: dict[str, Any]) -> str:
    """Shared mechanical schema gate, also used before saving a document part."""
    control = body.get("document_control") or {}
    for field in ("non_functional_requirements", "success_metrics"):
        values = body.get(field)
        if not isinstance(values, list):
            return f"prd-spec.v1 {field} must be a list"
        if control.get("profile") == "full" and field == "success_metrics" and not values:
            return "prd-spec.v1 full profile requires success_metrics"
        error = _validate_id_items(field, values)
        if error:
            return error
    if control.get("profile") == "full":
        for item in body.get("success_metrics") or []:
            for key in ("baseline", "target", "measurement_method", "owner", "measurement_timing", "status"):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    return f"prd-spec.v1 success_metrics {item.get('id') or '<unknown>'} requires {key}"
    return ""


def _validate_full_profile(
    body: dict[str, Any],
    headings: list[tuple[int, str]],
    requirement_headings: list[str],
) -> str:
    """Enforce the audience and independent-test contract of a full PRD."""
    declared_acceptance_ids = {
        str(item.get("id") or "")
        for item in body.get("acceptance_criteria") or []
        if isinstance(item, dict)
    }
    for field, required in (
        ("user_stories", ("id", "title", "priority", "persona", "value", "independent_test", "acceptance_ids")),
        ("edge_cases", ("id", "trigger", "expected_behavior")),
        ("key_entities", ("id", "name", "purpose", "lifecycle", "constraints")),
    ):
        values = body.get(field)
        if not isinstance(values, list) or not values:
            return f"prd-spec.v1 full profile requires non-empty {field}"
        seen: set[str] = set()
        for item in values:
            if not isinstance(item, dict):
                return f"prd-spec.v1 {field} entries must be objects"
            identity = item.get("id")
            if not isinstance(identity, str) or not identity.strip() or identity in seen:
                return f"prd-spec.v1 {field} id must be non-empty and unique"
            seen.add(identity)
            for key in required:
                value = item.get(key)
                if key in {"constraints", "acceptance_ids"}:
                    if (
                        not isinstance(value, list)
                        or not value
                        or any(not isinstance(entry, str) or not entry.strip() for entry in value)
                    ):
                        return f"prd-spec.v1 {field} {identity} requires non-empty {key}"
                    if key == "acceptance_ids" and not set(value) <= declared_acceptance_ids:
                        return f"prd-spec.v1 {field} {identity} references undeclared acceptance_ids"
                elif not isinstance(value, str) or not value.strip():
                    return f"prd-spec.v1 {field} {identity} requires {key}"
            if field == "edge_cases":
                acceptance_ids = item.get("acceptance_ids") or item.get("affected_acceptance_ids")
                if (
                    not isinstance(acceptance_ids, list)
                    or not acceptance_ids
                    or any(not isinstance(entry, str) or not entry.strip() for entry in acceptance_ids)
                ):
                    return f"prd-spec.v1 edge_cases {identity} requires acceptance_ids or affected_acceptance_ids"
                if not set(acceptance_ids) <= declared_acceptance_ids:
                    return f"prd-spec.v1 edge_cases {identity} references undeclared acceptance_ids"

    markdown = body["prd_markdown"]
    requirement_count = len(requirement_headings)
    scenario_count = sum(1 for _, title in headings if _is_scenario_heading(title))
    normative_count = len(re.findall(r"\b(?:MUST|SHALL)\b", markdown, re.IGNORECASE))
    if scenario_count < requirement_count:
        return "prd-spec.v1 full profile requires a named Scenario for every requirement"
    if normative_count < requirement_count:
        return "prd-spec.v1 full profile requires MUST or SHALL for every requirement"
    return ""


def _validate_id_items(field: str, values: list[Any]) -> str:
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, dict):
            return f"prd-spec.v1 {field} entries must be objects"
        identity = item.get("id")
        if not isinstance(identity, str) or not identity.strip() or identity in seen:
            return f"prd-spec.v1 {field} id must be non-empty and unique"
        seen.add(identity)
        statement = next(
            (item.get(key) for key in (
                "statement", "criterion", "description", "metric", "outcome", "requirement"
            ) if item.get(key)),
            None,
        )
        if not isinstance(statement, str) or not statement.strip():
            return f"prd-spec.v1 {field} {identity} requires a statement"
    return ""


def _markdown_headings(markdown: str) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    fence: str | None = None
    for line in markdown.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence) and not marker.group(2).strip():
                fence = None
            continue
        if marker:
            fence = marker.group(1)
            continue
        match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.append((len(match.group(1)), match.group(2).strip()))
    return headings


def _normalize_heading(value: object) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^\d{1,2}\s*[.)-]\s*", "", text)
    text = text.replace("&", " and ")
    return re.sub(r"[^\w\u4e00-\u9fff]+", " ", text, flags=re.UNICODE).strip()


def _section_matches(headings: set[str], alias: str) -> bool:
    normalized = _normalize_heading(alias)
    return any(
        normalized == heading or normalized in heading
        for heading in headings
    )


def _is_requirement_heading(title: str) -> bool:
    normalized = _normalize_heading(title)
    return normalized.startswith("requirement ") or normalized.startswith("fr ") or normalized in {"requirement", "功能需求"}


def _is_scenario_heading(title: str) -> bool:
    normalized = _normalize_heading(title)
    return normalized.startswith("scenario ") or normalized in {"scenario", "场景"}


def prd_view_index(markdown: str) -> dict[str, dict[str, Any]]:
    """Return stable, read-only section indexes for audience-specific views."""
    headings = [title for level, title in _markdown_headings(markdown) if level <= 2]
    normalized = [(title, _normalize_heading(title)) for title in headings]
    views: dict[str, dict[str, Any]] = {}
    labels = {"product": "产品经理", "architecture": "架构师", "developer": "资深开发"}
    for role, aliases in _PRD_VIEW_SECTIONS.items():
        selected = [
            title for title, normalized_title in normalized
            if any(_normalize_heading(alias) in normalized_title for alias in aliases)
        ]
        views[role] = {
            "label": labels[role],
            "sections": selected or headings,
        }
    return views


def render_prd_document(synthesis: dict[str, Any], *, channel_id: str, thread_id: str, source_refs: list[str]) -> str:
    """Preserve the full author body and carry review evidence in the export."""
    review_catalogs = []
    for title, field in (("假设", "assumptions"), ("风险", "risks"), ("未消除的分歧", "dissent")):
        if synthesis.get(field):
            review_catalogs.extend(_catalog_table(title, synthesis[field]))
    return synthesis["prd_markdown"].rstrip() + "\n\n" + "\n".join([
        "## 需求追溯与决策记录",
        "以下为作者声明的映射，需审阅者对照原文核实；不代表自动质量通过。",
        *_catalog_table("来源覆盖", synthesis.get("requirement_coverage", [])),
        *_catalog_table("新增决定", synthesis.get("decision_log", [])),
        *(["## 假设、风险与分歧", *review_catalogs] if review_catalogs else []),
        "## 实施交接合同",
        "以下为同版 PRD 的完整交付合同。channel_prd_scope=full_product 时据此编译 Task；"
        "channel_prd_scope=approved_task 时使用另行批准的局部目标、验收、验证命令与范围，"
        "仍须绑定并校验本版 PRD。局部 Task 不自动继承下列完整交付义务；测试命令中的文件可由后续实施创建。",
        *_catalog_table("验收目录", synthesis.get("acceptance_criteria", [])),
        *_catalog_table("成功指标", synthesis.get("success_metrics", [])),
        *_catalog_table("非功能需求", synthesis.get("non_functional_requirements", [])),
        *_catalog_table("验证命令", synthesis.get("verification_commands", [])),
        *_catalog_table("非目标", synthesis.get("out_of_scope", [])),
        *(["## 质量与阶段交接清单",
            "以下清单是同版 PRD 的评审与阶段交接记录；状态由评审者依据证据填写，"
            "不等于产品实现已通过。",
            *_catalog_table("质量清单", synthesis.get("prd_quality_checklist", [])),
            *_companion_catalog(synthesis.get("implementation_companion"))]
           if synthesis.get("prd_quality_checklist") or synthesis.get("implementation_companion") else []),
        "## Provenance",
        f"- Channel: `{channel_id}`", f"- Thread: `{thread_id}`",
        *[f"- Source: `{ref}`" for ref in source_refs], "",
    ])


def _companion_catalog(companion: Any) -> list[str]:
    if not isinstance(companion, dict):
        return _catalog_table("实现伴随与交接", companion)
    titles = {"source_baseline": "来源与兼容基线", "review_coverage": "独立评审覆盖",
              "acceptance_data_plan": "验收数据与证据计划", "handoff": "阶段交接索引"}
    return [line for key, value in companion.items()
            for line in _catalog_table(titles.get(key, key), value)]


def _catalog_table(title: str, values: Any) -> list[str]:
    """Render every declared field without copying a second JSON envelope."""
    lines = ["", f"### {title}", ""]
    if not values:
        return [*lines, "无。", ""]
    if not isinstance(values, list):
        values = [values]
    rows = [item if isinstance(item, dict) else {"value": item} for item in values]
    fields = list(dict.fromkeys(key for row in rows for key in row))
    if not fields:
        return [*lines, "```json", json.dumps(values, ensure_ascii=False), "```", ""]

    def cell(value: Any) -> str:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return escape(text, quote=False).replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`").replace("\r\n", "\n").replace("\n", "<br>")

    lines.extend([
        "| " + " | ".join(cell(field) for field in fields) + " |",
        "| " + " | ".join("---" for _ in fields) + " |",
    ])
    lines.extend("| " + " | ".join(cell(row[field]) if field in row else "" for field in fields) + " |" for row in rows)
    return [*lines, ""]
