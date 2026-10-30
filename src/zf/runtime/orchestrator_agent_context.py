"""Canonical input compilation for one Orchestrator Agent checkpoint."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from zf.core.config.render import redact_config, renderable_config_to_primitive
from zf.runtime.artifact_read_ledger import (
    build_attempt_source_manifest,
    build_input_consumption_policy,
    write_attempt_source_manifest,
    write_input_consumption_policy,
)
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


CHECKPOINT_INPUT_SCHEMA = "orchestrator-agent-checkpoint-input.v1"
STAGE_CARD_SCHEMA = "orchestrator-agent-stage-card.v1"
COMPACT_CHECKPOINT_PACK_SCHEMA = "orchestrator-agent-compact-checkpoint-pack.v1"


@dataclass(frozen=True)
class OrchestratorAgentContext:
    input_body: dict[str, Any]
    input_ref: dict[str, Any]
    source_manifest: dict[str, Any]
    source_manifest_ref: dict[str, Any]
    read_policy: dict[str, Any]
    read_policy_ref: dict[str, Any]
    stage_card: dict[str, Any]
    stage_card_ref: dict[str, Any]
    effective_config_ref: dict[str, Any]


def build_orchestrator_agent_context(
    runtime: Any,
    *,
    checkpoint: str,
    checkpoint_policy: str,
    workflow_run_id: str,
    operation_id: str,
    attempt_id: str,
    source_event_id: str,
    payload: Mapping[str, Any],
) -> OrchestratorAgentContext:
    effective_config_ref = _effective_config_ref(runtime, payload)
    from zf.runtime.orchestrator_agent_policy import checkpoint_risk_signals

    risk_signals = checkpoint_risk_signals(checkpoint, payload)
    from zf.runtime.orchestrator_agent_execution_policy import (
        EXECUTION_CHECKPOINTS, bounded_actions, build_execution_context,
    )

    execution_mode = (
        checkpoint in EXECUTION_CHECKPOINTS
        and bounded_actions(runtime.config, str(payload.get("flow_kind") or "")) is not None
    )
    input_mode = (
        "compact"
        if (
            checkpoint == "plan_candidate"
            and checkpoint_policy == "shadow"
            and not risk_signals
        )
        else "exhaustive"
    )
    effective_config_summary_ref = (
        _effective_config_summary_ref(runtime, effective_config_ref)
        if input_mode == "compact"
        else {}
    )
    sources = _canonical_sources(
        runtime,
        payload=payload,
        effective_config_ref=effective_config_ref,
        effective_config_summary_ref=effective_config_summary_ref,
    )
    execution_ref = {}
    if execution_mode:
        input_mode = "execution"
        execution_ref = write_immutable_json_sidecar(
            runtime.state_dir,
            build_execution_context(
                runtime,
                workflow_run_id=workflow_run_id,
                payload={**dict(payload), "effective_config_ref": effective_config_ref},
            ),
            root="orchestrator-agent/execution-contexts",
            kind="orchestrator_execution_context",
            schema_version="orchestrator-execution-context.v1",
            created_by="orchestrator-agent-context",
            source_event_id=source_event_id,
        )
        _append_descriptor(sources, source_id="execution-context", kind="execution_context", descriptor=execution_ref)
    input_body = {
        "schema_version": CHECKPOINT_INPUT_SCHEMA,
        "input_mode": input_mode,
        "identity": {
            "workflow_run_id": workflow_run_id,
            "operation_id": operation_id,
            "checkpoint": checkpoint,
            "checkpoint_policy": checkpoint_policy,
            "source_event_id": source_event_id,
            "plan_revision": str(payload.get("plan_revision") or ""),
            "task_map_generation": str(
                payload.get("task_map_generation") or ""
            ),
            "feedback_revision": str(
                payload.get("feedback_revision")
                or payload.get("rework_feedback_digest")
                or ""
            ),
            "plan_artifact_package_id": str(
                payload.get("plan_artifact_package_id") or ""
            ),
            "plan_artifact_package_ref": str(
                payload.get("plan_artifact_package_ref") or ""
            ),
            "plan_artifact_package_digest": str(
                payload.get("plan_artifact_package_digest") or ""
            ),
            "effective_config_ref": str(effective_config_ref.get("ref") or ""),
            "effective_config_digest": str(
                effective_config_ref.get("sha256") or ""
            ),
            "goal_id": str(payload.get("goal_id") or ""),
            "run_contract_ref": str(payload.get("run_contract_ref") or ""),
            "run_contract_digest": str(
                payload.get("run_contract_digest") or ""
            ),
        },
        "objective": _checkpoint_objective(checkpoint),
        "flow_kind": str(payload.get("flow_kind") or ""),
        "risk_signals": list(risk_signals),
        "checkpoint_context": {
            key: str(payload.get(key) or "")
            for key in (
                "failure_fingerprint",
                "target_task_id",
                "target_stage_id",
                "target_attempt_id",
                "target_role_instance",
                "terminal_event_id",
                "terminal_event_type",
                "dossier_ref",
                "dossier_source_fingerprint",
                "completion_receipt_ref",
                "completion_receipt_fingerprint",
            )
            if str(payload.get(key) or "")
        },
        "sources": [
            {
                key: source.get(key)
                for key in (
                    "source_id",
                    "artifact_id",
                    "kind",
                    "ref",
                    "sha256",
                    "schema_version",
                )
                if source.get(key) not in (None, "")
            }
            for source in sources
        ],
        "aggregation_input_refs": [
            dict(item)
            for item in payload.get("aggregation_input_refs", [])
            if isinstance(item, Mapping)
        ],
        "constraints": {
            "semantic_only": True,
            "canonical_state_mutation": False,
            "physical_dispatch": False,
            "terminal_self_approval": False,
        },
    }
    if execution_ref:
        input_body["execution_context_ref"] = execution_ref
    input_ref = write_immutable_json_sidecar(
        runtime.state_dir,
        input_body,
        root=f"orchestrator-agent/checkpoints/{_safe(operation_id)}",
        kind="orchestrator_agent_checkpoint_input",
        schema_version=CHECKPOINT_INPUT_SCHEMA,
        created_by="orchestrator-agent-context",
        source_event_id=source_event_id,
    )
    sources.append({
        "source_id": "checkpoint-input",
        "artifact_id": Path(str(input_ref["ref"])).name,
        "kind": "orchestrator_agent_checkpoint_input",
        "ref": str(input_ref["ref"]),
        "sha256": str(input_ref["sha256"]),
        "schema_version": CHECKPOINT_INPUT_SCHEMA,
        "allowed_paths": ["$"],
    })
    if input_mode == "compact":
        compact_pack_ref = _compact_checkpoint_pack_ref(
            runtime,
            operation_id=operation_id,
            source_event_id=source_event_id,
            input_body=input_body,
            sources=sources,
        )
        sources.append({
            "source_id": "checkpoint-pack",
            "artifact_id": Path(str(compact_pack_ref["ref"])).name,
            "kind": "orchestrator_agent_compact_checkpoint_pack",
            "ref": str(compact_pack_ref["ref"]),
            "sha256": str(compact_pack_ref["sha256"]),
            "schema_version": COMPACT_CHECKPOINT_PACK_SCHEMA,
            "allowed_paths": ["$"],
        })
    manifest = build_attempt_source_manifest(
        workflow_run_id=workflow_run_id,
        task_id="",
        attempt_id=attempt_id,
        dispatch_id=attempt_id,
        sources=sources,
        metadata={
            "source_event_id": source_event_id,
            "read_purpose": f"orchestrator-agent:{checkpoint}",
            "context_policy": input_mode,
            "plan_revision": str(payload.get("plan_revision") or ""),
            "task_map_generation": str(
                payload.get("task_map_generation") or ""
            ),
            "feedback_revision": str(
                payload.get("feedback_revision")
                or payload.get("rework_feedback_digest")
                or ""
            ),
            "plan_artifact_package_id": str(
                payload.get("plan_artifact_package_id") or ""
            ),
            "plan_artifact_package_ref": str(
                payload.get("plan_artifact_package_ref") or ""
            ),
            "plan_artifact_package_digest": str(
                payload.get("plan_artifact_package_digest") or ""
            ),
        },
    )
    manifest_ref = write_attempt_source_manifest(
        runtime.state_dir,
        manifest,
        source_event_id=source_event_id,
    )
    required_sources = (
        [
            source
            for source in manifest["sources"]
            if _compact_source_required(source)
        ]
        if input_mode == "compact"
        else list(manifest["sources"])
    )
    required_reads = [
        {
            "source_id": str(source["source_id"]),
            "artifact_id": str(source["artifact_id"]),
            "artifact_sha256": str(source["sha256"]),
            "json_path": "$",
            "min_returned_bytes": 1,
            "max_items": 0,
            "max_chars": 0,
            "allow_truncated": False,
        }
        for source in required_sources
        if input_mode != "execution" or source["source_id"] in {
            "checkpoint-input", "execution-context", "effective-config",
            "semantic-failure-input", "task-recovery-context",
        }
    ]
    read_policy = build_input_consumption_policy(
        workflow_run_id=workflow_run_id,
        attempt_id=attempt_id,
        required_reads=required_reads,
    )
    read_policy_ref = write_input_consumption_policy(
        runtime.state_dir,
        read_policy,
        source_event_id=source_event_id,
    )
    stage_card = {
        "schema_version": STAGE_CARD_SCHEMA,
        "workflow_run_id": workflow_run_id,
        "operation_id": operation_id,
        "attempt_id": attempt_id,
        "checkpoint": checkpoint,
        "checkpoint_policy": checkpoint_policy,
        "input_mode": input_mode,
        "objective": input_body["objective"],
        "source_manifest_ref": manifest_ref,
        "input_consumption_policy_ref": read_policy_ref,
        "output_profile": _output_profile(checkpoint),
        "source_count": len(manifest["sources"]),
        "required_source_count": len(required_sources),
        "prohibitions": [
            "do_not_write_canonical_state",
            "do_not_dispatch_transport",
            "do_not_self_declare_terminal_success",
        ],
    }
    stage_card_ref = write_immutable_json_sidecar(
        runtime.state_dir,
        stage_card,
        root=f"orchestrator-agent/stage-cards/{_safe(operation_id)}",
        kind="orchestrator_agent_stage_card",
        schema_version=STAGE_CARD_SCHEMA,
        created_by="orchestrator-agent-context",
        source_event_id=source_event_id,
    )
    return OrchestratorAgentContext(
        input_body=input_body,
        input_ref=input_ref,
        source_manifest=manifest,
        source_manifest_ref=manifest_ref,
        read_policy=read_policy,
        read_policy_ref=read_policy_ref,
        stage_card=stage_card,
        stage_card_ref=stage_card_ref,
        effective_config_ref=effective_config_ref,
    )


def _canonical_sources(
    runtime: Any,
    *,
    payload: Mapping[str, Any],
    effective_config_ref: Mapping[str, Any],
    effective_config_summary_ref: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    _append_descriptor(
        rows,
        source_id="effective-config",
        kind="effective_config_snapshot",
        descriptor=effective_config_ref,
    )
    _append_descriptor(
        rows,
        source_id="effective-config-summary",
        kind="effective_config_summary",
        descriptor=effective_config_summary_ref,
        schema_version="effective-config-summary.v1",
    )
    package_ref = str(payload.get("plan_artifact_package_ref") or "").strip()
    package_digest = str(
        payload.get("plan_artifact_package_digest") or ""
    ).strip()
    if package_ref and package_digest:
        package_descriptor = {"ref": package_ref, "sha256": package_digest}
        package = hydrate_plan_artifact_package(
            runtime.state_dir,
            package_descriptor,
        )
        _append_descriptor(
            rows,
            source_id="plan-artifact-package",
            kind="plan_artifact_package",
            descriptor=package_descriptor,
            schema_version=str(package.get("schema_version") or ""),
        )
        _append_descriptor(
            rows,
            source_id="run-contract",
            kind="run_contract_snapshot",
            descriptor={
                "ref": str(package.get("run_contract_ref") or ""),
                "sha256": str(package.get("run_contract_sha256") or ""),
            },
        )
        for port in [
            *list(package.get("produced") or []),
            *list(package.get("inherited") or []),
        ]:
            if not isinstance(port, Mapping):
                continue
            logical_name = str(port.get("logical_name") or "").strip()
            _append_descriptor(
                rows,
                source_id=f"plan-port-{_safe(logical_name)}",
                kind=str(port.get("kind") or logical_name or "plan_port"),
                descriptor={
                    "ref": str(port.get("ref") or ""),
                    "sha256": str(port.get("sha256") or ""),
                },
                schema_version=str(port.get("schema_version") or ""),
            )
    descriptors = (
        ("run-contract", "run_contract_ref", "run_contract_sha256"),
        ("goal-claim-set", "goal_claim_set_ref", "goal_claim_set_digest"),
        ("task-map", "task_map_ref", "task_map_digest"),
        ("task-contract", "contract_snapshot_ref", "contract_snapshot_digest"),
        ("target", "target_snapshot_ref", "target_snapshot_digest"),
        ("rework-feedback", "rework_feedback_ref", "rework_feedback_digest"),
        ("parent-call-result", "parent_call_result_ref", "parent_call_result_digest"),
    )
    for source_id, ref_key, digest_key in descriptors:
        _append_descriptor(
            rows,
            source_id=source_id,
            kind=source_id,
            descriptor={
                "ref": str(payload.get(ref_key) or ""),
                "sha256": str(payload.get(digest_key) or ""),
            },
        )
    for field in ("artifact_refs", "input_refs", "result_refs", "evidence_refs"):
        values = payload.get(field)
        if not isinstance(values, list):
            continue
        for index, item in enumerate(values):
            if not isinstance(item, Mapping):
                continue
            _append_descriptor(
                rows,
                source_id=str(
                    item.get("source_id") or f"{field}-{index + 1}"
                ),
                kind=str(item.get("kind") or field.rstrip("s")),
                descriptor=item,
                schema_version=str(item.get("schema_version") or ""),
            )
    for row in rows:
        hydrate_sidecar_ref(
            runtime.state_dir,
            {"ref": str(row["ref"]), "sha256": str(row["sha256"])},
        )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row["ref"]), str(row["sha256"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _append_descriptor(
    rows: list[dict[str, Any]],
    *,
    source_id: str,
    kind: str,
    descriptor: Mapping[str, Any],
    schema_version: str = "",
) -> None:
    ref = str(descriptor.get("ref") or "").strip()
    digest = str(descriptor.get("sha256") or "").strip()
    if not ref or not digest:
        return
    rows.append({
        "source_id": source_id,
        "artifact_id": Path(ref).name,
        "kind": kind,
        "ref": ref,
        "sha256": digest,
        "schema_version": schema_version,
        "allowed_paths": ["$"],
    })


def _effective_config_ref(
    runtime: Any,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    explicit = payload.get("effective_config_ref")
    if isinstance(explicit, Mapping) and str(explicit.get("ref") or "") and str(
        explicit.get("sha256") or ""
    ):
        hydrate_sidecar_ref(runtime.state_dir, dict(explicit))
        return dict(explicit)
    body = {
        "schema_version": "effective-config-snapshot.v1",
        "source": "runtime-loaded-zf-config",
        "config": redact_config(renderable_config_to_primitive(runtime.config)),
    }
    return write_immutable_json_sidecar(
        runtime.state_dir,
        body,
        root="orchestrator-agent/effective-configs",
        kind="effective_config_snapshot",
        schema_version="effective-config-snapshot.v1",
        created_by="orchestrator-agent-context",
    )


def _effective_config_summary_ref(
    runtime: Any,
    effective_config_ref: Mapping[str, Any],
) -> dict[str, Any]:
    hydrated = hydrate_sidecar_ref(
        runtime.state_dir,
        dict(effective_config_ref),
    ).payload
    body = hydrated if isinstance(hydrated, Mapping) else {}
    config = body.get("config") if isinstance(body.get("config"), Mapping) else {}
    roles = config.get("roles") if isinstance(config.get("roles"), list) else []
    workflow = (
        config.get("workflow")
        if isinstance(config.get("workflow"), Mapping)
        else {}
    )
    summary = {
        "schema_version": "effective-config-summary.v1",
        "effective_config_ref": str(effective_config_ref.get("ref") or ""),
        "effective_config_digest": str(
            effective_config_ref.get("sha256") or ""
        ),
        "project": dict(config.get("project") or {}),
        "roles": [
            {
                key: role.get(key)
                for key in (
                    "name",
                    "instance_id",
                    "role_kind",
                    "backend",
                    "skills",
                    "triggers",
                    "publishes",
                )
                if role.get(key) not in (None, "", [])
            }
            for role in roles
            if isinstance(role, Mapping)
        ],
        "workflow": {
            key: workflow.get(key)
            for key in (
                "orchestration",
                "kind_routes",
                "dag",
                "execution_profiles",
            )
            if workflow.get(key) not in (None, "", {}, [])
        },
    }
    return write_immutable_json_sidecar(
        runtime.state_dir,
        summary,
        root="orchestrator-agent/effective-config-summaries",
        kind="effective_config_summary",
        schema_version="effective-config-summary.v1",
        created_by="orchestrator-agent-context",
    )


def _compact_checkpoint_pack_ref(
    runtime: Any,
    *,
    operation_id: str,
    source_event_id: str,
    input_body: Mapping[str, Any],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    source_index = [
        {
            **{
                key: source.get(key)
                for key in (
                    "source_id",
                    "artifact_id",
                    "kind",
                    "ref",
                    "sha256",
                    "schema_version",
                )
                if source.get(key) not in (None, "")
            },
            "consumption": (
                "required" if _compact_source_required(source) else "optional"
            ),
        }
        for source in sources
    ]
    pack = {
        "schema_version": COMPACT_CHECKPOINT_PACK_SCHEMA,
        "identity": dict(input_body.get("identity") or {}),
        "objective": str(input_body.get("objective") or ""),
        "checkpoint_context": dict(input_body.get("checkpoint_context") or {}),
        "risk_signals": list(input_body.get("risk_signals") or []),
        "constraints": dict(input_body.get("constraints") or {}),
        "source_index": source_index,
        "required_source_ids": sorted(
            str(source.get("source_id") or "")
            for source in sources
            if _compact_source_required(source)
        ),
        "optional_source_ids": sorted(
            str(source.get("source_id") or "")
            for source in sources
            if not _compact_source_required(source)
        ),
    }
    return write_immutable_json_sidecar(
        runtime.state_dir,
        pack,
        root=f"orchestrator-agent/checkpoint-packs/{_safe(operation_id)}",
        kind="orchestrator_agent_compact_checkpoint_pack",
        schema_version=COMPACT_CHECKPOINT_PACK_SCHEMA,
        created_by="orchestrator-agent-context",
        source_event_id=source_event_id,
    )


def _compact_source_required(source: Mapping[str, Any]) -> bool:
    source_id = str(source.get("source_id") or "")
    return source_id not in {"effective-config", "checkpoint-input"}


def checkpoint_input_digest(body: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _checkpoint_objective(checkpoint: str) -> str:
    return {
        "run_start": "Adopt or revise the run-level semantic orchestration plan.",
        "pre_impl": "Adopt the Plan-bound execution graph before implementation.",
        "plan_candidate": "Review the current Plan Package for semantic adoption.",
        "stage_barrier": "Resolve result conflicts and select the next semantic action.",
        "semantic_failure": "Produce a bounded target-specific semantic recovery delta.",
        "goal_revision": "Revise the semantic graph and declare exact invalidation.",
        "pre_closeout": "Aggregate admitted results and identify remaining goal gaps.",
        "owner_delivery": "Synthesize an owner narrative grounded in the factual dossier.",
    }[checkpoint]


def _output_profile(checkpoint: str) -> dict[str, str]:
    if checkpoint == "owner_delivery":
        return {
            "profile_id": "owner-delivery-narrative",
            "revision": "1",
            "schema_version": "owner-delivery-narrative.v1",
        }
    return {
        "profile_id": "orchestrator-semantic-decision",
        "revision": "1",
        "schema_version": "orchestration-decision.v1",
    }


def _safe(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in str(value)
    ).strip(".-") or "item"


__all__ = [
    "CHECKPOINT_INPUT_SCHEMA",
    "COMPACT_CHECKPOINT_PACK_SCHEMA",
    "STAGE_CARD_SCHEMA",
    "OrchestratorAgentContext",
    "build_orchestrator_agent_context",
    "checkpoint_input_digest",
]
