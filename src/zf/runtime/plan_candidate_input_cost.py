"""Draft cost observation; the pinned Critic manifest remains authoritative."""
from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping

from zf.runtime.artifact_text_codec import encode_selected


def candidate_input_cost(
    task_map: Mapping[str, Any], ports: Mapping[str, Mapping[str, Any]],
    limits: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    bodies = {**ports, "task_map": task_map}
    distinct: dict[str, tuple[int, int]] = {}
    rows = []
    for name, body in bodies.items():
        text = encode_selected(body)
        raw = text.encode("utf-8")
        digest = sha256(raw).hexdigest()
        distinct[digest] = (len(raw), len(text))
        rows.append({"port": name, "rendered_bytes": len(raw), "rendered_chars": len(text)})
    size = sum(value[0] for value in distinct.values())
    pages = sum((value[1] + 8191) // 8192 for value in distinct.values())
    limits = dict(limits or {})
    over = (0 < int(limits.get("review_input_max_bytes") or 0) < size
            or 0 < int(limits.get("review_input_max_pages") or 0) < pages)
    tasks = task_map.get("tasks") or []
    selected = [str(task.get("task_id") or "") for task in tasks
                if isinstance(task, Mapping) and isinstance(task.get("evidence_contract"), Mapping)
                and task["evidence_contract"].get("plan_input_selection")]
    return {
        "estimate_kind": "full_candidate_ports_excluding_context_and_scoped_review",
        "authoritative": False,
        "status": "review_required" if over else "within_draft_estimate" if any(limits.values()) else "not_configured",
        "limits": limits, "distinct_rendered_bytes": size,
        "minimum_full_port_pages": pages,
        "largest_ports": sorted(rows, key=lambda row: (-row["rendered_bytes"], row["port"]))[:10],
        "task_count": len(tasks), "tasks_with_input_selection": selected,
        "note": "Not dispatch approval. Includes full candidate ports, excludes requirements, baseline, "
                "reports and output metadata; verified scoped review may reduce these reads. "
                "Final admission checks the pinned manifest. Do not omit acceptance to meet a budget.",
    }
