"""Versioned read receipts for the operator Inbox projection."""

from __future__ import annotations

import hashlib
import json
from typing import Any


_VERSION_FIELDS = (
    "id", "kind", "title", "summary", "plan_id", "approval_ref",
    "decision_token", "checkpoint_id", "question_id", "request_id",
    "revision", "question_count", "refs", "proposal_ref", "eval_ref",
    "candidate_task_map_ref", "actions", "verification_required", "decision_options",
    "decision_questions", "allow_other", "recommended_answer",
)


def inbox_item_version(item: dict[str, Any]) -> str:
    """Identify the decision content, excluding delivery and read metadata."""
    body = {key: item[key] for key in _VERSION_FIELDS if key in item}
    body["version_event_id"] = item.get("version_event_id", "")
    data = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "v1:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


def put_inbox_item(items: dict[str, dict[str, Any]], key: str, item: dict[str, Any]) -> None:
    previous = items.get(key)
    item["version_event_id"] = str(item.get("created_event_id") or "")
    if previous and previous.get("status") == "pending":
        item["dedupe_count"] = int(previous.get("dedupe_count") or 1) + 1
        item["first_seen_at"] = str(previous.get("first_seen_at") or previous.get("created_ts") or "")
        item["last_seen_at"] = str(item.get("created_ts") or "")
        item["version_event_id"] = previous.get("version_event_id", "")
        if inbox_item_version(previous) != inbox_item_version(item):
            item["version_event_id"] = str(item.get("created_event_id") or "")
    items[key] = item


def record_inbox_read(
    event_type: str,
    payload: dict[str, Any],
    items: dict[str, dict[str, Any]],
    read_versions: dict[str, set[str]],
) -> None:
    """Replay a receipt against the versions visible when it was recorded.

    New clients bind the displayed version explicitly. Legacy receipts are
    interpreted in event order, never by comparing unrelated wall clocks.
    """
    if event_type == "inbox.item.read":
        item_id = str(payload.get("item_id") or "").strip()
        version = str(payload.get("read_version") or "").strip()
        if not version and item_id in items:
            version = inbox_item_version(items[item_id])
        if item_id and version:
            read_versions.setdefault(item_id, set()).add(version)
        return

    if isinstance(payload.get("items"), list):
        for entry in payload["items"]:
            if not isinstance(entry, dict):
                continue
            item_id = str(entry.get("item_id") or "").strip()
            version = str(entry.get("read_version") or "").strip()
            if item_id and version:
                read_versions.setdefault(item_id, set()).add(version)
        return

    # Legacy "all" means the items present at this point in ledger order.
    for item_id, item in items.items():
        read_versions.setdefault(item_id, set()).add(inbox_item_version(item))
