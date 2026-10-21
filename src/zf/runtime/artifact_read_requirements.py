"""Canonical Required Read declarations for profiled operation inputs."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from zf.runtime.artifact_read_policy import (
    workflow_reader_required_source_paths,
)
from zf.runtime import semantic_replan_handoff


class ArtifactReadRequirementError(ValueError):
    """A profile cannot derive a complete Required Read declaration."""


def build_canonical_required_reads(
    manifest: Mapping[str, Any],
    *,
    output_profile_id: str,
    explicit: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Declare the canonical artifact slices one stage must consume."""

    rows = [dict(item) for item in explicit if isinstance(item, Mapping)]
    profile = str(output_profile_id or "").strip()
    required_paths: dict[str, tuple[str, ...]] = {}
    if profile == "implementation":
        required_paths = {
            "contract": ("$.acceptance_criteria", "$.verification_commands"),
        }
    elif profile == "task-verify":
        required_paths = {
            "contract": ("$.acceptance_criteria", "$.verification_commands"),
            "target": ("$",),
            "impl-self-check": ("$",),
        }
    elif profile == "candidate-verify":
        required_paths = {
            "contract": ("$.acceptance_criteria", "$.verification_commands"),
            "target": ("$",),
        }
        source_ids = {
            str(source.get("source_id") or "") for source in _sources(manifest)
        }
        if "candidate-freeze" in source_ids:
            required_paths["candidate-freeze"] = ("$",)
        else:
            # Legacy stage-barrier Candidate Verify remains source-compatible.
            required_paths["impl-self-check"] = ("$",)
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
                    and source.get("kind") == "task_scoped_plan_input"
                    and profile in {"implementation", "task-verify"}
                    else ("$",)
                )
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

    authority_contract = (
        manifest.get("handoff_authority_contract")
        if isinstance(manifest.get("handoff_authority_contract"), Mapping)
        else {}
    )
    if (
        profile == "plan-synth"
        or str(authority_contract.get("attempt_domain") or "") == "plan"
    ):
        for source in _sources(manifest):
            source_id = str(source.get("source_id") or "")
            if (
                source_id == "plan-synth-contract"
                or source_id.startswith("child-result-")
                or source_id.startswith("child-artifact-")
                or source_id.startswith("previous-plan-candidate-")
                or source_id in {
                    "goal-objective",
                    "requirement",
                    "review-artifact",
                    "plan-rework-context",
                    "plan-execution-review",
                    "workflow-input",
                    "workflow-prompt",
                }
            ):
                required_paths[source_id] = ("$",)

    sources = _sources(manifest)
    required_paths.update(semantic_replan_handoff.required_read_paths(sources))
    for source in sources:
        source_id = str(source.get("source_id") or "")
        for json_path in required_paths.get(source_id, ()):
            rows.append({
                "source_id": source_id,
                "artifact_id": str(source.get("artifact_id") or ""),
                "artifact_sha256": str(source.get("sha256") or ""),
                "json_path": json_path,
                "min_returned_bytes": 1,
                "max_items": 0,
                "max_chars": 0,
                "allow_truncated": False,
            })

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
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
        deduped.append(row)
    return deduped


def _sources(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = manifest.get("sources")
    return [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []


__all__ = [
    "ArtifactReadRequirementError",
    "build_canonical_required_reads",
]
