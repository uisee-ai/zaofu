"""Verify a compiled proposal represents the admitted synthesis selections."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.config.plan_review import resolve_plan_review
from zf.runtime.workflow_proposal_projection import selected_flow_spec


def synthesis_compatibility_blockers(
    *,
    synthesis: Mapping[str, Any],
    short_spec: Mapping[str, Any],
    source_docs: list[dict[str, Any]],
    preflight: Mapping[str, Any],
    closure: Mapping[str, Any],
    completion_profile: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not synthesis:
        return []
    mismatches: list[str] = []
    parameters = (
        short_spec.get("parameters")
        if isinstance(short_spec.get("parameters"), Mapping)
        else {}
    )
    family = str(short_spec.get("flow_family") or "")
    source_spec = selected_flow_spec(source_docs, family)
    requested_lanes = int(parameters.get("lanes") or 0)
    actual_lanes = int(source_spec.get("lanes", source_spec.get("laneCount", source_spec.get("lane_count", 0))) or 0)
    if requested_lanes and requested_lanes != actual_lanes:
        mismatches.append(
            f"lanes requested {requested_lanes}, compiled {actual_lanes or 'none'}"
        )
    if "stage_teams" in parameters:
        from zf.core.config.stage_teams import normalize_stage_teams
        actual_teams = source_spec.get("stageTeams", source_spec.get("stage_teams"))
        if actual_teams != normalize_stage_teams(parameters["stage_teams"]):
            mismatches.append("compiled stage_teams differ from the admitted division of work")
    if "plan_review" in parameters:
        if resolve_plan_review(parameters) != resolve_plan_review(source_spec):
            mismatches.append("plan_review differs from the selected review policy")
    pipeline = parameters.get("task_pipeline")
    if isinstance(pipeline, Mapping):
        actual = source_spec.get("taskPipeline", source_spec.get("task_pipeline", {}))
        actual = actual if isinstance(actual, Mapping) else {}
        pools = actual.get("pools") if isinstance(actual.get("pools"), Mapping) else {}
        checks = {
            "profile": source_spec.get("flowProfile", source_spec.get("flow_profile")),
            "mode": actual.get("mode", "shadow"),
            "max_active_task_pipelines": actual.get("maxActiveTaskPipelines", actual.get("max_active_task_pipelines")),
            "impl_capacity": (pools.get("impl") or {}).get("capacity"),
            "verify_capacity": (pools.get("verify") or {}).get("capacity"),
        }
        for key, value in checks.items():
            if pipeline.get(key) != value:
                mismatches.append(f"task_pipeline {key} requested {pipeline.get(key)!r}, compiled {value!r}")
    delivery_contract = (
        preflight.get("delivery_contract")
        if isinstance(preflight.get("delivery_contract"), Mapping)
        else {}
    )
    requested_strictness = str(parameters.get("strictness") or "")
    actual_strictness = str(delivery_contract.get("strictness") or "")
    if (
        requested_strictness
        and requested_strictness != actual_strictness
    ):
        mismatches.append(
            "strictness requested "
            f"{requested_strictness!r}, compiled {actual_strictness!r}"
        )
    requested_pattern = str(parameters.get("pattern_id") or "")
    actual_pattern = str(preflight.get("resolved_pattern_id") or "")
    if requested_pattern and requested_pattern != actual_pattern:
        mismatches.append(
            f"pattern_id requested {requested_pattern!r}, "
            f"compiled {actual_pattern!r}"
        )

    roles = {
        str(identity)
        for item in closure.get("roles", [])
        if isinstance(item, Mapping)
        for identity in (item.get("name"), item.get("instance_id"))
        if identity
    }
    skills = {
        str(skill)
        for item in closure.get("roles", [])
        if isinstance(item, Mapping)
        for skill in item.get("skills", [])
        if str(skill)
    }
    profiles = set(
        str(item)
        for item in (
            closure.get("execution_profiles", {})
            if isinstance(
                closure.get("execution_profiles"),
                Mapping,
            )
            else {}
        )
    )
    for label, requested, available in (
        ("role", synthesis.get("requested_roles"), roles),
        ("skill", synthesis.get("requested_skills"), skills),
        ("profile", synthesis.get("requested_profiles"), profiles),
    ):
        missing = sorted(
            set(str(item) for item in requested or []) - available
        )
        if missing:
            mismatches.append(
                f"{label} closure missing {', '.join(missing)}"
            )

    requested_completion = (
        synthesis.get("completion_profile")
        if isinstance(synthesis.get("completion_profile"), Mapping)
        else {}
    )
    completion_aliases = {
        "id": "id",
        "delivery_policy": "delivery_policy",
        "completion_threshold": "completion_threshold",
        "required_artifacts": "required_delivery_artifacts",
    }
    for requested_key, compiled_key in completion_aliases.items():
        requested_value = requested_completion.get(requested_key)
        if requested_value in (None, "", []):
            continue
        compiled_value = completion_profile.get(compiled_key)
        if requested_key == "required_artifacts":
            compiled_value = [
                str(item.get("source_ref") or "")
                for item in compiled_value or []
                if isinstance(item, Mapping)
            ]
        if requested_value != compiled_value:
            mismatches.append(
                f"{requested_key} requested {requested_value!r}, "
                f"compiled {compiled_value!r}"
            )
    if not mismatches:
        return []
    return [{
        "kind": "workflow_synthesis_compile_mismatch",
        "severity": "STOP",
        "title": "Synthesis result is not represented by compiled config",
        "message": "; ".join(mismatches),
        "why_it_matters": (
            "A Proposal cannot silently ignore Agent-selected parameters or "
            "capability requirements."
        ),
        "fix_it": (
            "Revise the Requirement/FlowSpec or submit an explicit "
            "config-changing candidate for deterministic compilation."
        ),
        "safe_auto_fix": False,
    }]
