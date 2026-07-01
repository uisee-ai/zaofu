"""Feishu target parsing and initialization helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from zf.core.task.kanban_projection import KANBAN_COLUMN_OPTIONS
from zf.core.state.atomic_io import atomic_write_text
from zf.integrations.feishu.renderers import (
    DEFAULT_AUTOMATION_FIELD_MAP,
    DEFAULT_KANBAN_FIELD_MAP,
)


TEXT_FIELD_TYPE = 1
SINGLE_SELECT_FIELD_TYPE = 3
KANBAN_VIEW_TYPE = "kanban"
GRID_VIEW_TYPE = "grid"

KANBAN_BOARD_COLUMNS = (
    *KANBAN_COLUMN_OPTIONS,
)

AUTOMATION_HIGHLIGHT_OPTIONS = (
    "P0 Action Required",
    "Decision Needed",
    "Blocked",
    "Runtime Alert",
    "Channel Attention",
    "Delivery Risk",
    "Watch",
    "Normal",
)


@dataclass(frozen=True)
class FeishuBitableRef:
    app_token: str
    table_id: str = ""
    view_id: str = ""


@dataclass(frozen=True)
class EnvUpdateResult:
    written: dict[str, str]
    skipped: dict[str, str]
    updated: dict[str, str]
    path: Path


def parse_feishu_document_id(value: str) -> str:
    target = value.strip()
    if not target:
        return ""
    if "://" not in target:
        return target

    parsed = urlparse(target)
    parts = [part for part in parsed.path.split("/") if part]
    for marker in ("docx", "doc"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                return parts[index + 1]
    return ""


def parse_feishu_bitable_ref(value: str) -> FeishuBitableRef:
    target = value.strip()
    if not target:
        return FeishuBitableRef(app_token="")
    if "://" not in target:
        return FeishuBitableRef(app_token=target)

    parsed = urlparse(target)
    parts = [part for part in parsed.path.split("/") if part]
    app_token = ""
    for marker in ("base", "bitable"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                app_token = parts[index + 1]
                break

    query = parse_qs(parsed.query)
    table_id = first_query_value(query, "table") or first_query_value(query, "table_id")
    view_id = first_query_value(query, "view") or first_query_value(query, "view_id")
    return FeishuBitableRef(app_token=app_token, table_id=table_id, view_id=view_id)


def first_query_value(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    if not values:
        return ""
    return values[0].strip()


def kanban_field_specs(field_map: dict[str, str] | None = None) -> list[dict[str, object]]:
    specs = _field_specs(DEFAULT_KANBAN_FIELD_MAP, field_map)
    fields = {**DEFAULT_KANBAN_FIELD_MAP, **(field_map or {})}
    select_fields = {
        fields["board_column"]: KANBAN_BOARD_COLUMNS,
    }
    for spec in specs:
        options = select_fields.get(str(spec.get("field_name") or ""))
        if options:
            spec["type"] = SINGLE_SELECT_FIELD_TYPE
            spec["property"] = {
                "options": [
                    {"name": name, "color": index}
                    for index, name in enumerate(options)
                ],
            }
    return specs


def automation_insight_field_specs(
    field_map: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    specs = _field_specs(DEFAULT_AUTOMATION_FIELD_MAP, field_map)
    fields = {**DEFAULT_AUTOMATION_FIELD_MAP, **(field_map or {})}
    highlight_field = fields["highlight"]
    for spec in specs:
        if str(spec.get("field_name") or "") != highlight_field:
            continue
        spec["type"] = SINGLE_SELECT_FIELD_TYPE
        spec["property"] = {
            "options": [
                {"name": name, "color": index}
                for index, name in enumerate(AUTOMATION_HIGHLIGHT_OPTIONS)
            ],
        }
    return specs


def kanban_view_specs() -> list[dict[str, str]]:
    return [
        {"view_name": "ZaoFu Grid", "view_type": GRID_VIEW_TYPE},
        {"view_name": "ZaoFu Kanban", "view_type": KANBAN_VIEW_TYPE},
    ]


def automation_insight_view_specs() -> list[dict[str, str]]:
    return [
        {"view_name": "ZaoFu Overview", "view_type": GRID_VIEW_TYPE},
        {"view_name": "ZaoFu Highlights", "view_type": GRID_VIEW_TYPE},
        {"view_name": "ZaoFu Action Required", "view_type": GRID_VIEW_TYPE},
        {"view_name": "ZaoFu Delivery Health", "view_type": GRID_VIEW_TYPE},
        {"view_name": "ZaoFu Runtime Health", "view_type": GRID_VIEW_TYPE},
        {"view_name": "ZaoFu History", "view_type": GRID_VIEW_TYPE},
    ]


def kanban_view_layout_specs(
    field_map: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    fields = {**DEFAULT_KANBAN_FIELD_MAP, **(field_map or {})}
    return [
        {
            "view_name": "ZaoFu Grid",
            "visible_fields": [
                fields["task_id"],
                fields["title"],
                fields["board_column"],
                fields["status"],
                fields["assigned_to"],
                fields["priority"],
                fields["blocked_reason"],
                fields["blocked_by"],
                fields["created_at"],
                fields["started_at"],
                fields["completed_at"],
                fields["project_name"],
                fields["synced_at"],
            ],
            "sort_config": [
                {"field": fields["priority"], "desc": False},
                {"field": fields["created_at"], "desc": True},
            ],
        },
        {
            "view_name": "ZaoFu Kanban",
            "visible_fields": [
                fields["title"],
                fields["assigned_to"],
                fields["priority"],
                fields["blocked_reason"],
                fields["started_at"],
                fields["created_at"],
                fields["completed_at"],
            ],
            "group_config": [
                {"field": fields["board_column"], "desc": False},
            ],
            "sort_config": [
                {"field": fields["priority"], "desc": False},
                {"field": fields["started_at"], "desc": True},
                {"field": fields["created_at"], "desc": True},
            ],
        },
    ]


def automation_insight_view_layout_specs(
    field_map: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    fields = {**DEFAULT_AUTOMATION_FIELD_MAP, **(field_map or {})}
    overview_fields = [
        fields["date"],
        fields["automation_id"],
        fields["highlight"],
        fields["severity"],
        fields["title"],
        fields["summary"],
        fields["suggested_action"],
        fields["metric"],
        fields["synced_at"],
    ]
    detail_fields = [
        fields["date"],
        fields["highlight"],
        fields["severity"],
        fields["category"],
        fields["title"],
        fields["summary"],
        fields["suggested_action"],
        fields["metric"],
        fields["automation_id"],
        fields["synced_at"],
    ]
    return [
        {
            "view_name": "ZaoFu Overview",
            "visible_fields": overview_fields,
            "filter_config": {
                "logic": "and",
                "conditions": [[fields["record_type"], "==", "summary"]],
            },
            "sort_config": [
                {"field": fields["date"], "desc": True},
                {"field": fields["automation_id"], "desc": False},
            ],
        },
        {
            "view_name": "ZaoFu Highlights",
            "visible_fields": [
                fields["date"],
                fields["highlight"],
                fields["title"],
                fields["summary"],
                fields["suggested_action"],
                fields["metric"],
                fields["automation_id"],
                fields["severity"],
                fields["category"],
                fields["task_refs"],
                fields["event_refs"],
                fields["synced_at"],
            ],
            "filter_config": {
                "logic": "or",
                "conditions": [
                    [fields["highlight"], "==", name]
                    for name in AUTOMATION_HIGHLIGHT_OPTIONS
                    if name != "Normal"
                ],
            },
            "group_config": [{"field": fields["highlight"], "desc": False}],
            "sort_config": [
                {"field": fields["highlight_rank"], "desc": False},
                {"field": fields["date"], "desc": True},
                {"field": fields["automation_id"], "desc": False},
            ],
        },
        {
            "view_name": "ZaoFu Action Required",
            "visible_fields": detail_fields,
            "filter_config": {
                "logic": "or",
                "conditions": [
                    [fields["severity"], "==", "critical"],
                    [fields["severity"], "==", "error"],
                    [fields["severity"], "==", "warn"],
                    [fields["severity"], "==", "warning"],
                ],
            },
            "sort_config": [
                {"field": fields["severity"], "desc": False},
                {"field": fields["date"], "desc": True},
            ],
        },
        {
            "view_name": "ZaoFu Delivery Health",
            "visible_fields": detail_fields,
            "filter_config": {
                "logic": "and",
                "conditions": [[fields["automation_id"], "==", "weekly-review"]],
            },
            "sort_config": [{"field": fields["date"], "desc": True}],
        },
        {
            "view_name": "ZaoFu Runtime Health",
            "visible_fields": detail_fields,
            "filter_config": {
                "logic": "and",
                "conditions": [[fields["automation_id"], "==", "project-monitor"]],
            },
            "sort_config": [
                {"field": fields["severity"], "desc": False},
                {"field": fields["date"], "desc": True},
            ],
        },
        {
            "view_name": "ZaoFu History",
            "visible_fields": [
                fields["date"],
                fields["record_type"],
                fields["automation_id"],
                fields["highlight"],
                fields["severity"],
                fields["category"],
                fields["title"],
                fields["summary"],
                fields["task_refs"],
                fields["event_refs"],
                fields["synced_at"],
            ],
            "group_config": [{"field": fields["date"], "desc": True}],
            "sort_config": [
                {"field": fields["date"], "desc": True},
                {"field": fields["automation_id"], "desc": False},
            ],
        },
    ]


def _field_specs(
    default_map: dict[str, str],
    field_map: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    merged = dict(default_map)
    if field_map:
        merged.update({
            key: value
            for key, value in field_map.items()
            if key in merged and value
        })
    seen: set[str] = set()
    specs: list[dict[str, object]] = []
    for field_name in merged.values():
        name = field_name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        specs.append({"field_name": name, "type": TEXT_FIELD_TYPE})
    return specs


def update_env_file(
    env_path: Path,
    values: dict[str, str],
    *,
    overwrite: bool = False,
) -> EnvUpdateResult:
    existing = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    key_lines: dict[str, int] = {}
    for index, line in enumerate(existing):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key and key not in key_lines:
            key_lines[key] = index

    lines = list(existing)
    written: dict[str, str] = {}
    updated: dict[str, str] = {}
    skipped: dict[str, str] = {}

    for key, value in values.items():
        normalized = value.strip()
        if not normalized:
            continue
        rendered = f"{key}={normalized}"
        if key in key_lines:
            if overwrite:
                lines[key_lines[key]] = rendered
                updated[key] = normalized
            else:
                skipped[key] = normalized
            continue
        lines.append(rendered)
        written[key] = normalized

    if written or updated:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(env_path, "\n".join(lines).rstrip() + "\n")

    return EnvUpdateResult(written=written, skipped=skipped, updated=updated, path=env_path)
