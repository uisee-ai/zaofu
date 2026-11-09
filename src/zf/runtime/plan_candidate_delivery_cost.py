"""Read-only candidate delivery estimate, never a substitute for pinned admission."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from zf.runtime.artifact_text_codec import decode_artifact_text, encode_selected
from zf.runtime.plan_synth_handoff import _child_artifact_refs, _PLAN_SOURCE_CANDIDATES
from zf.runtime.task_map import resolve_artifact_file


def candidate_delivery_cost(
    *, state_dir: Path, project_root: Path, request: Mapping[str, Any],
    semantic: Mapping[str, Any], execution_review: Mapping[str, Any],
    limits: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Count report/artifacts/review and frozen producer context by rendered digest.

    The Critic's contract, current task snapshot and scoped selections do not
    exist yet. Producer context is an explicit estimate of that future context,
    not a lower bound or an assertion that the final manifest fits.
    """
    components: dict[str, dict[str, Any]] = {}
    unresolved = ["critic_contract_and_role_context", "critic_current_state_and_scope",
                  "admitted_result_envelope_and_prepared_report"]

    def add(kind: str, name: str, value: Any) -> None:
        text = encode_selected(value)
        raw = text.encode("utf-8")
        components.setdefault(sha256(raw).hexdigest(), {
            "kind": kind, "name": name, "rendered_bytes": len(raw),
            "minimum_pages": (len(text) + 8191) // 8192,
        })

    add("author_report", "candidate", semantic)
    from zf.runtime.plan_execution_delivery import execution_review_delivery

    delivery, _details = execution_review_delivery(execution_review)
    add("execution_review", "execution_review", delivery)
    for ref in _child_artifact_refs(semantic):
        try:
            path = resolve_artifact_file(ref, project_root=project_root, state_dir=state_dir)
            add("candidate_artifact", ref, decode_artifact_text(path.read_bytes(), "$"))
        except (OSError, ValueError):
            unresolved.append(f"artifact:{ref}")

    # Use only the authenticated request's frozen manifest, never worker-supplied
    # context or mutable latest projections. Reading it creates no read receipts.
    attempt = str(request.get("active_attempt_id") or "")
    digest = str(request.get("source_manifest_digest") or "")
    safe_attempt = re.sub(r"[^A-Za-z0-9._-]+", "-", attempt).strip("-._") or "attempt"
    try:
        if not attempt or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("no pinned producer manifest")
        path = state_dir / "artifacts/attempts" / safe_attempt / "source-manifests" / f"{digest}.json"
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != digest:
            raise ValueError("producer manifest digest mismatch")
        manifest = json.loads(raw)
        if not isinstance(manifest, dict) or manifest.get("attempt_id") != attempt:
            raise ValueError("producer manifest identity mismatch")
        context_ids = {row[0] for row in _PLAN_SOURCE_CANDIDATES} | {"plan-rework-context"}
        for source in manifest.get("sources", []):
            if not isinstance(source, dict) or source.get("source_id") not in context_ids:
                continue
            selections = [row for row in source.get("read_selections", [])
                          if isinstance(row, dict) and row.get("json_path") == "$"]
            if len(selections) != 1:
                unresolved.append(f"producer_context:{source['source_id']}")
                continue
            row = selections[0]
            size, chars = row.get("selected_bytes"), row.get("selected_chars")
            selected_digest = row.get("selected_sha256")
            if (type(size) is not int or size < 0 or type(chars) is not int
                    or not 0 <= chars <= size or not isinstance(selected_digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", selected_digest)):
                unresolved.append(f"producer_context:{source['source_id']}")
                continue
            components.setdefault(selected_digest, {
                "kind": "producer_context", "name": source["source_id"],
                "rendered_bytes": size, "minimum_pages": (chars + 8191) // 8192,
            })
    except (OSError, ValueError, TypeError):
        unresolved.append("producer_context_manifest")

    size = sum(row["rendered_bytes"] for row in components.values())
    pages = sum(row["minimum_pages"] for row in components.values())
    limits = dict(limits or {})
    byte_cap = int(limits.get("review_input_max_bytes") or 0)
    page_cap = int(limits.get("review_input_max_pages") or 0)
    over = (0 < byte_cap < size) or (0 < page_cap < pages)
    return {
        "estimate_kind": "candidate_delivery_with_frozen_producer_context",
        "authoritative": False, "complete": False,
        "status": "review_required" if over else "incomplete_estimate",
        "estimated_rendered_bytes": size, "estimated_minimum_pages": pages,
        "remaining_bytes": byte_cap - size if byte_cap else None,
        "remaining_pages": page_cap - pages if page_cap else None,
        "components": sorted(components.values(), key=lambda row: (-row["rendered_bytes"], row["name"])),
        "unresolved_components": unresolved,
        "note": "Includes authored report, resolved artifacts, execution review and frozen producer "
                "context. Final Critic context/scope may differ; remaining capacity is not approval. "
                "Only the pinned Critic manifest decides admission. Preserve all acceptance obligations.",
    }
