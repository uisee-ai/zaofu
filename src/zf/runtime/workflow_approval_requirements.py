"""Mechanical compilation of an agent's source-bound human review decision."""
from __future__ import annotations

from typing import Any, Mapping


def validate_approval_requirements(parameters: Mapping, requirement: Mapping, *, required: bool = False) -> dict:
    policy = parameters.get("approval_requirements")
    if policy is None and not required:
        return {}
    if (not isinstance(policy, dict) or set(policy) != {"plan", "source_quotes"}
            or policy.get("plan") not in ("required", "not_requested")):
        raise ValueError("approval_requirements must explicitly select plan required/not_requested and source_quotes")
    quotes = policy["source_quotes"]
    if (not isinstance(quotes, list) or len(quotes) > 16
            or any(not isinstance(q, str) or not q.strip() or len(q) > 4096 for q in quotes)
            or (policy["plan"] == "required" and not quotes)):
        raise ValueError("required plan approval must cite bounded owner source_quotes")

    def text_leaves(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [s for item in value for s in text_leaves(item)]
        if isinstance(value, dict):
            return [s for item in value.values() for s in text_leaves(item)]
        return []

    sources = [s for key in ("objective", "acceptance", "constraints", "scope", "clarification_answers")
               for s in text_leaves(requirement.get(key))]
    if any(not any(quote in source for source in sources) for quote in quotes):
        raise ValueError("approval source_quotes are not bound to this Requirement")
    return dict(policy)


def enforce_approval_requirements(config: Any, parameters: Mapping) -> None:
    policy = parameters.get("approval_requirements") or {}
    if policy.get("plan") == "required" and not config.workflow.plan_approval_enabled:
        raise ValueError("required owner plan approval cannot be disabled by configuration")


def load_approval_bound_config(path, parameters: Mapping):
    from zf.core.config.loader import load_config
    from zf.runtime.workflow_proposal import WorkflowProposalError

    config = load_config(path)
    try:
        enforce_approval_requirements(config, parameters)
    except ValueError as exc:
        raise WorkflowProposalError(str(exc)) from exc
    return config
