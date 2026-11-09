"""Typed semantic contracts emitted by the Orchestrator Agent.

These validators only enforce mechanical shape, identity and reference
integrity.  They do not judge whether an orchestration proposal is good.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping


RUN_ORCHESTRATION_PLAN_SCHEMA = "run-orchestration-plan.v1"
ORCHESTRATION_DECISION_SCHEMA = "orchestration-decision.v1"
ORCHESTRATION_DELTA_SCHEMA = "orchestration-delta.v1"
ORCHESTRATION_RESULT_SCHEMA = "orchestration-result.v1"
OWNER_DELIVERY_NARRATIVE_SCHEMA = "owner-delivery-narrative.v1"

CHECKPOINT_ACTIONS: dict[str, frozenset[str]] = {
    "run_start": frozenset({"adopt", "revise", "clarify", "block"}),
    "pre_impl": frozenset({"adopt", "revise", "clarify", "block"}),
    "plan_candidate": frozenset({"adopt", "revise", "clarify", "block"}),
    "stage_barrier": frozenset({
        "continue", "replan", "aggregate", "partial", "halt", "escalate",
    }),
    "semantic_failure": frozenset({
        "continue", "rework", "return_to_plan", "rebind", "invalidate",
        "halt", "escalate",
    }),
    "goal_revision": frozenset({
        "revise", "replan", "invalidate", "halt", "escalate",
    }),
    "pre_closeout": frozenset({
        "continue", "aggregate", "partial", "halt", "escalate",
    }),
    "owner_delivery": frozenset({"aggregate", "partial", "escalate"}),
}

_DIGEST = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_DIRECTIVE_ACTIONS = CHECKPOINT_ACTIONS["semantic_failure"] | frozenset({
    "adopt", "revise", "clarify", "block", "aggregate", "partial",
})
_TARGETED_ACTIONS = frozenset({"rework", "rebind", "invalidate"})
_EXACT_TARGET_ACTIONS = _TARGETED_ACTIONS | frozenset({"return_to_plan"})


class OrchestratorAgentContractError(ValueError):
    """A semantic proposal cannot enter deterministic admission."""


def normalize_run_orchestration_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    body = _object(value, "run orchestration plan")
    _schema(body, RUN_ORCHESTRATION_PLAN_SCHEMA)
    identity = _required_object(body, "identity")
    _required_strings(
        identity,
        "operation_id",
        "workflow_run_id",
        "goal_id",
        "effective_config_digest",
        "run_contract_ref",
        "run_contract_digest",
    )
    _digest_value(identity["effective_config_digest"], "effective_config_digest")
    _digest_value(identity["run_contract_digest"], "run_contract_digest")
    revision = identity.get("plan_revision")
    if not isinstance(revision, int) or revision < 1:
        raise OrchestratorAgentContractError("identity.plan_revision must be >= 1")
    goal = _required_object(body, "goal_model")
    _required_strings(goal, "objective")
    _string_list(goal, "mandatory_claims", required=True)
    graph = _required_object(body, "graph")
    units = _object_list(graph, "work_units", required=True)
    unit_ids = {
        _required_string(unit, "work_unit_id", "graph.work_units") for unit in units
    }
    if len(unit_ids) != len(units):
        raise OrchestratorAgentContractError("graph.work_units ids must be unique")
    edges = _object_list(graph, "edges")
    adjacency = {unit_id: set() for unit_id in unit_ids}
    for edge in edges:
        source = _required_string(edge, "from", "graph.edges")
        target = _required_string(edge, "to", "graph.edges")
        if source not in unit_ids or target not in unit_ids or source == target:
            raise OrchestratorAgentContractError(
                "graph edge endpoints must reference distinct work units"
            )
        adjacency[source].add(target)
    if _has_cycle(adjacency):
        raise OrchestratorAgentContractError("graph.edges must form an acyclic DAG")
    delegation_units: set[str] = set()
    for row in _object_list(body, "delegation"):
        rank = row.get("dispatch_rank")
        if rank is not None and (type(rank) is not int or rank < 0):
            raise OrchestratorAgentContractError("delegation.dispatch_rank must be a nonnegative integer")
        work_unit_id = _required_string(row, "work_unit_id", "delegation")
        if work_unit_id not in unit_ids:
            raise OrchestratorAgentContractError(
                "delegation references unknown work unit"
            )
        if work_unit_id in delegation_units:
            raise OrchestratorAgentContractError(
                "delegation work_unit_id values must be unique"
            )
        delegation_units.add(work_unit_id)
        _string_list(row, "capability_refs")
        _string_list(row, "preferred_role_refs", required=True)
        _string_list(row, "skill_refs")
    context_route_units: set[str] = set()
    for row in _object_list(body, "context_routes"):
        work_unit_id = _required_string(row, "work_unit_id", "context_routes")
        if work_unit_id not in unit_ids:
            raise OrchestratorAgentContractError(
                "context route references unknown work unit"
            )
        if work_unit_id in context_route_units:
            raise OrchestratorAgentContractError(
                "context route work_unit_id values must be unique"
            )
        context_route_units.add(work_unit_id)
        _descriptor_list(row, "required_sources")
    return deepcopy(body)


def normalize_orchestration_decision(value: Mapping[str, Any]) -> dict[str, Any]:
    body = _object(value, "orchestration decision")
    _schema(body, ORCHESTRATION_DECISION_SCHEMA)
    identity = _required_object(body, "identity")
    _required_strings(
        identity,
        "operation_id",
        "workflow_run_id",
        "checkpoint",
        "input_digest",
        "effective_config_digest",
    )
    checkpoint = str(identity["checkpoint"])
    if checkpoint not in CHECKPOINT_ACTIONS:
        raise OrchestratorAgentContractError(
            f"unsupported orchestration checkpoint {checkpoint!r}"
        )
    _digest_value(identity["input_digest"], "identity.input_digest")
    _digest_value(
        identity["effective_config_digest"],
        "identity.effective_config_digest",
    )
    if checkpoint in {
        "pre_impl", "plan_candidate", "semantic_failure", "pre_closeout",
    }:
        _required_strings(
            identity,
            "plan_artifact_package_ref",
            "plan_artifact_package_digest",
            "task_map_generation",
        )
        _digest_value(
            identity["plan_artifact_package_digest"],
            "identity.plan_artifact_package_digest",
        )
    decision = _required_string(body, "decision", "orchestration decision")
    if decision not in CHECKPOINT_ACTIONS[checkpoint]:
        raise OrchestratorAgentContractError(
            f"decision {decision!r} is not allowed at {checkpoint!r}"
        )
    reason_codes = _string_list(body, "reason_codes", required=True)
    if len(reason_codes) != len(set(reason_codes)):
        raise OrchestratorAgentContractError("reason_codes must be unique")
    summary = str(body.get("summary") or "").strip()
    # orchestration-decision.v1 historically omitted summary, and legacy
    # result adapters may materialize that omission as an empty string.
    # Preserve admission compatibility while making it fail-visible.
    body = {
        **body,
        "summary": summary,
        "explanation_status": "complete" if summary else "degraded",
    }
    affected_work_units = _string_list(body, "affected_work_units")
    if len(affected_work_units) > 8:
        raise OrchestratorAgentContractError(
            "affected_work_units exceeds the bounded limit of 8"
        )
    delta = body.get("delta")
    if delta is not None:
        normalized_delta = normalize_orchestration_delta(delta)
        delta_identity = normalized_delta["identity"]
        for key in ("operation_id", "workflow_run_id", "checkpoint", "input_digest"):
            if str(delta_identity.get(key) or "") != str(identity.get(key) or ""):
                raise OrchestratorAgentContractError(
                    f"delta identity mismatch for {key}"
                )
        body = {**body, "delta": normalized_delta}
    if decision in {"revise", "replan", "rework", "rebind", "invalidate"} and delta is None:
        raise OrchestratorAgentContractError(
            f"decision {decision!r} requires an orchestration delta"
        )
    run_plan = body.get("run_plan")
    if checkpoint in {"run_start", "pre_impl"} and decision == "adopt":
        if not isinstance(run_plan, Mapping):
            raise OrchestratorAgentContractError(
                f"{checkpoint} adopt requires run_plan"
            )
        normalized_plan = normalize_run_orchestration_plan(run_plan)
        for key in ("operation_id", "workflow_run_id", "effective_config_digest"):
            if str(normalized_plan["identity"].get(key) or "") != str(
                identity.get(key) or ""
            ):
                raise OrchestratorAgentContractError(
                    f"run_plan identity mismatch for {key}"
                )
        body = {**body, "run_plan": normalized_plan}
    aggregation_result = body.get("aggregation_result")
    if checkpoint in {"stage_barrier", "pre_closeout"}:
        if not isinstance(aggregation_result, Mapping):
            raise OrchestratorAgentContractError(
                f"{checkpoint} requires aggregation_result"
            )
        normalized_result = normalize_orchestration_result(aggregation_result)
        result_identity = normalized_result["identity"]
        for key in ("operation_id", "workflow_run_id", "checkpoint"):
            if str(result_identity.get(key) or "") != str(identity.get(key) or ""):
                raise OrchestratorAgentContractError(
                    f"aggregation_result identity mismatch for {key}"
                )
        if str(normalized_result.get("recommendation") or "") != decision:
            raise OrchestratorAgentContractError(
                "aggregation_result recommendation must match decision"
            )
        body = {**body, "aggregation_result": normalized_result}
    return deepcopy(body)


def _has_cycle(adjacency: Mapping[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(target) for target in adjacency.get(node, set())):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in adjacency)


def normalize_orchestration_delta(value: Mapping[str, Any]) -> dict[str, Any]:
    body = _object(value, "orchestration delta")
    _schema(body, ORCHESTRATION_DELTA_SCHEMA)
    identity = _required_object(body, "identity")
    _required_strings(
        identity,
        "operation_id",
        "workflow_run_id",
        "checkpoint",
        "input_digest",
    )
    checkpoint = str(identity["checkpoint"])
    if checkpoint not in CHECKPOINT_ACTIONS:
        raise OrchestratorAgentContractError(
            f"unsupported orchestration checkpoint {checkpoint!r}"
        )
    _digest_value(identity["input_digest"], "delta.identity.input_digest")
    directives = _object_list(body, "directives", required=True)
    if len(directives) > 8:
        raise OrchestratorAgentContractError(
            "orchestration delta exceeds the bounded limit of 8 directives"
        )
    directive_ids: set[str] = set()
    for directive in directives:
        directive_id = _required_string(directive, "directive_id", "directives")
        if directive_id in directive_ids:
            raise OrchestratorAgentContractError("directive ids must be unique")
        directive_ids.add(directive_id)
        action = _required_string(directive, "action", "directives")
        if action not in _DIRECTIVE_ACTIONS:
            raise OrchestratorAgentContractError(
                f"unsupported orchestration directive action {action!r}"
            )
        target = directive.get("target")
        if action in _EXACT_TARGET_ACTIONS:
            if not isinstance(target, Mapping):
                raise OrchestratorAgentContractError(
                    f"directive {directive_id!r} requires a target"
                )
            if not any(
                str(target.get(key) or "").strip()
                for key in ("work_unit_id", "task_id", "stage_id", "attempt_id")
            ):
                raise OrchestratorAgentContractError(
                    f"directive {directive_id!r} target is empty"
                )
            if action in _EXACT_TARGET_ACTIONS:
                missing = [
                    key
                    for key in (
                        "task_id",
                        "stage_id",
                        "attempt_id",
                        "role_instance",
                    )
                    if not str(target.get(key) or "").strip()
                ]
                if missing:
                    raise OrchestratorAgentContractError(
                        f"directive {directive_id!r} exact target missing: "
                        + ", ".join(missing)
                    )
        _descriptor_list(directive, "basis_refs")
        if action in _EXACT_TARGET_ACTIONS:
            _string_list(directive, "required_actions", required=True)
        for key in ("reuse_refs", "invalidate_refs"):
            _descriptor_list(directive, key)
    return deepcopy(body)


def normalize_orchestration_result(value: Mapping[str, Any]) -> dict[str, Any]:
    body = _object(value, "orchestration result")
    _schema(body, ORCHESTRATION_RESULT_SCHEMA)
    identity = _required_object(body, "identity")
    _required_strings(identity, "operation_id", "workflow_run_id", "checkpoint")
    input_refs = _descriptor_list(body, "input_result_refs", required=True)
    selected_refs = _descriptor_list(body, "selected_result_refs")
    rejected_refs = _descriptor_list(body, "rejected_result_refs")
    inputs = {
        (str(row.get("ref") or ""), str(row.get("sha256") or ""))
        for row in input_refs
    }
    selected = {
        (str(row.get("ref") or ""), str(row.get("sha256") or ""))
        for row in selected_refs
    }
    rejected = {
        (str(row.get("ref") or ""), str(row.get("sha256") or ""))
        for row in rejected_refs
    }
    if not selected <= inputs or not rejected <= inputs:
        raise OrchestratorAgentContractError(
            "selected/rejected result refs must come from input_result_refs"
        )
    if selected & rejected:
        raise OrchestratorAgentContractError(
            "selected and rejected result refs must be disjoint"
        )
    _string_list(body, "unclosed_claim_ids")
    _object_list(body, "provenance_map")
    _string_list(body, "remaining_uncertainty")
    recommendation = _required_string(body, "recommendation", "orchestration result")
    if recommendation not in {
        "continue", "aggregate", "partial", "replan", "halt", "escalate",
    }:
        raise OrchestratorAgentContractError(
            f"unsupported orchestration result recommendation {recommendation!r}"
        )
    return deepcopy(body)


def normalize_owner_delivery_narrative(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    body = _object(value, "owner delivery narrative")
    _schema(body, OWNER_DELIVERY_NARRATIVE_SCHEMA)
    identity = _required_object(body, "identity")
    _required_strings(
        identity,
        "operation_id",
        "workflow_run_id",
        "terminal_event_id",
        "terminal_event_type",
        "dossier_ref",
        "dossier_source_fingerprint",
    )
    _digest_value(
        identity["dossier_source_fingerprint"],
        "identity.dossier_source_fingerprint",
    )
    status = _required_string(body, "status", "owner delivery narrative")
    if status not in {"completed", "blocked"}:
        raise OrchestratorAgentContractError(
            "owner delivery narrative status must be completed or blocked"
        )
    if status == "completed":
        _required_string(
            identity,
            "completion_receipt_ref",
            "owner delivery narrative identity",
        )
        receipt_fingerprint = _required_string(
            identity,
            "completion_receipt_fingerprint",
            "owner delivery narrative identity",
        )
        _digest_value(
            receipt_fingerprint,
            "identity.completion_receipt_fingerprint",
        )
    _required_string(body, "executive_summary", "owner delivery narrative")
    outcomes = _object_list(body, "delivered_outcomes", required=status == "completed")
    for outcome in outcomes:
        _string_list(outcome, "claim_ids")
        _string_list(outcome, "task_ids")
        _string_list(outcome, "gap_ids")
        result_refs = _descriptor_list(outcome, "result_refs")
        evidence_refs = _descriptor_list(outcome, "evidence_refs")
        if not result_refs and not evidence_refs:
            raise OrchestratorAgentContractError(
                "delivered outcome requires a result or evidence reference"
            )
        _required_string(outcome, "narrative", "delivered_outcomes")
    for key in (
        "decisions_and_tradeoffs",
        "remaining_risks",
        "recommended_next_actions",
    ):
        _string_list(body, key)
    return deepcopy(body)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OrchestratorAgentContractError(f"{label} must be an object")
    return dict(value)


def _schema(body: Mapping[str, Any], expected: str) -> None:
    actual = str(body.get("schema_version") or "")
    if actual != expected:
        raise OrchestratorAgentContractError(
            f"schema_version must be {expected!r}; got {actual!r}"
        )


def _required_object(body: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = body.get(key)
    if not isinstance(value, Mapping):
        raise OrchestratorAgentContractError(f"{key} must be an object")
    return dict(value)


def _required_string(body: Mapping[str, Any], key: str, label: str) -> str:
    value = str(body.get(key) or "").strip()
    if not value:
        raise OrchestratorAgentContractError(f"{label}.{key} is required")
    return value


def _required_strings(body: Mapping[str, Any], *keys: str) -> None:
    for key in keys:
        _required_string(body, key, "identity")


def _string_list(
    body: Mapping[str, Any],
    key: str,
    *,
    required: bool = False,
) -> list[str]:
    value = body.get(key, [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise OrchestratorAgentContractError(
            f"{key} must be a list of non-empty strings"
        )
    if required and not value:
        raise OrchestratorAgentContractError(f"{key} cannot be empty")
    return [item.strip() for item in value]


def _object_list(
    body: Mapping[str, Any],
    key: str,
    *,
    required: bool = False,
) -> list[dict[str, Any]]:
    value = body.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise OrchestratorAgentContractError(f"{key} must be a list of objects")
    if required and not value:
        raise OrchestratorAgentContractError(f"{key} cannot be empty")
    return [dict(item) for item in value]


def _descriptor_list(
    body: Mapping[str, Any],
    key: str,
    *,
    required: bool = False,
) -> list[dict[str, Any]]:
    rows = _object_list(body, key, required=required)
    for row in rows:
        _required_strings(row, "ref", "sha256")
        _digest_value(row["sha256"], f"{key}.sha256")
    return rows


def _digest_value(value: Any, label: str) -> None:
    if not _DIGEST.fullmatch(str(value or "").strip().lower()):
        raise OrchestratorAgentContractError(
            f"{label} must be a sha256 digest"
        )


__all__ = [
    "CHECKPOINT_ACTIONS",
    "ORCHESTRATION_DECISION_SCHEMA",
    "ORCHESTRATION_DELTA_SCHEMA",
    "ORCHESTRATION_RESULT_SCHEMA",
    "OWNER_DELIVERY_NARRATIVE_SCHEMA",
    "RUN_ORCHESTRATION_PLAN_SCHEMA",
    "OrchestratorAgentContractError",
    "normalize_orchestration_decision",
    "normalize_orchestration_delta",
    "normalize_orchestration_result",
    "normalize_owner_delivery_narrative",
    "normalize_run_orchestration_plan",
]
