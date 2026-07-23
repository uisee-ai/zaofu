"""Deterministic call-result admission and bounded output repair."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_read_ledger import (
    ArtifactReadError,
    seal_read_ledger,
    validate_required_reads,
)
from zf.runtime.call_result_adapters import (
    AdaptedControlResult,
    ControlResultAdapterError,
    ControlResultAdapterRegistry,
)
from zf.runtime.call_result_envelope import (
    envelope_identity_key,
    normalize_call_result_envelope,
    validate_call_result_envelope,
    write_immutable_json_sidecar,
)
from zf.runtime.call_result_authority import CallResultAuthorityMixin
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    load_workflow_operation,
    operation_request_hash,
    stable_operation_id,
)


CALL_RESULT_EVENT_TYPES = frozenset({
    "workflow.call.result.reported",
    "workflow.call.result.repair.requested",
    "workflow.call.result.admitted",
    "workflow.call.result.invalid",
})
CALL_RESULT_ADAPTER_VERSION = "legacy-call-result-adapters.v1"
DEFAULT_OUTPUT_REPAIR_CAP = 2
VALID_PROTOCOL_MODES = frozenset({"shadow", "warning", "blocking"})


@dataclass(frozen=True)
class CallResultAdmissionOutcome:
    status: str
    mode: str
    operation_id: str = ""
    request_hash: str = ""
    envelope_ref: dict[str, Any] | None = None
    control_result_ref: dict[str, Any] | None = None
    issues: tuple[dict[str, str], ...] = ()
    repair_round: int = 0
    correction_ref: dict[str, Any] | None = None
    correction_dispatch_required: bool = False
    admitted_event_id: str = ""

    @property
    def repair_requested(self) -> bool:
        return self.status == "repair_pending"

    @property
    def admitted(self) -> bool:
        return self.status == "admitted"


class CallResultAdmissionService(CallResultAuthorityMixin):
    def __init__(
        self,
        *,
        state_dir: Path,
        event_log: EventLog,
        event_writer: EventWriter,
        operation_service: WorkflowOperationService | None = None,
        adapters: ControlResultAdapterRegistry | None = None,
        repair_cap: int = DEFAULT_OUTPUT_REPAIR_CAP,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.event_log = event_log
        self.event_writer = event_writer
        self.operation_service = operation_service
        self.adapters = adapters or ControlResultAdapterRegistry()
        self.repair_cap = max(0, int(repair_cap))

    def report_legacy_result(
        self,
        event: ZfEvent,
        *,
        mode: str = "shadow",
        operation: Mapping[str, Any] | None = None,
        input_policy: Mapping[str, Any] | None = None,
    ) -> CallResultAdmissionOutcome:
        mode = str(mode or "shadow").strip().lower()
        if mode not in VALID_PROTOCOL_MODES:
            mode = "shadow"
        payload = dict(event.payload) if isinstance(event.payload, dict) else {}
        try:
            adapted = self.adapters.adapt(self.state_dir, event)
        except ControlResultAdapterError as exc:
            return CallResultAdmissionOutcome(
                status="unsupported",
                mode=mode,
                issues=({"field": "control_result", "code": "adapter_missing", "message": str(exc)},),
            )
        operation_identity = self._operation_identity(event, payload, operation)
        policy = self._input_policy(payload, input_policy)
        ledger_descriptor: dict[str, Any] = {}
        if policy:
            ledger_descriptor = self._ensure_sealed_ledger(payload, policy)
            if ledger_descriptor:
                payload.update({
                    "read_ledger_ref": str(ledger_descriptor.get("ref") or ""),
                    "read_ledger_digest": str(ledger_descriptor.get("sha256") or ""),
                    "input_consumption_status": "satisfied",
                })
        control_ref = {
            "schema_version": adapted.schema_version,
            "ref": str(adapted.descriptor.get("ref") or ""),
            "sha256": str(adapted.descriptor.get("sha256") or ""),
        }
        envelope = normalize_call_result_envelope(
            source_payload=payload,
            control_result=control_ref,
            workflow_run_id=operation_identity["workflow_run_id"],
            operation_id=operation_identity["operation_id"],
            request_hash=operation_identity["request_hash"],
            parent_operation_id=operation_identity["parent_operation_id"],
            source_event_id=event.id,
            source_event_type=event.type,
            actor=event.actor or "",
            task_id=str(event.task_id or payload.get("task_id") or ""),
            correlation_id=event.correlation_id or "",
        )
        issues = [dict(item) for item in adapted.issues]
        issues.extend(validate_call_result_envelope(
            envelope,
            require_target_snapshot=adapted.schema_version in {
                "verification-result.v1",
                "goal-closure-result.v1",
            },
            require_read_proof=bool(policy),
        ))
        issues.extend(self._control_result_identity_issues(envelope, adapted))
        currentness_issues = self._operation_result_currentness_issues(
            envelope,
            operation or {},
        )
        currentness_issues.extend(self._task_result_currentness_issues(
            envelope,
            adapted,
        ))
        issues.extend(currentness_issues)
        if adapted.schema_version == "goal-closure-result.v1":
            issues.extend(self._goal_closure_issues(adapted.payload))
        if policy and ledger_descriptor:
            issues.extend(validate_required_reads(
                self.state_dir,
                policy=policy,
                ledger_descriptor=ledger_descriptor,
            ))
        elif policy:
            issues.append({
                "field": "input_consumption.read_ledger_ref",
                "code": "required_read_missing",
            })
        key = envelope_identity_key(envelope)
        existing = self._existing_admission(key)
        if existing is not None and not currentness_issues:
            body = existing.payload if isinstance(existing.payload, dict) else {}
            existing_envelope = body.get("envelope_ref")
            existing_control = body.get("control_result_ref")
            return CallResultAdmissionOutcome(
                status=str(body.get("admission_status") or "admitted"),
                mode=mode,
                operation_id=key[0],
                request_hash=key[1],
                envelope_ref=(
                    dict(existing_envelope)
                    if isinstance(existing_envelope, Mapping)
                    else None
                ),
                control_result_ref=(
                    dict(existing_control)
                    if isinstance(existing_control, Mapping)
                    else adapted.descriptor
                ),
                issues=tuple(issues),
                admitted_event_id=existing.id,
            )
        envelope_descriptor = write_immutable_json_sidecar(
            self.state_dir,
            envelope,
            root="call-results/envelopes",
            kind="call_result_envelope",
            schema_version="call-result-envelope.v1",
            created_by="call-result-admission",
            source_event_id=event.id,
        )
        reported = self._emit_reported_once(
            event=event,
            envelope_descriptor=envelope_descriptor,
            control_result_descriptor=adapted.descriptor,
            adapter=adapted,
            mode=mode,
            issues=issues,
            identity_key=key,
            workflow_run_id=operation_identity["workflow_run_id"],
        )
        if currentness_issues:
            existing_invalid = self._existing_invalid(key)
            if existing_invalid is None:
                self.event_writer.append(ZfEvent(
                    type="workflow.call.result.invalid",
                    actor="zf-cli",
                    task_id=event.task_id,
                    payload={
                        **operation_identity,
                        "schema_version": "call-result-admission.v1",
                        "envelope_ref": envelope_descriptor,
                        "control_result_ref": adapted.descriptor,
                        "issues": currentness_issues,
                        "repair_round": 0,
                        "repair_cap": self.repair_cap,
                        "reason": "stale_call_result_superseded",
                        "semantic_attempt_incremented": False,
                    },
                    causation_id=reported.id,
                    correlation_id=event.correlation_id,
                ))
            return CallResultAdmissionOutcome(
                status="superseded",
                mode=mode,
                operation_id=key[0],
                request_hash=key[1],
                envelope_ref=envelope_descriptor,
                control_result_ref=adapted.descriptor,
                issues=tuple(currentness_issues),
            )
        if issues:
            if mode == "shadow":
                return CallResultAdmissionOutcome(
                    status="shadow_invalid",
                    mode=mode,
                    operation_id=key[0],
                    request_hash=key[1],
                    envelope_ref=envelope_descriptor,
                    control_result_ref=adapted.descriptor,
                    issues=tuple(issues),
                )
            existing_repair = self._existing_repair(key)
            if existing_repair is not None:
                body = (
                    existing_repair.payload
                    if isinstance(existing_repair.payload, dict)
                    else {}
                )
                correction = body.get("correction_ref")
                return CallResultAdmissionOutcome(
                    status="repair_pending",
                    mode=mode,
                    operation_id=key[0],
                    request_hash=key[1],
                    envelope_ref=envelope_descriptor,
                    control_result_ref=adapted.descriptor,
                    issues=tuple(issues),
                    repair_round=int(body.get("repair_round") or 0),
                    correction_ref=(
                        dict(correction) if isinstance(correction, Mapping) else None
                    ),
                    correction_dispatch_required=False,
                )
            repair_round = self._next_repair_round(key)
            if repair_round <= self.repair_cap:
                correction = self._write_correction_packet(
                    event=event,
                    envelope_descriptor=envelope_descriptor,
                    adapted=adapted,
                    issues=issues,
                    repair_round=repair_round,
                    operation_identity=operation_identity,
                )
                self.event_writer.append(ZfEvent(
                    type="workflow.call.result.repair.requested",
                    actor="zf-cli",
                    task_id=event.task_id,
                    payload={
                        **operation_identity,
                        "schema_version": "call-result-repair-request.v1",
                        "envelope_ref": envelope_descriptor,
                        "control_result_ref": adapted.descriptor,
                        "result_digest": key[2],
                        "correction_ref": correction,
                        "issues": issues,
                        "repair_round": repair_round,
                        "repair_cap": self.repair_cap,
                        "semantic_attempt_incremented": False,
                    },
                    causation_id=reported.id,
                    correlation_id=event.correlation_id,
                ))
                return CallResultAdmissionOutcome(
                    status="repair_pending",
                    mode=mode,
                    operation_id=key[0],
                    request_hash=key[1],
                    envelope_ref=envelope_descriptor,
                    control_result_ref=adapted.descriptor,
                    issues=tuple(issues),
                    repair_round=repair_round,
                    correction_ref=correction,
                    correction_dispatch_required=True,
                )
            existing_invalid = self._existing_invalid(key)
            if existing_invalid is not None:
                # ZF-REVIEW-140-B2(2026-07-16 评审复现):restart 清扫重放
                # 同一 terminal 事件时,repair-exhausted 分支此前无守卫地
                # 重复 append invalid(reported/repair/admitted 均有幂等,
                # 唯此漏)。与 _existing_repair docstring 的重放场景对齐。
                return CallResultAdmissionOutcome(
                    status="invalid",
                    mode=mode,
                    operation_id=key[0],
                    request_hash=key[1],
                    envelope_ref=envelope_descriptor,
                    control_result_ref=adapted.descriptor,
                    issues=tuple(issues),
                    repair_round=repair_round - 1,
                )
            invalid = self.event_writer.append(ZfEvent(
                type="workflow.call.result.invalid",
                actor="zf-cli",
                task_id=event.task_id,
                payload={
                    **operation_identity,
                    "schema_version": "call-result-admission.v1",
                    "envelope_ref": envelope_descriptor,
                    "control_result_ref": adapted.descriptor,
                    "issues": issues,
                    "repair_round": repair_round - 1,
                    "repair_cap": self.repair_cap,
                    "reason": "output_repair_exhausted",
                    "semantic_attempt_incremented": False,
                },
                causation_id=reported.id,
                correlation_id=event.correlation_id,
            ))
            if self.operation_service and load_workflow_operation(
                self.event_log, key[0]
            ) is not None:
                self.operation_service.fail(
                    operation_id=key[0],
                    request_hash=key[1],
                    workflow_run_id=operation_identity["workflow_run_id"],
                    task_id=str(event.task_id or payload.get("task_id") or ""),
                    reason="output_repair_exhausted",
                    causation_id=invalid.id,
                    correlation_id=event.correlation_id or "",
                )
            return CallResultAdmissionOutcome(
                status="invalid",
                mode=mode,
                operation_id=key[0],
                request_hash=key[1],
                envelope_ref=envelope_descriptor,
                control_result_ref=adapted.descriptor,
                issues=tuple(issues),
                repair_round=repair_round - 1,
            )
        admitted = self.event_writer.append(ZfEvent(
            type="workflow.call.result.admitted",
            actor="zf-cli",
            task_id=event.task_id,
            payload={
                **operation_identity,
                "schema_version": "call-result-admission.v1",
                "admission_status": "admitted",
                "mode": mode,
                "envelope_ref": envelope_descriptor,
                "control_result_ref": adapted.descriptor,
                "control_result_schema": adapted.schema_version,
                "semantic_verdict": _semantic_verdict(adapted.payload),
                "read_ledger_ref": ledger_descriptor,
                "source_event_id": event.id,
            },
            causation_id=reported.id,
            correlation_id=event.correlation_id,
        ))
        if self.operation_service and load_workflow_operation(
            self.event_log, key[0]
        ) is not None:
            self.operation_service.settle(
                operation_id=key[0],
                request_hash=key[1],
                workflow_run_id=operation_identity["workflow_run_id"],
                task_id=str(event.task_id or payload.get("task_id") or ""),
                admitted_call_result_ref=envelope_descriptor,
                causation_id=admitted.id,
                correlation_id=event.correlation_id or "",
            )
        return CallResultAdmissionOutcome(
            status="admitted",
            mode=mode,
            operation_id=key[0],
            request_hash=key[1],
            envelope_ref=envelope_descriptor,
            control_result_ref=adapted.descriptor,
            admitted_event_id=admitted.id,
        )

    def _operation_identity(
        self,
        event: ZfEvent,
        payload: Mapping[str, Any],
        operation: Mapping[str, Any] | None,
    ) -> dict[str, str]:
        operation = operation or {}
        workflow_run_id = str(
            operation.get("workflow_run_id")
            or payload.get("workflow_run_id")
            or payload.get("trace_id")
            or event.correlation_id
            or payload.get("pdd_id")
            or f"legacy-{event.task_id or 'run'}"
        )
        stage_id = str(payload.get("stage_id") or payload.get("stage_slot") or event.type.split(".")[0])
        child_key = str(
            payload.get("child_id")
            or payload.get("task_id")
            or event.task_id
            or payload.get("role_instance")
            or event.actor
            or event.type
        )
        operation_id = str(operation.get("operation_id") or payload.get("operation_id") or "")
        if not operation_id:
            operation_id = stable_operation_id(
                workflow_run_id=workflow_run_id,
                parent_stage_id=stage_id,
                operation_key=child_key,
                operation_type="agent",
            )
        request_hash = str(operation.get("request_hash") or payload.get("request_hash") or "")
        if not request_hash:
            request_hash = operation_request_hash({
                "workflow_run_id": workflow_run_id,
                "operation_id": operation_id,
                "event_type": event.type,
                "task_id": str(event.task_id or payload.get("task_id") or ""),
                "stage_id": stage_id,
                "child_id": str(payload.get("child_id") or ""),
                "target_commit": str(payload.get("target_commit") or payload.get("source_commit") or ""),
            })
        return {
            "workflow_run_id": workflow_run_id,
            "parent_operation_id": str(operation.get("parent_operation_id") or payload.get("parent_operation_id") or ""),
            "operation_id": operation_id,
            "request_hash": request_hash,
        }

    def _input_policy(
        self,
        payload: Mapping[str, Any],
        explicit: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if explicit:
            return dict(explicit)
        raw = payload.get("input_consumption_policy")
        if isinstance(raw, Mapping):
            return dict(raw)
        descriptor = payload.get("input_consumption_policy_ref")
        if isinstance(descriptor, Mapping):
            hydrated = hydrate_sidecar_ref(self.state_dir, dict(descriptor))
            return dict(hydrated.payload) if isinstance(hydrated.payload, dict) else {}
        return {}

    def _ensure_sealed_ledger(
        self,
        payload: Mapping[str, Any],
        policy: Mapping[str, Any],
    ) -> dict[str, Any]:
        descriptor = payload.get("read_ledger_ref")
        if isinstance(descriptor, Mapping):
            return dict(descriptor)
        attempt_id = str(policy.get("attempt_id") or payload.get("attempt_id") or payload.get("run_id") or "")
        if not attempt_id:
            return {}
        try:
            return seal_read_ledger(self.state_dir, attempt_id)
        except ArtifactReadError:
            return {}

    def _control_result_identity_issues(
        self,
        envelope: Mapping[str, Any],
        adapted: AdaptedControlResult,
    ) -> list[dict[str, str]]:
        identity = envelope.get("identity") if isinstance(envelope.get("identity"), Mapping) else {}
        issues: list[dict[str, str]] = []
        for field in (
            "workflow_run_id",
            "task_id",
            "plan_revision",
            "plan_synth_contract_ref",
            "plan_synth_contract_digest",
            "contract_revision",
            "task_map_generation",
            "base_commit",
            "task_ref",
            "contract_snapshot_ref",
            "contract_snapshot_digest",
            "target_snapshot_ref",
            "target_snapshot_digest",
            "target_commit",
        ):
            result_value = str(adapted.payload.get(field) or "")
            envelope_value = str(identity.get(field) or "")
            if result_value and envelope_value and result_value != envelope_value:
                issues.append({
                    "field": f"control_result.{field}",
                    "code": "identity_mismatch",
                    "message": f"expected {envelope_value}, got {result_value}",
                })
        if adapted.schema_version == "goal-closure-result.v1":
            required_result_identity = (
                "workflow_run_id",
                "task_map_generation",
                "target_commit",
                "goal_id",
                "goal_claim_set_ref",
                "goal_claim_set_digest",
                "closure_fact_ref",
                "closure_fact_digest",
            )
            for field in required_result_identity:
                if not str(adapted.payload.get(field) or "").strip():
                    issues.append({
                        "field": f"control_result.{field}",
                        "code": "missing_required",
                    })
        return issues

    def _existing_admission(self, key: tuple[str, str, str]) -> ZfEvent | None:
        for event in reversed(self.event_log.read_all()):
            if event.type != "workflow.call.result.admitted":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            control = payload.get("control_result_ref") if isinstance(payload.get("control_result_ref"), Mapping) else {}
            current = (
                str(payload.get("operation_id") or ""),
                str(payload.get("request_hash") or ""),
                str(control.get("sha256") or ""),
            )
            if current == key:
                return event
        return None

    def _emit_reported_once(
        self,
        *,
        event: ZfEvent,
        envelope_descriptor: Mapping[str, Any],
        control_result_descriptor: Mapping[str, Any],
        adapter: AdaptedControlResult,
        mode: str,
        issues: list[dict[str, str]],
        identity_key: tuple[str, str, str],
        workflow_run_id: str,
    ) -> ZfEvent:
        for existing in reversed(self.event_log.read_all()):
            if existing.type != "workflow.call.result.reported":
                continue
            body = existing.payload if isinstance(existing.payload, dict) else {}
            if (
                str(body.get("operation_id") or "") == identity_key[0]
                and str(body.get("request_hash") or "") == identity_key[1]
                and str(body.get("result_digest") or "") == identity_key[2]
            ):
                return existing
        return self.event_writer.append(ZfEvent(
            type="workflow.call.result.reported",
            actor="zf-cli",
            task_id=event.task_id,
            payload={
                "schema_version": "call-result-admission.v1",
                "workflow_run_id": workflow_run_id,
                "operation_id": identity_key[0],
                "request_hash": identity_key[1],
                "result_digest": identity_key[2],
                "mode": mode,
                "adapter_id": adapter.adapter_id,
                "adapter_version": CALL_RESULT_ADAPTER_VERSION,
                "envelope_ref": dict(envelope_descriptor),
                "control_result_ref": dict(control_result_descriptor),
                "parity": "match" if not issues else "diff",
                "issues": issues,
                "source_event_id": event.id,
                "source_event_type": event.type,
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))

    def _existing_repair(
        self,
        key: tuple[str, str, str],
    ) -> ZfEvent | None:
        """Return the pending repair for this exact provider result.

        Restart sweeps can replay a durable terminal event before the worker
        emits its correction. That replay must not consume another repair
        round or send a duplicate correction prompt.
        """

        for event in reversed(self.event_log.read_all()):
            if event.type != "workflow.call.result.repair.requested":
                continue
            body = event.payload if isinstance(event.payload, dict) else {}
            control = (
                body.get("control_result_ref")
                if isinstance(body.get("control_result_ref"), Mapping)
                else {}
            )
            result_digest = str(
                body.get("result_digest") or control.get("sha256") or ""
            )
            if (
                str(body.get("operation_id") or "") == key[0]
                and str(body.get("request_hash") or "") == key[1]
                and result_digest == key[2]
            ):
                return event
        return None

    def _existing_invalid(self, key: tuple[str, str, str]) -> ZfEvent | None:
        """Return the recorded invalid fact for this exact provider result
        (replay guard, mirrors _existing_repair)."""
        for event in reversed(self.event_log.read_all()):
            if event.type != "workflow.call.result.invalid":
                continue
            body = event.payload if isinstance(event.payload, dict) else {}
            control = (
                body.get("control_result_ref")
                if isinstance(body.get("control_result_ref"), Mapping)
                else {}
            )
            result_digest = str(control.get("sha256") or "")
            if (
                str(body.get("operation_id") or "") == key[0]
                and str(body.get("request_hash") or "") == key[1]
                and result_digest == key[2]
            ):
                return event
        return None

    def _next_repair_round(self, key: tuple[str, str, str]) -> int:
        rounds = 0
        for event in self.event_log.read_all():
            if event.type != "workflow.call.result.repair.requested":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if (
                str(payload.get("operation_id") or "") == key[0]
                and str(payload.get("request_hash") or "") == key[1]
            ):
                rounds = max(rounds, int(payload.get("repair_round") or 0))
        return rounds + 1

    def _write_correction_packet(
        self,
        *,
        event: ZfEvent,
        envelope_descriptor: Mapping[str, Any],
        adapted: AdaptedControlResult,
        issues: list[dict[str, str]],
        repair_round: int,
        operation_identity: Mapping[str, str],
    ) -> dict[str, Any]:
        packet = {
            "schema_version": "call-result-correction.v1",
            **dict(operation_identity),
            "task_id": str(event.task_id or (event.payload or {}).get("task_id") or ""),
            "attempt_id": str((event.payload or {}).get("attempt_id") or (event.payload or {}).get("run_id") or ""),
            "dispatch_id": str((event.payload or {}).get("dispatch_id") or (event.payload or {}).get("run_id") or ""),
            "invalid_envelope_ref": dict(envelope_descriptor),
            "invalid_control_result_ref": adapted.descriptor,
            "required_schema": adapted.schema_version,
            "issues": issues,
            "repair_round": repair_round,
            "instruction": "Correct only the result protocol; do not restart implementation work or change product semantics.",
        }
        return write_immutable_json_sidecar(
            self.state_dir,
            packet,
            root="call-results/corrections",
            kind="call_result_correction",
            schema_version="call-result-correction.v1",
            created_by="call-result-admission",
            source_event_id=event.id,
        )


def result_protocol_mode(config: Any, payload: Mapping[str, Any] | None = None) -> str:
    payload = payload or {}
    raw = payload.get("result_protocol_mode")
    if not raw and isinstance(payload.get("result_protocol"), Mapping):
        raw = payload["result_protocol"].get("mode")
    from zf.core.workflow.flow_metadata import flow_metadata_for

    metadata = flow_metadata_for(config, payload=payload)
    if not raw:
        raw = metadata.get("result_protocol_mode")
    if not raw and isinstance(metadata.get("result_protocol"), Mapping):
        raw = metadata["result_protocol"].get("mode")
    mode = str(raw or "shadow").strip().lower()
    return mode if mode in VALID_PROTOCOL_MODES else "shadow"


def dispatch_call_result_correction(
    runtime: Any,
    *,
    source_event: ZfEvent,
    outcome: CallResultAdmissionOutcome,
) -> bool:
    """Continue the same resident provider session with a minimal correction."""

    if not outcome.repair_requested or not outcome.correction_ref:
        return False
    payload = source_event.payload if isinstance(source_event.payload, dict) else {}
    actor = str(payload.get("role_instance") or payload.get("role") or source_event.actor or "")
    role = next((
        item for item in getattr(runtime.config, "roles", [])
        if actor in {item.instance_id, item.name}
    ), None)
    if role is None:
        return False
    _wait_for_source_turn_stop(
        runtime,
        source_event=source_event,
        actor=actor,
        backend=str(getattr(role, "backend", "") or ""),
    )
    task_id = str(source_event.task_id or payload.get("task_id") or "")
    briefing_dir = Path(runtime.state_dir) / "briefings"
    briefing_dir.mkdir(parents=True, exist_ok=True)
    path = briefing_dir / f"{role.instance_id}-{task_id or 'call'}-result-correction-{outcome.repair_round}.md"
    path.write_text(
        "\n".join([
            f"Active task: {task_id or '(none)'}",
            "",
            "# Call Result Protocol Correction",
            "",
            "The previous provider turn completed work but returned an invalid control result.",
            "Do not redo implementation or change verdict/evidence semantics.",
            f"Correction packet: `{outcome.correction_ref.get('ref', '')}`",
            f"Operation: `{outcome.operation_id}`",
            f"Request hash: `{outcome.request_hash}`",
            "",
            "Read the correction packet and emit one corrected terminal result using the same task/attempt/dispatch identity.",
            "",
        ]),
        encoding="utf-8",
    )
    from zf.runtime.injection import build_task_prompt

    prompt = build_task_prompt(role.instance_id, path)
    context = runtime._dispatch_context(
        role=role,
        briefing_path=path,
        task_id=task_id,
        trace_id=source_event.correlation_id,
    )
    runtime._send_transport_task(role.instance_id, path, prompt, context)
    return True


def _wait_for_source_turn_stop(
    runtime: Any,
    *,
    source_event: ZfEvent,
    actor: str,
    backend: str,
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.1,
) -> bool:
    """Avoid submitting a correction while the source provider turn is active.

    Terminal results are emitted from a provider tool call, before the TUI's
    stop hook and input loop settle. Sending the correction immediately can
    paste its text while dropping Enter. Other backends and synthetic tests do
    not expose a per-turn stop hook, so they keep the existing fast path.
    """

    stop_type = {
        "codex": "codex.hook.stop",
        "claude-code": "claude.hook.stop",
    }.get(backend)
    event_log = getattr(runtime, "event_log", None)
    if not stop_type or event_log is None:
        return True

    deadline = time.monotonic() + max(0.0, timeout_seconds)
    source_seen = False
    while True:
        try:
            events = event_log.read_all()
        except Exception:
            return False
        for event in events:
            if event.id == source_event.id:
                source_seen = True
                continue
            if (
                source_seen
                and event.type == stop_type
                and str(event.actor or "") == actor
            ):
                return True
        if not source_seen:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.01, poll_interval))


def _semantic_verdict(control_result: Mapping[str, Any]) -> str:
    return str(control_result.get("verdict") or "pending")


__all__ = [
    "CALL_RESULT_ADAPTER_VERSION",
    "CALL_RESULT_EVENT_TYPES",
    "DEFAULT_OUTPUT_REPAIR_CAP",
    "CallResultAdmissionOutcome",
    "CallResultAdmissionService",
    "dispatch_call_result_correction",
    "result_protocol_mode",
]
