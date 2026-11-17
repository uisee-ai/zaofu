"""Choose scoped reads only when exact, pinned text proves a byte reduction.

This is a mechanical input-size comparison, never a semantic exemption or a
prediction of provider tokens, traffic, latency or money. Sources stay immutable.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.artifact_read_snapshot import selected_text
from zf.runtime.artifact_source_versions import read_source_version
from zf.runtime.plan_review_input_diagnostics import review_diagnostics
from zf.runtime.sidecar_refs import sidecar_path


def choose_review_reads(
    state_dir: Path, manifest: Mapping[str, Any], *, before: list[dict[str, Any]],
    candidate: list[dict[str, Any]], explicit: bool,
    strategy: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostic = review_diagnostics(manifest, before=before, after=candidate, explicit=explicit)
    if strategy and before != candidate:
        diagnostic.update(strategy=strategy,
            mode="targeted" if strategy == "targeted_navigation" else "full",
            reason=strategy, eligibility="policy_bound" if strategy == "targeted_navigation" else "lossless",
            inherited_approval=False)
    if before == candidate:
        return candidate, diagnostic
    sources: dict[tuple, list] = {}
    for source in manifest.get("sources", []):
        key = (source.get("source_id"), source.get("artifact_id"), source.get("sha256"))
        sources.setdefault(key, []).append(source)
    raw_cache: dict[tuple, bytes] = {}
    selections: dict[tuple, tuple[str, int] | None] = {}

    def measure(rows: list[dict[str, Any]]) -> dict[str, int]:
        distinct: dict[str, int] = {}
        unknown = 0
        for row in rows:
            key = (row.get("source_id"), row.get("artifact_id"), row.get("artifact_sha256"))
            path = row.get("json_path") or "$"
            selection = (*key, path)
            if any(row.get(k) for k in ("allow_truncated", "max_items", "max_chars")):
                unknown += 1
                continue
            if selection not in selections:
                selections[selection] = None
                try:
                    matches = sources.get(key, [])
                    if len(matches) != 1 or not key[2]:
                        raise ValueError("source identity is not unique")
                    source = matches[0]
                    allowed = source.get("allowed_paths", ["$"])
                    if "$" not in allowed and path not in allowed:
                        raise ValueError("selection is outside source scope")
                    if key not in raw_cache:
                        raw, _ = read_source_version(state_dir, dict(source), path=sidecar_path(state_dir, source["ref"]))
                        if hashlib.sha256(raw).hexdigest() != key[2]:
                            raise ValueError("source digest differs")
                        raw_cache[key] = raw
                    body = selected_text(raw_cache[key], path).encode()
                    selections[selection] = (hashlib.sha256(body).hexdigest(), len(body))
                except (OSError, ValueError, TypeError, KeyError, IndexError):
                    pass
            value = selections[selection]
            if value is None:
                unknown += 1
            else:
                distinct[value[0]] = value[1]
        return {"required_rows": len(rows), "distinct_selected_bytes": sum(distinct.values()), "unknown_rows": unknown}

    full, scoped = measure(before), measure(candidate)
    reason = ("selection_size_unproven" if full["unknown_rows"] or scoped["unknown_rows"]
              else "no_selection_byte_saving" if scoped["distinct_selected_bytes"] >= full["distinct_selected_bytes"]
              else "")
    chosen = before if reason else candidate
    diagnostic.update(before=full, after=full if reason else scoped, candidate=scoped,
                      measurement="pinned_selected_text_bytes_not_provider_usage")
    if reason:
        diagnostic.update(mode="full", reason=reason, eligibility="verified")
    return chosen, diagnostic
