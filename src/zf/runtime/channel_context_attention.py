"""Current review context failures, without hiding unrelated rejected work."""
from typing import Any


def superseded_review_contexts(channel: dict[str, Any]) -> set[str]:
    source = channel.get("messages") or channel.get("recent_messages") or []
    messages = {
        row.get("message_id"): row
        for row in (source.values() if isinstance(source, dict) else source)
    }
    packs = channel.get("context_packs") or {}
    rows = list(packs.values()) if isinstance(packs, dict) else packs

    def binding(row: dict[str, Any]) -> tuple[str, ...] | None:
        message = messages.get(row.get("trigger_message_id")) or {}
        refs = message.get("refs") or {}
        if not all(refs.get(key) for key in ("consensus_review_id", "artifact_ref", "artifact_digest")):
            return None
        return (str(row.get("thread_id") or "main"), str(row.get("target_member_id") or ""),
                str(refs["artifact_ref"]), str(refs["artifact_digest"]).removeprefix("sha256:"))

    rebuilt: dict[tuple[str, ...], str] = {}
    for row in rows:
        key = binding(row)
        if key and row.get("status") == "built" and row.get("semantic_source_complete") is True:
            rebuilt[key] = max(rebuilt.get(key, ""), str(row.get("ts") or ""))
    return {
        str(row["context_pack_id"])
        for row in rows
        if row.get("context_pack_id") and row.get("status") == "rejected"
        and (key := binding(row)) in rebuilt
        and rebuilt[key] > str(row.get("ts") or "")
    }
