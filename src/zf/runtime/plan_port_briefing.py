"""Briefing text for Planner-owned Plan Artifact Package ports."""

from __future__ import annotations


def plan_port_contract_lines(*, flow_kind: str = "") -> list[str]:
    lines = [
        "Plan-port contract: when Controlled Artifact Inputs include portable "
        "matrix drafts, enrich them before success and return each ready body "
        "through `plan_ports` using canonical logical names such as "
        "`source_inventory`, `capability_matrix`, `acceptance_matrix`, "
        "`test_matrix`, `task_map`, and `real_e2e_matrix`.",
        "`plan_ports` MUST be a JSON array of descriptor objects shaped like "
        "`{\"logical_name\": \"acceptance_matrix\", \"schema_version\": "
        "\"acceptance-matrix.v1\", \"body\": {...}}`; do not use a "
        "`{logical_name: body}` object map.",
        "Return `plan_ports` once at the top level of the success payload; "
        "do not duplicate the matrix bodies inside `report`.",
        "Every required matrix body must set top-level `status: ready` and "
        "`metadata.enrichment_contract.status: fulfilled`; do not overwrite "
        "kernel state or claim an unadapted draft as ready.",
        "Test Matrix has exactly one command registry: `test_matrix.commands[]`; never redefine commands under `tests[]`.",
        "Passing fixture: every required matrix is `status:ready` with `metadata.enrichment_contract.status:fulfilled`; Task `TASK-1` command `test-command` uses tier `runtime`, exact command `pytest -q tests/test_command.py`, and `acceptance_ids:[\"AC-1\"]`.",
        "Cross-links: capability `CAP-1` has `task_ids:[\"TASK-1\"]` and `acceptance_ids:[\"AC-1\"]`; acceptance `AC-1` has `capability_id:\"CAP-1\"`, `task_id:\"TASK-1\"`, and `verification_command_ids:[\"test-command\"]`; Test Matrix repeats the exact command and acceptance ids.",
        "A pure aggregator may validate and carry child-supplied `plan_ports`, "
        "but it must reject rather than invent missing project facts or matrix bodies.",
        "When `product_acceptance_spec` is required, return one descriptor "
        "`{\"logical_name\": \"product_acceptance_spec\", "
        "\"schema_version\": \"product_acceptance_spec.v1\", "
        "\"body\": {...}}`. The body declares `assembly_owner`, runnable "
        "`entrypoints[]` with start/health/owner, mandatory `user_journeys[]` "
        "with observable assertions, and `provider_qualification`. Do not "
        "invent workflow run, plan revision, or task-map generation; the Kernel "
        "binds those identities while building the current Plan Artifact Package.",
        "Product Acceptance shape example (replace example semantics with the "
        "confirmed requirement; this is not evidence): "
        "`{\"logical_name\":\"product_acceptance_spec\","
        "\"schema_version\":\"product_acceptance_spec.v1\",\"body\":{"
        "\"assembly_owner\":\"ASSEMBLY\",\"entrypoints\":[{"
        "\"entrypoint_id\":\"cli\",\"start_command\":\"python -m app\","
        "\"health_check\":\"python -m app --help\",\"owner\":\"ASSEMBLY\"}],"
        "\"user_journeys\":[{\"journey_id\":\"J1\",\"title\":\"Run the application\","
        "\"mandatory\":true,\"verification_tier\":\"e2e\",\"assertions\":[{"
        "\"assertion_id\":\"A1\",\"statement\":\"The actual command produces the required output\","
        "\"observation_kind\":\"cli\"}]}],"
        "\"provider_qualification\":{\"required_for_goal\":false,\"providers\":[],\"ttl_seconds\":0}}}`",
    ]
    if str(flow_kind or "").strip().lower() == "issue":
        lines.append(
            "Issue compatibility: regression coverage belongs in the canonical "
            "`test_matrix` port; `regression_test_matrix` is a delivery alias, "
            "not a second logical port or mandatory duplicate file."
        )
    return lines


__all__ = ["plan_port_contract_lines"]
