"""Helpers for Feishu Base view layout payloads."""

from __future__ import annotations

from typing import Any


def visible_fields_body(
    spec: dict[str, object],
    field_refs: dict[str, str],
) -> dict[str, Any]:
    values = spec.get("visible_fields")
    if not isinstance(values, list):
        return {}
    fields = [_resolve_field_ref(field_refs, value) for value in values]
    fields = [field for field in fields if field]
    return {"visible_fields": fields} if fields else {}


def field_config_body(
    spec: dict[str, object],
    field_refs: dict[str, str],
    key: str,
) -> dict[str, Any]:
    values = spec.get(key)
    if not isinstance(values, list):
        return {}
    items: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        field = _resolve_field_ref(field_refs, value.get("field"))
        if not field:
            continue
        item = {"field": field}
        if "desc" in value:
            item["desc"] = bool(value.get("desc"))
        items.append(item)
    return {key: items}


def timebar_body(
    spec: dict[str, object],
    field_refs: dict[str, str],
) -> dict[str, Any]:
    value = spec.get("timebar")
    if not isinstance(value, dict):
        return {}
    start = _resolve_field_ref(field_refs, value.get("start_time"))
    end = _resolve_field_ref(field_refs, value.get("end_time"))
    title = _resolve_field_ref(field_refs, value.get("title"))
    if not (start and end and title):
        return {}
    return {"start_time": start, "end_time": end, "title": title}


def filter_body(
    spec: dict[str, object],
    field_refs: dict[str, str],
) -> dict[str, Any]:
    value = spec.get("filter_config")
    if not isinstance(value, dict):
        return {}
    body = dict(value)
    conditions = value.get("conditions")
    if isinstance(conditions, list):
        body["conditions"] = [
            _resolve_filter_condition(field_refs, condition)
            for condition in conditions
        ]
    return body


def _resolve_filter_condition(
    field_refs: dict[str, str],
    condition: object,
) -> object:
    if isinstance(condition, list) and condition:
        out = list(condition)
        out[0] = _resolve_field_ref(field_refs, out[0])
        return out
    if isinstance(condition, dict):
        out = dict(condition)
        for key in ("field", "field_name"):
            if key in out:
                out[key] = _resolve_field_ref(field_refs, out.get(key))
        return out
    return condition


def _resolve_field_ref(field_refs: dict[str, str], value: object) -> str:
    field = str(value or "").strip()
    return field_refs.get(field, field)
