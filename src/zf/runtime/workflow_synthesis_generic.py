"""Admission rules for registered Generic Workflow synthesis."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from zf.core.workflow.generic_workflow import (
    GenericWorkflowError,
    build_registered_template_spec,
)
from zf.core.workflow.bounded_agent import PARAMETERS as BOUNDED_PARAMETERS, TEMPLATE as BOUNDED_TEMPLATE
from zf.runtime.workflow_registered_controller import CONTROLLER_PARAMETERS


FLOW_PARAMETER_KEYS = CONTROLLER_PARAMETERS | frozenset({
    "artifact_kind",
    "artifact_name",
    "collector_roles",
    "lanes",
    "pattern_id",
    "scoper_role",
    "strictness",
    "task_pipeline",
    "synthesizer_role",
    "verifier_role",
    "executor_role",
    "deadline_seconds",
})

_GENERIC_PARAMETER_KEYS = frozenset({
    "artifact_kind",
    "artifact_name",
    "collector_roles",
    "scoper_role",
    "synthesizer_role",
    "verifier_role",
})
_CONTROLLER_PARAMETER_KEYS = CONTROLLER_PARAMETERS | frozenset({
    "lanes",
    "strictness",
    "pattern_id",
    "task_pipeline",
})


class GenericWorkflowSynthesisError(ValueError):
    pass


def canonical_flow_family(value: Any) -> str:
    text = str(value or "").strip()
    return "Workflow" if text.lower() == "workflow" else text


def admit_generic_workflow_selection(
    *,
    flow_family: str,
    intent: str,
    template: str,
    parameters: Mapping[str, Any],
    completion: Mapping[str, Any],
    required_artifacts: list[str] | None,
    catalog: Mapping[str, Any],
    requested_roles: list[str],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if flow_family != "Workflow":
        if intent or template:
            raise GenericWorkflowSynthesisError(
                "intent/template are only valid for Generic Workflow synthesis"
            )
        unsupported = sorted(set(parameters) - _CONTROLLER_PARAMETER_KEYS)
        if unsupported:
            raise GenericWorkflowSynthesisError(
                "controller FlowSpec parameters contain unsupported fields: "
                + ", ".join(unsupported)
            )
        return {}, dict(completion), ""

    allowed_parameters = BOUNDED_PARAMETERS if template == BOUNDED_TEMPLATE else _GENERIC_PARAMETER_KEYS
    unsupported = sorted(set(parameters) - allowed_parameters)
    if unsupported:
        raise GenericWorkflowSynthesisError(
            "Generic Workflow parameters contain unsupported fields: "
            + ", ".join(unsupported)
        )
    expected_intent = "bounded_execution" if template == BOUNDED_TEMPLATE else "research"
    if intent != expected_intent:
        raise GenericWorkflowSynthesisError(
            f"Generic Workflow synthesis currently requires intent {expected_intent}"
        )
    if template not in catalog["templates"]:
        raise GenericWorkflowSynthesisError(
            f"unknown registered workflow template: {template!r}"
        )
    selected_roles = _parameter_roles(parameters)
    _require_subset("role", selected_roles, catalog["roles"])
    missing_requested = sorted(set(selected_roles) - set(requested_roles))
    if missing_requested:
        raise GenericWorkflowSynthesisError(
            "Generic Workflow parameter roles must be declared in "
            "requested_roles: " + ", ".join(missing_requested)
        )
    try:
        generic_spec = build_registered_template_spec(template, parameters)
    except GenericWorkflowError as exc:
        raise GenericWorkflowSynthesisError(str(exc)) from exc
    expected_artifacts = list(
        generic_spec["completionProfile"]["requiredArtifacts"]
    )
    if str(completion.get("id") or "") != "artifact_delivery":
        raise GenericWorkflowSynthesisError(
            f"{template} requires artifact_delivery completion"
        )
    if (
        required_artifacts is not None
        and list(required_artifacts) != expected_artifacts
    ):
        raise GenericWorkflowSynthesisError(
            "completion_profile required_artifacts do not match the "
            "registered template"
        )
    normalized_completion = {
        **dict(completion),
        "id": "artifact_delivery",
        "delivery_policy": str(
            completion.get("delivery_policy") or "report_only"
        ),
        "completion_threshold": str(
            completion.get("completion_threshold") or "verified_artifacts"
        ),
        "required_artifacts": expected_artifacts,
    }
    return (
        generic_spec,
        normalized_completion,
        _stable_mapping_digest(generic_spec),
    )


def _parameter_roles(parameters: Mapping[str, Any]) -> list[str]:
    roles = [
        str(parameters.get("executor_role") or "").strip(),
        str(parameters.get("scoper_role") or "").strip(),
        str(parameters.get("synthesizer_role") or "").strip(),
        str(parameters.get("verifier_role") or "").strip(),
    ]
    collectors = parameters.get("collector_roles")
    if isinstance(collectors, list):
        roles.extend(str(item).strip() for item in collectors)
    return list(dict.fromkeys(item for item in roles if item))


def _require_subset(
    label: str,
    requested: list[str],
    allowed: set[str],
) -> None:
    unknown = sorted(set(requested) - set(allowed))
    if unknown:
        raise GenericWorkflowSynthesisError(
            f"workflow synthesis requested unknown {label}(s): "
            + ", ".join(unknown)
        )


def _stable_mapping_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "FLOW_PARAMETER_KEYS",
    "GenericWorkflowSynthesisError",
    "admit_generic_workflow_selection",
    "canonical_flow_family",
]
