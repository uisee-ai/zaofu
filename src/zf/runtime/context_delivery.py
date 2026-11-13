"""Session-dependent context delivery evidence.

The attempt Source Manifest remains the canonical input authority.  This
module only records how that immutable manifest was delivered to one provider
session and what a future delta selector would have chosen.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.runtime.call_result_envelope import (
    canonical_json_sha256,
    write_immutable_json_sidecar,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


CONTEXT_POLICY_SCHEMA = "context-section-policy.v1"
CONTEXT_DELIVERY_ENVELOPE_SCHEMA = "context-delivery-envelope.v1"
CONTEXT_DELIVERY_RECEIPT_SCHEMA = "context-delivery-receipt.v1"
CONTEXT_DELTA_SCHEMA = "context-section-delta.v1"
CONTEXT_RENDERER_VERSION = "source-manifest-renderer.v1"
CONTEXT_DELIVERY_MODE = "full-with-shadow-delta"

_CONTEXT_MODES = frozenset({"source_manifest", "bounded_turns", "none"})
_NON_DELTA_SOURCE_PREFIXES = (
    "candidate",
    "contract",
    "impl-self-check",
    "plan-port-",
    "plan-synth-contract",
    "rework-feedback",
    "target",
    "task-ref",
)


class ContextDeliveryError(ValueError):
    """Context delivery evidence could not be validated."""


def normalize_context_inheritance(value: Any) -> dict[str, Any]:
    """Return the stable operation-level context inheritance policy."""

    raw = dict(value) if isinstance(value, Mapping) else {}
    mode = str(raw.get("mode") or "source_manifest").strip()
    if mode not in _CONTEXT_MODES:
        raise ContextDeliveryError(
            f"unsupported context inheritance mode: {mode or '<empty>'}"
        )
    policy = {
        "schema_version": CONTEXT_POLICY_SCHEMA,
        "mode": mode,
        "renderer_version": CONTEXT_RENDERER_VERSION,
    }
    if mode == "bounded_turns":
        lineage = raw.get("lineage")
        if not isinstance(lineage, Mapping):
            raise ContextDeliveryError(
                "bounded_turns context inheritance requires lineage"
            )
        policy["lineage"] = {
            str(key): item
            for key, item in sorted(lineage.items(), key=lambda pair: str(pair[0]))
            if item not in (None, "", [], {})
        }
    return policy


def attach_context_sections(
    manifest: Mapping[str, Any],
    *,
    output_profile_id: str,
    explicit_required_reads: Iterable[Mapping[str, Any]] = (),
    context_inheritance: Any = None,
) -> dict[str, Any]:
    """Add stable, session-independent section identities to a manifest."""

    result = dict(manifest)
    sources = [
        dict(item)
        for item in result.get("sources", [])
        if isinstance(item, Mapping)
    ]
    required_keys = {
        (
            str(item.get("source_id") or ""),
            str(item.get("artifact_id") or ""),
        )
        for item in explicit_required_reads
        if isinstance(item, Mapping)
    }
    sections: list[dict[str, Any]] = []
    for source in sources:
        source_id = str(source.get("source_id") or "")
        artifact_id = str(source.get("artifact_id") or "")
        digest = str(source.get("sha256") or "")
        if not source_id or not artifact_id or not digest:
            continue
        section_group = _section_group(source_id, str(source.get("kind") or ""))
        sections.append({
            "section_id": _section_id(
                section_group=section_group,
                source_id=source_id,
                artifact_id=artifact_id,
            ),
            "section_group": section_group,
            "section_version": "1",
            "source_id": source_id,
            "artifact_id": artifact_id,
            "source_occurrence_id": str(source.get("occurrence_id") or ""),
            "current_content_digest": digest,
            "required": (source_id, artifact_id) in required_keys,
            "delta_allowed": not _is_authoritative_source(source_id),
        })
    result["context_policy"] = normalize_context_inheritance(context_inheritance)
    result["context_policy"]["output_profile_id"] = str(output_profile_id or "")
    result["context_sections"] = sorted(
        sections,
        key=lambda item: (
            str(item["section_group"]),
            str(item["section_id"]),
        ),
    )
    return result


def build_execution_binding(
    *,
    source_manifest: Mapping[str, Any],
    role_instance: str,
    provider_backend: str = "",
    permission_profile: Any = None,
) -> dict[str, Any]:
    """Bind delivery reuse to current canonical and execution identities."""

    binding = {
        "schema_version": "context-execution-binding.v1",
        "workflow_run_id": str(source_manifest.get("workflow_run_id") or ""),
        "task_id": str(source_manifest.get("task_id") or ""),
        "contract_revision": str(
            source_manifest.get("contract_revision") or ""
        ),
        "task_map_generation": str(
            source_manifest.get("task_map_generation") or ""
        ),
        "base_commit": str(source_manifest.get("base_commit") or ""),
        "target_commit": str(source_manifest.get("target_commit") or ""),
        "task_ref": str(source_manifest.get("task_ref") or ""),
        "plan_artifact_package_id": str(
            source_manifest.get("plan_artifact_package_id") or ""
        ),
        "plan_artifact_package_digest": str(
            source_manifest.get("plan_artifact_package_digest") or ""
        ),
        "role_instance": str(role_instance or ""),
        "provider_backend": str(provider_backend or ""),
        "context_mode": str(
            (
                source_manifest.get("context_policy")
                if isinstance(source_manifest.get("context_policy"), Mapping)
                else {}
            ).get("mode")
            or "source_manifest"
        ),
        "permission_profile": _stable_value(permission_profile),
    }
    binding["identity_digest"] = canonical_json_sha256(binding)
    return binding


def build_context_delivery_envelope(
    state_dir: Path,
    *,
    source_manifest: Mapping[str, Any],
    source_manifest_descriptor: Mapping[str, Any],
    workflow_run_id: str,
    operation_id: str,
    attempt_id: str,
    dispatch_id: str,
    role_instance: str,
    provider_session_id: str,
    execution_binding: Mapping[str, Any],
    previous_receipt_descriptor: Mapping[str, Any] | None = None,
    shadow_selected_section_ids: Iterable[str] | None = None,
    source_event_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write a full-delivery envelope plus a non-authoritative shadow choice."""

    previous, baseline_status = _load_compatible_receipt(
        state_dir,
        descriptor=previous_receipt_descriptor,
        workflow_run_id=workflow_run_id,
        role_instance=role_instance,
        provider_session_id=provider_session_id,
        execution_binding_digest=str(
            execution_binding.get("identity_digest") or ""
        ),
    )
    previous_sections = {
        str(item.get("section_id") or ""): dict(item)
        for item in previous.get("sections", [])
        if isinstance(item, Mapping) and str(item.get("section_id") or "")
    }
    selected = (
        {str(item) for item in shadow_selected_section_ids if str(item)}
        if shadow_selected_section_ids is not None
        else None
    )
    section_rows: list[dict[str, Any]] = []
    selector_misses: list[str] = []
    for section in source_manifest.get("context_sections", []):
        if not isinstance(section, Mapping):
            continue
        section_id = str(section.get("section_id") or "")
        current_digest = str(section.get("current_content_digest") or "")
        if not section_id or not current_digest:
            continue
        previous_section = previous_sections.get(section_id, {})
        previous_digest = str(
            previous_section.get("current_content_digest") or ""
        )
        shadow_delivery = "full"
        delta_descriptor: dict[str, Any] = {}
        if previous_digest and previous_digest == current_digest:
            shadow_delivery = "unchanged"
        elif previous_digest and bool(section.get("delta_allowed")):
            shadow_delivery = "delta"
            delta_descriptor = _write_shadow_delta(
                state_dir,
                section=section,
                previous_content_digest=previous_digest,
                source_event_id=source_event_id,
            )
        if selected is not None and section_id not in selected:
            if bool(section.get("required")):
                selector_misses.append(section_id)
            shadow_delivery = "omitted"
            delta_descriptor = {}
        row = {
            "section_id": section_id,
            "section_version": str(section.get("section_version") or "1"),
            "source_id": str(section.get("source_id") or ""),
            "artifact_id": str(section.get("artifact_id") or ""),
            "source_occurrence_id": str(
                section.get("source_occurrence_id") or ""
            ),
            "current_content_digest": current_digest,
            "previous_content_digest": previous_digest,
            # H1 is deliberately shadow-only: provider delivery stays complete.
            "delivery": "full",
            "shadow_delivery": shadow_delivery,
            "delta_allowed": bool(section.get("delta_allowed")),
            "required": bool(section.get("required")),
        }
        if delta_descriptor:
            row["delta_ref"] = delta_descriptor
            row["delta_digest"] = str(delta_descriptor.get("sha256") or "")
        section_rows.append(row)
    previous_descriptor = (
        dict(previous_receipt_descriptor)
        if isinstance(previous_receipt_descriptor, Mapping)
        else {}
    )
    envelope = {
        "schema_version": CONTEXT_DELIVERY_ENVELOPE_SCHEMA,
        "delivery_mode": CONTEXT_DELIVERY_MODE,
        "workflow_run_id": workflow_run_id,
        "operation_id": operation_id,
        "attempt_id": attempt_id,
        "dispatch_id": dispatch_id,
        "role_instance": role_instance,
        "source_manifest_ref": str(
            source_manifest_descriptor.get("ref") or ""
        ),
        "source_manifest_digest": str(
            source_manifest_descriptor.get("sha256") or ""
        ),
        "provider_session_id": provider_session_id,
        "renderer_version": CONTEXT_RENDERER_VERSION,
        "execution_binding": dict(execution_binding),
        "execution_binding_digest": str(
            execution_binding.get("identity_digest") or ""
        ),
        "previous_delivery_receipt_ref": str(
            previous_descriptor.get("ref") or ""
        ),
        "previous_delivery_receipt_digest": str(
            previous_descriptor.get("sha256") or ""
        ),
        "previous_state": baseline_status,
        "shadow_selector_misses": sorted(selector_misses),
        "sections": section_rows,
    }
    descriptor = write_immutable_json_sidecar(
        state_dir,
        envelope,
        root="context-delivery/envelopes",
        kind="context_delivery_envelope",
        schema_version=CONTEXT_DELIVERY_ENVELOPE_SCHEMA,
        created_by="context-delivery",
        source_event_id=source_event_id,
    )
    return envelope, descriptor


def write_context_delivery_receipt(
    state_dir: Path,
    *,
    envelope: Mapping[str, Any],
    envelope_descriptor: Mapping[str, Any],
    source_event_id: str = "",
) -> dict[str, Any]:
    """Record successful transport delivery without claiming artifact reads."""

    if str(envelope.get("schema_version") or "") != CONTEXT_DELIVERY_ENVELOPE_SCHEMA:
        raise ContextDeliveryError("unsupported context delivery envelope")
    receipt = {
        "schema_version": CONTEXT_DELIVERY_RECEIPT_SCHEMA,
        "workflow_run_id": str(envelope.get("workflow_run_id") or ""),
        "operation_id": str(envelope.get("operation_id") or ""),
        "attempt_id": str(envelope.get("attempt_id") or ""),
        "dispatch_id": str(envelope.get("dispatch_id") or ""),
        "role_instance": str(envelope.get("role_instance") or ""),
        "source_manifest_ref": str(envelope.get("source_manifest_ref") or ""),
        "source_manifest_digest": str(
            envelope.get("source_manifest_digest") or ""
        ),
        "provider_session_id": str(envelope.get("provider_session_id") or ""),
        "renderer_version": str(envelope.get("renderer_version") or ""),
        "execution_binding_digest": str(
            envelope.get("execution_binding_digest") or ""
        ),
        "delivery_envelope_ref": str(envelope_descriptor.get("ref") or ""),
        "delivery_envelope_digest": str(
            envelope_descriptor.get("sha256") or ""
        ),
        "sections": [
            {
                "section_id": str(section.get("section_id") or ""),
                "current_content_digest": str(
                    section.get("current_content_digest") or ""
                ),
                "delivery": str(section.get("delivery") or "full"),
            }
            for section in envelope.get("sections", [])
            if isinstance(section, Mapping)
        ],
    }
    return write_immutable_json_sidecar(
        state_dir,
        receipt,
        root="context-delivery/receipts",
        kind="context_delivery_receipt",
        schema_version=CONTEXT_DELIVERY_RECEIPT_SCHEMA,
        created_by="context-delivery",
        source_event_id=source_event_id,
    )


def latest_delivery_receipt(
    events: Iterable[Any],
    *,
    workflow_run_id: str,
    role_instance: str,
) -> dict[str, Any]:
    """Return only the newest receipt occurrence for one role/run."""

    rows = list(events)
    for index in range(len(rows) - 1, -1, -1):
        event = rows[index]
        if str(getattr(event, "type", "")) != "workflow.operation.started":
            continue
        payload = getattr(event, "payload", {})
        if not isinstance(payload, Mapping):
            continue
        if str(payload.get("workflow_run_id") or "") != workflow_run_id:
            continue
        if str(payload.get("role_instance") or "") != role_instance:
            continue
        descriptor = payload.get("context_delivery_receipt_ref")
        if not isinstance(descriptor, Mapping) or not descriptor:
            return {}  # A newer unknown delivery must not borrow older history.
        descriptor = dict(descriptor)
        for admitted in reversed(rows[index + 1:]):
            body = getattr(admitted, "payload", {})
            if (getattr(admitted, "type", "") != "workflow.call.result.admitted"
                    or not isinstance(body, Mapping)
                    or any(not payload.get(k) or body.get(k) != payload[k]
                           for k in ("workflow_run_id", "operation_id", "request_hash"))):
                continue
            observation = body.get("context_delivery_session_observation_ref")
            if isinstance(observation, Mapping):
                descriptor["session_observation_ref"] = dict(observation)
            break
        return descriptor
    return {}


def prepare_runtime_context_delivery(
    runtime: Any,
    *,
    payload: dict[str, Any],
    source_manifest: Mapping[str, Any],
    source_descriptor: Mapping[str, Any],
    workflow_run_id: str,
    operation_id: str,
    attempt_id: str,
    dispatch_id: str,
    role_instance: str,
    causation_id: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Resolve one runtime session and prepare its shadow delivery evidence."""

    provider_session_id = _provider_session_id(
        runtime,
        payload=payload,
        role_instance=role_instance,
    )
    execution_binding = build_execution_binding(
        source_manifest=source_manifest,
        role_instance=role_instance,
        provider_backend=_provider_backend(
            runtime,
            payload=payload,
            role_instance=role_instance,
        ),
        permission_profile=_permission_profile(payload),
    )
    previous_receipt = payload.get("previous_delivery_receipt_ref")
    if not isinstance(previous_receipt, Mapping):
        previous_receipt = latest_delivery_receipt(
            runtime.event_log.read_all(),
            workflow_run_id=workflow_run_id,
            role_instance=role_instance,
        )
    selected_sections = payload.get("shadow_selected_context_sections")
    envelope, descriptor = build_context_delivery_envelope(
        runtime.state_dir,
        source_manifest=source_manifest,
        source_manifest_descriptor=source_descriptor,
        workflow_run_id=workflow_run_id,
        operation_id=operation_id,
        attempt_id=attempt_id,
        dispatch_id=dispatch_id,
        role_instance=role_instance,
        provider_session_id=provider_session_id,
        execution_binding=execution_binding,
        previous_receipt_descriptor=previous_receipt,
        shadow_selected_section_ids=(
            selected_sections
            if isinstance(selected_sections, list)
            else None
        ),
        source_event_id=causation_id,
    )
    payload.update({
        "context_delivery_envelope": descriptor,
        "context_delivery_envelope_ref": str(descriptor.get("ref") or ""),
        "context_delivery_envelope_digest": str(descriptor.get("sha256") or ""),
        "provider_session_id": provider_session_id,
    })
    return provider_session_id, envelope, descriptor


def _load_compatible_receipt(
    state_dir: Path,
    *,
    descriptor: Mapping[str, Any] | None,
    workflow_run_id: str,
    role_instance: str,
    provider_session_id: str,
    execution_binding_digest: str,
) -> tuple[dict[str, Any], str]:
    if not isinstance(descriptor, Mapping) or not str(descriptor.get("ref") or ""):
        return {}, "absent"
    try:
        hydrated = hydrate_sidecar_ref(state_dir, dict(descriptor))
    except Exception:
        return {}, "unknown"
    receipt = hydrated.payload
    if not isinstance(receipt, Mapping):
        return {}, "unknown"
    if str(receipt.get("schema_version") or "") != CONTEXT_DELIVERY_RECEIPT_SCHEMA:
        return {}, "unknown"
    if not receipt.get("provider_session_id") and "session_observation_ref" in descriptor:
        from zf.runtime.context_session_observation import observed_session_id

        try:
            session_id = observed_session_id(
                state_dir, receipt_ref=descriptor,
                observation_ref=descriptor["session_observation_ref"],
            )
        except Exception:
            return {}, "unknown"
        receipt = {**receipt, "provider_session_id": session_id}
    compatible = all((
        str(receipt.get("workflow_run_id") or "") == workflow_run_id,
        str(receipt.get("role_instance") or "") == role_instance,
        bool(provider_session_id),
        str(receipt.get("provider_session_id") or "") == provider_session_id,
        str(receipt.get("renderer_version") or "") == CONTEXT_RENDERER_VERSION,
        bool(execution_binding_digest),
        str(receipt.get("execution_binding_digest") or "")
        == execution_binding_digest,
    ))
    return (dict(receipt), "known") if compatible else ({}, "incompatible")


def _write_shadow_delta(
    state_dir: Path,
    *,
    section: Mapping[str, Any],
    previous_content_digest: str,
    source_event_id: str,
) -> dict[str, Any]:
    delta = {
        "schema_version": CONTEXT_DELTA_SCHEMA,
        "algorithm": "replace-by-current-source-ref.v1",
        "section_id": str(section.get("section_id") or ""),
        "source_id": str(section.get("source_id") or ""),
        "artifact_id": str(section.get("artifact_id") or ""),
        "previous_content_digest": previous_content_digest,
        "current_content_digest": str(
            section.get("current_content_digest") or ""
        ),
    }
    return write_immutable_json_sidecar(
        state_dir,
        delta,
        root="context-delivery/deltas",
        kind="context_section_delta",
        schema_version=CONTEXT_DELTA_SCHEMA,
        created_by="context-delivery",
        source_event_id=source_event_id,
    )


def _section_group(source_id: str, kind: str) -> str:
    identity = f"{source_id} {kind}".lower()
    if any(token in identity for token in ("goal", "objective", "claim", "requirement")):
        return "goal_objective_and_claims"
    if source_id.startswith("plan-port-") or any(
        token in identity
        for token in ("plan", "task-map", "task_map", "source-index")
    ):
        return "plan_package_and_task_map"
    if source_id == "contract" or "scope" in identity:
        return "task_contract_and_scope"
    if any(
        token in identity
        for token in ("target", "task-ref", "candidate", "code-lineage")
    ):
        return "target_and_code_lineage"
    if any(
        token in identity
        for token in ("feedback", "self-check", "evidence", "review", "result")
    ):
        return "open_feedback_and_evidence_gaps"
    if any(token in identity for token in ("permission", "execution", "binding")):
        return "execution_binding_and_permissions"
    if any(token in identity for token in ("mailbox", "continuation", "history")):
        return "mailbox_and_continuation_summary"
    return "goal_objective_and_claims"


def _section_id(*, section_group: str, source_id: str, artifact_id: str) -> str:
    source_component = _safe_component(source_id)
    artifact_hash = hashlib.sha256(artifact_id.encode("utf-8")).hexdigest()[:10]
    return f"{section_group}:{source_component}:{artifact_hash}"


def _is_authoritative_source(source_id: str) -> bool:
    return source_id.startswith(_NON_DELTA_SOURCE_PREFIXES)


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-._") or "source"


def _stable_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _stable_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set)):
        normalized = [_stable_value(item) for item in value]
        return sorted(normalized, key=lambda item: str(item))
    if value in (None, ""):
        return {}
    return value


def _provider_session_id(
    runtime: Any,
    *,
    payload: Mapping[str, Any],
    role_instance: str,
) -> str:
    explicit = str(
        payload.get("provider_session_id")
        or payload.get("role_session_id")
        or payload.get("session_id")
        or ""
    ).strip()
    if explicit or not role_instance:
        return explicit
    try:
        from zf.core.state.role_sessions import RoleSessionRegistry

        session_id = RoleSessionRegistry(
            runtime.state_dir / "role_sessions.yaml",
            project_root=str(runtime.project_root),
        ).get(role_instance)
    except Exception:
        return ""
    return str(session_id or "")


def _provider_backend(
    runtime: Any,
    *,
    payload: Mapping[str, Any],
    role_instance: str,
) -> str:
    explicit = str(
        payload.get("provider_backend")
        or payload.get("backend")
        or ""
    ).strip()
    if explicit:
        return explicit
    for role in getattr(runtime.config, "roles", []) or []:
        instance_id = str(
            getattr(role, "instance_id", "")
            or (
                role.get("instance_id")
                if isinstance(role, Mapping)
                else ""
            )
        )
        name = str(
            getattr(role, "name", "")
            or (role.get("name") if isinstance(role, Mapping) else "")
        )
        if role_instance not in {instance_id, name}:
            continue
        return str(
            getattr(role, "backend", "")
            or (role.get("backend") if isinstance(role, Mapping) else "")
        )
    return ""


def _permission_profile(payload: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "approval_policy",
        "permissions",
        "sandbox",
        "sandbox_mode",
        "allowed_tools",
        "skills",
    )
    return {
        key: payload[key]
        for key in keys
        if payload.get(key) not in (None, "", [], {})
    }


__all__ = [
    "CONTEXT_DELIVERY_ENVELOPE_SCHEMA",
    "CONTEXT_DELIVERY_MODE",
    "CONTEXT_DELIVERY_RECEIPT_SCHEMA",
    "CONTEXT_POLICY_SCHEMA",
    "CONTEXT_RENDERER_VERSION",
    "ContextDeliveryError",
    "attach_context_sections",
    "build_context_delivery_envelope",
    "build_execution_binding",
    "latest_delivery_receipt",
    "normalize_context_inheritance",
    "prepare_runtime_context_delivery",
    "write_context_delivery_receipt",
]
