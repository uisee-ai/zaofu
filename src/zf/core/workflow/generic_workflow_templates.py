"""Deterministic expansion of registered Generic Workflow templates."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.workflow.generic_workflow_catalog import (
    EVIDENCE_SYNTHESIS_PARAMETER_KEYS,
    GENERIC_WORKFLOW_CONTRACT_VERSION,
    GenericWorkflowError,
    bounded_artifact_kind,
    bounded_identifier,
)


def build_registered_template_spec(
    template: str,
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    """Expand bounded template parameters into one safe Generic FlowSpec."""

    template_id = bounded_identifier(template, "registered template")
    if template_id == "bounded-agent-v1":
        from zf.core.workflow.bounded_agent import build_bounded_agent_spec
        return build_bounded_agent_spec(parameters)
    if template_id != "evidence-synthesis-v1":
        raise GenericWorkflowError(
            f"registered template {template_id!r} has no deterministic builder"
        )
    unknown = sorted(set(parameters) - EVIDENCE_SYNTHESIS_PARAMETER_KEYS)
    if unknown:
        raise GenericWorkflowError(
            "evidence-synthesis-v1 has unsupported parameter(s) "
            f"{unknown}"
        )
    scoper_role = bounded_identifier(
        parameters.get("scoper_role"),
        "evidence-synthesis-v1.scoper_role",
    )
    synthesizer_role = bounded_identifier(
        parameters.get("synthesizer_role"),
        "evidence-synthesis-v1.synthesizer_role",
    )
    verifier_role = bounded_identifier(
        parameters.get("verifier_role"),
        "evidence-synthesis-v1.verifier_role",
    )
    raw_collectors = parameters.get("collector_roles")
    if not isinstance(raw_collectors, list):
        raise GenericWorkflowError(
            "evidence-synthesis-v1.collector_roles must be a list"
        )
    collector_roles = [
        bounded_identifier(
            role,
            "evidence-synthesis-v1.collector_roles",
        )
        for role in raw_collectors
    ]
    if not 2 <= len(collector_roles) <= 8:
        raise GenericWorkflowError(
            "evidence-synthesis-v1.collector_roles must contain 2 to 8 roles"
        )
    if len(set(collector_roles)) != len(collector_roles):
        raise GenericWorkflowError(
            "evidence-synthesis-v1.collector_roles must be unique"
        )
    if verifier_role == synthesizer_role:
        raise GenericWorkflowError(
            "evidence-synthesis-v1 verifier_role must differ from "
            "synthesizer_role"
        )
    artifact_name = bounded_identifier(
        parameters.get("artifact_name") or "report",
        "evidence-synthesis-v1.artifact_name",
    )
    artifact_kind = bounded_artifact_kind(
        parameters.get("artifact_kind") or "report/markdown",
        "evidence-synthesis-v1.artifact_kind",
    )

    collect_tasks: list[dict[str, Any]] = []
    collect_refs: list[dict[str, Any]] = []
    collect_names: list[str] = []
    for index, role in enumerate(collector_roles, start=1):
        name = f"collect-{index}"
        output_name = f"evidence-{index}"
        collect_names.append(name)
        collect_tasks.append({
            "name": name,
            "operation": "agent.read",
            "dependencies": ["scope"],
            "role": role,
            "inputs": [{
                "name": "scope",
                "kind": "research/scope",
                "from": "scope.scope",
                "required": True,
            }],
            "outputs": [{
                "name": output_name,
                "kind": "research/evidence",
            }],
        })
        collect_refs.append({
            "name": output_name,
            "kind": "research/evidence",
            "from": f"{name}.{output_name}",
            "required": True,
        })

    return {
        "contractVersion": GENERIC_WORKFLOW_CONTRACT_VERSION,
        "intent": "research",
        "template": template_id,
        "entry": "scope",
        "completionProfile": {
            "id": "artifact_delivery",
            "requiredArtifacts": [f"synthesize.{artifact_name}"],
            "independentVerify": True,
        },
        "tasks": [
            {
                "name": "scope",
                "operation": "agent.read",
                "role": scoper_role,
                "inputs": [{
                    "name": "requirement",
                    "kind": "requirement/spec",
                    "from": "external.requirement",
                    "required": True,
                }],
                "outputs": [{
                    "name": "scope",
                    "kind": "research/scope",
                }],
            },
            *collect_tasks,
            {
                "name": "synthesize",
                "operation": "agent.synthesize",
                "dependencies": collect_names,
                "role": synthesizer_role,
                "inputs": collect_refs,
                "outputs": [{
                    "name": artifact_name,
                    "kind": artifact_kind,
                }],
            },
            {
                "name": "verify",
                "operation": "agent.verify",
                "dependencies": ["synthesize"],
                "role": verifier_role,
                "inputs": [{
                    "name": artifact_name,
                    "kind": artifact_kind,
                    "from": f"synthesize.{artifact_name}",
                    "required": True,
                }],
                "outputs": [{
                    "name": "verification",
                    "kind": "verification/verdict",
                }],
            },
        ],
    }


__all__ = ["build_registered_template_spec"]
