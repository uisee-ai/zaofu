"""Registered capabilities for safe Generic Workflow compilation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


GENERIC_WORKFLOW_CONTRACT_VERSION = "generic-workflow.v1"
GENERIC_WORKFLOW_ENTRY_EVENT = "workflow.invoke.requested"
DEPENDENCY_BARRIER_EVENT = "workflow.dependency_barrier.satisfied"
DEPENDENCY_BARRIER_BLOCKED_EVENT = "workflow.dependency_barrier.blocked"

EVIDENCE_SYNTHESIS_PARAMETER_KEYS = frozenset({
    "artifact_kind",
    "artifact_name",
    "collector_roles",
    "scoper_role",
    "synthesizer_role",
    "verifier_role",
})

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_ARTIFACT_KIND = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,191}$")


class GenericWorkflowError(ValueError):
    """A safe Generic Workflow contract cannot be compiled."""


@dataclass(frozen=True)
class RegisteredOperation:
    operation_id: str
    topologies: tuple[str, ...]
    effect: str
    result_semantics: str
    independent_verify: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "topologies": list(self.topologies),
            "effect": self.effect,
            "result_semantics": self.result_semantics,
            "independent_verify": self.independent_verify,
        }


REGISTERED_OPERATIONS: dict[str, RegisteredOperation] = {
    "agent.read": RegisteredOperation(
        "agent.read",
        ("fanout_reader",),
        "read_only",
        "artifact_production",
    ),
    "agent.synthesize": RegisteredOperation(
        "agent.synthesize",
        ("fanout_reader",),
        "artifact_write",
        "artifact_production",
    ),
    "agent.verify": RegisteredOperation(
        "agent.verify",
        ("fanout_reader",),
        "read_only",
        "subject_gate",
        independent_verify=True,
    ),
    "agent.write": RegisteredOperation(
        "agent.write",
        ("fanout_writer_scoped",),
        "source_write",
        "artifact_production",
    ),
}

COMPLETION_PROFILES: dict[str, dict[str, Any]] = {
    "software_delivery": {
        "candidate_required": True,
        "independent_verify_required": True,
    },
    "artifact_delivery": {
        "candidate_required": False,
        "independent_verify_required": True,
    },
}

REGISTERED_TEMPLATES: dict[str, dict[str, tuple[str, ...]]] = {
    "bounded-agent-v1": {
        "intents": ("bounded_execution",),
        "completion_profiles": ("artifact_delivery",),
    },
    "evidence-synthesis-v1": {
        "intents": ("research",),
        "completion_profiles": ("artifact_delivery",),
    },
    "generic-delivery-v1": {
        "intents": ("delivery", "implementation"),
        "completion_profiles": ("software_delivery",),
    },
}


def generic_workflow_catalog_projection() -> dict[str, Any]:
    return {
        "schema_version": "generic-workflow-catalog.v1",
        "contract_version": GENERIC_WORKFLOW_CONTRACT_VERSION,
        "entry_event": GENERIC_WORKFLOW_ENTRY_EVENT,
        "operations": {
            key: value.to_dict()
            for key, value in sorted(REGISTERED_OPERATIONS.items())
        },
        "completion_profiles": {
            key: dict(value)
            for key, value in sorted(COMPLETION_PROFILES.items())
        },
        "templates": {
            key: {
                "intents": list(value["intents"]),
                "completion_profiles": list(value["completion_profiles"]),
                **({
                    "parameters": {
                        "required": sorted(
                            EVIDENCE_SYNTHESIS_PARAMETER_KEYS
                            - {"artifact_kind", "artifact_name"}
                        ),
                        "optional": ["artifact_kind", "artifact_name"],
                        "collector_roles": {
                            "min_items": 2,
                            "max_items": 8,
                        },
                    },
                } if key == "evidence-synthesis-v1" else {}),
                **({"parameters": {
                    "required": ["executor_role", "verifier_role"],
                    "optional": ["deadline_seconds"],
                    "deadline_seconds": {"minimum": 1, "maximum": 3600, "default": 600},
                }} if key == "bounded-agent-v1" else {}),
            }
            for key, value in sorted(REGISTERED_TEMPLATES.items())
        },
    }


def bounded_identifier(value: Any, context: str) -> str:
    text = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(text):
        raise GenericWorkflowError(
            f"{context} must be a bounded identifier"
        )
    return text


def bounded_artifact_kind(value: Any, context: str) -> str:
    text = str(value or "").strip()
    if not _ARTIFACT_KIND.fullmatch(text):
        raise GenericWorkflowError(
            f"{context} must be a bounded artifact kind"
        )
    return text


__all__ = [
    "COMPLETION_PROFILES",
    "DEPENDENCY_BARRIER_BLOCKED_EVENT",
    "DEPENDENCY_BARRIER_EVENT",
    "EVIDENCE_SYNTHESIS_PARAMETER_KEYS",
    "GENERIC_WORKFLOW_CONTRACT_VERSION",
    "GENERIC_WORKFLOW_ENTRY_EVENT",
    "GenericWorkflowError",
    "REGISTERED_OPERATIONS",
    "REGISTERED_TEMPLATES",
    "RegisteredOperation",
    "bounded_artifact_kind",
    "bounded_identifier",
    "generic_workflow_catalog_projection",
]
