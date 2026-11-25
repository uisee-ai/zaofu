"""Static catalog and prompt helpers for Workflow Synthesis."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from zf.core.workflow.generic_workflow import (
    COMPLETION_PROFILES,
    REGISTERED_OPERATIONS,
    REGISTERED_TEMPLATES,
    generic_workflow_catalog_projection,
)
from zf.runtime.execution_profiles import execution_profile_catalog, execution_profile_catalog_projection
from zf.runtime.workflow_route_policy import effective_execution_policy_digest


WORKFLOW_SYNTHESIS_RESULT_SCHEMA = "workflow-synthesis-result.v1"
ALLOWED_FLOW_FAMILIES = frozenset({
    "IssueFlow",
    "PrdFlow",
    "RefactorFlow",
    "Workflow",
})
RESULT_KEYS = frozenset({
    "schema_version",
    "request_id",
    "request_revision",
    "requirement_ref",
    "requirement_digest",
    "selected_flow_family",
    "short_flow_spec",
    "decision_rationale",
    "assumptions",
    "open_questions",
    "requested_roles",
    "requested_skills",
    "requested_profiles",
    "completion_profile",
    "risk_hints",
})
FLOW_SPEC_KEYS = frozenset({
    "flow_family",
    "intent",
    "parameters",
    "purpose",
    "template",
})
COMPLETION_KEYS = frozenset({
    "id",
    "delivery_policy",
    "completion_threshold",
    "required_artifacts",
})


def admission_catalog(config: Any, project_root: Path) -> dict[str, Any]:
    roles: set[str] = set()
    skills: set[str] = set()
    for role in getattr(config, "roles", []) or []:
        roles.update(filter(None, {
            str(getattr(role, "name", "") or ""),
            str(getattr(role, "instance_id", "") or ""),
        }))
        raw_skills = getattr(role, "skills", [])
        if isinstance(raw_skills, (list, tuple, set)):
            skills.update(
                str(item).strip()
                for item in raw_skills
                if str(item).strip()
            )
    for skill_path in Path(project_root).glob("skills/*/SKILL.md"):
        skills.add(skill_path.parent.name)
    execution = execution_profile_catalog_projection(config)
    from zf.runtime.workflow_registered_controller import controller_catalog
    from zf.runtime.workflow_proposal_projection import completion_profile
    workflow = getattr(config, "workflow", None)
    metadata_by_kind = dict(getattr(workflow, "flow_metadata_by_kind", {}) or {})
    primary = getattr(workflow, "flow_metadata", {}) or {}
    if primary.get("flow_kind"):
        metadata_by_kind.setdefault(primary["flow_kind"], primary)
    completion_by_family = {}
    for kind, metadata in metadata_by_kind.items():
        completion = completion_profile({}, {"effective_flow_metadata": metadata})
        completion_by_family[kind] = {
            "id": completion["id"], "delivery_policy": completion["delivery_policy"],
            "completion_threshold": completion["completion_threshold"],
            "required_artifacts": completion["required_delivery_artifacts"],
        }
    for role in getattr(config, "roles", []) or []:
        execution["roles"][str(role.instance_id or role.name)].update(
            role_kind=role.role_kind,
            permission_mode=role.permission_mode,
            native_delegation=getattr(role.provider_session, "native_delegation", None),
        )
    from zf.core.config.stage_teams import authorized_judges
    return {
        "roles": roles,
        "authorized_judges": authorized_judges(config),
        "reader_roles": {str(r.instance_id or r.name) for r in (getattr(config, "roles", []) or []) if r.role_kind == "reader"},
        "skills": skills,
        "profiles": set(execution_profile_catalog(config)),
        "templates": set(REGISTERED_TEMPLATES),
        "operations": set(REGISTERED_OPERATIONS),
        "completion_profiles": set(COMPLETION_PROFILES),
        "template_contracts": generic_workflow_catalog_projection()["templates"],
        "execution_catalog": execution,
        "effective_policy_digest": effective_execution_policy_digest(config),
        "configured_role_skills": {str(role.instance_id or role.name): list(role.skills)
                                  for role in getattr(config, "roles", []) or []},
        "configured_completion_by_family": completion_by_family,
        "registered_controllers": controller_catalog(),
    }


def admission_contract(catalog: Mapping[str, Any]) -> dict[str, Any]:
    """Pin the same effective catalog in the provider prompt and request hash."""
    return {
        "effective_policy_digest": catalog["effective_policy_digest"],
        "execution_catalog": catalog["execution_catalog"],
        "template_contracts": catalog["template_contracts"],
        "allowed_flow_families": sorted(ALLOWED_FLOW_FAMILIES),
        "allowed_roles": sorted(catalog["roles"]),
        "authorized_judges": sorted(catalog.get("authorized_judges", [])),
        "registered_readers": sorted(catalog.get("reader_roles", [])),
        "allowed_skills": sorted(catalog["skills"]),
        "allowed_profiles": sorted(catalog["profiles"]),
        "allowed_generic_templates": sorted(catalog["templates"]),
        "allowed_generic_operations": sorted(catalog["operations"]),
        "allowed_completion_profiles": sorted(catalog["completion_profiles"]),
        "configured_role_skills": catalog.get("configured_role_skills", {}),
        "configured_completion_by_family": catalog.get("configured_completion_by_family", {}),
        "registered_controllers": catalog.get("registered_controllers", {}),
    }


def synthesis_prompt(
    *,
    request: Mapping[str, Any],
    requirement: Mapping[str, Any],
    catalog: Mapping[str, Any],
) -> str:
    contract = {
        "schema_version": WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
        "request_id": str(request.get("request_id") or ""),
        "request_revision": int(request.get("revision") or 0),
        "requirement_ref": str(request.get("requirement_spec_ref") or ""),
        "requirement_digest": str(
            request.get("requirement_spec_digest") or ""
        ),
        **admission_contract(catalog),
        "requirement": dict(requirement),
    }
    return (
        "Use the zf-workflow-synthesis method. Return exactly one JSON object "
        "with no prose or code fence. Do not emit expanded config, handlers, "
        "approval, submit, or task objects. For a requested registered controller (including V5), "
        "select its exact controller_id and controller_digest in short_flow_spec.parameters, "
        "and supply verification_commands appropriate to this project's acceptance. This selects "
        "the full registered configuration, including its profiles/roles/completion policy; "
        "those selected capabilities need not already exist in the current project. Use "
        "parameters.approval_requirements with exactly plan (required or not_requested) and "
        "source_quotes (verbatim strings from this Requirement). Always explicitly assess Plan "
        "human review for registered controllers. If the owner asks to review/approve the Plan "
        "before implementation, choose required and cite that instruction; Workflow approval "
        "alone is a separate gate. Do not infer no Plan approval from a template default. "
        "Use the selected entry's completion_profile. Keep its lane/pipeline defaults; do not combine "
        "this selection with task_pipeline overrides. Do not ask how to install a controller "
        "that is already registered. Project identity, PRD/target paths and Git baseline are "
        "bound by the compiler, followed by explicit Apply config and Approve & Run. "
        "For a confirmed Task-bound Requirement without source_ref, the compiler validates "
        "task_input_contract_ref/digest/binding and snapshots the entire Requirement as the "
        "project-local input, including its objective and clarification answers. Do not ask "
        "the owner to invent a separate PRD file when these canonical inputs are present. "
        "For registered blocking Task Pipeline controllers, candidate_verify_width (1..registered lanes) "
        "selects the independent global Candidate Verify fanout only; task pools and all acceptance "
        "gates remain intact. Choose the smallest width justified by distinct risks/coverage; a "
        "single simple contract normally needs one independent verifier. Explain the choice. "
        "If the owner explicitly requests PRD v4, "
        "short_flow_spec.parameters can include task_pipeline with exactly profile "
        "(prd-flow-v4-task-pipeline), mode (shadow or blocking), impl_capacity, "
        "verify_capacity and max_active_task_pipelines, plus lanes large enough for "
        "both pools. Preserve requested capacities. Ask when the mode or a decision-critical "
        "limit is unknown; shadow is observation, not actual scheduling. "
        "When tasked to select a PRD team, use parameters.stage_teams.plan={reason,author,members}. "
        "Select the unique Plan author and specialists from the selected registered reader instances; "
        "include them in requested_roles. Each member has obligation_id, role, question, input_refs "
        "(source labels or references within the existing authorized scope) and expected_output. "
        "Use an empty members array when the author can plan directly. Never select its independent reviewers. "
        "For active research, reserve_roles may name additional independent readers in requested_roles. "
        "A reserve does not start until an authorized request-fanout binds new evidence and a scoped obligation. "
        "Record selection_reason and completion_condition for selected Plan, Task Verify and Judge specialists in their respective decisions. "
        "Use approved project-relative input_refs, with a unique #Markdown-heading when a section is sufficient. "
        "Optional Plan members add required:false and coverage_owner equal to author. To transfer an existing "
        "optional assignment, retain the whole obligation and add disposition with status reassigned, reason, "
        "previous_proposal_digest and previous_run_id, for a newly approved successor Proposal. "

        "Optional stage_teams.task_verify={mode:'oa_run_plan',reason} assigns independent verification "
        "at pre_impl; candidate_verify={width,reason} assigns bounded Reader seats and requires "
        "complete freeze-bound coverage. judge={reason,author,members} chooses a named authorized_judges instance as formal "
        "Judge and distinct read-only specialists. Each member declares obligation_id, role, question, input_refs "
        "and expected_output. Judge opinions are required by default. Set required:false for advisory input; it "
        "can inform the Judge if admitted on time and never blocks signing. Set judge.objectives and judge.questions "
        "([{question_id, question}]) for the formal Judge even with zero specialists. To withdraw an optional Judge "
        "member on a successor Proposal retain its obligation, name the formal Judge as coverage_owner and add the "
        "reassigned disposition bound to the previous approved Run and Proposal. "
        "When the owner authorizes risk-based review selection, select parameters.plan_review "
        "with mode none|targeted|full, a decision reason, and focus (non-empty review questions for targeted). "
        "Use existing requirement and risk evidence, not task-count thresholds; targeted checks include "
        "affected dependencies and shared rules. Missing coverage evidence calls for wider review, not none. "
        "Do not add a selection Agent or weaken an explicit mandatory review. Keep requested approvals. "
        "This is an explicit config proposal for approval, never a Planner self-exemption. "
        "When plan_review.mode is none (or plan_critic_enabled is false), the compiled PrdFlow "
        "has no Plan Critic: do not request plan-critic or its renamed instance in requested_roles. "
        "Channel PRD reviewers are separate from Workflow roles. For targeted/full, request only "
        "roles in the selected effective controller. Do not copy the unmodified catalog role list. "
        "When an owner explicitly requires no dedicated PRD Plan Critic, set "
        "parameters.plan_critic_enabled to false and keep requested Plan approval; "
        "otherwise omit it to preserve the existing configuration. "
        "This only prepares a reviewed candidate and never applies configuration or starts a Run. Contract:\n"
        + json.dumps(contract, ensure_ascii=False, sort_keys=True)
    )


def normalized_strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def safe_component(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in "._-" else "_"
        for char in str(value)
    )[:120] or "request"


__all__ = [
    "ALLOWED_FLOW_FAMILIES",
    "COMPLETION_KEYS",
    "FLOW_SPEC_KEYS",
    "RESULT_KEYS",
    "WORKFLOW_SYNTHESIS_RESULT_SCHEMA",
    "admission_catalog",
    "normalized_strings",
    "safe_component",
    "synthesis_prompt",
]
