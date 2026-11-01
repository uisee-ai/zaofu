"""Readiness of a bound decision register, separate from unresolved decisions."""
from __future__ import annotations

import json
import re
from typing import Any


def decision_register_rows(body: str) -> list[dict[str, Any]] | None:
    try:
        value = json.loads(body)
    except ValueError:
        value = None
    if isinstance(value, dict):
        rows = value.get("decisions", value.get("owner_decision_items"))
        if isinstance(rows, list) and all(
            isinstance(row, dict) and str(row.get("decision_id") or row.get("id") or "").strip()
            for row in rows
        ):
            return rows
        return None
    # Markdown is supported only when a named register contains a valid table;
    # prose saying "none" is not evidence of a structured empty register.
    match = re.search(r"(?im)^#+ .*?(?:decision register|owner 决策项).*\n", body)
    if match is None:
        return None
    section = re.split(r"(?m)^#+ ", body[match.end():], maxsplit=1)[0]
    lines = [line.strip() for line in section.splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return None
    headers = [cell.strip().lower() for cell in lines[0].strip("|").split("|")]
    if headers[0] not in {"id", "decision id", "decision_id"} or not re.fullmatch(r"[|:\-\s]+", lines[1]):
        return None
    rows = []
    for line in lines[2:]:
        cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        if len(cells) != len(headers) or not cells[0]:
            return None
        rows.append({"decision_id": cells[0], **dict(zip(headers[1:], cells[1:]))})
    return rows


def pending_decisions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    closed = {"resolved", "approved", "rejected", "closed", "answered", "done", "not_applicable"}
    return [row for row in rows if str(row.get("status") or "").lower() not in closed]


def decision_readiness(
    bodies: dict[str, str], payload: dict[str, Any], items: list[dict[str, Any]], markdown: str,
) -> tuple[bool, list[dict[str, Any]]]:
    if "decision_register" in bodies:
        rows = decision_register_rows(bodies["decision_register"])
        return rows is not None, pending_decisions(rows or [])
    # Bound structured rows retain status even when the reading-friendly
    # Markdown combines status and owner in one presentation column.
    for body in (bodies.get("planning_result", ""), bodies.get("task_map", ""), markdown):
        rows = decision_register_rows(body)
        if rows is not None:
            return True, pending_decisions(rows)
    explicit = payload.get("owner_decision_items")
    if isinstance(explicit, list) and all(isinstance(row, dict) and (row.get("id") or row.get("decision_id")) for row in explicit):
        return True, pending_decisions(explicit)
    return bool(items), pending_decisions(items)
