"""Enforce the frozen structural authority of a pure Plan critic."""

from typing import Any, Mapping


_STRUCTURAL_FIELDS = (
    "artifact_refs", "plan_ports", "plan_artifact_ref", "plan_md", "refactor_plan_md",
    "task_map_ref", "task_map", "source_index_ref", "backlog_ref", "requirement_spec_ref",
    "risk_register_ref", "backlog_candidates_ref", "scan_quality_audit_ref",
    "gates", "risk_register", "backlog_candidates",
)


def plan_synth_output_binding_issues(
    identity: Mapping[str, Any], result: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Use kernel-pinned authority, never an agent's claimed output mode.

    Older requests retain their original authority. New pure-critic requests
    permit verdict metadata and evidence citations, including historical
    comparisons, while the current Planner owns executable structure.
    """
    if identity.get("plan_synth_verdict_only") is not True:
        return []
    bodies = [("", result)]
    if isinstance(result.get("report"), Mapping):
        bodies.append(("report.", result["report"]))
    return [{
        "field": prefix + field,
        "code": "plan_synth_structural_output_forbidden",
        "message": f"Pure Plan critic cannot author {prefix}{field}; leave structural fields "
                   "empty. The Kernel preserves the current admitted Planner artifacts. "
                   "Cite reviewed or historical inputs in evidence_refs instead.",
    } for prefix, body in bodies for field in _STRUCTURAL_FIELDS
      if body.get(field) not in (None, "", [], {})]
