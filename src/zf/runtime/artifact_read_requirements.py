"""Canonical Required Read declarations for profiled operation inputs."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping
from pathlib import Path

from zf.runtime.artifact_read_policy import (
    workflow_reader_required_source_paths,
)
from zf.runtime import semantic_replan_handoff


class ArtifactReadRequirementError(ValueError):
    """A profile cannot derive a complete Required Read declaration."""


def required_reads_for_attempt(
    events: Iterable[Any], attempt_id: str, *, state_dir: Path | None = None,
    operation_id: str = "",
) -> list[dict[str, Any]]:
    """Read the latest kernel-pinned declaration, never a worker-supplied list."""
    events = list(events)
    for event in reversed(events):
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        if attempt_id not in {str(payload.get(key) or "") for key in (
            "attempt_id", "active_attempt_id", "dispatch_id",
        )}:
            continue
        rows = payload.get("required_reads")
        if isinstance(rows, list):
            return [dict(row) for row in rows if isinstance(row, Mapping)]
    # Reader operations keep their complete delivery body in an immutable
    # request sidecar; the event ledger intentionally only contains its ref.
    if state_dir is not None and operation_id:
        from zf.runtime.sidecar_refs import hydrate_sidecar_ref
        from zf.runtime.workflow_operation import reduce_workflow_operations

        operation = reduce_workflow_operations(events).get(operation_id, {})
        descriptor = operation.get("request_ref")
        if isinstance(descriptor, Mapping):
            request = hydrate_sidecar_ref(state_dir, dict(descriptor)).payload.get("request", {})
            policy_ref = request.get("input_consumption_policy_ref")
            if isinstance(policy_ref, Mapping) and policy_ref.get("ref"):
                policy = hydrate_sidecar_ref(state_dir, dict(policy_ref)).payload
                if policy.get("attempt_id") != attempt_id:
                    raise ArtifactReadRequirementError("required-read policy belongs to another attempt")
                rows = policy.get("required_reads")
                if isinstance(rows, list):
                    return [dict(row) for row in rows if isinstance(row, Mapping)]
    raise ArtifactReadRequirementError(f"no required-read declaration for attempt {attempt_id}")


def build_canonical_required_reads(
    manifest: Mapping[str, Any],
    *,
    output_profile_id: str,
    explicit: Iterable[Mapping[str, Any]] = (),
    state_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Declare the canonical artifact slices one stage must consume."""

    rows = [dict(item) for item in explicit if isinstance(item, Mapping)]
    explicit_ids = {(row.get('source_id'), row.get('artifact_id')) for row in rows}
    profile = str(output_profile_id or "").strip()
    required_paths: dict[str, tuple[str, ...]] = {}
    # A task contract is already scoped. AC/command-only reads silently omit
    # evidence obligations, output requirements and scope boundaries.
    if profile == "implementation":
        required_paths = {
            "contract": ("$",),
        }
    elif profile == "task-verify":
        required_paths = {
            "contract": ("$",),
            "target": ("$",),
            "impl-self-check": ("$",),
        }
    elif profile == "candidate-verify":
        required_paths = {
            "contract": ("$",),
            "target": ("$",),
        }
        source_ids = {
            str(source.get("source_id") or "") for source in _sources(manifest)
        }
        # A recovered Reader needs the native roster summary to distinguish original
        # Task operations from the later Candidate review. Member rows stay on demand.
        if {"critical-reader-repair-context", "milestone-task-lifecycles"} <= source_ids:
            required_paths["milestone-task-lifecycles"] = ("$.summary",)
        if "candidate-freeze" in source_ids:
            required_paths["candidate-freeze"] = ("$",)
        else:
            # Legacy stage-barrier Candidate Verify remains source-compatible.
            required_paths["impl-self-check"] = ("$",)
        for source_id in source_ids:
            if source_id in {"candidate-host-execution", "candidate-host-review"} or source_id.startswith(
                    ("candidate-host-fact-", "candidate-host-media-")):
                required_paths[source_id] = ("$",)
    elif profile == "integration-acceptance-review":
        required_paths = {
            "contract": (
                "$.acceptance_criteria",
                "$.risk_class",
                "$.integration_admission_profile",
            ),
            "target": ("$",),
            "task-verification-result": ("$",),
        }
    if profile in {"implementation", "task-verify", "candidate-verify"}:
        for source in _sources(manifest):
            source_id = str(source.get("source_id") or "")
            if source_id.startswith(("plan-port-", "oa-route-")):
                required_paths[source_id] = (
                    tuple(source.get("allowed_paths") or ["$"])
                    if source_id.startswith("plan-port-")
                    and ((source.get("kind") == "task_scoped_plan_input"
                          and profile in {"implementation", "task-verify"})
                         or (source.get("kind") == "candidate_scoped_plan_input"
                             and profile == "candidate-verify"))
                    else ("$",)
                )
    if profile == "candidate-verify":
        for source_id, selected in manifest.get("candidate_plan_read_selections", {}).items():
            if source_id in required_paths:
                required_paths[source_id] = tuple(selected)
    elif profile == "artifact-delivery":
        for source in _sources(manifest):
            source_id = str(source.get("source_id") or "")
            if source_id:
                required_paths[source_id] = ("$",)

    reader_paths, missing_reader_stage = workflow_reader_required_source_paths(
        manifest,
        output_profile_id=profile,
    )
    if missing_reader_stage:
        raise ArtifactReadRequirementError(
            f"workflow-read stage {missing_reader_stage!r} requires at least "
            "one canonical source"
        )
    required_paths.update(reader_paths)

    if profile == "plan-synth":
        # Only controlled review-only requests supply these sources. The owner
        # reason and citations suffice; the full candidate index is already bound.
        required_paths.update({
            "plan-review-counter-evidence": ("$.reason", "$.counter_evidence"),
            "plan-review-prior-verdict": ("$",),
        })

    authority_contract = (
        manifest.get("handoff_authority_contract")
        if isinstance(manifest.get("handoff_authority_contract"), Mapping)
        else {}
    )
    if (
        profile == "plan-synth"
        or str(authority_contract.get("attempt_domain") or "") == "plan"
    ):
        from zf.runtime.plan_diagnosis_read_scope import previous_candidate_slices

        selected_previous = previous_candidate_slices(state_dir, manifest, profile)
        for source in _sources(manifest):
            source_id = str(source.get("source_id") or "")
            if (
                source_id == "plan-synth-contract"
                or source_id.startswith("child-result-")
                or source_id.startswith("child-artifact-")
                or source_id.startswith("previous-plan-candidate-")
                or source_id.startswith("plan-declared-input-")
                or source_id in {
                    "goal-objective",
                    "requirement",
                    "owner-requirement-amendment",
                    "review-artifact",
                    "plan-rework-context",
                    "plan-rejected-review",
                    "plan-rework-package",
                    "plan-rework-decision",
                    "plan-rework-delta",
                    "plan-execution-review",
                    "workflow-input",
                    "workflow-prompt",
                }
            ):
                required_paths[source_id] = (
                    tuple(source.get("allowed_paths") or ["$"])
                    if (source_id in selected_previous or source.get("kind") == "plan_rework_candidate_partition"
                        or (source.get("kind") == "plan_declared_input" and not source_id.startswith("previous-plan-candidate-")))
                    else ("$",)
                )

    sources = _sources(manifest)
    required_paths.update(semantic_replan_handoff.required_read_paths(sources))
    if profile == "plan-synth":
        # Keep the authority identity when its bytes also appear as a port.
        # Local-review eligibility validates the authority's exact receipt.
        sources = sorted(sources, key=lambda source: not (
            source.get("source_id") in {"requirement", "goal-objective"}
            or str(source.get("source_id") or "").startswith("critical-")))
    for source in sources:
        source_id = str(source.get("source_id") or "")
        if source_id.startswith("critical-") or source_id in {
            "rework-feedback", "task-handoff", "task-handoff-cited-evidence", "candidate-repair-feedback",
        }:
            required_paths[source_id] = ("$",)
        for json_path in required_paths.get(source_id, ()):
            rows.append({
                "source_id": source_id,
                "artifact_id": str(source.get("artifact_id") or ""),
                "artifact_sha256": str(source.get("sha256") or ""),
                "json_path": json_path,
                "min_returned_bytes": _selected_minimum_bytes(source, json_path),
                "max_items": 0,
                "max_chars": 0,
                "allow_truncated": False,
            })

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    source_by_identity = {
        (str(source.get("source_id") or ""), str(source.get("artifact_id") or ""),
         str(source.get("sha256") or "")): source for source in sources
    }
    seen_plan_slices: set[tuple[Any, ...]] = set()
    seen_profile_slices: set[tuple[Any, ...]] = set()
    for row in rows:
        key = (
            str(row.get("source_id") or ""),
            str(row.get("artifact_id") or ""),
            str(row.get("artifact_sha256") or row.get("sha256") or ""),
            str(row.get("json_path") or "$"),
        )
        if not all(key[:3]) or key in seen:
            continue
        seen.add(key)
        source = source_by_identity.get(key[:3], {})
        ref = str(source.get("ref") or "")
        constraints = (
            row.get("min_returned_bytes", 1), row.get("max_items", 0),
            row.get("max_chars", 0), row.get("allow_truncated", False),
            json.dumps(source.get("access_scope") or {}, sort_keys=True),
            json.dumps(source.get("retention") or {}, sort_keys=True),
        )
        if key[0].startswith("plan-port-") and ref:
            # Preserve the existing same-file Plan port alias contract.
            slice_key = (ref, key[2], key[3], *constraints)
            if slice_key in seen_plan_slices:
                continue
            seen_plan_slices.add(slice_key)
        elif profile in {"plan-synth", "workflow-read"} and ref and key[:2] not in explicit_ids:
            # Occurrence IDs differ for logical aliases. Their full identity
            # stays in the manifest; only the identical delivered body merges.
            slice_key = (key[2], key[3], source.get('source_event_id', ''),
                         tuple(source.get('allowed_paths') or ['$']), *constraints)
            if slice_key in seen_profile_slices:
                continue
            seen_profile_slices.add(slice_key)
        deduped.append(row)
    # Only new declarations change order; pinned attempts/cursors keep theirs.
    # The default page must expose the recovery input before bulk product data.
    recovery_order = {"critical-reader-repair-context": 0, "plan-review-counter-evidence": 1,
                      "plan-review-prior-verdict": 2, "milestone-task-lifecycles": 1}
    return sorted(deduped, key=lambda row: recovery_order.get(row["source_id"], 3))


def _selected_minimum_bytes(source: Mapping[str, Any], json_path: str) -> int:
    """A pinned empty value still requires a complete, identity-bound receipt."""
    selections = [row for row in source.get("read_selections", [])
                  if isinstance(row, Mapping) and row.get("json_path") == json_path]
    if len(selections) != 1:
        return 1
    selected = selections[0]
    empty = (
        selected.get("schema_version") == "artifact-selected-text.v1"
        and selected.get("selection_fidelity") == "lossless-text.v1"
        and bool(source.get("sha256"))
        and selected.get("source_sha256") == source["sha256"]
        and all(type(selected.get(key)) is int and selected[key] == 0
                for key in ("selected_bytes", "selected_chars"))
        and selected.get("selected_sha256") == hashlib.sha256(b"").hexdigest()
        and selected.get("chunks") == []
    )
    return 0 if empty else 1


def _sources(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = manifest.get("sources")
    return [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []


__all__ = [
    "ArtifactReadRequirementError",
    "build_canonical_required_reads",
    "required_reads_for_attempt",
]
