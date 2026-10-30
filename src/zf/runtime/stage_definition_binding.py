"""Immutable registered-stage bindings carried by existing workflow operations."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from zf.core.config import schema
from zf.runtime.call_result_envelope import canonical_json_bytes, write_immutable_json_sidecar
from zf.runtime.run_contract import stable_json_sha256
from zf.runtime.sidecar_refs import DEFAULT_HYDRATE_LIMIT_BYTES, hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


SCHEMA = "stage-definition-binding.v1"


def pin_stage_definition(
    state_dir: Path, config: Any, pattern_id: str, *, project_root: Path | None = None,
) -> dict:
    stage = next((s for s in config.workflow.stages if s.id == pattern_id), None)
    if stage is None:
        raise ValueError(f"stage definition missing: {pattern_id}")
    refs = set(stage.roles) | {c.role_instance or c.role for c in stage.children}
    if stage.aggregate.synth_role:
        refs.add(stage.aggregate.synth_role)
    roles = {}
    bound_roles = []
    for ref in sorted(refs):
        matches = [r for r in config.roles if ref in {r.name, r.instance_id}]
        if not matches or any(r.role_kind != "reader" for r in matches):
            raise ValueError(f"stage binding requires configured reader: {ref}")
        # Role/session authority remains with its existing owner. Persist a
        # fingerprint, never a second copy of credentials or tool authority.
        roles[ref] = [stable_json_sha256(asdict(r)) for r in matches]
        bound_roles.extend(matches)
    from zf.runtime.stage_method_binding import pin_stage_methods

    methods = pin_stage_methods(
        state_dir=state_dir, project_root=project_root, config=config, roles=bound_roles,
    )
    profiles = _profile_definitions(config, bound_roles)
    definition = {"schema_version": SCHEMA, "stage": asdict(stage), "role_digests": roles,
                  "method_packages": methods, "execution_profiles": profiles}
    if len(canonical_json_bytes(definition)) > DEFAULT_HYDRATE_LIMIT_BYTES:
        raise ValueError("stage definition exceeds recoverable snapshot limit")
    return write_immutable_json_sidecar(
        state_dir, definition,
        root="workflow-definitions", kind="stage_definition", schema_version=SCHEMA,
        created_by="kernel",
    )


def restore_stage_definition(state_dir: Path, config: Any, descriptor: Mapping) -> schema.WorkflowStageConfig:
    body = hydrate_sidecar_ref(state_dir, dict(descriptor)).payload
    if not isinstance(body, dict) or body.get("schema_version") != SCHEMA:
        raise ValueError("invalid stage definition schema")
    from zf.runtime.stage_method_binding import check_stage_methods

    check_stage_methods(body.get("method_packages") or {})
    for ref, expected in body["role_digests"].items():
        actual = [stable_json_sha256(asdict(r)) for r in config.roles if ref in {r.name, r.instance_id}]
        if actual != expected:
            raise ValueError(f"stage binding role capability changed or missing: {ref}")
    roles = [r for r in config.roles if {r.name, r.instance_id}.intersection(body["role_digests"])]
    if _profile_definitions(config, roles) != body.get("execution_profiles", {}):
        raise ValueError("stage binding execution profile changed or missing")
    raw = dict(body["stage"])
    raw["aggregate"] = schema.FanoutAggregateConfig(**raw["aggregate"])
    raw["assignment"] = schema.FanoutAssignmentConfig(**raw["assignment"])
    raw["children"] = [schema.FanoutChildConfig(**c) for c in raw["children"]]
    for key in ("input_ports", "output_ports"):
        raw[key] = [schema.WorkflowPortConfig(**p) for p in raw[key]]
    for key in ("on_reject", "on_fail"):
        raw[key] = schema.WorkflowStageBackedgeConfig(**raw[key])
    criteria = dict(raw["criteria"])
    criteria["output"] = schema.WorkflowStageOutputConfig(**criteria["output"])
    criteria["retry"] = schema.WorkflowStageRetryPolicyConfig(**criteria["retry"])
    raw["criteria"] = schema.WorkflowStageCriteriaConfig(**criteria)
    stage = schema.WorkflowStageConfig(**raw)
    if stage.topology != "fanout_reader":
        raise ValueError("bound stage is not a reader pattern")
    return stage


def bound_operation_stage(
    state_dir: Path, event_log: Any, config: Any, payload: Mapping,
) -> schema.WorkflowStageConfig | None:
    """Hydrate only a canonical operation's binding, not a caller-supplied ref."""
    operation_id = str(payload.get("workflow_operation_id") or "")
    if not operation_id:
        return None
    operation = load_workflow_operation(event_log, operation_id)
    if not operation or operation.get("operation_type") != "dynamic_read_only_workflow":
        return None
    from zf.runtime.dynamic_continuation_lifecycle import parent_fence
    from zf.runtime.workflow_operation import reduce_workflow_operations

    reason, _ = parent_fence(reduce_workflow_operations(event_log.read_all()), operation_id)
    if reason:
        raise ValueError(reason)
    if payload.get("workflow_operation_request_hash") != operation.get("request_hash"):
        raise ValueError("stage binding operation hash mismatch")
    request = hydrate_sidecar_ref(state_dir, dict(operation["request_ref"])).payload["request"]
    for key in ("workflow_run_id", "task_id", "pattern_id"):
        if str(request.get(key) or "") != str(payload.get(key) or ""):
            raise ValueError(f"stage binding identity mismatch: {key}")
    descriptor = request.get("stage_definition_ref")
    if not descriptor:
        return None  # Pre-binding operations keep their existing recovery path.
    stage = restore_stage_definition(state_dir, config, descriptor)
    if stage.id != request["pattern_id"]:
        raise ValueError("stage binding pattern mismatch")
    return stage


def bound_stage(runtime: Any, payload: Mapping) -> schema.WorkflowStageConfig | None:
    if not payload.get("workflow_operation_id"):
        return None
    return bound_operation_stage(runtime.state_dir, runtime.event_log, runtime.config, payload)


def stage_for_payload(runtime: Any, stage_id: str, payload: Mapping) -> schema.WorkflowStageConfig | None:
    frozen = bound_stage(runtime, payload)
    if frozen is not None:
        return frozen
    workflow = getattr(getattr(runtime, "config", None), "workflow", None)
    return next((s for s in getattr(workflow, "stages", []) if s.id == stage_id), None)


def _profile_definitions(config: Any, roles: list) -> dict:
    from zf.runtime.execution_profiles import execution_profile_catalog

    catalog = execution_profile_catalog(config)
    definitions = {}
    for role in roles:
        for key in {role.execution.default_profile, *role.execution.profile_allowlist}:
            profile = catalog.get(key)
            if profile is None or profile.strategy != "direct":
                raise ValueError(f"stage binding requires a registered direct profile: {key}")
            definitions[key] = asdict(profile)
    return definitions


def declared_reader_stage(
    runtime: Any, event: Any, payload: Mapping, task_id: str,
) -> schema.WorkflowStageConfig | WorkflowRuntimeDecision | None:
    pattern_id = str(payload.get("pattern_id") or "").strip()
    if not pattern_id:
        return None
    try:
        stage = bound_stage(runtime, payload) or runtime._workflow_stage_by_id(pattern_id)
    except ValueError as exc:
        runtime._emit_task_fanout_rejected(event, str(exc))
        return WorkflowRuntimeDecision(action="block", task_id=task_id, reason=str(exc))
    if stage is not None and stage.topology != "fanout_reader":
        runtime._emit_task_fanout_rejected(
            event, "declared workflow pattern is not an external reader entry",
            expected="topology=fanout_reader", actual=f"topology={stage.topology or 'unset'}",
        )
        return WorkflowRuntimeDecision(
            action="block", task_id=task_id,
            reason=f"unsupported declared workflow pattern: {pattern_id}",
        )
    return stage


def fanout_timeout_seconds(
    runtime: Any, stage_id: str, manifest: dict | None, default: int,
) -> int:
    stage = bound_stage(runtime, (manifest or {}).get("trigger_payload") or {})
    if stage is None:
        stage = next((s for s in runtime.config.workflow.stages if s.id == stage_id), None)
    # Every fanout needs a deadline, including legacy definitions without one.
    return int(stage.timeout_seconds or default) if stage else default
