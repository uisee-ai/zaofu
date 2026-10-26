"""Bounded executor contracts compiled onto existing operation/attempt machinery."""
from __future__ import annotations

from typing import Any, Mapping

from zf.core.workflow.generic_workflow_catalog import GenericWorkflowError, bounded_identifier

TEMPLATE = "bounded-agent-v1"
PARAMETERS = frozenset({"executor_role", "verifier_role", "deadline_seconds"})


def build_bounded_agent_spec(parameters: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(parameters) - PARAMETERS
    if unknown:
        raise GenericWorkflowError(f"{TEMPLATE}: unsupported parameters {sorted(unknown)}")
    executor = bounded_identifier(parameters.get("executor_role"), "executor_role")
    verifier = bounded_identifier(parameters.get("verifier_role"), "verifier_role")
    if executor == verifier:
        raise GenericWorkflowError("bounded Agent requires an independent verifier")
    deadline = parameters.get("deadline_seconds", 600)
    if isinstance(deadline, bool) or not isinstance(deadline, int) or not 1 <= deadline <= 3600:
        raise GenericWorkflowError("deadline_seconds must be an integer in 1..3600")
    return {
        "contractVersion": "generic-workflow.v1", "intent": "bounded_execution",
        "template": TEMPLATE, "entry": "agent-execute",
        "completionProfile": {
            "id": "artifact_delivery", "requiredArtifacts": ["agent-execute.result"],
            "independentVerify": True,
        },
        "tasks": [
            {"name": "agent-execute", "operation": "agent.synthesize", "role": executor,
             "deadlineSeconds": deadline,
             "inputs": [{"name": "request", "kind": "text/request", "from": "external.request"}],
             "outputs": [{"name": "result", "kind": "report/markdown"}]},
            {"name": "agent-verify", "operation": "agent.verify", "role": verifier,
             "deadlineSeconds": deadline, "dependencies": ["agent-execute"],
             "inputs": [{"name": "result", "kind": "report/markdown", "from": "agent-execute.result"}],
             "outputs": [{"name": "verification", "kind": "verification/verdict"}]},
        ],
    }


def validate_bounded_agent_contract(tasks: list[dict[str, Any]], raw_tasks: Mapping[str, Any]) -> None:
    executors = [task for task in tasks if task["operation"] != "agent.verify"]
    verifiers = [task for task in tasks if task["operation"] == "agent.verify"]
    if len(executors) != 1 or len(verifiers) != 1:
        raise GenericWorkflowError("bounded Agent requires exactly one executor and one verifier")
    root, verify = executors[0], verifiers[0]
    if root["operation"] not in {"agent.read", "agent.synthesize"}:
        raise GenericWorkflowError("bounded Agent v1 supports artifact delivery; source writes require a delivery route")
    if root["dependencies"] or verify["dependencies"] != [root["name"]]:
        raise GenericWorkflowError("bounded Agent requires executor -> independent verifier")
    for task in tasks:
        raw = raw_tasks[task["name"]]
        deadline = raw.get("deadlineSeconds", raw.get("timeout_seconds"))
        aggregate = raw.get("aggregate") or {}
        if not isinstance(aggregate, Mapping):
            raise GenericWorkflowError("bounded Agent aggregate must be a mapping")
        if (len(task["roles"]) != 1 or aggregate.get("synthRole")
                or aggregate.get("synth_role")):
            raise GenericWorkflowError("bounded Agent stages cannot introduce additional executors")
        if isinstance(deadline, bool) or not isinstance(deadline, int) or not 1 <= deadline <= 3600:
            raise GenericWorkflowError("bounded Agent requires a deadline in 1..3600 for each operation")


def validate_bounded_agent_config(config: Any) -> None:
    """Require operative limits and explicit provider delegation opt-out."""
    contracts = getattr(config.workflow, "generic_workflows", [])
    for contract in contracts:
        if contract.get("template") != TEMPLATE:
            continue
        roles = {identity: role for role in config.roles for identity in (role.name, role.instance_id)}
        instances: set[str] = set()
        for task in contract["tasks"]:
            for identity in task["roles"]:
                role = roles.get(identity)
                if role is None or role.role_kind != "reader":
                    raise GenericWorkflowError("bounded Agent v1 requires configured reader roles")
                if role.instance_id in instances:
                    raise GenericWorkflowError("bounded Agent executor and verifier must use different role instances")
                instances.add(role.instance_id)
                if role.provider_session is None or role.provider_session.native_delegation is not False:
                    raise GenericWorkflowError("bounded Agent requires native_delegation: false")
                profile_ids = set(role.execution.profile_allowlist) | {role.execution.default_profile}
                for profile_id in profile_ids:
                    profile = config.workflow.execution_profiles.get(profile_id)
                    if (profile is None or profile.strategy != "direct" or profile.collaboration != "single"
                            or profile.limits.timeout_seconds <= 0 or profile.limits.token_budget <= 0
                            or profile.limits.max_usage_samples <= 0):
                        raise GenericWorkflowError("bounded Agent requires direct profiles with time, token and sample limits")
