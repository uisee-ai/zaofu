"""Safe Generic Workflow contract compiled into canonical workflow stages."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from zf.core.workflow.generic_workflow_catalog import (
    COMPLETION_PROFILES,
    DEPENDENCY_BARRIER_BLOCKED_EVENT,
    DEPENDENCY_BARRIER_EVENT,
    GENERIC_WORKFLOW_CONTRACT_VERSION,
    GENERIC_WORKFLOW_ENTRY_EVENT,
    GenericWorkflowError,
    REGISTERED_OPERATIONS,
    REGISTERED_TEMPLATES,
    bounded_artifact_kind as _artifact_kind,
    bounded_identifier as _identifier,
    generic_workflow_catalog_projection,
)
from zf.core.workflow.generic_workflow_templates import (
    build_registered_template_spec,
)

_TOP_LEVEL_KEYS = frozenset({
    "contractVersion",
    "contract_version",
    "entry",
    "intent",
    "template",
    "completionProfile",
    "completion_profile",
    "tasks",
})
_TASK_KEYS = frozenset({
    "name",
    "operation",
    "dependencies",
    "role",
    "fanout",
    "target",
    "source",
    "criteria",
    "gateProfile",
    "gate_profile",
    "onFailure",
    "on_failure",
    "onFail",
    "on_fail",
    "onReject",
    "on_reject",
    "deadlineSeconds",
    "timeout_seconds",
    "synthesizeCanonicalTasks",
    "synthesize_canonical_tasks",
    "aggregate",
    "inputs",
    "outputs",
})
_INPUT_KEYS = frozenset({"name", "kind", "from", "required"})
_OUTPUT_KEYS = frozenset({"name", "kind"})
_COMPLETION_KEYS = frozenset({
    "id",
    "requiredArtifacts",
    "required_artifacts",
    "independentVerify",
    "independent_verify",
})


@dataclass(frozen=True)
class PreparedGenericWorkflow:
    legacy_spec: dict[str, Any]
    stage_extensions: dict[str, dict[str, Any]]
    contract: dict[str, Any]
    flow_metadata: dict[str, Any]


def is_safe_generic_workflow(spec: Mapping[str, Any]) -> bool:
    return any(
        key in spec
        for key in {
            "contractVersion",
            "contract_version",
            "intent",
            "template",
            "completionProfile",
            "completion_profile",
        }
    )


def prepare_generic_workflow(
    spec: Mapping[str, Any],
    *,
    context: str,
) -> PreparedGenericWorkflow:
    unknown = sorted(str(key) for key in spec if str(key) not in _TOP_LEVEL_KEYS)
    if unknown:
        raise GenericWorkflowError(
            f"{context}: unsupported Generic Workflow key(s) {unknown}"
        )
    version = _one_of(spec, "contractVersion", "contract_version")
    if str(version or "") != GENERIC_WORKFLOW_CONTRACT_VERSION:
        raise GenericWorkflowError(
            f"{context}: contractVersion must be "
            f"{GENERIC_WORKFLOW_CONTRACT_VERSION!r}"
        )
    intent = _identifier(spec.get("intent"), f"{context}.intent")
    template = _identifier(spec.get("template"), f"{context}.template")
    tasks = spec.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise GenericWorkflowError(f"{context}: tasks must be a non-empty list")
    entry = _identifier(spec.get("entry"), f"{context}.entry")

    names: list[str] = []
    task_by_name: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(tasks):
        if not isinstance(raw, Mapping):
            raise GenericWorkflowError(
                f"{context}.tasks[{index}] must be a mapping"
            )
        unknown_task = sorted(
            str(key) for key in raw if str(key) not in _TASK_KEYS
        )
        if unknown_task:
            raise GenericWorkflowError(
                f"{context}.tasks[{index}]: unsupported field(s) {unknown_task}"
            )
        name = _identifier(
            raw.get("name"),
            f"{context}.tasks[{index}].name",
        )
        if name in task_by_name:
            raise GenericWorkflowError(f"{context}: duplicate task {name!r}")
        names.append(name)
        task_by_name[name] = raw
    if entry not in task_by_name:
        raise GenericWorkflowError(
            f"{context}: entry {entry!r} is not a workflow task"
        )

    completion = _completion_profile(
        _one_of(spec, "completionProfile", "completion_profile"),
        context=context,
    )
    template_contract = REGISTERED_TEMPLATES.get(template)
    if template_contract is None:
        raise GenericWorkflowError(
            f"{context}: template {template!r} is not registered"
        )
    if intent not in template_contract["intents"]:
        raise GenericWorkflowError(
            f"{context}: template {template!r} does not allow intent {intent!r}"
        )
    if completion["id"] not in template_contract["completion_profiles"]:
        raise GenericWorkflowError(
            f"{context}: template {template!r} does not allow completion "
            f"profile {completion['id']!r}"
        )
    normalized_tasks: list[dict[str, Any]] = []
    outputs_by_ref: dict[str, dict[str, Any]] = {}
    for name in names:
        raw = task_by_name[name]
        operation_id = _identifier(
            raw.get("operation"),
            f"{context}.tasks[{name}].operation",
        )
        operation = REGISTERED_OPERATIONS.get(operation_id)
        if operation is None:
            raise GenericWorkflowError(
                f"{context}.tasks[{name}]: operation {operation_id!r} is not "
                "registered"
            )
        topology = _task_topology(raw, context=f"{context}.tasks[{name}]")
        if topology not in operation.topologies:
            raise GenericWorkflowError(
                f"{context}.tasks[{name}]: operation {operation_id!r} does "
                f"not allow topology {topology!r}"
            )
        if (
            operation.effect == "source_write"
            and completion["id"] != "software_delivery"
        ):
            raise GenericWorkflowError(
                f"{context}.tasks[{name}]: source-writing operation requires "
                "software_delivery"
            )
        outputs = _outputs(
            raw.get("outputs"),
            context=f"{context}.tasks[{name}].outputs",
        )
        for output in outputs:
            ref = f"{name}.{output['name']}"
            outputs_by_ref[ref] = {
                **output,
                "producer": name,
                "ref": ref,
            }
        normalized_tasks.append({
            "name": name,
            "operation": operation_id,
            "result_semantics": operation.result_semantics,
            "topology": topology,
            "roles": _task_roles(
                raw,
                context=f"{context}.tasks[{name}]",
            ),
            "dependencies": _dependencies(
                raw.get("dependencies"),
                name=name,
                known=set(names),
                context=context,
            ),
            "inputs": _inputs(
                raw.get("inputs"),
                context=f"{context}.tasks[{name}].inputs",
            ),
            "outputs": outputs,
        })

    _validate_dag(normalized_tasks, context=context)
    entry_task = next(task for task in normalized_tasks if task["name"] == entry)
    if entry_task["dependencies"]:
        raise GenericWorkflowError(
            f"{context}: entry task {entry!r} cannot declare dependencies"
        )
    _validate_ports(
        normalized_tasks,
        outputs_by_ref=outputs_by_ref,
        context=context,
    )
    _validate_completion(
        completion,
        normalized_tasks=normalized_tasks,
        outputs_by_ref=outputs_by_ref,
        context=context,
    )
    if template == "bounded-agent-v1":
        from zf.core.workflow.bounded_agent import validate_bounded_agent_contract
        validate_bounded_agent_contract(normalized_tasks, task_by_name)

    legacy_tasks: list[dict[str, Any]] = []
    extensions: dict[str, dict[str, Any]] = {}
    normalized_by_name = {
        str(item["name"]): item for item in normalized_tasks
    }
    for name in names:
        raw = task_by_name[name]
        normalized = normalized_by_name[name]
        dependencies = list(normalized["dependencies"])
        legacy = {
            key: value
            for key, value in raw.items()
            if key not in {"operation", "inputs", "outputs", "dependencies"}
        }
        aggregate = dict(legacy.get("aggregate") or {})
        aggregate.setdefault("childSuccessEvent", "workflow.child.completed")
        aggregate.setdefault("childFailureEvent", "workflow.child.failed")
        aggregate.setdefault("failureEvent", f"{name}.failed")
        legacy["aggregate"] = aggregate
        if not dependencies:
            if name != entry:
                raise GenericWorkflowError(
                    f"{context}.tasks[{name}]: non-entry task requires "
                    "dependencies"
                )
            legacy["trigger"] = GENERIC_WORKFLOW_ENTRY_EVENT
        elif len(dependencies) == 1:
            legacy["dependencies"] = dependencies
        else:
            legacy["trigger"] = DEPENDENCY_BARRIER_EVENT
        legacy_tasks.append(legacy)

        dependency_events = [
            f"{dependency}.completed" for dependency in dependencies
        ]
        dependency_failure_events = [
            f"{dependency}.failed" for dependency in dependencies
        ]
        barrier_body = {
            "stage_id": name,
            "dependencies": dependencies,
            "required_events": dependency_events,
            "failure_events": dependency_failure_events,
        }
        barrier_digest = _stable_digest(barrier_body) if len(dependencies) > 1 else ""
        extensions[name] = {
            "flow_kind": "workflow",
            "operation": str(normalized["operation"]),
            "result_semantics": str(normalized["result_semantics"]),
            "input_ports": list(normalized["inputs"]),
            "output_ports": list(normalized["outputs"]),
            "dependencies": dependencies,
            "dependency_events": dependency_events if len(dependencies) > 1 else [],
            "dependency_failure_events": (
                dependency_failure_events if len(dependencies) > 1 else []
            ),
            "dependency_barrier_id": (
                f"barrier:{name}:{barrier_digest[:16]}"
                if barrier_digest
                else ""
            ),
            "dependency_barrier_digest": barrier_digest,
        }

    contract_body = {
        "schema_version": GENERIC_WORKFLOW_CONTRACT_VERSION,
        "intent": intent,
        "template": template,
        "entry": entry,
        "completion_profile": completion,
        "tasks": normalized_tasks,
        "operation_catalog": {
            operation_id: operation.to_dict()
            for operation_id, operation in sorted(
                REGISTERED_OPERATIONS.items()
            )
        },
    }
    contract = {
        **contract_body,
        "contract_digest": _stable_digest(contract_body),
    }
    required_delivery_artifacts = [
        {
            "name": str(outputs_by_ref[ref]["name"]),
            "kind": str(outputs_by_ref[ref]["kind"]),
            "source_ref": ref,
            "required_for": "standard",
        }
        for ref in completion["required_artifacts"]
    ]
    result_protocol_mode = (
        "blocking"
        if completion["id"] == "artifact_delivery"
        else "shadow"
    )
    flow_metadata = {
        "flow_kind": "workflow",
        "intent": intent,
        "workflow_template": template,
        "generic_workflow_contract_digest": contract["contract_digest"],
        "completion_profile": completion["id"],
        "delivery_policy": "report_only",
        "completion_threshold": (
            "verified_artifacts"
            if completion["id"] == "artifact_delivery"
            else "verified_candidate"
        ),
        "result_protocol_mode": result_protocol_mode,
        "result_protocol": {
            "mode": result_protocol_mode,
            "semantic_submit_profiles": (
                {
                    "workflow-read": "blocking",
                    "artifact-delivery": "blocking",
                }
                if completion["id"] == "artifact_delivery"
                else {}
            ),
        },
        "required_delivery_artifacts": required_delivery_artifacts,
    }
    return PreparedGenericWorkflow(
        legacy_spec={"entry": entry, "tasks": legacy_tasks},
        stage_extensions=extensions,
        contract=contract,
        flow_metadata=flow_metadata,
    )


def _completion_profile(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GenericWorkflowError(
            f"{context}.completionProfile must be a mapping"
        )
    unknown = sorted(str(key) for key in value if str(key) not in _COMPLETION_KEYS)
    if unknown:
        raise GenericWorkflowError(
            f"{context}.completionProfile: unsupported field(s) {unknown}"
        )
    profile_id = _identifier(
        value.get("id"),
        f"{context}.completionProfile.id",
    )
    profile = COMPLETION_PROFILES.get(profile_id)
    if profile is None:
        raise GenericWorkflowError(
            f"{context}.completionProfile: unknown profile {profile_id!r}"
        )
    raw_artifacts = _one_of(
        value,
        "requiredArtifacts",
        "required_artifacts",
    )
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise GenericWorkflowError(
            f"{context}.completionProfile.requiredArtifacts must be a "
            "non-empty list"
        )
    artifacts = [
        _port_ref(item, f"{context}.completionProfile.requiredArtifacts")
        for item in raw_artifacts
    ]
    if len(set(artifacts)) != len(artifacts):
        raise GenericWorkflowError(
            f"{context}.completionProfile.requiredArtifacts contains duplicates"
        )
    independent = _one_of(
        value,
        "independentVerify",
        "independent_verify",
    )
    if independent is None:
        independent = bool(profile["independent_verify_required"])
    if not isinstance(independent, bool):
        raise GenericWorkflowError(
            f"{context}.completionProfile.independentVerify must be boolean"
        )
    if profile["independent_verify_required"] and not independent:
        raise GenericWorkflowError(
            f"{context}.completionProfile {profile_id!r} requires independent "
            "Verify"
        )
    return {
        "id": profile_id,
        "required_artifacts": artifacts,
        "independent_verify": independent,
        "candidate_required": bool(profile["candidate_required"]),
    }


def _dependencies(
    value: Any,
    *,
    name: str,
    known: set[str],
    context: str,
) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise GenericWorkflowError(
            f"{context}.tasks[{name}].dependencies must be a list"
        )
    dependencies = [
        _identifier(
            item,
            f"{context}.tasks[{name}].dependencies",
        )
        for item in value
    ]
    if len(dependencies) != len(set(dependencies)):
        raise GenericWorkflowError(
            f"{context}.tasks[{name}] has duplicate dependencies"
        )
    if name in dependencies:
        raise GenericWorkflowError(
            f"{context}.tasks[{name}] cannot depend on itself"
        )
    unknown = sorted(set(dependencies) - known)
    if unknown:
        raise GenericWorkflowError(
            f"{context}.tasks[{name}] references unknown dependencies {unknown}"
        )
    return dependencies


def _inputs(value: Any, *, context: str) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise GenericWorkflowError(f"{context} must be a list")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise GenericWorkflowError(f"{context}[{index}] must be a mapping")
        unknown = sorted(str(key) for key in raw if str(key) not in _INPUT_KEYS)
        if unknown:
            raise GenericWorkflowError(
                f"{context}[{index}] has unsupported field(s) {unknown}"
            )
        name = _identifier(raw.get("name"), f"{context}[{index}].name")
        if name in seen:
            raise GenericWorkflowError(f"{context} has duplicate port {name!r}")
        seen.add(name)
        required = raw.get("required", True)
        if not isinstance(required, bool):
            raise GenericWorkflowError(
                f"{context}[{index}].required must be boolean"
            )
        out.append({
            "name": name,
            "kind": _artifact_kind(
                raw.get("kind"),
                f"{context}[{index}].kind",
            ),
            "source": _port_ref(
                raw.get("from"),
                f"{context}[{index}].from",
                allow_external=True,
            ),
            "required": required,
        })
    return out


def _outputs(value: Any, *, context: str) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise GenericWorkflowError(f"{context} must be a list")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise GenericWorkflowError(f"{context}[{index}] must be a mapping")
        unknown = sorted(str(key) for key in raw if str(key) not in _OUTPUT_KEYS)
        if unknown:
            raise GenericWorkflowError(
                f"{context}[{index}] has unsupported field(s) {unknown}"
            )
        name = _identifier(raw.get("name"), f"{context}[{index}].name")
        if name in seen:
            raise GenericWorkflowError(f"{context} has duplicate port {name!r}")
        seen.add(name)
        out.append({
            "name": name,
            "kind": _artifact_kind(
                raw.get("kind"),
                f"{context}[{index}].kind",
            ),
        })
    return out


def _validate_ports(
    tasks: list[dict[str, Any]],
    *,
    outputs_by_ref: Mapping[str, Mapping[str, Any]],
    context: str,
) -> None:
    dependencies = {
        str(task["name"]): set(str(item) for item in task["dependencies"])
        for task in tasks
    }
    for task in tasks:
        task_name = str(task["name"])
        for input_port in task["inputs"]:
            source = str(input_port["source"])
            if source.startswith("external."):
                continue
            producer = outputs_by_ref.get(source)
            if producer is None:
                raise GenericWorkflowError(
                    f"{context}.tasks[{task_name}]: input {input_port['name']!r} "
                    f"references unknown output {source!r}"
                )
            producer_name = str(producer["producer"])
            if producer_name not in dependencies[task_name]:
                raise GenericWorkflowError(
                    f"{context}.tasks[{task_name}]: input {source!r} producer "
                    "must be a direct dependency"
                )
            if str(producer["kind"]) != str(input_port["kind"]):
                raise GenericWorkflowError(
                    f"{context}.tasks[{task_name}]: input {source!r} kind "
                    f"{input_port['kind']!r} does not match producer "
                    f"{producer['kind']!r}"
                )


def _validate_completion(
    completion: Mapping[str, Any],
    *,
    normalized_tasks: list[dict[str, Any]],
    outputs_by_ref: Mapping[str, Mapping[str, Any]],
    context: str,
) -> None:
    missing = sorted(
        set(str(item) for item in completion["required_artifacts"])
        - set(outputs_by_ref)
    )
    if missing:
        raise GenericWorkflowError(
            f"{context}.completionProfile references unknown outputs {missing}"
        )
    if completion["independent_verify"]:
        verify_tasks = [
            task
            for task in normalized_tasks
            if str(task["operation"]) == "agent.verify"
        ]
        if not verify_tasks:
            raise GenericWorkflowError(
                f"{context}: completion profile requires agent.verify"
            )
        required_producers = {
            str(outputs_by_ref[ref]["producer"])
            for ref in completion["required_artifacts"]
        }
        required_producer_roles = {
            str(role)
            for task in normalized_tasks
            if str(task["name"]) in required_producers
            for role in task.get("roles") or []
        }
        for verify in verify_tasks:
            if str(verify["name"]) in required_producers:
                raise GenericWorkflowError(
                    f"{context}: Verify cannot produce a required delivery "
                    "artifact"
                )
            overlapping_roles = sorted(
                set(str(role) for role in verify.get("roles") or [])
                & required_producer_roles
            )
            if overlapping_roles:
                raise GenericWorkflowError(
                    f"{context}: independent Verify role(s) "
                    f"{overlapping_roles} also produce required artifacts"
                )
        verified_sources = {
            str(input_port["source"])
            for verify in verify_tasks
            for input_port in verify["inputs"]
        }
        unverified = sorted(
            set(str(item) for item in completion["required_artifacts"])
            - verified_sources
        )
        if unverified:
            raise GenericWorkflowError(
                f"{context}: independent Verify must consume required "
                f"artifact output(s) {unverified}"
            )


def _validate_dag(tasks: list[dict[str, Any]], *, context: str) -> None:
    dependencies = {
        str(task["name"]): list(task["dependencies"])
        for task in tasks
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str, path: tuple[str, ...]) -> None:
        if name in visited:
            return
        if name in visiting:
            cycle = " -> ".join((*path, name))
            raise GenericWorkflowError(f"{context}: dependency cycle {cycle}")
        visiting.add(name)
        for dependency in dependencies[name]:
            visit(str(dependency), (*path, name))
        visiting.remove(name)
        visited.add(name)

    for name in dependencies:
        visit(name, ())


def _task_topology(task: Mapping[str, Any], *, context: str) -> str:
    role = str(task.get("role") or "").strip()
    fanout = task.get("fanout")
    if role and fanout:
        raise GenericWorkflowError(f"{context}: role and fanout are exclusive")
    if role:
        return "fanout_reader"
    if not isinstance(fanout, Mapping):
        raise GenericWorkflowError(f"{context}: requires role or fanout")
    if fanout.get("fromTaskMap") or fanout.get("from_task_map"):
        return "fanout_writer_scoped"
    if fanout.get("roles"):
        return "fanout_reader"
    raise GenericWorkflowError(
        f"{context}.fanout requires roles or fromTaskMap"
    )


def _task_roles(task: Mapping[str, Any], *, context: str) -> list[str]:
    role = str(task.get("role") or "").strip()
    if role:
        return [_identifier(role, f"{context}.role")]
    fanout = task.get("fanout")
    if not isinstance(fanout, Mapping):
        return []
    raw_roles = fanout.get("roles")
    if raw_roles in (None, ""):
        return []
    if not isinstance(raw_roles, list) or not raw_roles:
        raise GenericWorkflowError(f"{context}.fanout.roles must be a list")
    roles = [
        _identifier(item, f"{context}.fanout.roles")
        for item in raw_roles
    ]
    if len(roles) != len(set(roles)):
        raise GenericWorkflowError(
            f"{context}.fanout.roles contains duplicates"
        )
    return roles


def _port_ref(
    value: Any,
    context: str,
    *,
    allow_external: bool = False,
) -> str:
    text = str(value or "").strip()
    if allow_external and text.startswith("external."):
        _identifier(text, context)
        return text
    parts = text.split(".")
    if len(parts) != 2:
        raise GenericWorkflowError(
            f"{context} must use task.output"
        )
    _identifier(parts[0], context)
    _identifier(parts[1], context)
    return text


def _one_of(value: Mapping[str, Any], first: str, second: str) -> Any:
    if first in value and second in value:
        raise GenericWorkflowError(
            f"cannot declare both {first!r} and {second!r}"
        )
    return value.get(first) if first in value else value.get(second)


def _stable_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "COMPLETION_PROFILES",
    "DEPENDENCY_BARRIER_BLOCKED_EVENT",
    "DEPENDENCY_BARRIER_EVENT",
    "GENERIC_WORKFLOW_CONTRACT_VERSION",
    "GENERIC_WORKFLOW_ENTRY_EVENT",
    "GenericWorkflowError",
    "PreparedGenericWorkflow",
    "REGISTERED_OPERATIONS",
    "REGISTERED_TEMPLATES",
    "build_registered_template_spec",
    "generic_workflow_catalog_projection",
    "is_safe_generic_workflow",
    "prepare_generic_workflow",
]
