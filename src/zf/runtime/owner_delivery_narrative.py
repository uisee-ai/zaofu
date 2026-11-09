"""Post-terminal OA narrative operation and factual composite admission."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator_agent_contracts import (
    OrchestratorAgentContractError,
    normalize_owner_delivery_narrative,
)
from zf.runtime.orchestrator_agent_operations import (
    PreparedOrchestratorAgentOperation,
    request_orchestrator_agent_checkpoint,
)
from zf.runtime.orchestrator_agent_policy import (
    checkpoint_policy,
    orchestration_flow_kind,
)
from zf.runtime.orchestrator_agent_role import is_configured_orchestrator_role
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation


COMPOSITE_SCHEMA = "owner-delivery-composite.v1"
NARRATIVE_ADMITTED = "owner.delivery.narrative.admitted"
NARRATIVE_REJECTED = "owner.delivery.narrative.rejected"
NARRATIVE_DEGRADED = "owner.delivery.narrative.degraded"


def prepare_owner_delivery_narrative_operation(
    *,
    state_dir: Path,
    project_root: Path,
    config: Any,
    event_log: EventLog,
    writer: EventWriter,
    terminal: ZfEvent,
    dossier: Mapping[str, Any],
    dossier_path: Path,
    receipt: Mapping[str, Any] | None,
    receipt_path: Path | None,
) -> PreparedOrchestratorAgentOperation | None:
    flow_kind = orchestration_flow_kind(dossier, terminal)
    policy = checkpoint_policy(
        config,
        "owner_delivery",
        flow_kind=flow_kind,
    )
    if not policy or not any(
        is_configured_orchestrator_role(config, role) for role in config.roles
    ):
        return None
    run_id = str(dossier.get("run_id") or terminal.correlation_id or "")
    if not run_id:
        return None
    dossier_snapshot = write_immutable_json_sidecar(
        state_dir,
        dict(dossier),
        root="orchestrator-agent/owner-delivery/dossiers",
        kind="goal_dossier_snapshot",
        schema_version="goal-dossier.v1",
        created_by="owner-delivery-narrative",
        source_event_id=terminal.id,
    )
    artifact_refs: list[dict[str, Any]] = [{
        **dossier_snapshot,
        "source_id": "goal-dossier",
    }]
    completion_receipt_ref = ""
    completion_receipt_fingerprint = ""
    if receipt is not None and receipt_path is not None:
        receipt_snapshot = write_immutable_json_sidecar(
            state_dir,
            dict(receipt),
            root="orchestrator-agent/owner-delivery/receipts",
            kind="goal_completion_receipt_snapshot",
            schema_version="goal-completion-receipt.v1",
            created_by="owner-delivery-narrative",
            source_event_id=terminal.id,
        )
        artifact_refs.append({
            **receipt_snapshot,
            "source_id": "goal-completion-receipt",
        })
        completion_receipt_ref = receipt_path.relative_to(state_dir).as_posix()
        completion_receipt_fingerprint = str(
            receipt.get("source_fingerprint") or ""
        )
    terminal_snapshot = write_immutable_json_sidecar(
        state_dir,
        json.loads(terminal.to_json()),
        root="orchestrator-agent/owner-delivery/terminals",
        kind="terminal_event_snapshot",
        schema_version="zf-event.v1",
        created_by="owner-delivery-narrative",
        source_event_id=terminal.id,
    )
    artifact_refs.append({
        **terminal_snapshot,
        "source_id": "terminal-event",
    })
    source_fingerprint = str(dossier.get("source_fingerprint") or "")
    payload = {
        "workflow_run_id": run_id,
        "flow_kind": flow_kind,
        "request_revision": source_fingerprint or terminal.id,
        "terminal_event_id": terminal.id,
        "terminal_event_type": terminal.type,
        "dossier_ref": dossier_path.relative_to(state_dir).as_posix(),
        "dossier_source_fingerprint": source_fingerprint,
        "completion_receipt_ref": completion_receipt_ref,
        "completion_receipt_fingerprint": completion_receipt_fingerprint,
        "artifact_refs": artifact_refs,
    }
    runtime = SimpleNamespace(
        state_dir=Path(state_dir),
        project_root=Path(project_root),
        config=config,
        event_log=event_log,
        event_writer=writer,
    )
    return request_orchestrator_agent_checkpoint(
        runtime,
        checkpoint="owner_delivery",
        checkpoint_policy=policy,
        workflow_run_id=run_id,
        source_event=terminal,
        payload=payload,
    )


def apply_owner_delivery_narrative(runtime: Any, event: ZfEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    operation_id = str(payload.get("operation_id") or "")
    if event.type.endswith(".failed"):
        return _degrade(runtime, event, operation_id, "agent_execution_failed")
    try:
        hydrated = hydrate_profiled_control_result_event(runtime.state_dir, event)
        narrative = normalize_owner_delivery_narrative(
            hydrated.payload.get("owner_delivery_narrative")
        )
        request = _settled_request(runtime, operation_id, hydrated)
        _validate_current_truth(runtime, request, narrative)
        _validate_citations(runtime, request, narrative)
    except (OrchestratorAgentContractError, ValueError, OSError) as exc:
        runtime.event_writer.append(ZfEvent(
            type=NARRATIVE_REJECTED,
            actor="zf-cli",
            origin="kernel",
            payload={
                "operation_id": operation_id,
                "source_event_id": event.id,
                "reason": str(exc),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        return _degrade(runtime, event, operation_id, str(exc))
    descriptor = hydrated.payload.get("control_result_ref")
    descriptor = dict(descriptor) if isinstance(descriptor, Mapping) else {}
    composite = write_owner_delivery_composite(
        state_dir=runtime.state_dir,
        run_id=str(narrative["identity"]["workflow_run_id"]),
        dossier_ref=str(narrative["identity"]["dossier_ref"]),
        dossier_source_fingerprint=str(
            narrative["identity"]["dossier_source_fingerprint"]
        ),
        completion_receipt_ref=str(
            narrative["identity"].get("completion_receipt_ref") or ""
        ),
        terminal_event_id=str(narrative["identity"]["terminal_event_id"]),
        narrative_status="admitted",
        narrative_reason="current_cited_narrative",
        narrative=narrative,
        narrative_ref=descriptor,
    )
    admitted = runtime.event_writer.append(ZfEvent(
        type=NARRATIVE_ADMITTED,
        actor="zf-cli",
        origin="kernel",
        payload={
            "schema_version": "owner-delivery-narrative-admission.v1",
            "operation_id": operation_id,
            "workflow_run_id": narrative["identity"]["workflow_run_id"],
            "terminal_event_id": narrative["identity"]["terminal_event_id"],
            "narrative_ref": descriptor,
            "owner_delivery_composite_ref": composite.relative_to(
                runtime.state_dir
            ).as_posix(),
            "status": "admitted",
        },
        causation_id=event.id,
        correlation_id=event.correlation_id,
    ))
    _emit_owner_update(
        runtime,
        narrative=narrative,
        descriptor=descriptor,
        composite=composite,
        causation_id=admitted.id,
    )
    return {
        "status": "admitted",
        "operation_id": operation_id,
        "narrative_ref": descriptor,
        "composite_ref": composite.relative_to(runtime.state_dir).as_posix(),
    }


def write_owner_delivery_composite(
    *,
    state_dir: Path,
    run_id: str,
    dossier_ref: str,
    dossier_source_fingerprint: str,
    completion_receipt_ref: str,
    terminal_event_id: str,
    narrative_status: str,
    narrative_reason: str,
    narrative: Mapping[str, Any] | None = None,
    narrative_ref: Mapping[str, Any] | None = None,
) -> Path:
    path = (
        Path(state_dir)
        / "projections"
        / "goals"
        / _safe(run_id)
        / f"{COMPOSITE_SCHEMA}.json"
    )
    payload = {
        "schema_version": COMPOSITE_SCHEMA,
        "is_derived_projection": True,
        "run_id": run_id,
        "terminal_event_id": terminal_event_id,
        "factual": {
            "dossier_ref": dossier_ref,
            "dossier_source_fingerprint": dossier_source_fingerprint,
            "completion_receipt_ref": completion_receipt_ref,
        },
        "narrative_status": narrative_status,
        "narrative_reason": narrative_reason,
        "narrative_ref": dict(narrative_ref or {}),
        "narrative": dict(narrative or {}),
    }
    atomic_write_text(
        path,
        json.dumps(redact_obj(payload), ensure_ascii=False, indent=2) + "\n",
    )
    return path


def _settled_request(
    runtime: Any,
    operation_id: str,
    event: ZfEvent,
) -> dict[str, Any]:
    operation = load_workflow_operation(runtime.event_log, operation_id)
    if operation is None or str(operation.get("status") or "") != "settled":
        raise ValueError("narrative operation is not settled")
    request_ref = operation.get("request_ref")
    if not isinstance(request_ref, Mapping):
        raise ValueError("narrative operation request is missing")
    stored = hydrate_sidecar_ref(runtime.state_dir, dict(request_ref)).payload
    request = stored.get("request") if isinstance(stored, Mapping) else None
    if not isinstance(request, Mapping):
        raise ValueError("narrative operation request is invalid")
    settled_ref = operation.get("admitted_call_result_ref")
    envelope_ref = event.payload.get("call_result_envelope_ref")
    if not isinstance(settled_ref, Mapping) or not isinstance(envelope_ref, Mapping):
        raise ValueError("narrative admitted result refs are missing")
    if _descriptor(settled_ref) != _descriptor(envelope_ref):
        raise ValueError("narrative settled result ref mismatch")
    return dict(request)


def _validate_current_truth(
    runtime: Any,
    request: Mapping[str, Any],
    narrative: Mapping[str, Any],
) -> None:
    identity = narrative["identity"]
    expected = request.get("result_identity")
    expected = expected if isinstance(expected, Mapping) else {}
    for key in (
        "workflow_run_id",
        "terminal_event_id",
        "terminal_event_type",
        "dossier_ref",
        "dossier_source_fingerprint",
        "completion_receipt_ref",
        "completion_receipt_fingerprint",
    ):
        if str(identity.get(key) or "") != str(expected.get(key) or ""):
            raise ValueError(f"narrative identity mismatch:{key}")
    terminal = next(
        (
            item for item in runtime.event_log.read_all()
            if item.id == str(identity["terminal_event_id"])
        ),
        None,
    )
    if terminal is None or terminal.type != str(identity["terminal_event_type"]):
        raise ValueError("terminal event is missing or stale")
    scoped_terminals = [
        item
        for item in runtime.event_log.read_all()
        if item.type in {"run.goal.completed", "run.goal.blocked"}
        and str(
            (item.payload or {}).get("run_id")
            or item.correlation_id
            or ""
        ) == str(identity["workflow_run_id"])
    ]
    if not scoped_terminals or scoped_terminals[-1].id != terminal.id:
        raise ValueError("terminal event is no longer current")
    expected_status = (
        "completed" if terminal.type == "run.goal.completed" else "blocked"
    )
    if str(narrative.get("status") or "") != expected_status:
        raise ValueError("narrative status conflicts with terminal truth")
    dossier_path = Path(runtime.state_dir) / str(identity["dossier_ref"])
    dossier = _read_json(dossier_path)
    if str(dossier.get("source_fingerprint") or "") != str(
        identity["dossier_source_fingerprint"]
    ):
        raise ValueError("dossier fingerprint is stale")
    receipt_ref = str(identity.get("completion_receipt_ref") or "")
    if terminal.type == "run.goal.completed":
        receipt_path = Path(runtime.state_dir) / receipt_ref
        if not receipt_ref or not receipt_path.is_file():
            raise ValueError("completion receipt is missing")
        receipt = _read_json(receipt_path)
        if str(receipt.get("source_fingerprint") or "") != str(
            identity.get("completion_receipt_fingerprint") or ""
        ):
            raise ValueError("completion receipt fingerprint is stale")


def _validate_citations(
    runtime: Any,
    request: Mapping[str, Any],
    narrative: Mapping[str, Any],
) -> None:
    identity = narrative["identity"]
    dossier = _read_json(Path(runtime.state_dir) / str(identity["dossier_ref"]))
    claim_ids = _collect_ids(dossier, singular={"claim_id", "goal_claim_id"}, plural={"claim_ids", "goal_claim_ids"})
    task_ids = _collect_ids(dossier, singular={"task_id"}, plural={"task_ids", "affected_task_ids"})
    gap_ids = _collect_ids(dossier, singular={"gap_id"}, plural={"gap_ids"})
    descriptors = _collect_descriptors(dossier)
    claim_ref_scope = _claim_ref_scope(dossier)
    for outcome in narrative.get("delivered_outcomes") or []:
        cited_claim_ids = [str(value) for value in outcome.get("claim_ids") or []]
        for value in outcome.get("claim_ids") or []:
            if str(value) not in claim_ids:
                raise ValueError(f"unknown claim citation:{value}")
        for value in outcome.get("task_ids") or []:
            if str(value) not in task_ids:
                raise ValueError(f"unknown task citation:{value}")
        for value in outcome.get("gap_ids") or []:
            if str(value) not in gap_ids:
                raise ValueError(f"unknown gap citation:{value}")
        for key in ("result_refs", "evidence_refs"):
            for descriptor in outcome.get(key) or []:
                if _descriptor(descriptor) not in descriptors:
                    raise ValueError(f"unknown {key} citation:{descriptor.get('ref')}")
                scoped_claims = [
                    claim_id for claim_id in cited_claim_ids
                    if claim_id in claim_ref_scope
                ]
                allowed = set().union(*(
                    claim_ref_scope[claim_id].get(key, set())
                    for claim_id in scoped_claims
                )) if scoped_claims else set()
                if scoped_claims and str(descriptor.get("ref") or "") not in allowed:
                    raise ValueError(
                        f"claim-specific {key} citation mismatch:"
                        f"{descriptor.get('ref')}"
                    )


def _claim_ref_scope(dossier: Mapping[str, Any]) -> dict[str, dict[str, set[str]]]:
    matrix = dossier.get("claim_to_evidence")
    matrix = matrix if isinstance(matrix, Mapping) else {}
    rows = matrix.get("rows")
    if not isinstance(rows, list):
        return {}
    scoped: dict[str, dict[str, set[str]]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        claim_id = str(row.get("goal_claim_id") or row.get("claim_id") or "")
        if not claim_id:
            continue
        scoped[claim_id] = {
            "result_refs": _citation_refs(row.get("result_refs")),
            "evidence_refs": _citation_refs(row.get("evidence_refs")),
        }
    return scoped


def _citation_refs(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    refs: set[str] = set()
    for item in value:
        ref = (
            str(item.get("ref") or "")
            if isinstance(item, Mapping)
            else str(item or "")
        ).strip()
        if ref:
            refs.add(ref)
    return refs


def _degrade(
    runtime: Any,
    event: ZfEvent,
    operation_id: str,
    reason: str,
) -> dict[str, Any]:
    request: dict[str, Any] = {}
    operation = load_workflow_operation(runtime.event_log, operation_id)
    request_ref = operation.get("request_ref") if isinstance(operation, Mapping) else None
    if isinstance(request_ref, Mapping):
        try:
            stored = hydrate_sidecar_ref(runtime.state_dir, dict(request_ref)).payload
            request = dict(stored.get("request") or {})
        except Exception:
            request = {}
    identity = request.get("result_identity")
    identity = identity if isinstance(identity, Mapping) else {}
    if identity:
        write_owner_delivery_composite(
            state_dir=runtime.state_dir,
            run_id=str(identity.get("workflow_run_id") or event.correlation_id or ""),
            dossier_ref=str(identity.get("dossier_ref") or ""),
            dossier_source_fingerprint=str(
                identity.get("dossier_source_fingerprint") or ""
            ),
            completion_receipt_ref=str(
                identity.get("completion_receipt_ref") or ""
            ),
            terminal_event_id=str(identity.get("terminal_event_id") or ""),
            narrative_status="degraded",
            narrative_reason=reason,
        )
    runtime.event_writer.append(ZfEvent(
        type=NARRATIVE_DEGRADED,
        actor="zf-cli",
        origin="kernel",
        payload={
            "operation_id": operation_id,
            "source_event_id": event.id,
            "reason": reason,
            "terminal_truth_unchanged": True,
        },
        causation_id=event.id,
        correlation_id=event.correlation_id,
    ))
    return {"status": "degraded", "operation_id": operation_id, "reason": reason}


def _emit_owner_update(
    runtime: Any,
    *,
    narrative: Mapping[str, Any],
    descriptor: Mapping[str, Any],
    composite: Path,
    causation_id: str,
) -> None:
    terminal_id = str(narrative["identity"]["terminal_event_id"])
    factual = next(
        (
            item for item in reversed(runtime.event_log.read_all())
            if item.type == "owner.visible_message.requested"
            and str((item.payload or {}).get("terminal_event_id") or "") == terminal_id
        ),
        None,
    )
    if factual is None:
        return
    payload = dict(factual.payload or {})
    raw = "\0".join((terminal_id, *_descriptor(descriptor)))
    payload.update({
        "schema_version": "goal-dossier-delivery.v1",
        "message_id": "goal-narrative-" + hashlib.sha256(
            raw.encode("utf-8")
        ).hexdigest()[:24],
        "source": "owner-delivery-narrative",
        "handled_by": "owner-delivery-narrative",
        "title": (
            "目标交付语义总结"
            if narrative.get("status") == "completed"
            else "目标阻塞语义总结"
        ),
        "summary": str(narrative.get("executive_summary") or ""),
        "narrative_status": "admitted",
        "narrative_ref": str(descriptor.get("ref") or ""),
        "narrative_digest": str(descriptor.get("sha256") or ""),
        "owner_delivery_composite_ref": composite.relative_to(
            runtime.state_dir
        ).as_posix(),
    })
    runtime.event_writer.append(ZfEvent(
        type="owner.visible_message.requested",
        actor="zf-owner-delivery-narrative",
        origin="kernel",
        payload=payload,
        causation_id=causation_id,
        correlation_id=str(narrative["identity"]["workflow_run_id"]),
    ))


def _collect_ids(
    value: Any,
    *,
    singular: set[str],
    plural: set[str],
) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in singular and str(item or ""):
                found.add(str(item))
            elif str(key) in plural and isinstance(item, list):
                found.update(str(row) for row in item if str(row or ""))
            _merge_ids(found, _collect_ids(item, singular=singular, plural=plural))
    elif isinstance(value, list):
        for item in value:
            _merge_ids(found, _collect_ids(item, singular=singular, plural=plural))
    return found


def _merge_ids(target: set[str], values: set[str]) -> None:
    target.update(values)


def _collect_descriptors(value: Any) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    if isinstance(value, Mapping):
        descriptor = _descriptor(value)
        if all(descriptor):
            found.add(descriptor)
        for item in value.values():
            found.update(_collect_descriptors(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_collect_descriptors(item))
    return found


def _descriptor(value: Mapping[str, Any]) -> tuple[str, str]:
    return str(value.get("ref") or ""), str(value.get("sha256") or "")


def _read_json(path: Path) -> dict[str, Any]:
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError(f"{path} must contain an object")
    return body


def _safe(value: str) -> str:
    return "".join(
        item if item.isalnum() or item in "._-" else "-" for item in value
    ).strip(".-") or "unscoped"


__all__ = [
    "COMPOSITE_SCHEMA",
    "NARRATIVE_ADMITTED",
    "NARRATIVE_DEGRADED",
    "NARRATIVE_REJECTED",
    "apply_owner_delivery_narrative",
    "prepare_owner_delivery_narrative_operation",
    "write_owner_delivery_composite",
]
