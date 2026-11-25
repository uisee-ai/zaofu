"""Recover a required-page cursor from evidence of this session's output.

This is an explicit convenience for a worker that can still use the material;
historical tool output does not prove that it survived context compaction.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.artifact_output_delivery import observe_artifact_delivery
from zf.runtime.artifact_read_ledger import active_read_ledger_path
from zf.runtime.artifact_read_ledger_validation import _parse_ledger
from zf.runtime.artifact_text_page import parse_artifact_page
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@dataclass(frozen=True)
class ResumeDecision:
    index: int = 0
    offset: int = 0
    reason: str = "evidence_unavailable"
    historical_gap: tuple[int, int] | None = None
    # Empty unless the session witnessed content beyond its first delivery gap.
    missing_ranges: tuple[tuple[int, int, int], ...] = ()
    cursor_proof: str = ""
    historical_navigation_proof: str = ""


def resume_position(*args: Any, **kwargs: Any) -> tuple[int, int]:
    """Compatibility cursor for callers that do not display a decision reason."""
    decision = resume_decision(*args, **kwargs)
    return decision.index, decision.offset


def resume_decision(
    state_dir: Path, *, manifest: dict[str, Any], identity: dict[str, Any],
    bundle_digest: str, groups: list[dict[str, Any]], receipts: list[list[dict[str, Any]]],
    event_log: Any = None, navigation_proof: str = "",
) -> ResumeDecision:
    """Return the first gap; unavailable or inconsistent evidence starts over."""
    attempt = str(manifest.get("attempt_id") or "")
    operation = str(identity.get("operation_id") or "")
    if not attempt or not operation or identity.get("provider") != "codex":
        return ResumeDecision(reason="unsupported_identity_or_provider")
    try:
        registry = RoleSessionRegistry(state_dir / "role_sessions.yaml", state_dir.parent)
        session = str(registry.get(identity["actor"]) or "")
        if not session:
            return ResumeDecision(reason="session_unbound")
        from zf.cli.artifact_resume_boundary import continuation_boundary, _epoch

        boundary = continuation_boundary(
            event_log=event_log, transcript=registry.get_path(identity["actor"]),
            actor=identity["actor"], session_id=session,
        )
        if boundary is None:
            return ResumeDecision(reason="continuation_boundary_unavailable")
        active = active_read_ledger_path(state_dir, attempt)
        paths = sorted(active.parent.glob("read-ledger-*.jsonl"))
        if active.exists():
            paths.append(active)
        rows: list[dict[str, Any]] = []
        for path in paths:
            raw = path.read_bytes()
            if path != active and not path.stem.endswith(hashlib.sha256(raw).hexdigest()[:16]):
                return ResumeDecision(reason="read_ledger_invalid")
            rows.extend(_parse_ledger(raw, expected_attempt_id=attempt))
        candidates = [row for row in rows if row.get("operation_id") == operation
                      and row.get("bundle_digest") == bundle_digest
                      and row.get("consumer_actor") == identity["actor"]
                      and row.get("consumer_role") == identity["role"]
                      and row.get("consumer_provider") == identity["provider"]
                      and row.get("consumer_purpose") == identity["purpose"]]
        if not candidates:
            return ResumeDecision(reason="compatible_receipts_missing")
        observed = observe_artifact_delivery(state_dir, candidates)
        observed = [row for row in observed if row.get("provider_delivery_status") == "observed_in_transcript"
                    and row.get("provider_session_id") == session]
        if not observed:
            return ResumeDecision(reason="current_session_delivery_unverified")
        def coverage(rows: list[dict[str, Any]]) -> ResumeDecision:
            return _coverage(state_dir, rows, groups, receipts, attempt, bundle_digest, session, boundary)

        if boundary:
            historical = coverage(observed)
            retained = []
            for row in observed:
                witness = row.get("provider_observation_ref")
                if not isinstance(witness, dict):
                    continue
                try:
                    delivered = hydrate_sidecar_ref(state_dir, witness).payload
                    if _epoch(delivered.get("timestamp")) > boundary:
                        retained.append(row)
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            current = (coverage(retained)
                       if retained else ResumeDecision(reason="delivery_predates_compaction"))
            if historical.reason == "observed_complete" and current.reason != "observed_complete":
                return ResumeDecision(len(groups), 0, "context_recovery")
            gap = (historical.index, historical.offset)
            if gap > (current.index, current.offset):
                # Navigation is scoped to this session and compaction boundary. Only the
                # newly verified historical coverage determines where the actual gap is.
                navigation = hashlib.sha256(json.dumps(
                    ["historical_gap", session, boundary, bundle_digest],
                ).encode()).hexdigest()
                continues_gap = navigation_proof and navigation_proof in {navigation, historical.cursor_proof}
                if continues_gap or _delivered_gap_notice(state_dir, retained, attempt, bundle_digest):
                    return ResumeDecision(*gap, "continue_historical_delivery", missing_ranges=historical.missing_ranges,
                                          cursor_proof=historical.cursor_proof)
                # Render current context once before offering the scoped, revalidated gap cursor.
                return ResumeDecision(current.index, current.offset, current.reason, gap, current.missing_ranges,
                                      current.cursor_proof, historical_navigation_proof=navigation)
            return current
        return coverage(observed)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return ResumeDecision(reason="evidence_invalid")


def _delivered_gap_notice(state_dir: Path, retained: list[dict[str, Any]],
                          attempt: str, bundle_digest: str) -> bool:
    """A repeated resume may use the delivery frontier after explaining it to this session."""
    seen: set[str] = set()
    for row in reversed(retained):
        digest = row["output_sha256"]
        if digest in seen:
            continue
        seen.add(digest)
        page = parse_artifact_page(hydrate_sidecar_ref(state_dir, row["output_ref"]).payload["body"])
        if (page.get("bundle_digest") == bundle_digest and page.get("attempt_id") == attempt
                and page.get("delivery_id") == row.get("delivery_id")
                and page.get("continuation", {}).get("next_resume") == "continue_witnessed_delivery_gap"):
            return True
    return False


def _coverage(
    state_dir: Path, observed: list[dict[str, Any]], groups: list[dict[str, Any]],
    receipts: list[list[dict[str, Any]]], attempt: str, bundle_digest: str, session: str, boundary: float,
) -> ResumeDecision:
    """Validate exact delivered bytes independently of their retention in memory."""
    bodies: dict[str, Any] = {}
    missing: list[tuple[int, int, int]] = []
    lengths: list[int] = []
    by_occurrence: dict[tuple, list[dict]] = {}
    fields = ("workflow_run_id", "task_id", "attempt_id", "dispatch_id", "source_id",
              "artifact_id", "artifact_sha256", "json_path", "occurrence_id")
    for row in observed:
        by_occurrence.setdefault(tuple(row.get(k) for k in fields), []).append(row)
    for index, (group, aliases) in enumerate(zip(groups, receipts, strict=True)):
        selection = group.get("selection")
        content = group.get("content", "")  # Legacy direct callers.
        total = selection.chars if selection else len(content)
        digest = selection.digest if selection else hashlib.sha256(content.encode()).hexdigest()
        byte_count = selection.byte_count if selection else len(content.encode())
        lengths.append(total)
        alias_gaps: list[tuple[int, int]] = []
        for alias in aliases:
            ranges: list[tuple[int, int]] = []
            for row in by_occurrence.get(tuple(alias.get(k) for k in fields), []):
                start, end = row.get("page_start"), row.get("page_end")
                if (type(start) is not int or type(end) is not int or not (0 <= start < end <= total or start == end == total == 0)
                        or row.get("page_total") != total or row.get("selected_sha256") != digest
                        or row.get("selected_bytes") != byte_count):
                    continue
                output_digest = row["output_sha256"]
                if output_digest not in bodies:
                    bodies[output_digest] = parse_artifact_page(hydrate_sidecar_ref(state_dir, row["output_ref"]).payload["body"])
                page = bodies[output_digest]
                if (page.get("schema_version") != "required-artifact-page.v2"
                        or page.get("attempt_id") != attempt or page.get("bundle_digest") != bundle_digest
                        or page.get("delivery_id") != row.get("delivery_id")):
                    continue
                for item in page.get("items", []):
                    sources = [item, *item.get("aliases", [])]
                    if (any(s.get("source_id") == alias["source_id"] and s.get("artifact_id") == alias["artifact_id"]
                            and s.get("json_path") == alias["json_path"]
                            and (not s.get("artifact_sha256") or s["artifact_sha256"] == alias["artifact_sha256"])
                            for s in sources)
                            and item.get("page_start") == start and item.get("page_end") == end
                            and item.get("content") == (selection.read(start, end) if selection else content[start:end])):
                        ranges.append((start, end))
                        break
            frontier = 0
            for start, end in sorted(ranges):
                if start > frontier:
                    alias_gaps.append((frontier, start))
                frontier = max(frontier, end)
            if frontier < total or (not total and not ranges):
                alias_gaps.append((frontier, total))
        # Every alias needs its own delivery evidence even when the body is shared.
        for start, end in sorted(alias_gaps):
            if missing and missing[-1][0] == index and start <= missing[-1][2]:
                index_, start_, end_ = missing[-1]
                missing[-1] = (index_, start_, max(end_, end))
            else:
                missing.append((index, start, end))
    if not missing:
        return ResumeDecision(len(groups), 0, "observed_complete")
    index, offset, _ = missing[0]
    # Explicit bounds also distinguish an unseen empty source from an observed one.
    suffix = [(i, offset if i == index else 0, lengths[i]) for i in range(index, len(groups))]
    sparse = missing != suffix
    # An explicit continuation may advance inside an unobserved gap only while
    # the native session, compaction boundary and verified coverage still match.
    proof = hashlib.sha256(json.dumps([session, boundary, missing]).encode()).hexdigest() if sparse else ""
    return ResumeDecision(index, offset, "observed_prefix" if index or offset else "coverage_gap",
                          missing_ranges=tuple(missing) if sparse else (), cursor_proof=proof)
