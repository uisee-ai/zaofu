"""FanoutCoordinationMixin — reader/writer fanout, synth and child
coordination (49 methods moved verbatim from orchestrator.py, P3).

Same shape as the other Orchestrator mixins: methods share the
Orchestrator instance state via self; composed in orchestrator.py.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from zf.core.config.schema import RoleConfig
from zf.core.events.module_parity import is_module_parity_scan_completed_event
from zf.core.events.model import ZfEvent
from zf.runtime.channel_workflow_bridge import emit_fanout_channel_state_update
from zf.runtime.cli_command import zf_cli_cmd
from zf.runtime.failure_kind import (
    aggregate_failure_kind,
    classify_dispatch_exception,
)
from zf.runtime.fanout_briefing_runtime import FanoutBriefingMixin
from zf.runtime.briefing_metrics import write_briefing_with_metrics
from zf.runtime.fanout_dispatch_liveness import FanoutDispatchLivenessMixin
from zf.runtime.fanout_recovery_runtime import FanoutRecoveryRuntimeMixin
from zf.runtime.durable_call_fanout import DurableCallFanoutMixin
from zf.runtime.fanout_stage_criteria import evaluate_fanout_stage_success_criteria_for_orchestrator
from zf.runtime.writer_fanout_result_binding import WriterFanoutResultBindingMixin
from zf.runtime.writer_dispatch_fence import WriterDispatchFenceMixin
from zf.runtime.injection import build_task_prompt
from zf.runtime.lane_stage_handoff import (
    LANE_STAGE_HANDOFF_FAILURE_EVENT,
    LANE_STAGE_HANDOFF_SUCCESS_EVENT,
    LANE_STAGE_REWORK_QUARANTINED_EVENT,
    LANE_STAGE_REWORK_REQUESTED_EVENT,
    evaluate_final_readiness,
    failure_target_stage_slot,
    final_readiness_already_published,
    lane_stage_event_recorded,
    lane_stage_rework_already_requested,
    per_lane_flow_for_handoff_target,
    per_lane_flow_match,
)
from zf.runtime.plan_admission import (
    emit_plan_admission_cancel,
    emit_task_map_admitted,
)
from zf.runtime.plan_synth_runtime import (
    PLAN_SYNTH_HANDOFF_KEYS,
    PlanSynthRuntimeMixin,
)
from zf.runtime.workflow_inputs import render_workflow_input_briefing_section
from zf.runtime.writer_fanout_data import _FANOUT_AFFINITY_METADATA_KEYS
from zf.runtime.task_contract_snapshot import (
    TaskContractSnapshotError,
    build_target_snapshot,
    build_task_contract_snapshot,
    current_task_contract_identity,
    descriptor_from_payload as contract_descriptor_from_payload,
    hydrate_task_contract_snapshot,
    hydrate_target_snapshot,
    snapshot_payload_fields,
    target_descriptor_from_payload,
    target_payload_fields,
    task_map_generation,
    write_task_contract_snapshot,
    write_target_snapshot,
)
from zf.runtime.attempt_ledger import failure_fingerprint
from zf.runtime.canonical_recovery import (
    PRODUCT_FAILURE_CLASS,
    build_rework_cap_payload,
    failure_class_from_payload,
    recovery_series_from_event,
    rework_dispatch_count,
    valid_series_failures,
)
from zf.runtime.rework_feedback import (
    ReworkFeedbackError,
    descriptor_from_payload as feedback_descriptor_from_payload,
    feedback_briefing_lines,
    feedback_payload_fields,
    hydrate_rework_feedback,
    write_rework_feedback,
)
from zf.runtime.verification_result import (
    VerificationResultError,
    normalize_verification_result,
    recovery_owner as verification_recovery_owner,
)

# B3: when a reject-on-fail aggregate already has a failed child the
# verdict cannot flip — cap the synth wait at this budget instead of
# the full stage timeout (R25: 40min idle on a decided round).
DEFAULT_FANOUT_TIMEOUT_S = 7200  # E6:未配置 stage 超时的保守地板
_SYNTH_DECIDED_TIMEOUT_S = 600

# Reader/verify fanout child dispatch failures caused by tmux/transport infra
# are not candidate findings. Defer them briefly so the existing pending-child
# recovery sweep can re-dispatch the briefing; after the cap, surface the real
# terminal failure to avoid an infinite loop on a permanently dead pane.
_INFRA_DISPATCH_FAILURE_MARKERS = (
    "tmux command timed out",
    "send-keys",
    "pane dispatch",
    "transport dispatch unavailable",
)
_READER_CHILD_INFRA_DISPATCH_RETRY_CAP = 3


def _is_infra_dispatch_failure(reason: str) -> bool:
    text = reason.lower()
    return any(marker in text for marker in _INFRA_DISPATCH_FAILURE_MARKERS)


_HANDOFF_REF_FIELDS = (
    "prd_ref", "task_map_ref", "artifact_refs", "evidence_refs", "source_index_ref",
)

_CONTRACT_HANDOFF_KEYS = (
    "workflow_run_id",
    "contract_revision",
    "task_map_generation",
    "base_commit",
    "contract_snapshot_ref",
    "contract_snapshot_digest",
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
    "target_snapshot_ref",
    "target_commit",
    "target_snapshot_digest",
    "impl_self_check_ref",
    "impl_self_check_digest",
    "operation_id",
    "parent_operation_id",
    "request_hash",
    "attempt_id",
    "result_protocol_mode",
    "attempt_source_manifest_ref",
    "attempt_source_manifest_digest",
    "attempt_source_manifest",
    "input_consumption_policy_ref",
    "input_consumption_policy",
    "input_consumption_policy_digest",
    "required_reads",
    "admitted_call_result_ref",
    "admitted_call_result_digest",
    "rework_of",
    "rework_attempt",
    "rework_source",
    "rework_feedback",
    "rework_categories",
    "rework_summary",
    "replan_classification",
    "failed_task_ids",
    "downstream_task_ids",
    "resume_scope",
)

def _lane_child_scope(affinity_tag: str, task_id: str) -> str:
    """Scope a lane stage child by task so durable identity never collides.

    ZF-REVIEW-140-B4(07-16 实弹):同 lane 串行多任务时,child 键只含
    feature 级 affinity_tag → 两个任务派生同一 operation id;前一任务
    settled 后,后一任务以同 id 不同 request_hash 预注册即被拒
    (request_hash_divergence),有界环烧光 rework 预算。
    """
    if task_id and task_id != affinity_tag:
        return f"{affinity_tag}-{task_id}" if affinity_tag else task_id
    return affinity_tag


_DURABLE_CALL_TRIGGER_KEYS = (
    "workflow_run_id",
    "workflow_input_manifest_ref",
    "workflow_prompt_ref",
    "prompt_kind",
    "artifact_refs",
    "input_refs",
    "required_reads",
    "result_protocol_mode",
    "durable_operation",
    "contract_snapshot_ref",
    "contract_snapshot_digest",
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
    "target_snapshot_ref",
    "target_snapshot_digest",
    "target_commit",
    "goal_id",
    "flow_kind",
    "task_map_generation",
    "candidate_head_commit",
    "candidate_ref",
    "objective_ref",
    "planning_result_ref",
    "goal_claim_set_ref",
    "goal_claim_set_digest",
    "closure_fact_ref",
    "closure_fact_digest",
    "closure_identity",
    "input_result_refs",
    "rework_of",
    "rework_attempt",
    "rework_source",
    "rework_feedback",
    "rework_categories",
    "rework_summary",
    "replan_classification",
    "task_ids",
    "failed_task_ids",
    "downstream_task_ids",
    "resume_scope",
)

_PRD_STAGE_LOOSE_FALLBACK = {
    "prd.ready": ("prd_ref", "artifact_refs", "evidence_refs"),
    "prd.approved": ("prd_ref", "artifact_refs", "evidence_refs"),
    "task_map.ready": ("task_map_ref", "artifact_refs", "evidence_refs"),
}

def assign_nonaffinity_writer_roles(task_items: list, roles: list) -> list:
    """Map each non-affinity writer task to the role it was planned for.

    The legacy dispatch assigned by list position (``roles[index]``), which
    ignored the task_map's ``owner_role`` and routed tasks to the wrong
    specialist (e.g. a frontend role doing backend work). Match each task to the
    role whose ``instance_id`` equals its ``owner_role`` when that role exists
    and is still free; fill any remaining tasks positionally with the leftover
    roles. With no matchable owner_role this reproduces the prior positional
    assignment, so owner_role-less plans are unaffected.

    Returns a list of RoleConfig aligned to ``task_items`` indices.
    """
    used: set[str] = set()
    assigned: list = []
    for item in task_items:
        owner = str((item or {}).get("owner_role") or "").strip()
        match = next(
            (r for r in roles
             if r.instance_id == owner and r.instance_id not in used),
            None,
        )
        assigned.append(match)
        if match is not None:
            used.add(match.instance_id)
    remaining = [r for r in roles if r.instance_id not in used]
    fill = 0
    for idx in range(len(assigned)):
        if assigned[idx] is None:
            assigned[idx] = remaining[fill]
            fill += 1
    return assigned


def _writer_task_value(task_item: dict, key: str) -> str:
    value = task_item.get(key)
    if value not in (None, ""):
        return str(value).strip()
    payload = task_item.get("payload")
    if isinstance(payload, dict):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value).strip()
    raw_task = task_item.get("raw_task")
    if isinstance(raw_task, dict):
        value = raw_task.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _writer_task_dependency_ids(task_item: dict) -> list[str]:
    def _coerce(value: object) -> list[str]:
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    out: list[str] = []
    seen: set[str] = set()
    sources: list[dict] = [task_item]
    payload = task_item.get("payload")
    if isinstance(payload, dict):
        sources.append(payload)
    raw_task = task_item.get("raw_task")
    if isinstance(raw_task, dict):
        sources.append(raw_task)
    for source in sources:
        for key in ("blocked_by", "depends_on"):
            for dep in _coerce(source.get(key)):
                if dep in seen:
                    continue
                seen.add(dep)
                out.append(dep)
    return out


def _writer_task_dependencies_satisfied(
    task_store,
    task_item: dict,
    *,
    completed_task_ids: set[str] | None = None,
) -> bool:
    terminal_statuses = {"done", "cancelled", "superseded"}
    completed_task_ids = completed_task_ids or set()
    for dep in _writer_task_dependency_ids(task_item):
        if dep in completed_task_ids:
            continue
        task = task_store.get(dep)
        if task is None or str(task.status or "") not in terminal_statuses:
            return False
    return True


def _contract_handoff_ref_fields(
    config,
    success_event: str,
    *,
    flow_kind: str = "",
) -> list[str]:
    from zf.core.verification.event_schema import event_schemas_for_config

    schemas = event_schemas_for_config(config, flow_kind=flow_kind)
    rule = schemas.get(success_event)
    required = rule.get("required", []) if isinstance(rule, dict) else []
    fields = [f for f in required if f in _HANDOFF_REF_FIELDS]
    if fields:
        return fields
    return list(_PRD_STAGE_LOOSE_FALLBACK.get(success_event, ()))

class FanoutCoordinationMixin(
    DurableCallFanoutMixin,
    FanoutBriefingMixin,
    FanoutDispatchLivenessMixin,
    FanoutRecoveryRuntimeMixin,
    PlanSynthRuntimeMixin,
    WriterDispatchFenceMixin,
    WriterFanoutResultBindingMixin,
):
    def _recover_unrecorded_writer_fanout_results(self) -> None:
        try:
            from zf.runtime.event_window import read_runtime_events

            events = read_runtime_events(self.event_log, self.state_dir)
        except Exception:
            return
        self._recover_durable_fanout_aggregate_results(events)
        if self._resume_unrecorded_writer_fanout_results(events):
            try:
                events = read_runtime_events(self.event_log, self.state_dir)
            except Exception:
                return
        if self._recover_overwritten_writer_fanout_dispatches(events):
            try:
                events = read_runtime_events(self.event_log, self.state_dir)
            except Exception:
                return
        if self._recover_pending_writer_fanout_dispatches(events):
            try:
                events = read_runtime_events(self.event_log, self.state_dir)
            except Exception:
                return
        self._reconcile_active_affinity_writer_fanouts()
        self._recover_incomplete_writer_fanout_aggregates(events)

    def _reconcile_active_affinity_writer_fanouts(self) -> int:
        from zf.runtime.writer_slot_reconciler import (
            reconcile_active_affinity_writer_fanouts,
        )

        return reconcile_active_affinity_writer_fanouts(self)
    @staticmethod
    def _fanout_child_wave(child: dict) -> int | None:
        value = child.get("wave")
        if value in (None, ""):
            payload = child.get("payload")
            if isinstance(payload, dict):
                value = payload.get("wave")
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    def _task_has_active_writer_fanout_dispatch(self, task_id: str) -> bool:
        task = self.task_store.get(task_id)
        if task is None:
            return False
        active_dispatch_id = str(task.active_dispatch_id or "")
        if not active_dispatch_id:
            return False
        fanout_root = self.state_dir / "fanouts"
        if not fanout_root.exists():
            return False
        for manifest_path in fanout_root.glob("*/manifest.json"):
            manifest = self._fanout_manifest(manifest_path.parent.name)
            if not manifest or manifest.get("topology") != "fanout_writer_scoped":
                continue
            aggregate = (
                manifest.get("aggregate")
                if isinstance(manifest.get("aggregate"), dict)
                else {}
            )
            if aggregate.get("status") in {"completed", "failed", "timed_out", "cancelled"}:
                continue
            for child in manifest.get("children", []) or []:
                if not isinstance(child, dict):
                    continue
                if str(child.get("status") or "") != "dispatched":
                    continue
                if str(child.get("task_id") or "") != task_id:
                    continue
                if str(child.get("run_id") or "") == active_dispatch_id:
                    return True
        return False
    def _recover_unrecorded_reader_fanout_results(self) -> None:
        try:
            from zf.runtime.event_window import read_runtime_events

            events = read_runtime_events(self.event_log, self.state_dir)
        except Exception:
            return
        self._recover_durable_fanout_aggregate_results(events)
        if self._resume_unstarted_reader_fanouts(events):
            try:
                events = read_runtime_events(self.event_log, self.state_dir)
            except Exception:
                return
        if self._recover_pending_reader_fanout_dispatches(events):
            try:
                events = read_runtime_events(self.event_log, self.state_dir)
            except Exception:
                return
        if self._recover_lost_reader_fanout_dispatches(events):
            try:
                events = read_runtime_events(self.event_log, self.state_dir)
            except Exception:
                return
        if self._resume_unrecorded_reader_fanout_results(events):
            try:
                events = read_runtime_events(self.event_log, self.state_dir)
            except Exception:
                return
        if self._resume_unrecorded_reader_synth_results(events):
            try:
                events = read_runtime_events(self.event_log, self.state_dir)
            except Exception:
                return
        self._recover_incomplete_reader_fanout_aggregates(events)

    def _fanout_stage_by_id(self, stage_id: str):
        for stage in getattr(getattr(self.config, "workflow", None), "stages", []) or []:
            if str(getattr(stage, "id", "") or "") == stage_id:
                return stage
        return None

    def _select_writer_affinity_lane_role(
        self,
        stage,
        task_item: dict,
        *,
        lane_roles: list[tuple[str, RoleConfig]],
        used_lane_ids: set[str],
    ) -> tuple[str, RoleConfig] | None:
        affinity_key = self._fanout_affinity_key(stage)
        affinity_tag = _writer_task_value(task_item, affinity_key)
        desired_roles = [
            value for value in (
                _writer_task_value(task_item, "owner_instance"),
                _writer_task_value(task_item, "owner_role"),
                _writer_task_value(task_item, "preferred_impl_role"),
            )
            if value
        ]

        def _available() -> list[tuple[str, RoleConfig]]:
            return [
                (lane_id, role) for lane_id, role in lane_roles
                if lane_id not in used_lane_ids
            ]

        if affinity_tag:
            for lane_id, role in _available():
                if lane_id == affinity_tag:
                    return lane_id, role
        for desired in desired_roles:
            for lane_id, role in _available():
                if desired in {role.instance_id, role.name}:
                    return lane_id, role
        available = _available()
        return available[0] if available else None

    def _dispatch_reader_fanout_child(
        self,
        *,
        context,
        child,
        role: RoleConfig,
        aggregate,
        causation_id: str,
        prepared_dispatch: dict[str, Any] | None = None,
    ) -> bool:
        run_id = f"run-{context.fanout_id}-{child.child_id}"
        try:
            if not self._ensure_fanout_role_dispatchable(
                role=role,
                fanout_id=context.fanout_id,
                stage_id=context.stage_id,
                child_id=child.child_id,
                run_id=run_id,
                trace_id=context.trace_id,
                causation_id=causation_id,
                prompt_kind="fanout_child",
            ):
                return False
            prepared_dispatch = prepared_dispatch or self._prepare_reader_fanout_child_operation(
                context=context,
                child=child,
                role=role,
                causation_id=causation_id,
                aggregate=aggregate,
            )
            if prepared_dispatch.get("skip"):
                return True
            skill_entries = list(prepared_dispatch.get("skill_entries") or [])
            prepared_call = prepared_dispatch.get("prepared_call")
            if prepared_call is not None and not prepared_call.should_dispatch:
                if prepared_call.ensure_status == "settled":
                    self._replay_settled_fanout_call(
                        context=context,
                        child=child,
                        prepared=prepared_call,
                        topology="fanout_reader",
                        causation_id=causation_id,
                    )
                return True
            briefing_path = self._write_fanout_briefing(
                role=role,
                context=context,
                child_id=child.child_id,
                run_id=run_id,
                aggregate=aggregate,
                child_payload=child.payload,
                skill_entries=skill_entries,
            )
            prompt = build_task_prompt(
                role.instance_id,
                briefing_path,
                prompt_kind="fanout_child",
            )
            dispatch_context = self._dispatch_context(
                role=role,
                briefing_path=briefing_path,
                trace_id=context.trace_id,
            )
            self._send_transport_task(
                role.instance_id,
                briefing_path,
                prompt,
                dispatch_context,
            )
            if prepared_call is not None:
                from zf.runtime.call_result_runtime import mark_call_operation_started

                mark_call_operation_started(
                    self,
                    prepared_call,
                    task_id=str(child.payload.get("task_id") or ""),
                    dispatch_id=run_id,
                    causation_id=causation_id,
                    correlation_id=context.trace_id,
                )
            self._note_prompt_sent(role.instance_id, run_id)
            dispatched = context.child_dispatched_event(child, run_id=run_id)
            dispatched.payload["skills"] = list(role.skills)
            dispatched.payload["briefing_path"] = str(briefing_path)
            if child.payload:
                dispatched.payload["payload"] = dict(child.payload)
                self._copy_fanout_assignment_metadata(
                    dispatched.payload,
                    child.payload,
                )
                for key in _CONTRACT_HANDOFF_KEYS:
                    value = child.payload.get(key)
                    if value not in (None, ""):
                        dispatched.payload[key] = value
            try:
                snapshot_result, snapshot_payload = (
                    self._write_fanout_child_runtime_snapshot(
                        role=role,
                        payload=dispatched.payload,
                        briefing_path=briefing_path,
                    )
                )
                dispatched.payload["snapshot_ref"] = snapshot_result.snapshot_ref
                self.event_writer.append(ZfEvent(
                    type="runtime.snapshot.recorded",
                    actor="zf-cli",
                    task_id=dispatched.payload.get("task_id") or None,
                    payload=snapshot_payload,
                    causation_id=causation_id,
                    correlation_id=context.trace_id,
                ))
            except Exception as snapshot_exc:
                self.event_writer.append(ZfEvent(
                    type="runtime.snapshot.invalid",
                    actor="zf-cli",
                    task_id=dispatched.payload.get("task_id") or None,
                    payload={
                        "source": "fanout_child",
                        "reason": str(snapshot_exc),
                        "fanout_id": context.fanout_id,
                        "child_id": child.child_id,
                        "run_id": run_id,
                    },
                    causation_id=causation_id,
                    correlation_id=context.trace_id,
                ))
            self.event_writer.append(dispatched)
            self._set_worker_state(
                role.instance_id,
                "busy",
                reason=f"dispatched fanout child {context.fanout_id}/{child.child_id}",
                task_id=str(dispatched.payload.get("task_id") or ""),
            )
            return True
        except Exception as exc:
            reason = str(exc)
            if (
                _is_infra_dispatch_failure(reason)
                and self._reader_child_infra_dispatch_deferrals(
                    context.fanout_id,
                    child.child_id,
                )
                < _READER_CHILD_INFRA_DISPATCH_RETRY_CAP
            ):
                self.event_writer.append(ZfEvent(
                    type="fanout.child.dispatch_deferred",
                    actor="zf-cli",
                    payload={
                        "fanout_id": context.fanout_id,
                        "trace_id": context.trace_id,
                        "stage_id": context.stage_id,
                        "child_id": child.child_id,
                        "run_id": run_id,
                        "role_instance": child.role_instance,
                        "reason": reason,
                    },
                    causation_id=causation_id,
                    correlation_id=context.trace_id,
                ))
                return True
            self.event_writer.append(ZfEvent(
                type="fanout.child.failed",
                actor="zf-cli",
                payload={
                    "fanout_id": context.fanout_id,
                    "trace_id": context.trace_id,
                    "stage_id": context.stage_id,
                    "child_id": child.child_id,
                    "run_id": run_id,
                    "role_instance": child.role_instance,
                    "reason": reason,
                },
                causation_id=causation_id,
                correlation_id=context.trace_id,
            ))
            return True

    def _recover_lost_reader_fanout_dispatches(
        self,
        events: list[ZfEvent],
    ) -> bool:
        """Retry reader children whose dispatched provider session was replaced.

        Reader fanout children often have no kanban task id, so a watcher
        restart or context refresh can leave the manifest at ``dispatched``
        while the worker pane/session has been refreshed back to idle. Treat
        explicit session replacement events as infrastructure loss and rerun
        once without spending business retry budget.
        """
        terminal_statuses = {"completed", "failed", "timed_out", "cancelled"}
        event_index = {event.id: index for index, event in enumerate(events)}
        dispatches: dict[tuple[str, str], list[ZfEvent]] = {}
        terminal_children: set[tuple[str, str]] = set()
        lost_events_by_role: dict[str, list[tuple[int, ZfEvent]]] = {}
        dispatch_lost_count: dict[tuple[str, str], int] = {}
        for index, event in enumerate(events):
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.type == "fanout.child.dispatched":
                fanout_id = str(payload.get("fanout_id") or "")
                child_id = str(payload.get("child_id") or "")
                if fanout_id and child_id:
                    dispatches.setdefault((fanout_id, child_id), []).append(event)
                continue
            if event.type in {"fanout.child.completed", "fanout.child.failed"}:
                fanout_id = str(payload.get("fanout_id") or "")
                child_id = str(payload.get("child_id") or "")
                if fanout_id and child_id:
                    terminal_children.add((fanout_id, child_id))
                continue
            if event.type == "fanout.child.dispatch_lost":
                fanout_id = str(payload.get("fanout_id") or "")
                child_id = str(payload.get("child_id") or "")
                if fanout_id and child_id:
                    key = (fanout_id, child_id)
                    dispatch_lost_count[key] = dispatch_lost_count.get(key, 0) + 1
                continue
            role_instance = self._reader_dispatch_lost_role(event)
            if role_instance:
                lost_events_by_role.setdefault(role_instance, []).append((index, event))

        fanout_root = self.state_dir / "fanouts"
        if not fanout_root.exists():
            return False
        for manifest_path in fanout_root.glob("*/manifest.json"):
            fanout_id = manifest_path.parent.name
            manifest = self._fanout_manifest(fanout_id)
            if not manifest or manifest.get("topology") != "fanout_reader":
                continue
            stale_reason, _superseded_by = self._fanout_identity_stale_reason(
                fanout_id,
            )
            if stale_reason:
                continue
            aggregate = (
                manifest.get("aggregate")
                if isinstance(manifest.get("aggregate"), dict)
                else {}
            )
            if (
                str(manifest.get("status") or "") in terminal_statuses
                or str(aggregate.get("status") or "") in terminal_statuses
            ):
                continue
            max_retries = int(
                (manifest.get("aggregate_config") or {}).get("max_retries") or 0
            )
            allowed_recoveries = max(1, max_retries)
            for child in manifest.get("children", []) or []:
                if not isinstance(child, dict):
                    continue
                if str(child.get("status") or "") != "dispatched":
                    continue
                child_id = str(child.get("child_id") or "")
                role_instance = str(child.get("role_instance") or "")
                if not child_id or not role_instance:
                    continue
                key = (fanout_id, child_id)
                if key in terminal_children:
                    continue
                child_dispatches = dispatches.get(key, [])
                if not child_dispatches:
                    continue
                if dispatch_lost_count.get(key, 0) >= allowed_recoveries:
                    continue
                latest_dispatch = child_dispatches[-1]
                latest_index = event_index.get(latest_dispatch.id, -1)
                if latest_index < 0:
                    continue
                lost_event = self._reader_dispatch_lost_event_after(
                    lost_events_by_role.get(role_instance, []),
                    latest_index,
                )
                if lost_event is None:
                    continue
                lost_index = event_index.get(lost_event.id, latest_index)
                activity_index = (
                    latest_index
                    if lost_event.type == "cost.usage.capture_miss"
                    else lost_index
                )
                if self._reader_role_has_activity_after_dispatch(
                    events,
                    role_instance,
                    activity_index,
                ):
                    continue
                self.event_writer.append(ZfEvent(
                    type="fanout.child.dispatch_lost",
                    actor="zf-cli",
                    task_id=str(child.get("task_id") or "") or None,
                    payload={
                        "fanout_id": fanout_id,
                        "trace_id": str(manifest.get("trace_id") or ""),
                        "stage_id": str(manifest.get("stage_id") or ""),
                        "child_id": child_id,
                        "run_id": str(child.get("run_id") or ""),
                        "role_instance": role_instance,
                        "task_id": str(child.get("task_id") or ""),
                        "reason": "reader_worker_session_replaced_after_dispatch",
                        "lost_signal_event_id": lost_event.id,
                        "lost_signal_type": lost_event.type,
                    },
                    causation_id=lost_event.id,
                    correlation_id=str(manifest.get("trace_id") or ""),
                ))
                self._set_worker_state(
                    role_instance,
                    "idle",
                    reason=(
                        "reader fanout dispatch lost after worker session "
                        "replacement"
                    ),
                    force=True,
                )
                self._retry_fanout_child(
                    manifest=manifest,
                    child=child,
                    previous_dispatch=latest_dispatch,
                    attempt=len(child_dispatches),
                )
                return True
        return False

    @staticmethod
    def _reader_dispatch_lost_role(event: ZfEvent) -> str:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "worker.refresh.triggered":
            reason = str(payload.get("reason") or "").strip().lower()
            if reason in {"drift", "context_pressure", "task_complete"}:
                return ""
            return str(
                payload.get("role")
                or payload.get("instance_id")
                or event.actor
                or ""
            ).strip()
        if event.type == "cost.usage.capture_miss":
            reason = str(payload.get("reason") or "")
            if "session file not found" in reason:
                return str(event.actor or payload.get("role") or "").strip()
        if event.type == "worker.launch_artifact.written":
            try:
                launch_attempt = int(payload.get("launch_attempt") or 0)
            except (TypeError, ValueError):
                launch_attempt = 0
            if launch_attempt > 1:
                return str(
                    payload.get("instance_id")
                    or payload.get("role")
                    or event.actor
                    or ""
                ).strip()
        return ""

    @staticmethod
    def _reader_dispatch_lost_event_after(
        role_events: list[tuple[int, ZfEvent]],
        dispatch_index: int,
    ) -> ZfEvent | None:
        for index, event in role_events:
            if index > dispatch_index:
                return event
        return None

    @staticmethod
    def _reader_role_has_activity_after_dispatch(
        events: list[ZfEvent],
        role_instance: str,
        dispatch_index: int,
    ) -> bool:
        """Return whether a reader worker showed real activity after dispatch.

        Cost capture misses and lock cleanup telemetry are weaker than direct
        agent output. If a role has emitted usage/text/tool/result events after
        the fanout child was dispatched, the briefing was accepted and should be
        allowed to finish or be handled by the normal stuck-worker path.
        """

        activity_types = {
            "agent.usage",
            "agent.text",
            "agent.tool.use",
            "agent.tool.result",
            "refactor.scan.completed",
            "verify.child.completed",
            "verify.child.failed",
            "judge.passed",
            "judge.failed",
        }
        for index, event in enumerate(events):
            if index <= dispatch_index:
                continue
            if event.type not in activity_types:
                continue
            if str(event.actor or "").strip() == role_instance:
                return True
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("role_instance") or "").strip() == role_instance:
                return True
        return False

    def _recover_unstarted_reader_fanouts(self) -> None:
        try:
            from zf.runtime.event_window import read_runtime_events

            events = read_runtime_events(self.event_log, self.state_dir)
        except Exception:
            return
        self._resume_unstarted_reader_fanouts(events)
    def _resume_unstarted_reader_fanouts(self, events: list[ZfEvent]) -> bool:
        reader_stages_by_trigger: dict[str, list] = {}
        reader_stages_by_id: dict[str, object] = {}
        for stage in getattr(self.config.workflow, "stages", []):
            if stage.topology != "fanout_reader":
                continue
            reader_stages_by_trigger.setdefault(str(stage.trigger or ""), []).append(stage)
            reader_stages_by_id[str(stage.id or "")] = stage
        if not reader_stages_by_trigger:
            return False

        events_by_id = {event.id: event for event in events}
        terminal_pairs: set[tuple[str, str]] = set()
        terminal_keys: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        for event in events:
            if event.type not in {"fanout.started", "fanout.cancelled"}:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            stage_id = str(payload.get("stage_id") or "")
            trigger_event_id = str(payload.get("trigger_event_id") or "")
            if stage_id and trigger_event_id:
                terminal_pairs.add((stage_id, trigger_event_id))
                stage = reader_stages_by_id.get(stage_id)
                trigger_event = events_by_id.get(trigger_event_id)
                if stage is not None and trigger_event is not None:
                    terminal_keys.add((
                        stage_id,
                        self._reader_fanout_trigger_key(stage, trigger_event),
                    ))

        replay_candidates: dict[
            tuple[str, tuple[tuple[str, str], ...]],
            tuple[int, object, ZfEvent],
        ] = {}
        for index, event in enumerate(events):
            stages = reader_stages_by_trigger.get(event.type)
            if not stages:
                continue
            for stage in stages:
                if not self._fanout_stage_matches_trigger_event(stage, event):
                    continue
                stage_id = str(stage.id or "")
                if (stage_id, event.id) in terminal_pairs:
                    continue
                key = (stage_id, self._reader_fanout_trigger_key(stage, event))
                if key in terminal_keys:
                    continue
                replay_candidates[key] = (index, stage, event)

        recovered = False
        for _index, stage, event in sorted(
            replay_candidates.values(),
            key=lambda item: item[0],
        ):
            stage_id = str(stage.id or "")
            if (stage_id, event.id) in terminal_pairs:
                continue
            key = (stage_id, self._reader_fanout_trigger_key(stage, event))
            if key in terminal_keys:
                continue
            before = len(self.event_log.read_all())
            self._maybe_start_reader_fanout(event)
            new_events = self.event_log.read_all()[before:]
            if not new_events:
                continue
            recovered = True
            for new_event in new_events:
                payload = new_event.payload if isinstance(new_event.payload, dict) else {}
                new_stage_id = str(payload.get("stage_id") or "")
                trigger_event_id = str(payload.get("trigger_event_id") or "")
                if (
                    new_event.type in {"fanout.started", "fanout.cancelled"}
                    and new_stage_id
                    and trigger_event_id
                ):
                    terminal_pairs.add((new_stage_id, trigger_event_id))
                    terminal_stage = reader_stages_by_id.get(new_stage_id)
                    trigger_event = events_by_id.get(trigger_event_id)
                    if terminal_stage is not None and trigger_event is not None:
                        terminal_keys.add((
                            new_stage_id,
                            self._reader_fanout_trigger_key(terminal_stage, trigger_event),
                        ))
        return recovered
    @staticmethod
    def _reader_fanout_trigger_key(
        stage,
        event: ZfEvent,
    ) -> tuple[tuple[str, str], ...]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        key_parts: list[tuple[str, str]] = []
        for name in (
            "workflow_run_id",
            "pattern_id",
            "fanout_id",
            "candidate_ref",
            "candidate_head",
            "candidate_head_commit",
            "commit",
            "task_map_ref",
            "source_index_ref",
            "rework_of",
            "pdd_id",
            "feature_id",
        ):
            value = str(payload.get(name) or "").strip()
            if value:
                key_parts.append((name, value))
        task_id = str(event.task_id or "").strip()
        if task_id:
            key_parts.append(("task_id", task_id))
        if key_parts:
            return tuple(key_parts)
        return (("event_id", event.id),)
    def _resume_unrecorded_reader_fanout_results(
        self,
        events: list[ZfEvent],
    ) -> bool:
        """Replay reader child result events whose fanout terminal was missed."""
        terminal_sources: set[str] = set()
        terminal_children: set[tuple[str, str]] = set()
        for event in events:
            if event.type not in {"fanout.child.completed", "fanout.child.failed"}:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            fanout_id = str(payload.get("fanout_id") or "")
            child_id = str(payload.get("child_id") or "")
            if fanout_id and child_id:
                terminal_children.add((fanout_id, child_id))
            result_event_id = str(payload.get("result_event_id") or "")
            if result_event_id:
                terminal_sources.add(result_event_id)
            if event.causation_id:
                terminal_sources.add(str(event.causation_id))

        recovered = False
        for event in events:
            if event.id in terminal_sources:
                continue
            payload = self._fanout_result_payload(event)
            fanout_id = str(payload.get("fanout_id") or "")
            child_id = str(payload.get("child_id") or payload.get("child_run") or "")
            if not fanout_id or not child_id:
                resolved = self._resolve_orphan_reader_fanout_child(event, payload)
                if resolved is None:
                    continue
                fanout_id, child_id = resolved
            manifest = self._fanout_manifest(fanout_id)
            if not manifest or manifest.get("topology") != "fanout_reader":
                continue
            aggregate_config = manifest.get("aggregate_config") or {}
            success_event = str(aggregate_config.get("success_event") or "")
            failure_event = str(aggregate_config.get("failure_event") or "")
            child_success_event, child_failure_event = self._fanout_child_result_events(
                aggregate_config,
            )
            status = str(payload.get("status") or "")
            is_child_result = (
                event.type in {
                    child_success_event,
                    child_failure_event,
                    success_event,
                    failure_event,
                }
                or status in {
                    "completed",
                    "passed",
                    "approved",
                    "success",
                    "failed",
                    "failure",
                    "rejected",
                }
            )
            if not is_child_result:
                continue
            child = self._fanout_child(manifest, child_id)
            if not child:
                continue
            if (
                str(child.get("status") or "") in {"completed", "failed"}
                and (fanout_id, child_id) in terminal_children
            ):
                continue
            before = len(self.event_log.read_all())
            self._maybe_update_reader_fanout(event)
            after = len(self.event_log.read_all())
            recovered = recovered or after > before
        return recovered
    def _resume_unrecorded_reader_synth_results(
        self,
        events: list[ZfEvent],
    ) -> bool:
        """Replay reader synth verdicts whose aggregate terminal was missed."""
        completed_fanouts: set[str] = set()
        completed_synth_sources: set[tuple[str, str]] = set()
        for event in events:
            if event.type != "fanout.aggregate.completed":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            fanout_id = str(payload.get("fanout_id") or "")
            if not fanout_id:
                continue
            completed_fanouts.add(fanout_id)
            synth_event_id = str(payload.get("synth_event_id") or "")
            if synth_event_id:
                completed_synth_sources.add((fanout_id, synth_event_id))
            if event.causation_id:
                completed_synth_sources.add((fanout_id, str(event.causation_id)))

        recovered = False
        for event in events:
            if event.type != "fanout.synth.completed":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            fanout_id = str(payload.get("fanout_id") or "")
            if not fanout_id:
                continue
            if fanout_id in completed_fanouts:
                continue
            if (fanout_id, event.id) in completed_synth_sources:
                continue
            manifest = self._fanout_manifest(fanout_id)
            if not manifest or manifest.get("topology") != "fanout_reader":
                continue
            aggregate = (
                manifest.get("aggregate")
                if isinstance(manifest.get("aggregate"), dict)
                else {}
            )
            if aggregate.get("status") in {"completed", "failed", "timed_out", "cancelled"}:
                continue
            aggregate_config = (
                manifest.get("aggregate_config")
                if isinstance(manifest.get("aggregate_config"), dict)
                else {}
            )
            if not str(aggregate_config.get("synth_role") or ""):
                continue
            children = [
                child for child in manifest.get("children", []) or []
                if isinstance(child, dict)
            ]
            if not children:
                continue
            statuses = [str(child.get("status") or "") for child in children]
            if not all(status in {"completed", "failed"} for status in statuses):
                continue
            before = len(self.event_log.read_all())
            self._handle_fanout_synth_completed(event)
            after = len(self.event_log.read_all())
            recovered = recovered or after > before
        return recovered
    def _recover_incomplete_reader_fanout_aggregates(
        self,
        events: list[ZfEvent],
    ) -> bool:
        completed_fanouts = {
            str((event.payload or {}).get("fanout_id") or "")
            for event in events
            if event.type == "fanout.aggregate.completed"
            and isinstance(event.payload, dict)
        }
        recovered = False
        fanout_root = self.state_dir / "fanouts"
        if not fanout_root.exists():
            return False
        for manifest_path in fanout_root.glob("*/manifest.json"):
            fanout_id = manifest_path.parent.name
            manifest = self._fanout_manifest(fanout_id)
            if not manifest or manifest.get("topology") != "fanout_reader":
                continue
            if fanout_id in completed_fanouts:
                continue
            aggregate = (
                manifest.get("aggregate")
                if isinstance(manifest.get("aggregate"), dict)
                else {}
            )
            if aggregate.get("status") in {"completed", "failed", "timed_out", "cancelled"}:
                continue
            children = [
                child for child in manifest.get("children", []) or []
                if isinstance(child, dict)
            ]
            if not children:
                continue
            statuses = [str(child.get("status") or "") for child in children]
            if not all(status in {"completed", "failed"} for status in statuses):
                continue
            before = len(self.event_log.read_all())
            self._evaluate_reader_fanout(fanout_id)
            after = len(self.event_log.read_all())
            recovered = recovered or after > before
        return recovered
    def _resume_unrecorded_writer_fanout_results(
        self,
        events: list[ZfEvent],
    ) -> bool:
        """Replay writer completion events whose fanout terminal was missed.

        A watcher restart or mid-run code refresh can leave ``dev.build.done``
        durable in ``events.jsonl`` while the derived
        ``fanout.child.completed``/``fanout.child.failed`` event was never
        appended. Late ``task.ref.updated`` events from pending handoff
        reconciliation can also repair an earlier "missing task ref" failure.
        Re-run the normal writer fanout updater so recovery uses the same
        admission gates and aggregate logic as live event handling.
        """
        terminal_sources: set[str] = set()
        terminal_children: set[tuple[str, str]] = set()
        for event in events:
            if event.type not in {"fanout.child.completed", "fanout.child.failed"}:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            fanout_id = str(payload.get("fanout_id") or "")
            child_id = str(payload.get("child_id") or "")
            if fanout_id and child_id:
                terminal_children.add((fanout_id, child_id))
            result_event_id = str(payload.get("result_event_id") or "")
            if result_event_id:
                terminal_sources.add(result_event_id)
            if event.causation_id:
                terminal_sources.add(str(event.causation_id))

        recovered = False
        for event in events:
            if event.type not in {
                "dev.build.done",
                "dev.failed",
                "dev.blocked",
                "task.ref.updated",
                "task.ref.rejected",
            }:
                continue
            if event.id in terminal_sources:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            fanout_id = str(payload.get("fanout_id") or "")
            child_id = str(payload.get("child_id") or payload.get("child_run") or "")
            if not fanout_id or not child_id:
                continue
            manifest = self._fanout_manifest(fanout_id)
            if not manifest or manifest.get("topology") != "fanout_writer_scoped":
                continue
            child = self._fanout_child(manifest, child_id)
            if not child:
                continue
            status = str(child.get("status") or "")
            if status == "completed":
                continue
            if status == "failed" and event.type not in {
                "dev.build.done",
                "task.ref.updated",
            }:
                continue
            if status in {"completed", "failed"} and (fanout_id, child_id) in terminal_children:
                continue
            before = len(self.event_log.read_all())
            self._maybe_update_writer_fanout(event)
            after = len(self.event_log.read_all())
            recovered = recovered or after > before
        return recovered
    def _recover_overwritten_writer_fanout_dispatches(
        self,
        events: list[ZfEvent],
    ) -> bool:
        """Retry a writer child whose lane was reused before it completed.

        R37 exposed a double-routing bug: the fanout runtime released an
        affinity lane and dispatched a queued child, while the normal task
        rework path also sent another task to the same role. The original
        fanout child stayed ``dispatched`` in the manifest, but a later
        ``fanout.child.completed`` for a different child on the same
        ``role_instance`` proved that the role had moved on. Recover that
        state immediately instead of waiting for the full stage timeout.
        """
        root = self.state_dir / "fanouts"
        if not root.exists():
            return False
        event_index = {event.id: index for index, event in enumerate(events)}
        dispatches: dict[tuple[str, str], list[ZfEvent]] = {}
        terminal_by_role: dict[tuple[str, str], list[tuple[int, ZfEvent]]] = {}
        for index, event in enumerate(events):
            if event.type not in {
                "fanout.child.dispatched",
                "fanout.child.completed",
                "fanout.child.failed",
            }:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            fanout_id = str(payload.get("fanout_id") or "")
            child_id = str(payload.get("child_id") or "")
            role_instance = str(payload.get("role_instance") or "")
            if not fanout_id or not child_id:
                continue
            if event.type == "fanout.child.dispatched":
                dispatches.setdefault((fanout_id, child_id), []).append(event)
                continue
            if role_instance:
                terminal_by_role.setdefault(
                    (fanout_id, role_instance),
                    [],
                ).append((index, event))

        for manifest_path in root.glob("*/manifest.json"):
            fanout_id = manifest_path.parent.name
            manifest = self._fanout_manifest(fanout_id)
            if not manifest or manifest.get("topology") != "fanout_writer_scoped":
                continue
            stale_reason, _superseded_by = self._fanout_identity_stale_reason(
                fanout_id,
            )
            if stale_reason:
                continue
            aggregate = (
                manifest.get("aggregate")
                if isinstance(manifest.get("aggregate"), dict)
                else {}
            )
            if aggregate.get("status") in {
                "completed",
                "failed",
                "timed_out",
                "cancelled",
            }:
                continue
            max_retries = int(
                (manifest.get("aggregate_config") or {}).get("max_retries") or 0
            )
            allowed_recoveries = max(1, max_retries)
            for child in manifest.get("children", []) or []:
                if not isinstance(child, dict):
                    continue
                if str(child.get("status") or "") != "dispatched":
                    continue
                child_id = str(child.get("child_id") or "")
                role_instance = str(child.get("role_instance") or "")
                if not child_id or not role_instance:
                    continue
                child_dispatches = dispatches.get((fanout_id, child_id), [])
                if not child_dispatches:
                    continue
                if len(child_dispatches) > allowed_recoveries:
                    continue
                latest_dispatch = child_dispatches[-1]
                latest_index = event_index.get(latest_dispatch.id, -1)
                if latest_index < 0:
                    continue
                overwrite_event = None
                for terminal_index, terminal_event in terminal_by_role.get(
                    (fanout_id, role_instance),
                    [],
                ):
                    if terminal_index <= latest_index:
                        continue
                    terminal_payload = (
                        terminal_event.payload
                        if isinstance(terminal_event.payload, dict)
                        else {}
                    )
                    if str(terminal_payload.get("child_id") or "") == child_id:
                        overwrite_event = None
                        break
                    overwrite_event = terminal_event
                if overwrite_event is None:
                    continue
                overwrite_payload = (
                    overwrite_event.payload
                    if isinstance(overwrite_event.payload, dict)
                    else {}
                )
                self.event_writer.append(ZfEvent(
                    type="fanout.child.dispatch_lost",
                    actor="zf-cli",
                    task_id=str(child.get("task_id") or "") or None,
                    payload={
                        "fanout_id": fanout_id,
                        "trace_id": str(manifest.get("trace_id") or ""),
                        "stage_id": str(manifest.get("stage_id") or ""),
                        "child_id": child_id,
                        "run_id": str(child.get("run_id") or ""),
                        "role_instance": role_instance,
                        "task_id": str(child.get("task_id") or ""),
                        "reason": "role_instance_completed_other_child_after_dispatch",
                        "overwritten_by_child_id": str(
                            overwrite_payload.get("child_id") or ""
                        ),
                        "overwritten_by_event_id": overwrite_event.id,
                    },
                    causation_id=overwrite_event.id,
                    correlation_id=str(manifest.get("trace_id") or ""),
                ))
                self._set_worker_state(
                    role_instance,
                    "idle",
                    reason="writer fanout dispatch overwritten by another child",
                    force=True,
                )
                self._retry_fanout_child(
                    manifest=manifest,
                    child=child,
                    previous_dispatch=latest_dispatch,
                    attempt=len(child_dispatches),
                )
                return True
        return False
    def _recover_incomplete_writer_fanout_aggregates(
        self,
        events: list[ZfEvent],
    ) -> bool:
        completed_fanouts = {
            str((event.payload or {}).get("fanout_id") or "")
            for event in events
            if event.type == "fanout.aggregate.completed"
            and isinstance(event.payload, dict)
        }
        recovered = False
        fanout_root = self.state_dir / "fanouts"
        if not fanout_root.exists():
            return False
        for manifest_path in fanout_root.glob("*/manifest.json"):
            fanout_id = manifest_path.parent.name
            manifest = self._fanout_manifest(fanout_id)
            if not manifest or manifest.get("topology") != "fanout_writer_scoped":
                continue
            children = [
                child for child in manifest.get("children", []) or []
                if isinstance(child, dict)
            ]
            if not children:
                continue
            task_ids = {
                str(child.get("task_id") or "")
                for child in children
                if child.get("task_id")
            }
            retry_requested = self._failed_writer_fanout_retry_requested(
                fanout_id,
                events,
                task_ids,
            )
            refresh_requested = self._completed_writer_fanout_refresh_requested(
                fanout_id,
                events,
            )
            if (
                fanout_id in completed_fanouts
                and not retry_requested
                and not refresh_requested
            ):
                continue
            statuses = [str(child.get("status") or "") for child in children]
            if not all(status in {"completed", "failed"} for status in statuses):
                continue
            before = len(self.event_log.read_all())
            self._evaluate_writer_fanout(
                fanout_id,
                force_retry=retry_requested or refresh_requested,
            )
            after = len(self.event_log.read_all())
            recovered = recovered or after > before
        return recovered

    @staticmethod
    def _completed_writer_fanout_refresh_requested(
        fanout_id: str,
        events: list[ZfEvent],
    ) -> bool:
        latest_aggregate_idx = -1
        latest_repair_idx = -1
        for idx, event in enumerate(events):
            if not isinstance(event.payload, dict):
                continue
            if event.payload.get("fanout_id") != fanout_id:
                continue
            if event.type == "fanout.aggregate.completed":
                latest_aggregate_idx = idx
            elif (
                event.type == "fanout.child.completed"
                and event.payload.get("recovery_reason")
                == "late_dev_build_done_replaces_completed_child"
            ):
                latest_repair_idx = idx
        return latest_repair_idx > latest_aggregate_idx

    @staticmethod
    def _failed_writer_fanout_retry_requested(
        fanout_id: str,
        events: list[ZfEvent],
        task_ids: set[str],
    ) -> bool:
        latest_failure_idx = -1
        latest_failure_event_ids: set[str] = set()
        for idx, event in enumerate(events):
            if not isinstance(event.payload, dict):
                continue
            if event.payload.get("fanout_id") != fanout_id:
                continue
            if event.type == "integration.failed":
                latest_failure_idx = idx
                latest_failure_event_ids = {event.id}
            elif (
                event.type == "fanout.aggregate.completed"
                and event.payload.get("status") == "failed"
            ):
                latest_failure_idx = idx
                latest_failure_event_ids = {event.id}
        if latest_failure_idx < 0:
            return False

        saw_retry_signal = False
        for event in events[latest_failure_idx + 1:]:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if (
                event.type == "fanout.aggregate.started"
                and payload.get("fanout_id") == fanout_id
            ):
                return False
            if (
                event.type == "workflow.resume.applied"
                and str(payload.get("source_event_id") or "") in latest_failure_event_ids
            ):
                saw_retry_signal = True
            elif (
                event.type in {"task.ref.updated", "dev.build.done"}
                and str(event.task_id or "") in task_ids
            ):
                saw_retry_signal = True
        return saw_retry_signal
    @staticmethod
    def _fanout_stage_matches_trigger_event(stage, event: ZfEvent) -> bool:
        payload = event.payload if isinstance(event.payload, dict) else {}
        stage_kind = str(getattr(stage, "flow_kind", "") or "").strip().lower()
        if stage_kind:
            from zf.core.workflow.flow_metadata import flow_kind_from_payload

            if flow_kind_from_payload(payload) != stage_kind:
                return False
        if getattr(event, "type", "") != "workflow.invoke.requested":
            return True
        pattern_id = str(
            payload.get("pattern_id")
            or payload.get("stage_id")
            or ""
        ).strip()
        if not pattern_id:
            return False
        return pattern_id == str(getattr(stage, "id", "") or "")

    def _normalize_lane_verification_result(
        self,
        child_payload: dict,
        *,
        manifest: dict,
    ) -> tuple[dict | None, str]:
        """Return a typed result only for reader-side immutable targets."""

        if str(manifest.get("topology") or "") != "fanout_reader":
            return None, ""
        if not str(child_payload.get("target_snapshot_digest") or "").strip():
            return None, ""
        try:
            descriptor = contract_descriptor_from_payload(child_payload)
            contract_snapshot = hydrate_task_contract_snapshot(
                self.state_dir,
                descriptor,
                expected={"task_id": str(child_payload.get("task_id") or "")},
            )
            target_descriptor = target_descriptor_from_payload(child_payload)
            target_body = hydrate_target_snapshot(
                self.state_dir,
                target_descriptor,
                expected={
                    "contract_snapshot_ref": descriptor["ref"],
                    "contract_snapshot_digest": descriptor["sha256"],
                    "target_commit": str(child_payload.get("target_commit") or ""),
                },
            )
            target_snapshot = {
                **target_body,
                **target_payload_fields(target_descriptor),
            }
            explicit_result = isinstance(child_payload.get("verification_result"), dict)
            profile = str(
                getattr(getattr(self.config.workflow, "dag", None), "schema_profile", "")
                or ""
            )
            result = normalize_verification_result(
                child_payload,
                contract_snapshot=contract_snapshot,
                target_snapshot=target_snapshot,
                default_owner="task_verify",
                default_tier="task_non_smoke",
                strict=explicit_result or profile == "canonical-dag/v4",
                require_rework_items=bool(
                    getattr(self.config.workflow, "impl_self_check_required", False)
                ),
            )
            return result, ""
        except (TaskContractSnapshotError, VerificationResultError) as exc:
            try:
                descriptor = contract_descriptor_from_payload(child_payload)
                contract_snapshot = hydrate_task_contract_snapshot(
                    self.state_dir,
                    descriptor,
                    expected={"task_id": str(child_payload.get("task_id") or "")},
                )
                target_descriptor = target_descriptor_from_payload(child_payload)
                target_body = hydrate_target_snapshot(
                    self.state_dir,
                    target_descriptor,
                    expected={
                        "contract_snapshot_ref": descriptor["ref"],
                        "contract_snapshot_digest": descriptor["sha256"],
                        "target_commit": str(child_payload.get("target_commit") or ""),
                    },
                )
                target_snapshot = {
                    **target_body,
                    **target_payload_fields(target_descriptor),
                }
                failed = normalize_verification_result(
                    {"status": "failed", "reason": str(exc)},
                    contract_snapshot=contract_snapshot,
                    target_snapshot=target_snapshot,
                    default_owner="task_verify",
                    default_tier="task_non_smoke",
                    strict=False,
                )
                return failed, str(exc)
            except Exception:
                return None, str(exc)

    def _emit_lane_stage_result(
        self,
        *,
        event_type: str,
        status: str,
        source_event: ZfEvent,
        manifest: dict,
        child_payload: dict,
        extra_payload: dict | None = None,
    ) -> ZfEvent | None:
        if str(child_payload.get("assignment_strategy") or "") != "affinity_stage_slots":
            return None
        stage_id = str(child_payload.get("stage_id") or manifest.get("stage_id") or "")
        stage_slot = str(child_payload.get("stage_slot") or "")
        if not stage_id or not stage_slot:
            return None
        match = per_lane_flow_match(self.config, stage_id, stage_slot)
        if match is None:
            return None
        verification_result, verification_error = (
            self._normalize_lane_verification_result(
                child_payload,
                manifest=manifest,
            )
        )
        if verification_error:
            event_type = LANE_STAGE_HANDOFF_FAILURE_EVENT
            status = "failed"
        if verification_result is not None:
            execution_status = str(verification_result.get("execution_status") or "")
            verdict = str(verification_result.get("verdict") or "")
            if (
                execution_status == "failed"
                or verdict in {"rejected", "blocked", "abstained"}
            ):
                event_type = LANE_STAGE_HANDOFF_FAILURE_EVENT
                status = "failed"
        fanout_id = str(child_payload.get("fanout_id") or manifest.get("fanout_id") or "")
        child_id = str(child_payload.get("child_id") or "")
        try:
            events = self.event_log.read_all()
        except Exception:
            events = []
        if lane_stage_event_recorded(
            events,
            event_type=event_type,
            fanout_id=fanout_id,
            child_id=child_id,
            stage_slot=stage_slot,
            source_event_id=source_event.id,
        ):
            return None
        failure_target = (
            failure_target_stage_slot(match)
            if event_type == LANE_STAGE_HANDOFF_FAILURE_EVENT
            else ""
        )
        root_fanout_id = str(
            child_payload.get("root_fanout_id")
            or child_payload.get("upstream_root_fanout_id")
            or (
                fanout_id
                if manifest.get("topology") == "fanout_writer_scoped"
                else child_payload.get("upstream_fanout_id")
            )
            or fanout_id
            or ""
        )
        trace_id = str(
            child_payload.get("trace_id")
            or manifest.get("trace_id")
            or source_event.correlation_id
            or ""
        )
        payload = {
            "pipeline_id": str(getattr(match.pipeline, "pipeline_id", "") or ""),
            "flow_kind": str(
                getattr(match.pipeline, "flow_kind", "")
                or child_payload.get("flow_kind")
                or manifest.get("flow_kind")
                or ""
            ),
            "fanout_id": fanout_id,
            "root_fanout_id": root_fanout_id,
            "trace_id": trace_id,
            "stage_id": stage_id,
            "stage_slot": stage_slot,
            "next_stage_slot": match.next_stage_slot,
            "child_id": child_id,
            # canonical-dag v2/v3 lane 契约 required 键:kernel 锻造侧必须
            # 保证键在(值可空)。缺键曾在 blocking 档下把本事件整个换成
            # discriminator.failed,verify 级联熄火(2026-07-08 实测)。
            "attempt_id": str(child_payload.get("attempt_id") or ""),
            "handoff_ref": str(
                child_payload.get("handoff_ref")
                or child_payload.get("task_ref")
                or ""
            ),
            "run_id": str(child_payload.get("run_id") or ""),
            "role_instance": str(child_payload.get("role_instance") or ""),
            "task_id": str(child_payload.get("task_id") or source_event.task_id or ""),
            "lane_id": str(child_payload.get("lane_id") or ""),
            "lane_profile": str(child_payload.get("lane_profile") or ""),
            "affinity_tag": str(child_payload.get("affinity_tag") or ""),
            "task_ref": str(child_payload.get("task_ref") or ""),
            "task_map_ref": str(child_payload.get("task_map_ref") or manifest.get("task_map_ref") or ""),
            "source_index_ref": str(child_payload.get("source_index_ref") or manifest.get("source_index_ref") or ""),
            "source_branch": str(child_payload.get("source_branch") or ""),
            "source_commit": str(child_payload.get("source_commit") or ""),
            "workdir": str(child_payload.get("workdir") or ""),
            "pdd_id": str(child_payload.get("pdd_id") or manifest.get("pdd_id") or ""),
            "feature_id": str(
                child_payload.get("feature_id")
                or manifest.get("feature_id")
                or manifest.get("pdd_id")
                or ""
            ),
            "upstream_fanout_id": str(child_payload.get("upstream_fanout_id") or ""),
            "upstream_child_id": str(child_payload.get("upstream_child_id") or ""),
            "upstream_task_id": str(child_payload.get("upstream_task_id") or ""),
            "source_event_id": source_event.id,
            "result_event_id": str(
                child_payload.get("result_event_id")
                or (
                    source_event.payload.get("result_event_id", "")
                    if isinstance(source_event.payload, dict)
                    else ""
                )
            ),
            "status": status,
        }
        if event_type == LANE_STAGE_HANDOFF_FAILURE_EVENT:
            # canonical-dag v3 requires `failure_target` PRESENT on the failure
            # event (value may be empty when the failed stage declares no
            # rework_to). Same "kernel forges required keys present, value can
            # be empty" rule as the v2 contract keys above — omitting it trips
            # the blocking schema and replaces the whole lane.stage.failed with
            # discriminator.failed, which is strictly worse: rework routing then
            # never sees the failure at all (2026-07-08 E2E: dev.blocked lane
            # with no rework_to → discriminator.failed → run wedged).
            payload["failure_target"] = failure_target
            if failure_target:
                payload["max_rework_attempts"] = int(
                    getattr(match.pipeline, "max_rework_attempts", 0) or 0
                )
        if verification_result is not None:
            payload["verification_result"] = verification_result
            payload["verification_owner"] = str(
                verification_result.get("verification_owner") or ""
            )
            payload["verification_tier"] = str(
                verification_result.get("verification_tier") or ""
            )
            payload["failure_class"] = (
                "verifier_contract_failure"
                if verification_error
                else failure_class_from_payload({
                    "verification_result": verification_result,
                })
            )
            payload["recovery_owner"] = verification_recovery_owner(
                verification_result,
            )
            if verification_error:
                payload["reason"] = verification_error
        for key in ("report_path", "artifact_refs", "evidence_refs", "findings", "reason"):
            value = child_payload.get(key)
            if value not in (None, ""):
                payload[key] = value
        for key in _CONTRACT_HANDOFF_KEYS:
            value = child_payload.get(key)
            if value not in (None, ""):
                payload[key] = value
        if isinstance(child_payload.get("target_snapshot"), dict):
            payload["target_snapshot"] = dict(child_payload["target_snapshot"])
        payload.setdefault("evidence_refs", [])
        if event_type == LANE_STAGE_HANDOFF_FAILURE_EVENT:
            # `reason` is likewise a v3-required key on the failure event and is
            # only set above when the child supplied one — guarantee it present
            # (empty ok) so a reasonless failure does not become discriminator.
            # failed (same class as failure_target; fix the class, not one key).
            payload.setdefault("reason", "")
        if extra_payload:
            for key, value in extra_payload.items():
                if value not in (None, ""):
                    payload[key] = value
        if verification_result is not None:
            if not payload.get("evidence_refs"):
                payload["evidence_refs"] = list(
                    verification_result.get("evidence_refs") or []
                )
            if not payload.get("findings"):
                payload["findings"] = list(
                    verification_result.get("findings") or []
                )
            if not payload.get("reproduction_commands"):
                payload["reproduction_commands"] = list(
                    verification_result.get("reproduction_commands") or []
                )
        if verification_error:
            payload["reason"] = verification_error
            payload["failure_class"] = "verifier_contract_failure"
            payload["recovery_owner"] = "run_manager"
        pending_lane_event = ZfEvent(
            type=event_type,
            actor="zf-cli",
            task_id=payload["task_id"] or None,
            payload=payload,
            causation_id=source_event.id,
            correlation_id=trace_id,
        )
        if (
            event_type == LANE_STAGE_HANDOFF_FAILURE_EVENT
            and failure_class_from_payload(payload) == PRODUCT_FAILURE_CLASS
        ):
            fingerprint = failure_fingerprint(pending_lane_event)
            pending_lane_event.payload["failure_fingerprint"] = fingerprint
            task = self.task_store.get(payload["task_id"]) if payload["task_id"] else None
            allowed_paths = (
                list(getattr(getattr(task, "contract", None), "scope", []) or [])
                if task is not None
                else []
            )
            try:
                descriptor = write_rework_feedback(
                    self.state_dir,
                    task_id=payload["task_id"],
                    failure_fingerprint=fingerprint,
                    source_event=pending_lane_event,
                    source_attempt=0,
                    verification_result=verification_result,
                    allowed_paths=allowed_paths,
                    summary=str(payload.get("reason") or "lane stage rejected"),
                )
                pending_lane_event.payload.update(feedback_payload_fields(descriptor))
            except ReworkFeedbackError as exc:
                pending_lane_event.payload.update({
                    "failure_class": "verifier_contract_failure",
                    "recovery_owner": "run_manager",
                    "reason": f"rework feedback invalid: {exc}",
                })
        lane_event = self.event_writer.append(pending_lane_event)
        if event_type == LANE_STAGE_HANDOFF_SUCCESS_EVENT:
            if match.next_stage_slot:
                self._maybe_start_reader_fanout(lane_event)
            else:
                self._close_final_lane_task(lane_event)
                self._maybe_publish_lane_stage_final_ready(
                    lane_event=lane_event,
                    pipeline=match.pipeline,
                )
        elif event_type == LANE_STAGE_HANDOFF_FAILURE_EVENT:
            rework_started = self._maybe_start_lane_stage_rework(
                lane_event=lane_event,
                pipeline=match.pipeline,
            )
            if not rework_started and not match.next_stage_slot:
                self._maybe_publish_lane_stage_final_ready(
                    lane_event=lane_event,
                    pipeline=match.pipeline,
                )
        return lane_event

    def _close_final_lane_task(self, lane_event: ZfEvent) -> bool:
        """Durably close one task after its final lane stage passes.

        Terminal events are written before the TaskStore projection so a crash
        cannot archive a task without replayable closeout evidence. Candidate
        judgment remains separate and this method deliberately does not close
        the feature.
        """

        payload = lane_event.payload if isinstance(lane_event.payload, dict) else {}
        task_id = str(payload.get("task_id") or lane_event.task_id or "").strip()
        if not task_id:
            return False
        task = self.task_store.get(task_id)
        if task is None or task.status == "cancelled":
            return False
        try:
            events = self.event_log.read_all()
        except Exception:
            events = []
        evidence_exists = any(
            event.type == "task.done.evidence"
            and event.task_id == task_id
            and isinstance(event.payload, dict)
            and event.payload.get("source") == "lane_pipeline_final_stage"
            and event.payload.get("lane_stage_event_id") == lane_event.id
            for event in events
        )
        if not evidence_exists:
            if task.status != "done":
                self.event_writer.append(ZfEvent(
                    type="task.status_changed",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "from": task.status,
                        "to": "done",
                        "source": "lane_pipeline_final_stage",
                        "trigger_event": lane_event.type,
                        "trigger_event_id": lane_event.id,
                    },
                    causation_id=lane_event.id,
                    correlation_id=lane_event.correlation_id,
                ))
            self.event_writer.append(ZfEvent(
                type="task.done.evidence",
                actor="zf-cli",
                task_id=task_id,
                payload={
                    "source": "lane_pipeline_final_stage",
                    "trigger_event": lane_event.type,
                    "trigger_event_id": lane_event.id,
                    "lane_stage_event_id": lane_event.id,
                    "stage_id": str(payload.get("stage_id") or ""),
                    "stage_slot": str(payload.get("stage_slot") or ""),
                    "contract_snapshot_ref": str(
                        payload.get("contract_snapshot_ref") or ""
                    ),
                    "contract_snapshot_digest": str(
                        payload.get("contract_snapshot_digest") or ""
                    ),
                    "target_snapshot_ref": str(
                        payload.get("target_snapshot_ref") or ""
                    ),
                    "target_snapshot_digest": str(
                        payload.get("target_snapshot_digest") or ""
                    ),
                    "target_commit": str(payload.get("target_commit") or ""),
                    "verification_owner": str(
                        payload.get("verification_owner") or ""
                    ),
                    "verification_tier": str(
                        payload.get("verification_tier") or ""
                    ),
                    "evidence_refs": list(payload.get("evidence_refs") or []),
                },
                causation_id=lane_event.id,
                correlation_id=lane_event.correlation_id,
            ))
        if task.status != "done":
            updated = self.task_store.update(task_id, status="done")
            if updated is None:
                return False
            self._refresh_task_doc_projection(
                updated,
                source_event=lane_event.type,
            )
            self._unblock_resolved_dependents(
                task_id,
                trigger_event=lane_event.type,
            )
        return True

    def _maybe_start_lane_stage_rework(
        self,
        *,
        lane_event: ZfEvent,
        pipeline,
    ) -> bool:
        payload = lane_event.payload if isinstance(lane_event.payload, dict) else {}
        failure_target = str(payload.get("failure_target") or "").strip()
        if not failure_target:
            return False
        pipeline_id = str(getattr(pipeline, "pipeline_id", "") or "")
        task_id = str(payload.get("task_id") or lane_event.task_id or "")
        lane_id = str(payload.get("lane_id") or "")
        root_fanout_id = str(payload.get("root_fanout_id") or "")
        failed_stage_slot = str(payload.get("stage_slot") or "")
        if not pipeline_id or not task_id or not lane_id or not root_fanout_id:
            return False
        try:
            events = self.event_log.read_all()
        except Exception:
            events = [lane_event]
        if lane_stage_rework_already_requested(
            events,
            lane_stage_event_id=lane_event.id,
        ):
            return True
        failure_class = failure_class_from_payload(payload)
        if failure_class != PRODUCT_FAILURE_CLASS:
            self.event_writer.append(ZfEvent(
                type="lane.stage.recovery.deferred",
                actor="zf-cli",
                task_id=task_id,
                payload={
                    "task_id": task_id,
                    "lane_stage_event_id": lane_event.id,
                    "failure_class": failure_class,
                    "recovery_owner": str(payload.get("recovery_owner") or "run_manager"),
                    "reason": str(payload.get("reason") or "non-semantic lane failure"),
                },
                causation_id=lane_event.id,
                correlation_id=lane_event.correlation_id,
            ))
            return True
        if payload.get("rework_feedback_ref") or payload.get("rework_feedback_digest"):
            try:
                hydrate_rework_feedback(
                    self.state_dir,
                    feedback_descriptor_from_payload(payload),
                    expected_task_id=task_id,
                    expected_fingerprint=str(payload.get("failure_fingerprint") or ""),
                )
            except ReworkFeedbackError as exc:
                self.event_writer.append(ZfEvent(
                    type="lane.stage.rework.feedback_invalid",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "task_id": task_id,
                        "lane_stage_event_id": lane_event.id,
                        "reason": str(exc),
                        "recovery_owner": "run_manager",
                    },
                    causation_id=lane_event.id,
                    correlation_id=lane_event.correlation_id,
                ))
                return True
        elif payload.get("target_snapshot_digest"):
            self.event_writer.append(ZfEvent(
                type="lane.stage.rework.feedback_invalid",
                actor="zf-cli",
                task_id=task_id,
                payload={
                    "task_id": task_id,
                    "lane_stage_event_id": lane_event.id,
                    "reason": "typed product rejection lacks feedback ref/digest",
                    "recovery_owner": "run_manager",
                },
                causation_id=lane_event.id,
                correlation_id=lane_event.correlation_id,
            ))
            return True
        max_attempts = int(getattr(pipeline, "max_rework_attempts", 0) or 0)
        series = recovery_series_from_event(lane_event)
        failures = valid_series_failures(
            events,
            series,
            event_types={LANE_STAGE_HANDOFF_FAILURE_EVENT},
        )
        attempt = len(failures)
        dispatch_count = rework_dispatch_count(
            events,
            series,
            event_type=LANE_STAGE_REWORK_REQUESTED_EVENT,
        )
        if max_attempts and attempt > max_attempts:
            self._emit_lane_stage_rework_quarantined(
                lane_event=lane_event,
                attempt=attempt,
                max_attempts=max_attempts,
                target_stage_slot=failure_target,
                failures=failures,
            )
            return True
        target_stage = self._fanout_stage_by_id(f"{pipeline_id}-{failure_target}")
        if target_stage is None:
            return False
        if str(getattr(target_stage, "topology", "") or "") != "fanout_writer_scoped":
            return False
        if self._fanout_assignment_strategy(target_stage) != "affinity_stage_slots":
            return False
        role = self._fanout_affinity_lane_role(
            target_stage,
            lane_id=lane_id,
            stage_slot=failure_target,
        )
        if role is None:
            return False
        task_item = self._lane_stage_rework_task_item(
            lane_event=lane_event,
            pipeline_id=pipeline_id,
            target_stage_slot=failure_target,
        )
        if not task_item:
            return False
        task_item = self._writer_affinity_task_item(
            target_stage,
            task_item,
            lane_id=lane_id,
            role_instance=role.instance_id,
        )
        rework_event = self.event_writer.append(ZfEvent(
            type=LANE_STAGE_REWORK_REQUESTED_EVENT,
            actor="zf-cli",
            task_id=task_id,
            payload={
                "pipeline_id": pipeline_id,
                "root_fanout_id": root_fanout_id,
                "task_id": task_id,
                "lane_id": lane_id,
                "failed_stage_slot": failed_stage_slot,
                "target_stage_slot": failure_target,
                "attempt": attempt,
                "failure_count": attempt,
                "rework_dispatch_count": dispatch_count + 1,
                "max_attempts": max_attempts,
                "lane_stage_event_id": lane_event.id,
                "source_event_id": str(payload.get("source_event_id") or ""),
                "result_event_id": str(payload.get("result_event_id") or ""),
                "fanout_id": str(payload.get("fanout_id") or ""),
                "child_id": str(payload.get("child_id") or ""),
                "role_instance": role.instance_id,
                "reason": str(payload.get("reason") or "lane stage failed"),
                **series.to_payload(),
                "failure_class": PRODUCT_FAILURE_CLASS,
                "recovery_owner": "implementation_owner",
                "rework_feedback_ref": str(payload.get("rework_feedback_ref") or ""),
                "rework_feedback_digest": str(payload.get("rework_feedback_digest") or ""),
            },
            causation_id=lane_event.id,
            correlation_id=str(payload.get("trace_id") or lane_event.correlation_id or ""),
        ))
        self._start_lane_stage_rework_writer_fanout(
            rework_event=rework_event,
            stage=target_stage,
            role=role,
            task_item=task_item,
            attempt=attempt,
        )
        return True

    def _emit_lane_stage_rework_quarantined(
        self,
        *,
        lane_event: ZfEvent,
        attempt: int,
        max_attempts: int,
        target_stage_slot: str,
        failures: list[ZfEvent],
    ) -> None:
        payload = lane_event.payload if isinstance(lane_event.payload, dict) else {}
        task_id = str(payload.get("task_id") or lane_event.task_id or "")
        quarantine = self.event_writer.append(ZfEvent(
            type=LANE_STAGE_REWORK_QUARANTINED_EVENT,
            actor="zf-cli",
            task_id=task_id or None,
            payload={
                "pipeline_id": str(payload.get("pipeline_id") or ""),
                "root_fanout_id": str(payload.get("root_fanout_id") or ""),
                "task_id": task_id,
                "lane_id": str(payload.get("lane_id") or ""),
                "failed_stage_slot": str(payload.get("stage_slot") or ""),
                "target_stage_slot": target_stage_slot,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "lane_stage_event_id": lane_event.id,
                "reason": "lane stage rework attempts exceeded",
            },
            causation_id=lane_event.id,
            correlation_id=str(payload.get("trace_id") or lane_event.correlation_id or ""),
        ))
        if task_id:
            try:
                self.task_store.update(
                    task_id,
                    status="blocked",
                    blocked_reason=(
                        "lane_stage_rework_quarantined:"
                        f"{quarantine.id}"
                    ),
                )
            except Exception:
                pass
            series = recovery_series_from_event(lane_event)
            cap_payload = build_rework_cap_payload(
                series=series,
                failures=failures,
                max_attempts=max_attempts,
                trigger_event=lane_event,
                extra={
                    "lane_id": str(payload.get("lane_id") or ""),
                    "pipeline_id": str(payload.get("pipeline_id") or ""),
                    "root_fanout_id": str(payload.get("root_fanout_id") or ""),
                    "lane_stage_rework_quarantined_event_id": quarantine.id,
                    "rework_feedback_ref": str(payload.get("rework_feedback_ref") or ""),
                    "rework_feedback_digest": str(payload.get("rework_feedback_digest") or ""),
                },
            )
            self.event_writer.append(ZfEvent(
                type="task.rework.capped",
                actor="zf-cli",
                task_id=task_id,
                payload=cap_payload,
                causation_id=quarantine.id,
                correlation_id=quarantine.correlation_id,
            ))

    def _lane_stage_rework_task_item(
        self,
        *,
        lane_event: ZfEvent,
        pipeline_id: str,
        target_stage_slot: str,
    ) -> dict:
        payload = lane_event.payload if isinstance(lane_event.payload, dict) else {}
        task_id = str(payload.get("task_id") or lane_event.task_id or "")
        root_fanout_id = str(payload.get("root_fanout_id") or "")
        task_item: dict = {}
        root_manifest = self._fanout_manifest(root_fanout_id)
        if root_manifest:
            for child in root_manifest.get("children", []) or []:
                if not isinstance(child, dict):
                    continue
                if str(child.get("task_id") or "") != task_id:
                    continue
                if isinstance(child.get("payload"), dict):
                    task_item.update(dict(child["payload"]))
                task_item.update({
                    key: child[key]
                    for key in (
                        "task_id",
                        "scope",
                        "task_ref",
                        "task_map_ref",
                        "source_index_ref",
                        "pdd_id",
                        "feature_id",
                        "affinity_tag",
                    )
                    if child.get(key) not in (None, "")
                })
                break
        if not task_item:
            task = self.task_store.get(task_id) if task_id else None
            contract = getattr(task, "contract", None)
            scope = list(getattr(contract, "scope", []) or []) if contract else []
            task_item = {
                "task_id": task_id,
                "scope": ", ".join(scope),
                "allowed_paths": scope,
                "verification": str(getattr(contract, "verification", "") or ""),
                "payload": {
                    "instruction": str(getattr(contract, "behavior", "") or ""),
                },
            }
        task_item["task_id"] = task_id
        task_item.setdefault("payload", {})
        if not isinstance(task_item["payload"], dict):
            task_item["payload"] = {}
        task_item["pipeline_id"] = pipeline_id
        task_item["root_fanout_id"] = root_fanout_id
        task_item["upstream_root_fanout_id"] = root_fanout_id
        task_item["upstream_fanout_id"] = str(payload.get("fanout_id") or "")
        task_item["upstream_child_id"] = str(payload.get("child_id") or "")
        task_item["upstream_task_id"] = task_id
        task_item["upstream_stage_slot"] = str(payload.get("stage_slot") or "")
        task_item["stage_slot"] = target_stage_slot
        for key in (
            "lane_id",
            "lane_profile",
            "affinity_tag",
            "task_ref",
            "task_map_ref",
            "source_index_ref",
            "pdd_id",
            "feature_id",
            "source_branch",
            "source_commit",
            "workdir",
            *_CONTRACT_HANDOFF_KEYS,
            "rework_feedback_ref",
            "rework_feedback_digest",
        ):
            value = payload.get(key)
            if value not in (None, ""):
                task_item[key] = value
        if not str(task_item.get("affinity_tag") or ""):
            task_item["affinity_tag"] = task_id
        return task_item

    def _start_lane_stage_rework_writer_fanout(
        self,
        *,
        rework_event: ZfEvent,
        stage,
        role,
        task_item: dict,
        attempt: int,
    ) -> None:
        from zf.runtime.fanout import FanoutChild, FanoutContext

        payload = rework_event.payload if isinstance(rework_event.payload, dict) else {}
        trace_id = (
            rework_event.correlation_id
            or str(payload.get("trace_id") or "")
            or rework_event.id
        )
        target_ref = self.config.runtime.git.candidate_base_ref
        base_context = FanoutContext.create(
            stage_id=stage.id,
            topology=stage.topology,
            trace_id=trace_id,
            trigger_event_id=rework_event.id,
            target_ref=target_ref,
            role_instances=[],
        )
        task_id = str(task_item.get("task_id") or "")
        child = FanoutChild(
            child_id=FanoutContext.child_id(
                role.instance_id,
                scope=f"{task_id}-rework-{attempt}",
            ),
            role_instance=role.instance_id,
            target_ref=target_ref,
            payload=task_item,
        )
        context = FanoutContext(
            fanout_id=base_context.fanout_id,
            stage_id=base_context.stage_id,
            topology=base_context.topology,
            trace_id=base_context.trace_id,
            trigger_event_id=base_context.trigger_event_id,
            target_ref=base_context.target_ref,
            expected_children=[child],
        )
        started = context.started_event()
        pdd_id = str(task_item.get("pdd_id") or payload.get("pdd_id") or "")
        feature_id = str(task_item.get("feature_id") or pdd_id)
        started.payload.update({
            "pdd_id": pdd_id,
            "feature_id": feature_id,
            # F4(bizsim r4):代际隔离——task_id/attempt 必须进 identity
            # logical_key,否则 rework fanout 与原 stage fanout 撞 key,
            # 原代被判非当前,兄弟 lane 在飞完工被 stale 连坐。
            "task_id": task_id,
            "rework_attempt": attempt,
            "task_map_ref": str(task_item.get("task_map_ref") or ""),
            "source_index_ref": str(task_item.get("source_index_ref") or ""),
            "rework_of_lane_stage_event_id": str(
                payload.get("lane_stage_event_id") or ""
            ),
            "root_fanout_id": str(task_item.get("root_fanout_id") or ""),
        })
        child_success_event, child_failure_event = self._fanout_child_result_events(
            stage.aggregate
        )
        started.payload["aggregate"] = {
            "mode": stage.aggregate.mode,
            "success_event": stage.aggregate.success_event,
            "failure_event": stage.aggregate.failure_event,
            "child_success_event": child_success_event,
            "child_failure_event": child_failure_event,
            "synth_role": stage.aggregate.synth_role,
            "max_retries": stage.aggregate.max_retries,
            "review_strategy": stage.aggregate.review_strategy,
            "synth_timeout_seconds": stage.aggregate.synth_timeout_seconds,
        }
        self.event_writer.append(started)
        slot_payload = {
            "fanout_id": context.fanout_id,
            "trace_id": context.trace_id,
            "stage_id": context.stage_id,
            "child_id": child.child_id,
            "role_instance": role.instance_id,
            "task_id": task_id,
        }
        self._copy_fanout_assignment_metadata(slot_payload, task_item)
        self.event_writer.append(ZfEvent(
            type="fanout.slot.assigned",
            actor="zf-cli",
            payload=slot_payload,
            causation_id=started.id,
            correlation_id=context.trace_id,
        ))
        try:
            current = self.task_store.get(task_id) if task_id else None
            if current is not None:
                self.task_store.update(
                    task_id,
                    retry_count=max(int(current.retry_count or 0), attempt),
                )
        except Exception:
            pass
        rework_feedback = [str(payload.get("reason") or "lane stage failed")]
        rework_summary = {
            "lane_stage_event_id": str(payload.get("lane_stage_event_id") or ""),
            "failed_stage_slot": str(payload.get("failed_stage_slot") or ""),
            "target_stage_slot": str(payload.get("target_stage_slot") or ""),
            "failure_fingerprint": str(payload.get("failure_fingerprint") or ""),
            "rework_feedback_ref": str(payload.get("rework_feedback_ref") or ""),
            "rework_feedback_digest": str(payload.get("rework_feedback_digest") or ""),
        }
        if payload.get("rework_feedback_ref") or payload.get("rework_feedback_digest"):
            feedback_body = hydrate_rework_feedback(
                self.state_dir,
                feedback_descriptor_from_payload(payload),
                expected_task_id=task_id,
                expected_fingerprint=str(payload.get("failure_fingerprint") or ""),
            )
            rework_feedback = feedback_briefing_lines(feedback_body)
            rework_summary["failed_acceptance_ids"] = list(
                feedback_body.get("failed_acceptance_ids") or []
            )
        self._dispatch_writer_fanout_child(
            context=context,
            child=child,
            task_item=task_item,
            role=role,
            pdd_id=pdd_id,
            feature_id=feature_id,
            task_map_ref=str(task_item.get("task_map_ref") or ""),
            source_index_ref=str(task_item.get("source_index_ref") or ""),
            wave=None,
            causation_id=started.id,
            rework_feedback=rework_feedback,
            rework_attempt=attempt,
            rework_summary=rework_summary,
        )

    def _maybe_publish_lane_stage_final_ready(
        self,
        *,
        lane_event: ZfEvent,
        pipeline,
    ) -> ZfEvent | None:
        payload = lane_event.payload if isinstance(lane_event.payload, dict) else {}
        root_fanout_id = str(payload.get("root_fanout_id") or "")
        pipeline_id = str(getattr(pipeline, "pipeline_id", "") or "")
        if not root_fanout_id or not pipeline_id:
            return None
        root_manifest = self._fanout_manifest(root_fanout_id)
        if not root_manifest:
            return None
        root_aggregate = (
            root_manifest.get("aggregate")
            if isinstance(root_manifest.get("aggregate"), dict)
            else {}
        )
        candidate_ref = str(
            root_aggregate.get("candidate_ref")
            or root_manifest.get("candidate_ref")
            or ""
        ).strip()
        candidate_head_commit = str(
            root_aggregate.get("candidate_head_commit")
            or root_aggregate.get("commit")
            or root_manifest.get("candidate_head_commit")
            or ""
        ).strip()
        candidate_base_commit = str(
            root_aggregate.get("candidate_base_commit")
            or root_aggregate.get("base_commit")
            or root_manifest.get("candidate_base_commit")
            or ""
        ).strip()
        diff_ref = str(
            root_aggregate.get("diff_ref")
            or (
                f"{candidate_base_commit}..{candidate_head_commit}"
                if candidate_base_commit and candidate_head_commit
                else ""
            )
        ).strip()
        required_task_ids = sorted({
            str(child.get("task_id") or "")
            for child in root_manifest.get("children", []) or []
            if isinstance(child, dict) and str(child.get("task_id") or "")
        })
        if not required_task_ids:
            return None
        try:
            events = self.event_log.read_all()
        except Exception:
            events = [lane_event]
        readiness = evaluate_final_readiness(
            events,
            pipeline=pipeline,
            root_fanout_id=root_fanout_id,
            required_task_ids=required_task_ids,
        )
        if readiness.ready:
            publish_event = readiness.success_event
            status = "completed"
        elif (
            set(readiness.completed_task_ids + readiness.failed_task_ids)
            == set(required_task_ids)
            and readiness.failed_task_ids
            and not readiness.stale_task_ids
        ):
            publish_event = readiness.failure_event
            status = "failed"
        else:
            return None
        if final_readiness_already_published(
            events,
            event_type=publish_event,
            pipeline_id=pipeline_id,
            root_fanout_id=root_fanout_id,
            lane_stage_event_ids=readiness.lane_stage_event_ids,
        ):
            return None
        trace_id = str(payload.get("trace_id") or lane_event.correlation_id or "")
        ready_event = self.event_writer.append(ZfEvent(
            type=publish_event,
            actor="zf-cli",
            payload={
                **self._fanout_flow_identity_payload(
                    root_manifest,
                    payloads=[payload],
                ),
                "pipeline_id": pipeline_id,
                "root_fanout_id": root_fanout_id,
                "fanout_id": root_fanout_id,
                "trace_id": trace_id,
                "stage_id": str(payload.get("stage_id") or ""),
                "stage_slot": str(payload.get("stage_slot") or ""),
                "pdd_id": str(payload.get("pdd_id") or root_manifest.get("pdd_id") or ""),
                "feature_id": str(
                    payload.get("feature_id")
                    or root_manifest.get("feature_id")
                    or root_manifest.get("pdd_id")
                    or ""
                ),
                "status": status,
                "required_task_ids": readiness.required_task_ids,
                "completed_task_ids": readiness.completed_task_ids,
                "failed_task_ids": readiness.failed_task_ids,
                "stale_task_ids": readiness.stale_task_ids,
                "lane_stage_event_ids": readiness.lane_stage_event_ids,
                "target_ref": candidate_ref or root_manifest.get("target_ref", ""),
                "candidate_ref": candidate_ref,
                "candidate_head_commit": candidate_head_commit,
                "candidate_base_commit": candidate_base_commit,
                "diff_ref": diff_ref,
                "child_count": len(required_task_ids),
            },
            causation_id=lane_event.id,
            correlation_id=trace_id,
        ))
        self._maybe_start_reader_fanout(ready_event)
        return ready_event

    def _maybe_start_reader_fanout(self, event: ZfEvent) -> bool:
        stages = [
            stage for stage in getattr(self.config.workflow, "stages", [])
            if stage.topology == "fanout_reader" and stage.trigger == event.type
            and self._fanout_stage_matches_trigger_event(stage, event)
        ]
        if not stages:
            return False
        from zf.runtime.fanout import FanoutChild, FanoutContext

        started_any = False
        for stage in stages:
            if self._fanout_started(stage.id, event.id):
                started_any = True
                continue
            trace_id = event.correlation_id or (
                event.payload.get("trace_id", "") if isinstance(event.payload, dict) else ""
            ) or event.id
            trigger_payload = event.payload if isinstance(event.payload, dict) else {}
            target_ref = self._render_fanout_target(stage.target_ref, event)
            workflow_target_ref = str(trigger_payload.get("target_ref") or "").strip()
            if (
                workflow_target_ref
                and str(trigger_payload.get("workflow_invoke_pattern_id") or "").strip()
            ):
                target_ref = workflow_target_ref
            elif (
                workflow_target_ref
                and (
                    str(trigger_payload.get("rework_of") or "").strip()
                    or str(event.causation_id or "").strip()
                )
            ):
                target_ref = workflow_target_ref
            target_ref_error = self._refactor_scan_target_ref_error(
                stage=stage,
                event=event,
                target_ref=target_ref,
            )
            if target_ref_error:
                failure_context = FanoutContext.create(
                    stage_id=stage.id,
                    topology=stage.topology,
                    trace_id=trace_id,
                    trigger_event_id=event.id,
                    target_ref=target_ref,
                    role_instances=[],
                )
                self._emit_refactor_scan_target_ref_failed(
                    event=event,
                    stage_id=stage.id,
                    trace_id=trace_id,
                    fanout_id=failure_context.fanout_id,
                    target_ref=target_ref,
                    reason=target_ref_error,
                )
                continue
            if getattr(stage, "retrigger_requires_delta", False):
                if not self._delta_gate_allows(
                    stage=stage,
                    event=event,
                    target_ref=target_ref,
                    trace_id=trace_id,
                ):
                    continue
            if self._fanout_assignment_strategy(stage) == "affinity_stage_slots":
                base_context = FanoutContext.create(
                    stage_id=stage.id,
                    topology=stage.topology,
                    trace_id=trace_id,
                    trigger_event_id=event.id,
                    target_ref=target_ref,
                    role_instances=[],
                )
                stage_slot = str(
                    getattr(getattr(stage, "assignment", None), "stage_slot", "")
                    or ""
                )
                if event.type == LANE_STAGE_HANDOFF_SUCCESS_EVENT:
                    next_stage_slot = str(
                        trigger_payload.get("next_stage_slot") or ""
                    )
                    if next_stage_slot != stage_slot:
                        continue
                    pipeline = per_lane_flow_for_handoff_target(
                        self.config,
                        str(getattr(stage, "id", "") or ""),
                        next_stage_slot,
                    )
                    if pipeline is None:
                        continue
                    lane_id = str(trigger_payload.get("lane_id") or "")
                    role = self._fanout_affinity_lane_role(
                        stage,
                        lane_id=lane_id,
                        stage_slot=stage_slot,
                    )
                    if role is None:
                        # E7(prd-goal e2e finding-16):rework 代际触发的
                        # lane_id 缺失/失配曾整盘取消。单 lane 档无歧义
                        # → 兜底用唯一 lane;多 lane 歧义维持 fail-closed。
                        lane_roles = self._fanout_affinity_lane_roles(stage)
                        if len(lane_roles) == 1:
                            lane_id = lane_roles[0][0]
                            role = lane_roles[0][1]
                    if role is None:
                        self.event_writer.append(ZfEvent(
                            type="fanout.cancelled",
                            actor="zf-cli",
                            payload={
                                "fanout_id": base_context.fanout_id,
                                "trace_id": trace_id,
                                "stage_id": stage.id,
                                "trigger_event_id": event.id,
                                "reason": "unknown_affinity_lane_role",
                                "lane_id": lane_id,
                                "stage_slot": stage_slot,
                            },
                            causation_id=event.id,
                            correlation_id=trace_id,
                        ))
                        continue
                    affinity_tag = str(
                        trigger_payload.get("affinity_tag")
                        or trigger_payload.get("task_id")
                        or trigger_payload.get("child_id")
                        or ""
                    )
                    child_id = FanoutContext.child_id(
                        role.instance_id,
                        ordinal=0,
                        scope=_lane_child_scope(
                            affinity_tag,
                            str(trigger_payload.get("task_id") or ""),
                        ),
                    )
                    upstream_fanout_id = str(trigger_payload.get("fanout_id") or "")
                    child_payload = {
                        "assignment_strategy": "affinity_stage_slots",
                        "lane_profile": str(
                            getattr(getattr(stage, "assignment", None), "lane_profile", "")
                            or ""
                        ),
                        "pipeline_id": str(trigger_payload.get("pipeline_id") or ""),
                        "flow_kind": str(trigger_payload.get("flow_kind") or ""),
                        "root_fanout_id": str(
                            trigger_payload.get("root_fanout_id")
                            or upstream_fanout_id
                        ),
                        "lane_id": lane_id,
                        "stage_slot": stage_slot,
                        "affinity_tag": affinity_tag,
                        "upstream_fanout_id": upstream_fanout_id,
                        "upstream_child_id": str(trigger_payload.get("child_id") or ""),
                        "upstream_task_id": str(trigger_payload.get("task_id") or ""),
                        "upstream_stage_slot": str(trigger_payload.get("stage_slot") or ""),
                        "task_id": str(trigger_payload.get("task_id") or ""),
                        "task_ref": str(trigger_payload.get("task_ref") or ""),
                        "task_map_ref": str(trigger_payload.get("task_map_ref") or ""),
                        "source_index_ref": str(trigger_payload.get("source_index_ref") or ""),
                        "source_branch": str(trigger_payload.get("source_branch") or ""),
                        "source_commit": str(trigger_payload.get("source_commit") or ""),
                        "workdir": str(trigger_payload.get("workdir") or ""),
                        "pdd_id": str(trigger_payload.get("pdd_id") or ""),
                        "feature_id": str(
                            trigger_payload.get("feature_id")
                            or trigger_payload.get("pdd_id")
                            or ""
                        ),
                    }
                    for key in _CONTRACT_HANDOFF_KEYS:
                        value = trigger_payload.get(key)
                        if value not in (None, ""):
                            child_payload[key] = value
                    children = [FanoutChild(
                        child_id=child_id,
                        role_instance=role.instance_id,
                        target_ref=target_ref,
                        payload=child_payload,
                    )]
                    roles = [role]
                    context = FanoutContext(
                        fanout_id=base_context.fanout_id,
                        stage_id=stage.id,
                        topology=stage.topology,
                        trace_id=trace_id,
                        trigger_event_id=event.id,
                        target_ref=target_ref,
                        expected_children=children,
                    )
                else:
                    operator_recovery = trigger_payload.get("operator_recovery")
                    operator_upstream_fanout_id = (
                        str(operator_recovery.get("upstream_fanout_id") or "").strip()
                        if isinstance(operator_recovery, dict)
                        else ""
                    )
                    upstream_fanout_id = str(
                        operator_upstream_fanout_id
                        or trigger_payload.get("upstream_fanout_id")
                        or trigger_payload.get("fanout_id")
                        or ""
                    ).strip()
                    upstream_manifest = self._fanout_manifest(upstream_fanout_id)
                    if not upstream_manifest:
                        self.event_writer.append(ZfEvent(
                            type="fanout.cancelled",
                            actor="zf-cli",
                            payload={
                                "fanout_id": base_context.fanout_id,
                                "trace_id": trace_id,
                                "stage_id": stage.id,
                                "trigger_event_id": event.id,
                                "reason": "missing_upstream_affinity_fanout",
                                "upstream_fanout_id": upstream_fanout_id,
                            },
                            causation_id=event.id,
                            correlation_id=trace_id,
                        ))
                        continue
                    children = []
                    roles = []
                    seen_roles: set[str] = set()
                    seen_child_ids: dict[str, int] = {}
                    identity_diagnostics: list[dict[str, object]] = []
                    for upstream_child in upstream_manifest.get("children", []) or []:
                        if not isinstance(upstream_child, dict):
                            continue
                        if str(upstream_child.get("status") or "") != "completed":
                            # ZF-E2E-PRDCTL-P2-7-5:manifest status 可能陈旧
                            # (completion-adoption 路径完成但未回写——deepwater
                            # TOPWORDS 的 verify child 因此漏派生)。事件证据
                            # 为准:有完成证据则照常派生并记诊断。
                            child_task_id = str(upstream_child.get("task_id") or "")
                            if not child_task_id or not self._task_completed_by_events(
                                child_task_id,
                            ):
                                continue
                            identity_diagnostics.append({
                                "upstream_child_id": str(upstream_child.get("child_id") or ""),
                                "task_id": child_task_id,
                                "errors": ["manifest_status_stale_completed_by_events"],
                                "adopted": True,
                            })
                        lane_id = str(upstream_child.get("lane_id") or "")
                        if not lane_id:
                            identity_diagnostics.append({
                                "upstream_child_id": str(upstream_child.get("child_id") or ""),
                                "task_id": str(upstream_child.get("task_id") or ""),
                                "errors": ["missing_lane_id"],
                            })
                            continue
                        role = self._fanout_affinity_lane_role(
                            stage,
                            lane_id=lane_id,
                            stage_slot=stage_slot,
                        )
                        if role is None:
                            identity_diagnostics.append({
                                "upstream_child_id": str(upstream_child.get("child_id") or ""),
                                "task_id": str(upstream_child.get("task_id") or ""),
                                "lane_id": lane_id,
                                "stage_slot": stage_slot,
                                "errors": ["unknown_affinity_lane_role"],
                            })
                            continue
                        if role.instance_id not in seen_roles:
                            roles.append(role)
                            seen_roles.add(role.instance_id)
                        affinity_tag = str(
                            upstream_child.get("affinity_tag")
                            or upstream_child.get("task_id")
                            or upstream_child.get("child_id")
                            or ""
                        )
                        ordinal = seen_child_ids.get(role.instance_id, 0)
                        seen_child_ids[role.instance_id] = ordinal + 1
                        child_id = FanoutContext.child_id(
                            role.instance_id,
                            ordinal=ordinal,
                            scope=affinity_tag,
                        )
                        child_payload = {
                            "assignment_strategy": "affinity_stage_slots",
                            "lane_profile": str(
                                getattr(getattr(stage, "assignment", None), "lane_profile", "")
                                or ""
                            ),
                            "lane_id": lane_id,
                            "stage_slot": stage_slot,
                            "affinity_tag": affinity_tag,
                            "upstream_fanout_id": upstream_fanout_id,
                            "upstream_child_id": str(upstream_child.get("child_id") or ""),
                            "upstream_task_id": str(upstream_child.get("task_id") or ""),
                            "task_id": str(upstream_child.get("task_id") or ""),
                            "task_ref": str(upstream_child.get("task_ref") or ""),
                            "source_commit": str(upstream_child.get("source_commit") or ""),
                        }
                        upstream_payload = (
                            upstream_child.get("payload")
                            if isinstance(upstream_child.get("payload"), dict)
                            else {}
                        )
                        for key in _CONTRACT_HANDOFF_KEYS:
                            value = upstream_child.get(key) or upstream_payload.get(key)
                            if value not in (None, ""):
                                child_payload[key] = value
                        children.append(FanoutChild(
                            child_id=child_id,
                            role_instance=role.instance_id,
                            target_ref=target_ref,
                            payload=child_payload,
                        ))
                    if not children:
                        self.event_writer.append(ZfEvent(
                            type="fanout.cancelled",
                            actor="zf-cli",
                            payload={
                                "fanout_id": base_context.fanout_id,
                                "trace_id": trace_id,
                                "stage_id": stage.id,
                                "trigger_event_id": event.id,
                                "reason": (
                                    "missing_affinity_child_identity"
                                    if identity_diagnostics
                                    else "no_affinity_stage_slot_children"
                                ),
                                "upstream_fanout_id": upstream_fanout_id,
                                "stage_slot": stage_slot,
                                "diagnostics": identity_diagnostics,
                            },
                            causation_id=event.id,
                            correlation_id=trace_id,
                        ))
                        continue
                    derivation_errors = [
                        item for item in identity_diagnostics
                        if not item.get("adopted")
                    ]
                    if derivation_errors:
                        # ZF-E2E-PRDCTL-P2-7-5:漏派生不再静默——部分 impl
                        # 任务无 verify child 时 fail-closed 取消(no-dead-end
                        # 机制会按拓扑发上游 failure_event 路由返工),judge
                        # 不再是第一个发现覆盖缺口的人。
                        self.event_writer.append(ZfEvent(
                            type="verify.children.derivation_gap",
                            actor="zf-cli",
                            payload={
                                "fanout_id": base_context.fanout_id,
                                "trace_id": trace_id,
                                "stage_id": stage.id,
                                "trigger_event_id": event.id,
                                "upstream_fanout_id": upstream_fanout_id,
                                "stage_slot": stage_slot,
                                "derived_count": len(children),
                                "diagnostics": identity_diagnostics,
                                "missing_task_ids": [
                                    str(item.get("task_id") or "")
                                    for item in derivation_errors
                                ],
                            },
                            causation_id=event.id,
                            correlation_id=trace_id,
                        ))
                        self.event_writer.append(ZfEvent(
                            type="fanout.cancelled",
                            actor="zf-cli",
                            payload={
                                "fanout_id": base_context.fanout_id,
                                "trace_id": trace_id,
                                "stage_id": stage.id,
                                "trigger_event_id": event.id,
                                "reason": "verify_children_derivation_gap",
                                "failure_kind": "infra",
                                "upstream_fanout_id": upstream_fanout_id,
                                "stage_slot": stage_slot,
                                "diagnostics": identity_diagnostics,
                            },
                            causation_id=event.id,
                            correlation_id=trace_id,
                        ))
                        continue
                    context = FanoutContext(
                        fanout_id=base_context.fanout_id,
                        stage_id=stage.id,
                        topology=stage.topology,
                        trace_id=trace_id,
                        trigger_event_id=event.id,
                        target_ref=target_ref,
                        expected_children=children,
                    )
            else:
                roles = self._fanout_roles(stage.roles)
                if not roles and not getattr(stage, "children", []):
                    continue
            if self._fanout_assignment_strategy(stage) == "affinity_stage_slots":
                pass
            elif getattr(stage, "children", []):
                children: list[FanoutChild] = []
                child_roles_by_instance: dict[str, RoleConfig] = {}
                seen: dict[str, int] = {}
                for raw_child in stage.children:
                    target = raw_child.role_instance or raw_child.role
                    child_roles = self._fanout_roles([target])
                    for role in child_roles:
                        child_roles_by_instance[role.instance_id] = role
                        payload = dict(raw_child.payload or {})
                        if raw_child.scope:
                            payload.setdefault("scope", raw_child.scope)
                        if raw_child.task_id:
                            payload.setdefault("task_id", raw_child.task_id)
                        ordinal = seen.get(role.instance_id, 0)
                        seen[role.instance_id] = ordinal + 1
                        child_id = str(
                            payload.get("child_id")
                            or raw_child.task_id
                            or FanoutContext.child_id(
                                role.instance_id,
                                ordinal=ordinal,
                                scope=raw_child.scope,
                            )
                        )
                        children.append(FanoutChild(
                            child_id=child_id,
                            role_instance=role.instance_id,
                            target_ref=target_ref,
                            payload=payload,
                        ))
                roles = list(child_roles_by_instance.values())
                context = FanoutContext(
                    fanout_id=FanoutContext.create(
                        stage_id=stage.id,
                        topology=stage.topology,
                        trace_id=trace_id,
                        trigger_event_id=event.id,
                        target_ref=target_ref,
                        role_instances=[],
                    ).fanout_id,
                    stage_id=stage.id,
                    topology=stage.topology,
                    trace_id=trace_id,
                    trigger_event_id=event.id,
                    target_ref=target_ref,
                    expected_children=children,
                )
            else:
                context = FanoutContext.create(
                    stage_id=stage.id,
                    topology=stage.topology,
                    trace_id=trace_id,
                    trigger_event_id=event.id,
                    target_ref=target_ref,
                    role_instances=[role.instance_id for role in roles],
                )
            if trigger_payload:
                for child in context.expected_children:
                    child.payload.setdefault("trigger_payload", dict(trigger_payload))
                    parent_operation_id = str(
                        trigger_payload.get("parent_operation_id")
                        or trigger_payload.get("workflow_operation_id")
                        or ""
                    )
                    if parent_operation_id:
                        child.payload.setdefault(
                            "parent_operation_id",
                            parent_operation_id,
                        )
                    for key in _DURABLE_CALL_TRIGGER_KEYS:
                        value = trigger_payload.get(key)
                        if value not in (None, ""):
                            child.payload.setdefault(key, value)
            identity_diagnostics = self._fanout_child_identity_diagnostics(context)
            if identity_diagnostics:
                self.event_writer.append(ZfEvent(
                    type="fanout.cancelled",
                    actor="zf-cli",
                    payload={
                        "fanout_id": context.fanout_id,
                        "trace_id": trace_id,
                        "stage_id": stage.id,
                        "trigger_event_id": event.id,
                        "reason": "missing_affinity_child_identity",
                        "diagnostics": identity_diagnostics,
                    },
                    causation_id=event.id,
                    correlation_id=trace_id,
                ))
                continue
            started = context.started_event()
            identity_pdd_id = str(
                trigger_payload.get("pdd_id")
                or trigger_payload.get("feature_id")
                or event.task_id
                or ""
            ).strip()
            identity_feature_id = str(
                trigger_payload.get("feature_id")
                or trigger_payload.get("pdd_id")
                or event.task_id
                or ""
            ).strip()
            if identity_pdd_id:
                started.payload["pdd_id"] = identity_pdd_id
            if identity_feature_id:
                started.payload["feature_id"] = identity_feature_id
            if trigger_payload:
                started.payload["trigger_payload"] = dict(trigger_payload)
            child_success_event, child_failure_event = self._fanout_child_result_events(
                stage.aggregate
            )
            started.payload["aggregate"] = {
                "mode": stage.aggregate.mode,
                "success_event": stage.aggregate.success_event,
                "failure_event": stage.aggregate.failure_event,
                "child_success_event": child_success_event,
                "child_failure_event": child_failure_event,
                "synth_role": stage.aggregate.synth_role,
                "max_retries": stage.aggregate.max_retries,
                "review_strategy": stage.aggregate.review_strategy,
                "synth_timeout_seconds": stage.aggregate.synth_timeout_seconds,
            }
            self.event_writer.append(started)
            started_any = True
            role_by_instance = {role.instance_id: role for role in roles}
            prepared_dispatches = self._preregister_reader_fanout_operations(
                context=context,
                roles_by_instance=role_by_instance,
                causation_id=started.id,
                aggregate=stage.aggregate,
            )
            for child in context.expected_children:
                role = role_by_instance.get(child.role_instance)
                if role is None:
                    continue
                self._dispatch_reader_fanout_child(
                    context=context,
                    child=child,
                    role=role,
                    aggregate=stage.aggregate,
                    causation_id=started.id,
                    prepared_dispatch=prepared_dispatches.get(child.child_id),
                )
        return started_any
    def _writer_source_index_gate(
        self,
        *,
        loaded,
        stage_id: str,
        trigger_event: ZfEvent,
        trace_id: str,
    ):
        """B4 (doc 91 P1): source-index 双向锚门的 IO 接线层。

        评估在 source_index_gate 纯函数;本层只做读 index 文件、写
        degraded 工件、发证据/取消事件。评估自身异常 → None(不挡主路,
        observe-first)。
        """
        try:
            import json as _json

            from zf.runtime.source_index_gate import (
                evaluate_source_index_gate,
            )

            source_index = None
            ref = str(loaded.source_index_ref or "")
            if ref:
                path = Path(ref)
                if not path.is_absolute():
                    path = (self.project_root / ref).resolve()
                if path.exists():
                    try:
                        source_index = _json.loads(
                            path.read_text(encoding="utf-8")
                        )
                    except (OSError, ValueError):
                        source_index = None
            profile = str(
                getattr(self.config.workflow, "harness_profile", "") or ""
            )
            result = evaluate_source_index_gate(
                task_items=loaded.task_items,
                source_index=source_index,
                findings=None,
                harness_profile=profile,
            )
            base_payload = {
                "stage_id": stage_id,
                "trigger_event_id": trigger_event.id,
                "trace_id": trace_id,
                "task_map_ref": loaded.task_map_ref,
                "source_index_ref": ref,
            }
            if result.mode == "degraded" and result.degraded_index is not None:
                out = (
                    self.state_dir / "artifacts"
                    / f"source-index-degraded-{trigger_event.id}.json"
                )
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(
                    _json.dumps(
                        result.degraded_index, ensure_ascii=False, indent=2,
                    ) + "\n",
                    encoding="utf-8",
                )
                self.event_writer.append(ZfEvent(
                    type="provenance.degraded",
                    actor="zf-cli",
                    payload={
                        **base_payload,
                        "missing_anchor_task_ids":
                            result.missing_anchor_task_ids,
                        "degraded_index_ref": str(out),
                        "note": result.note,
                    },
                    causation_id=trigger_event.id,
                    correlation_id=trace_id,
                ))
            if not result.passed:
                emit_plan_admission_cancel(
                    self,
                    trigger_event=trigger_event,
                    stage_id=stage_id,
                    trace_id=trace_id,
                    pdd_id=str(getattr(loaded, "pdd_id", "") or ""),
                    feature_id=str(
                        getattr(loaded, "feature_id", "")
                        or getattr(loaded, "pdd_id", "")
                        or ""
                    ),
                    task_map_ref=str(getattr(loaded, "task_map_ref", "") or ""),
                    task_map_path=getattr(loaded, "task_map_path", None),
                    source_index_ref=ref,
                    task_ids=[
                        str(item.get("task_id") or "")
                        for item in getattr(loaded, "task_items", []) or []
                    ],
                    reason="source_index_gap",
                    extra_payload={
                        "missing_anchor_task_ids": result.missing_anchor_task_ids,
                        "note": result.note,
                    },
                )
            return result
        except Exception:
            return None

    def _plan_approval_satisfied(
        self,
        *,
        stage_id: str,
        trigger_event: ZfEvent,
        loaded,
        trace_id: str,
    ) -> bool:
        """B14 (doc 93 §3/§8): plan 审核门判定。

        plan_id = 触发事件 id。enabled=False → 无 approved 即 kernel
        自动铸(payload auto:true),返回 True;enabled=True → 已有
        operator 的 approved 返回 True,否则幂等发 approval.requested
        并返回 False(hold)。rejected 后同 plan_id 不再重发 requested
        (rework 回路会产新 task_map.ready = 新 plan_id)。
        """
        try:
            events = self.event_log.read_all()
        except Exception:
            return True  # 评估不可用不挡主路(observe-first)
        plan_id = trigger_event.id
        requested = False
        suppressed = False
        decided_plan_ids: set[str] = set()
        pending_fingerprints: dict[str, str] = {}
        for existing in events:
            payload = (
                existing.payload if isinstance(existing.payload, dict) else {}
            )
            pid = str(payload.get("plan_id") or "")
            if not pid:
                continue
            if existing.type in ("plan.approved", "plan.rejected"):
                decided_plan_ids.add(pid)
            elif existing.type == "plan.approval.requested":
                pending_fingerprints[pid] = str(
                    payload.get("plan_fingerprint") or "",
                )
        for existing in reversed(events):
            payload = (
                existing.payload if isinstance(existing.payload, dict) else {}
            )
            if str(payload.get("plan_id") or "") != plan_id:
                continue
            if existing.type == "plan.approved":
                return True
            if existing.type == "plan.rejected":
                return False
            if existing.type == "plan.approval.requested":
                requested = True
            if existing.type == "plan.minting.suppressed":
                suppressed = True
        if suppressed and not requested:
            return False
        enabled = bool(getattr(
            self.config.workflow, "plan_approval_enabled", False,
        ))
        base_payload = {
            "plan_id": plan_id,
            "stage_id": stage_id,
            "trace_id": trace_id,
            "pdd_id": str(getattr(loaded, "pdd_id", "") or ""),
            "task_map_ref": str(getattr(loaded, "task_map_ref", "") or ""),
        }
        if not enabled:
            self.event_writer.append(ZfEvent(
                type="plan.approved",
                actor="zf-cli",
                payload={**base_payload, "auto": True},
                causation_id=plan_id,
                correlation_id=trace_id,
            ))
            return True
        if not requested:
            task_items = list(getattr(loaded, "task_items", []) or [])
            # FIX-12(bizsim r4 F12):plan 语义指纹判重。r4 judge cap 冻结期
            # 每轮 replan 铸新 plan_id(14 请求/7 批,批一生一),同
            # stage+pdd+task 集的未决 plan 已在队时不再铸新单。
            fingerprint = self._plan_fingerprint(
                stage_id=stage_id,
                pdd_id=base_payload["pdd_id"],
                task_items=task_items,
            )
            duplicate_of = next(
                (
                    pid for pid, fp in pending_fingerprints.items()
                    if fp and fp == fingerprint
                    and pid != plan_id
                    and pid not in decided_plan_ids
                ),
                "",
            )
            if duplicate_of:
                self.event_writer.append(ZfEvent(
                    type="plan.minting.suppressed",
                    actor="zf-cli",
                    payload={
                        **base_payload,
                        "plan_fingerprint": fingerprint,
                        "duplicate_of": duplicate_of,
                        "reason": "pending_plan_same_fingerprint",
                    },
                    causation_id=plan_id,
                    correlation_id=trace_id,
                ))
                return False
            # B-93-03 (doc 93 §4): 投影 plan-digest 落 artifact,payload 带
            # digest_ref —— CLI/Web/Feishu 共用同一份人读摘要,不各自重算。
            digest_ref = self._write_plan_digest(plan_id, task_items, base_payload["task_map_ref"])
            self.event_writer.append(ZfEvent(
                type="plan.approval.requested",
                actor="zf-cli",
                payload={
                    **base_payload,
                    "plan_fingerprint": fingerprint,
                    "task_count": len(task_items),
                    "digest_ref": digest_ref,
                },
                causation_id=plan_id,
                correlation_id=trace_id,
            ))
        return False

    def _plan_fingerprint(
        self,
        *,
        stage_id: str,
        pdd_id: str,
        task_items: list,
    ) -> str:
        import hashlib

        task_ids = sorted(
            str(
                getattr(item, "task_id", "")
                or (item.get("task_id") if isinstance(item, dict) else "")
                or ""
            )
            for item in task_items
        )
        raw = "|".join([stage_id, pdd_id, *task_ids])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _write_plan_digest(self, plan_id: str, task_items: list, task_map_ref: str) -> str:
        """B-93-03: 落 plan-digest.md,返回相对 state_dir 的 digest_ref(失败返回 '')。"""
        try:
            from zf.runtime.plan_digest import render_plan_digest

            md = render_plan_digest(task_items, plan_id=plan_id, task_map_ref=task_map_ref)
            out_dir = self.state_dir / "artifacts" / "plan-digest"
            out_dir.mkdir(parents=True, exist_ok=True)
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in (plan_id or "plan"))
            path = out_dir / f"{safe}.md"
            path.write_text(md, encoding="utf-8")
            return str(path.relative_to(self.state_dir))
        except Exception:
            return ""

    def _resume_writer_fanout_on_plan_approved(self, event: ZfEvent) -> None:
        """B14: operator 的 plan.approved 到来 → 以原触发事件重入孵化。

        幂等:重入后 _fanout_started 按 trigger_event_id 判重;auto 铸的
        approved(payload auto:true)无需重入(同 run_once 已继续)。
        """
        payload = event.payload if isinstance(event.payload, dict) else {}
        if payload.get("auto"):
            return
        plan_id = str(payload.get("plan_id") or "")
        if not plan_id:
            return
        original = None
        try:
            for existing in self.event_log.read_all():
                if existing.id == plan_id:
                    original = existing
                    break
        except Exception:
            return
        if original is not None:
            self._maybe_start_writer_fanout(original)

    def _maybe_start_writer_fanout(self, event: ZfEvent) -> None:
        if getattr(event, "type", "") == "plan.approved":
            self._resume_writer_fanout_on_plan_approved(event)
            return
        stages = [
            stage for stage in getattr(self.config.workflow, "stages", [])
            if stage.topology == "fanout_writer_scoped" and stage.trigger == event.type
            and self._fanout_stage_matches_trigger_event(stage, event)
        ]
        if not stages:
            return
        from zf.runtime.plan_artifact_package_runtime import admit_task_map_trigger_package
        event = admit_task_map_trigger_package(self, event, stages)
        if event is None:
            return
        # R22 no-livelock: once candidate rework for this pdd has escalated
        # (human.escalate at the cap), a spurious fresh task_map.ready must not
        # re-arm impl and restart the capped loop from zero. Bounded sweep
        # retriggers (rework_of) and operator-authorized re-plans pass through.
        ipayload = event.payload if isinstance(event.payload, dict) else {}
        if not ipayload.get("rework_of") and not ipayload.get("operator_authorized"):
            from zf.runtime.rework_quarantine import is_pdd_rework_quarantined
            from zf.runtime.event_window import read_runtime_events

            recent = read_runtime_events(self.event_log, self.state_dir)
            pdd_id = self._fanout_pdd_id(event)
            if is_pdd_rework_quarantined(recent, pdd_id):
                already = any(
                    e.type == "candidate.rework.quarantined"
                    and isinstance(e.payload, dict)
                    and e.payload.get("trigger_event_id") == event.id
                    for e in recent
                )
                if not already:
                    self.event_writer.append(ZfEvent(
                        type="candidate.rework.quarantined",
                        actor="zf-cli",
                        payload={
                            "pdd_id": pdd_id,
                            "trigger_event_id": event.id,
                            "reason": (
                                "candidate rework already escalated "
                                "(human.escalate); suppressing spurious "
                                "task_map.ready re-arm to avoid restarting the "
                                "capped rework loop. Resume needs an "
                                "operator-authorized task_map.ready "
                                "(operator_authorized) or a "
                                "candidate.rework.cleared event."
                            ),
                        },
                        correlation_id=event.correlation_id or event.id,
                    ))
                return
        from zf.runtime.fanout import FanoutChild, FanoutContext
        from zf.runtime.writer_fanout_admission import (
            WriterTaskMapPolicyError,
            admit_writer_fanout,
            load_writer_task_map,
        )

        for stage in stages:
            if self._fanout_started(stage.id, event.id):
                continue
            if self._equivalent_rework_fanout_started(stage.id, event):
                continue
            if self._equivalent_task_map_writer_fanout_started(stage.id, event):
                continue
            use_affinity = (
                self._fanout_assignment_strategy(stage) == "affinity_stage_slots"
            )
            lane_roles: list[tuple[str, RoleConfig]] = []
            trace_id = event.correlation_id or (
                event.payload.get("trace_id", "") if isinstance(event.payload, dict) else ""
            ) or event.id
            pdd_id = self._fanout_pdd_id(event)
            loaded = None
            try:
                loaded = load_writer_task_map(
                    stage=stage,
                    event=event,
                    pdd_id=pdd_id,
                    state_dir=self.state_dir,
                    project_root=self.project_root,
                    pipeline_spec=self._lane_pipeline_for_trigger(
                        getattr(event, "type", ""),
                    ),
                    candidate_quality_source=str(getattr(
                        self.config.workflow,
                        "candidate_quality_source",
                        "auto",
                    ) or "auto"),
                    work_units_config=getattr(
                        self.config.workflow, "work_units", None,
                    ),
                )
            except Exception as exc:
                if isinstance(exc, WriterTaskMapPolicyError):
                    self._block_writer_fanout_tasks(
                        task_items=exc.task_items,
                        reason="task_map_policy_failed",
                    )
                # prod-e2e(2026-07-04 prd 轮实弹):planner 交付 .md 而非
                # task_map.json → 这里以前只发 fanout.cancelled 即死端
                # (rework_routing 只认 stage 失败事件,没人发它)。按拓扑
                # 找到产出触发事件的上游 stage,发其 failure_event,让
                # 路由把坏 task_map 送回 plan 侧返工。
                trigger_payload = (
                    event.payload if isinstance(event.payload, dict) else {}
                )
                emit_plan_admission_cancel(
                    self,
                    trigger_event=event,
                    stage_id=stage.id,
                    trace_id=trace_id,
                    pdd_id=pdd_id,
                    feature_id=str(trigger_payload.get("feature_id") or pdd_id),
                    task_map_ref=str(trigger_payload.get("task_map_ref") or ""),
                    reason=str(exc),
                )
                continue
            try:
                if use_affinity:
                    lane_roles = self._fanout_affinity_lane_roles(stage)
                    roles = [role for _, role in lane_roles]
                    if not lane_roles:
                        raise RuntimeError(
                            "writer fanout affinity_stage_slots has no available lanes"
                        )
                else:
                    roles = self._fanout_roles(stage.roles)
                task_items = loaded.task_items
                pin_event = event
                if not str(
                    (event.payload if isinstance(event.payload, dict) else {}).get(
                        "task_map_ref"
                    )
                    or ""
                ).strip():
                    pin_event = replace(
                        event,
                        payload={
                            **(
                                event.payload
                                if isinstance(event.payload, dict)
                                else {}
                            ),
                            "task_map_ref": str(loaded.task_map_path),
                            "pdd_id": loaded.pdd_id or pdd_id,
                            "feature_id": loaded.feature_id or loaded.pdd_id or pdd_id,
                        },
                    )
                if not self._pin_goal_claim_set(pin_event):
                    self._block_writer_fanout_tasks(
                        task_items=task_items,
                        reason="goal_claim_set_pin_failed",
                    )
                    emit_plan_admission_cancel(
                        self,
                        trigger_event=event,
                        stage_id=stage.id,
                        trace_id=trace_id,
                        pdd_id=loaded.pdd_id or pdd_id,
                        feature_id=loaded.feature_id or loaded.pdd_id or pdd_id,
                        task_map_ref=loaded.task_map_ref,
                        task_map_path=loaded.task_map_path,
                        source_index_ref=loaded.source_index_ref,
                        reason="goal_claim_set_pin_failed",
                        extra_payload={
                            "suggested_action": "repair_plan_artifact_refs",
                        },
                    )
                    continue
                # Opt-in (stage.synthesize_canonical_tasks): make the task_map's
                # tasks canonical in the kanban when this writer-fanout stage is
                # driven directly by task_map.ready (refactor scan flow) rather
                # than the product-delivery handshake. Source-index admission
                # must run before this materialization; otherwise a fail-closed
                # source_index_gap can leave canonical backlog tasks behind and
                # the regular scheduler may dispatch them outside writer fanout.
                source_gate_checked = False
                source_gate = None
                if getattr(stage, "synthesize_canonical_tasks", False):
                    source_gate = self._writer_source_index_gate(
                        loaded=loaded,
                        stage_id=stage.id,
                        trigger_event=event,
                        trace_id=trace_id,
                    )
                    source_gate_checked = True
                    if source_gate is not None and not source_gate.passed:
                        return
                    # B14 leak guard: gate plan approval BEFORE seeding canonical
                    # tasks. Otherwise the seeded backlog tasks are dispatchable by
                    # feature_backlog_scheduler while the fanout is parked pending
                    # plan.approved — the worker then runs the task OUTSIDE the
                    # fanout, emits a bare worker completion, and the writer-fanout
                    # aggregator (which never started) can't turn it into
                    # candidate.ready. enabled=False auto-mints approved (no park,
                    # no leak); enabled=True parks here with zero seeded tasks.
                    if not self._plan_approval_satisfied(
                        stage_id=stage.id,
                        trigger_event=event,
                        loaded=loaded,
                        trace_id=trace_id,
                    ):
                        return
                    self._ensure_writer_tasks_canonical(loaded)
                if not use_affinity and len(task_items) > len(roles):
                    raise RuntimeError(
                        "writer fanout has more tasks than writer role instances"
                    )
                admission = admit_writer_fanout(
                    task_store=self.task_store,
                    loaded=loaded,
                )
                if admission.passed:
                    # B4 (doc 91 P1): source-index 双向锚门。普通
                    # product-delivery 路径在 admission 之后、孵化之前跑;
                    # synthesize_canonical_tasks 路径已在 canonical seed 之前跑。
                    if not source_gate_checked:
                        source_gate = self._writer_source_index_gate(
                            loaded=loaded,
                            stage_id=stage.id,
                            trigger_event=event,
                            trace_id=trace_id,
                        )
                    if source_gate is not None and not source_gate.passed:
                        return
                    # B14 (doc 93): 人工 plan 审核门 — fanout 触发语义
                    # 恒挂 plan.approved;enabled 只决定这枚由谁铸
                    # (False=kernel auto / True=operator)。hold 时发
                    # requested 并停在此处,approved 事件到来后重入。
                    if not self._plan_approval_satisfied(
                        stage_id=stage.id,
                        trigger_event=event,
                        loaded=loaded,
                        trace_id=trace_id,
                    ):
                        return
                if not admission.passed:
                    emit_plan_admission_cancel(
                        self,
                        trigger_event=event,
                        stage_id=stage.id,
                        trace_id=trace_id,
                        pdd_id=loaded.pdd_id or pdd_id,
                        feature_id=(
                            loaded.feature_id or loaded.pdd_id or pdd_id
                        ),
                        task_map_ref=loaded.task_map_ref,
                        task_map_path=loaded.task_map_path,
                        source_index_ref=loaded.source_index_ref,
                        wave=loaded.wave,
                        task_ids=[
                            str(item.get("task_id") or "")
                            for item in loaded.task_items
                        ],
                        reason=admission.reason,
                        extra_payload=admission.failure_payload(),
                    )
                    continue
                task_items = admission.task_items
            except Exception as exc:
                emit_plan_admission_cancel(
                    self,
                    trigger_event=event,
                    stage_id=stage.id,
                    trace_id=trace_id,
                    pdd_id=(loaded.pdd_id if loaded is not None else pdd_id),
                    feature_id=(
                        loaded.feature_id or loaded.pdd_id or pdd_id
                        if loaded is not None else pdd_id
                    ),
                    task_map_ref=(loaded.task_map_ref if loaded is not None else ""),
                    task_map_path=(loaded.task_map_path if loaded is not None else None),
                    source_index_ref=(loaded.source_index_ref if loaded is not None else ""),
                    reason=str(exc),
                )
                continue
            if not task_items or not roles:
                continue

            admitted = emit_task_map_admitted(
                self,
                trigger_event=event,
                stage_id=stage.id,
                trace_id=trace_id,
                loaded=loaded,
                task_items=task_items,
            )

            # doc 78 W3a: publish the orchestrator-produced task_map into the
            # artifact-ledger version chain. The refactor flow's task_map comes
            # from the orchestrator agent's task_map.ready (not the plan-stage
            # aggregate), so THIS is the path that populates the re-plan
            # supersedes chain (task_map_history / delivery_trace) for refactor
            # runs — each task_map.ready (incl. replans) is a new version.
            self._publish_task_map_version_manifest(
                loaded=loaded,
                trace_id=trace_id,
                is_replan=bool(
                    isinstance(event.payload, dict)
                    and (event.payload.get("rework_attempt") or event.payload.get("rework_of"))
                ),
            )

            # α-1 (2026-05-17): refuse fanout when proposed task siblings
            # write overlapping files. Emit fanout.serialize and skip the
            # parallel dispatch — backlog scheduler will pick the tasks
            # up sequentially. See docs/design/36 §4.2 and the α-1 backlog.
            independent, conflict_reason = self._check_fanout_independence(
                task_items,
            )
            if not independent:
                self.event_writer.append(ZfEvent(
                    type="fanout.serialize",
                    actor="zf-cli",
                    payload={
                        "stage_id": stage.id,
                        "trigger_event_id": event.id,
                        "trace_id": trace_id,
                        "pdd_id": pdd_id,
                        "reason": conflict_reason,
                        "task_ids": [
                            str(item.get("task_id") or "")
                            for item in task_items
                        ],
                    },
                    causation_id=event.id,
                    correlation_id=trace_id,
                ))
                continue

            writer_target_ref = (
                loaded.dispatch_base_commit
                or self.config.runtime.git.candidate_base_ref
            )
            base_context = FanoutContext.create(
                stage_id=stage.id,
                topology=stage.topology,
                trace_id=trace_id,
                trigger_event_id=event.id,
                target_ref=writer_target_ref,
                role_instances=[],
            )
            children: list[FanoutChild] = []
            assignments: list[tuple[dict, RoleConfig, FanoutChild]] = []
            queued_children: list[tuple[dict, FanoutChild]] = []
            try:
                # Honor the plan's owner_role for non-affinity writer dispatch
                # (positional fallback). See assign_nonaffinity_writer_roles.
                nonaffinity_role_by_index = (
                    []
                    if use_affinity
                    else assign_nonaffinity_writer_roles(task_items, roles)
                )
                used_affinity_lanes: set[str] = set()
                from zf.runtime.contract_authority import (
                    apply_contract_authority,
                )

                for index, raw_task_item in enumerate(task_items):
                    # avbs-r4 F4: kanban 契约是 verification 唯一权威源;
                    # task_map 工件副本可能滞后于 task.contract.update
                    # (r4 三向分叉),派发 payload 以 TaskStore 为准。
                    raw_task_item = apply_contract_authority(
                        raw_task_item, self.task_store,
                    )
                    trigger_payload = (
                        event.payload if isinstance(event.payload, dict) else {}
                    )
                    parent_operation_id = str(
                        trigger_payload.get("parent_operation_id")
                        or trigger_payload.get("workflow_operation_id")
                        or ""
                    )
                    if parent_operation_id:
                        raw_task_item.setdefault(
                            "parent_operation_id",
                            parent_operation_id,
                        )
                    for key in _DURABLE_CALL_TRIGGER_KEYS:
                        value = trigger_payload.get(key)
                        if value not in (None, ""):
                            raw_task_item.setdefault(key, value)
                    if use_affinity:
                        if not _writer_task_dependencies_satisfied(
                            self.task_store,
                            raw_task_item,
                        ):
                            task_item = self._writer_affinity_task_item(
                                stage,
                                raw_task_item,
                            )
                            child = FanoutChild(
                                child_id=FanoutContext.child_id(
                                    "queued",
                                    ordinal=index,
                                    scope=str(task_item.get("task_id") or ""),
                                ),
                                role_instance="",
                                target_ref=writer_target_ref,
                                payload=task_item,
                            )
                            queued_children.append((task_item, child))
                            children.append(child)
                            continue
                        selected = self._select_writer_affinity_lane_role(
                            stage,
                            raw_task_item,
                            lane_roles=lane_roles,
                            used_lane_ids=used_affinity_lanes,
                        )
                        if selected is not None:
                            lane_id, role = selected
                            used_affinity_lanes.add(lane_id)
                            task_item = self._writer_affinity_task_item(
                                stage,
                                raw_task_item,
                                lane_id=lane_id,
                                role_instance=role.instance_id,
                            )
                            child = FanoutChild(
                                child_id=FanoutContext.child_id(
                                    role.instance_id,
                                    scope=str(task_item.get("task_id") or ""),
                                ),
                                role_instance=role.instance_id,
                                target_ref=writer_target_ref,
                                payload=task_item,
                            )
                            assignments.append((task_item, role, child))
                        else:
                            task_item = self._writer_affinity_task_item(
                                stage,
                                raw_task_item,
                            )
                            child = FanoutChild(
                                child_id=FanoutContext.child_id(
                                    "queued",
                                    ordinal=index,
                                    scope=str(task_item.get("task_id") or ""),
                                ),
                                role_instance="",
                                target_ref=writer_target_ref,
                                payload=task_item,
                            )
                            queued_children.append((task_item, child))
                    else:
                        role = nonaffinity_role_by_index[index]
                        task_item = raw_task_item
                        child = FanoutChild(
                            child_id=FanoutContext.child_id(
                                role.instance_id,
                                scope=str(task_item.get("task_id") or ""),
                            ),
                            role_instance=role.instance_id,
                            target_ref=writer_target_ref,
                            payload=task_item,
                        )
                        assignments.append((task_item, role, child))
                    children.append(child)
            except Exception as exc:
                if use_affinity:
                    self._block_writer_fanout_tasks(
                        task_items=task_items,
                        reason=str(exc),
                    )
                self.event_writer.append(ZfEvent(
                    type="fanout.cancelled",
                    actor="zf-cli",
                    payload={
                        "stage_id": stage.id,
                        "trigger_event_id": event.id,
                        "trace_id": trace_id,
                        "pdd_id": loaded.pdd_id or pdd_id,
                        "feature_id": loaded.feature_id or loaded.pdd_id or pdd_id,
                        "task_map_ref": loaded.task_map_ref,
                        "source_index_ref": loaded.source_index_ref,
                        "reason": str(exc),
                    },
                    causation_id=event.id,
                    correlation_id=trace_id,
                ))
                continue
            context = FanoutContext(
                fanout_id=base_context.fanout_id,
                stage_id=base_context.stage_id,
                topology=base_context.topology,
                trace_id=base_context.trace_id,
                trigger_event_id=base_context.trigger_event_id,
                target_ref=base_context.target_ref,
                expected_children=children,
            )
            started = context.started_event()
            started.payload["pdd_id"] = loaded.pdd_id or pdd_id
            started.payload["feature_id"] = loaded.feature_id or loaded.pdd_id or pdd_id
            started.payload["task_map_ref"] = loaded.task_map_ref
            started.payload["source_index_ref"] = loaded.source_index_ref
            if loaded.dispatch_base_commit:
                started.payload["dispatch_base_commit"] = loaded.dispatch_base_commit
            admitted_payload = (
                admitted.payload if isinstance(admitted.payload, dict) else {}
            )
            started.payload["task_map_admitted_event_id"] = admitted.id
            started.payload["plan_admission_incident_id"] = str(
                admitted_payload.get("plan_admission_incident_id") or ""
            )
            started.payload["task_map_digest"] = str(
                admitted_payload.get("task_map_digest") or ""
            )
            if loaded.wave is not None:
                started.payload["wave"] = loaded.wave
            if loaded.requested_task_ids:
                started.payload["task_ids"] = list(loaded.requested_task_ids)
            child_success_event, child_failure_event = self._fanout_child_result_events(
                stage.aggregate
            )
            started.payload["aggregate"] = {
                "mode": stage.aggregate.mode,
                "success_event": stage.aggregate.success_event,
                "failure_event": stage.aggregate.failure_event,
                "child_success_event": child_success_event,
                "child_failure_event": child_failure_event,
                "synth_role": stage.aggregate.synth_role,
                "max_retries": stage.aggregate.max_retries,
                "review_strategy": stage.aggregate.review_strategy,
                "synth_timeout_seconds": stage.aggregate.synth_timeout_seconds,
            }
            self.event_writer.append(started)

            if loaded.is_replan:
                operator_authorized = bool(
                    isinstance(event.payload, dict)
                    and event.payload.get("operator_authorized")
                )
                for task_item, role, _child in assignments:
                    state = str(
                        getattr(self, "_last_worker_state", {}).get(
                            role.instance_id,
                            "idle",
                        )
                    )
                    if state == "blocked" or (
                        state == "blocked_human" and operator_authorized
                    ):
                        self._set_worker_state(
                            role.instance_id,
                            "idle",
                            task_id=str(task_item.get("task_id") or ""),
                            reason=(
                                "writer replan admitted; releasing prior "
                                f"{state} generation"
                            ),
                            force=True,
                        )

            # Surface candidate-rework feedback to the re-dispatched writers.
            # The self-heal sweep re-emits task_map.ready carrying the reviewers'
            # findings; without threading them into the briefing the writers
            # re-run blind and reproduce the same rejected defect.
            ev_payload = event.payload if isinstance(event.payload, dict) else {}
            rework_feedback = [
                str(x) for x in (ev_payload.get("rework_feedback") or []) if str(x).strip()
            ]
            rework_attempt = int(ev_payload.get("rework_attempt") or 0)
            rework_summary = (
                ev_payload.get("rework_summary")
                if isinstance(ev_payload.get("rework_summary"), dict)
                else {}
            )

            if use_affinity:
                for task_item, role, child in assignments:
                    slot_payload = {
                        "fanout_id": context.fanout_id,
                        "trace_id": context.trace_id,
                        "stage_id": context.stage_id,
                        "child_id": child.child_id,
                        "role_instance": role.instance_id,
                        "task_id": str(task_item.get("task_id") or ""),
                    }
                    self._copy_fanout_assignment_metadata(slot_payload, task_item)
                    self.event_writer.append(ZfEvent(
                        type="fanout.slot.assigned",
                        actor="zf-cli",
                        payload=slot_payload,
                        causation_id=started.id,
                        correlation_id=context.trace_id,
                    ))
                for queue_order, (task_item, child) in enumerate(queued_children):
                    queued_payload = {
                        "fanout_id": context.fanout_id,
                        "trace_id": context.trace_id,
                        "stage_id": context.stage_id,
                        "child_id": child.child_id,
                        "target_ref": child.target_ref,
                        "task_id": str(task_item.get("task_id") or ""),
                        "scope": str(task_item.get("scope") or ""),
                        "pdd_id": loaded.pdd_id or pdd_id,
                        "feature_id": loaded.feature_id or loaded.pdd_id or pdd_id,
                        "task_map_ref": loaded.task_map_ref,
                        "source_index_ref": loaded.source_index_ref,
                        "queue_order": queue_order,
                    }
                    self._copy_fanout_assignment_metadata(queued_payload, task_item)
                    self.event_writer.append(ZfEvent(
                        type="fanout.child.queued",
                        actor="zf-cli",
                        payload=queued_payload,
                        causation_id=started.id,
                        correlation_id=context.trace_id,
                    ))
                    self._park_writer_fanout_queued_task(
                        task_id=str(task_item.get("task_id") or ""),
                        fanout_id=context.fanout_id,
                        child_id=child.child_id,
                    )

            prepared_dispatches = self._preregister_writer_fanout_operations(
                context=context,
                assignments=assignments,
                causation_id=started.id,
            )
            for task_item, role, child in assignments:
                self._dispatch_writer_fanout_child(
                    context=context,
                    child=child,
                    task_item=task_item,
                    role=role,
                    pdd_id=loaded.pdd_id or pdd_id,
                    feature_id=loaded.feature_id or loaded.pdd_id or pdd_id,
                    task_map_ref=loaded.task_map_ref,
                    source_index_ref=loaded.source_index_ref,
                    wave=loaded.wave,
                    causation_id=started.id,
                    rework_feedback=rework_feedback,
                    rework_attempt=rework_attempt,
                    rework_summary=rework_summary,
                    prepared_dispatch=prepared_dispatches.get(child.child_id),
                )
    def _prepare_writer_contract_snapshot(
        self,
        *,
        task_item: dict,
        context,
        project_path: str,
    ) -> tuple[dict, dict]:
        task_id = str(task_item.get("task_id") or "")
        task = self.task_store.get(task_id) if task_id else None
        if task is None:
            raise TaskContractSnapshotError(
                f"cannot snapshot missing canonical task {task_id!r}"
            )
        try:
            descriptor = contract_descriptor_from_payload(task_item)
        except TaskContractSnapshotError:
            descriptor = {}
        if descriptor:
            snapshot = hydrate_task_contract_snapshot(
                self.state_dir,
                descriptor,
                expected=current_task_contract_identity(
                    task,
                    task_map_ref=str(task_item.get("task_map_ref") or ""),
                ),
            )
        else:
            from zf.runtime.orchestrator_dispatch import _capture_head

            base_commit = str(
                task_item.get("base_commit")
                or task_item.get("source_commit")
                or ""
            ).strip() or _capture_head(Path(project_path))
            snapshot = build_task_contract_snapshot(
                task,
                workflow_run_id=str(
                    task_item.get("workflow_run_id")
                    or getattr(context, "trace_id", "")
                    or ""
                ),
                task_map_generation_id=task_map_generation(
                    task,
                    task_map_ref=str(task_item.get("task_map_ref") or ""),
                ),
                base_commit=base_commit,
                task_ref=f"{self.config.runtime.git.task_ref_prefix}/{task_id}",
            )
            descriptor = write_task_contract_snapshot(
                self.state_dir,
                snapshot,
                source_event_id=str(getattr(context, "trigger_event_id", "") or ""),
            )
        fields = {
            **snapshot_payload_fields(descriptor),
            "workflow_run_id": str(snapshot["workflow_run_id"]),
            "contract_revision": str(snapshot["contract_revision"]),
            "task_map_generation": str(snapshot["task_map_generation"]),
            "base_commit": str(snapshot["base_commit"]),
            "plan_artifact_package_id": str(snapshot.get("plan_artifact_package_id") or ""),
            "plan_artifact_package_ref": str(snapshot.get("plan_artifact_package_ref") or ""),
            "plan_artifact_package_digest": str(snapshot.get("plan_artifact_package_digest") or ""),
        }
        task_item.update(fields)
        task_payload = task_item.get("payload")
        if isinstance(task_payload, dict):
            for key, value in fields.items():
                task_payload.setdefault(key, value)
        return snapshot, descriptor

    def _typed_task_contract_handoff_enabled(self, task_item: dict) -> bool:
        if str(task_item.get("contract_snapshot_ref") or "").strip():
            return True
        from zf.core.verification.event_schema import event_schemas_for_config

        task_payload = task_item.get("payload")
        payload = task_payload if isinstance(task_payload, dict) else task_item
        schemas = event_schemas_for_config(self.config, payload=payload)
        if not isinstance(schemas, dict):
            return False
        for event_type in (
            "verify.child.completed",
            "review.child.completed",
            "judge.child.completed",
        ):
            rule = schemas.get(event_type)
            if isinstance(rule, dict) and "verification_result" in (
                rule.get("required") or []
            ):
                return True
        return False

    def _prepare_reader_contract_target(self, child) -> None:
        payload = child.payload if isinstance(child.payload, dict) else {}
        if not (
            str(payload.get("contract_snapshot_ref") or "").strip()
            or str(payload.get("contract_snapshot_digest") or "").strip()
        ):
            return
        if (
            str(payload.get("closure_identity") or "").strip()
            and str(payload.get("goal_claim_set_ref") or "").strip()
        ):
            from zf.runtime.goal_closure_identity import (
                validate_goal_closure_dispatch_snapshots,
            )

            validate_goal_closure_dispatch_snapshots(self.state_dir, payload)
            return
        descriptor = contract_descriptor_from_payload(payload)
        task_id = str(payload.get("task_id") or "")
        task = self.task_store.get(task_id) if task_id else None
        expected = {"task_id": task_id}
        if task is not None:
            expected.update(current_task_contract_identity(
                task,
                task_map_ref=str(payload.get("task_map_ref") or ""),
            ))
        snapshot = hydrate_task_contract_snapshot(
            self.state_dir,
            descriptor,
            expected=expected,
        )
        target = build_target_snapshot(
            descriptor,
            target_commit=str(payload.get("target_commit") or child.target_ref or ""),
            contract_snapshot=snapshot,
        )
        try:
            target_descriptor = target_descriptor_from_payload(payload)
            target = hydrate_target_snapshot(
                self.state_dir,
                target_descriptor,
                expected={
                    "contract_snapshot_ref": descriptor["ref"],
                    "contract_snapshot_digest": descriptor["sha256"],
                    "target_commit": str(target["target_commit"]),
                },
            )
        except TaskContractSnapshotError:
            target_descriptor = write_target_snapshot(
                self.state_dir,
                target,
                source_event_id=str(payload.get("trigger_event_id") or ""),
            )
        payload.update({
            "workflow_run_id": str(snapshot["workflow_run_id"]),
            "contract_revision": str(snapshot["contract_revision"]),
            "task_map_generation": str(snapshot["task_map_generation"]),
            "base_commit": str(snapshot["base_commit"]),
            "plan_artifact_package_id": str(snapshot.get("plan_artifact_package_id") or ""),
            "plan_artifact_package_ref": str(snapshot.get("plan_artifact_package_ref") or ""),
            "plan_artifact_package_digest": str(snapshot.get("plan_artifact_package_digest") or ""),
            "target_snapshot": target,
            "target_commit": str(target["target_commit"]),
            **target_payload_fields(target_descriptor),
        })

    def _dispatch_writer_fanout_child(
        self,
        *,
        context,
        child,
        task_item: dict,
        role: RoleConfig,
        pdd_id: str,
        feature_id: str,
        task_map_ref: str,
        source_index_ref: str,
        wave: int | None,
        causation_id: str,
        rework_feedback: list[str] | None = None,
        rework_attempt: int = 0,
        rework_summary: dict | None = None,
        prepared_dispatch: dict[str, Any] | None = None,
    ) -> bool:
        run_id = f"run-{context.fanout_id}-{child.child_id}"
        try:
            from zf.runtime.affinity_review_scope import affinity_scope_identity_errors
            task_id = str(task_item.get("task_id") or "")
            identity_errors = affinity_scope_identity_errors(
                task_item,
                role_instance=role.instance_id,
            )
            if identity_errors:
                raise RuntimeError(
                    "fanout affinity child identity invalid: "
                    + ", ".join(identity_errors)
                )
            fence_reason = self._writer_task_dispatch_fence_reason(
                task_id,
                role_instance=role.instance_id,
                run_id=run_id,
            )
            if fence_reason:
                self._defer_writer_fanout_dispatch(
                    context=context,
                    child=child,
                    task_item=task_item,
                    role=role,
                    run_id=run_id,
                    causation_id=causation_id,
                    reason=fence_reason,
                    release_slot=True,
                )
                return False
            if not self._ensure_fanout_role_dispatchable(
                role=role,
                fanout_id=context.fanout_id,
                stage_id=context.stage_id,
                child_id=child.child_id,
                run_id=run_id,
                trace_id=context.trace_id,
                causation_id=causation_id,
                prompt_kind="fanout_child",
            ):
                self._park_writer_fanout_deferred_task(
                    task_id=task_id,
                    fanout_id=context.fanout_id,
                    child_id=child.child_id,
                )
                self._release_writer_fanout_slot(
                    context=context,
                    child=child,
                    task_item=task_item,
                    role=role,
                    causation_id=causation_id,
                    reason="dispatch_deferred",
                )
                return False
            # Bind the canonical kanban task to this fanout dispatch, mirroring
            # the normal dispatch path. The fanout-writer child completes via
            # task.ref.updated, not the normal dispatch lifecycle, so without
            # this the task keeps active_dispatch_id="" and the Stop-guard
            # (provider.stop.check) is unsatisfiable: the worker cannot cleanly
            # stop after dev.build.done and the (affinity) lane never releases,
            # which strands candidate-rework re-dispatch. Only acts on tasks the
            # writer-fanout admission has already made canonical in the kanban.
            current_task = self.task_store.get(task_id) if task_id else None
            if current_task is not None:
                if current_task.status == "backlog":
                    self._move_task(task_id, "in_progress")
                self.task_store.update(
                    task_id,
                    assigned_to=role.instance_id,
                    active_dispatch_id=run_id,
                )
            prepared_dispatch = prepared_dispatch or self._prepare_writer_fanout_child_operation(
                context=context,
                child=child,
                task_item=task_item,
                role=role,
                causation_id=causation_id,
            )
            if prepared_dispatch.get("skip"):
                return False
            plan = prepared_dispatch["plan"]
            skill_entries = list(prepared_dispatch.get("skill_entries") or [])
            contract_dispatch_fields = dict(
                prepared_dispatch.get("contract_dispatch_fields") or {}
            )
            operation_payload = dict(prepared_dispatch.get("operation_payload") or {})
            prepared_call = prepared_dispatch.get("prepared_call")
            if prepared_call is not None and not prepared_call.should_dispatch:
                if prepared_call.ensure_status == "settled":
                    self._replay_settled_fanout_call(
                        context=context,
                        child=child,
                        prepared=prepared_call,
                        topology="fanout_writer_scoped",
                        causation_id=causation_id,
                    )
                return False
            briefing_path = self._write_writer_fanout_briefing(
                role=role,
                context=context,
                child=child,
                task_item=task_item,
                run_id=run_id,
                pdd_id=pdd_id,
                workdir_plan=plan,
                skill_entries=skill_entries,
                rework_feedback=rework_feedback or [],
                rework_attempt=rework_attempt,
                rework_summary=rework_summary or {},
            )
            prompt = build_task_prompt(
                role.instance_id,
                briefing_path,
                prompt_kind="fanout_child",
            )
            dispatch_context = self._dispatch_context(
                role=role,
                briefing_path=briefing_path,
                trace_id=context.trace_id,
            )
            self._send_transport_task(
                role.instance_id,
                briefing_path,
                prompt,
                dispatch_context,
            )
            if prepared_call is not None:
                from zf.runtime.call_result_runtime import mark_call_operation_started

                mark_call_operation_started(
                    self,
                    prepared_call,
                    task_id=task_id,
                    dispatch_id=run_id,
                    causation_id=causation_id,
                    correlation_id=context.trace_id,
                )
            self._note_prompt_sent(role.instance_id, run_id)
            dispatched = context.child_dispatched_event(child, run_id=run_id)
            dispatched.payload.update({
                "task_id": task_id,
                "scope": str(task_item.get("scope") or ""),
                "workdir": plan.project_path,
                "source_branch": plan.branch_or_ref,
                "pdd_id": pdd_id,
                "feature_id": feature_id,
                "task_map_ref": task_map_ref,
                "source_index_ref": source_index_ref,
                "skills": list(role.skills),
                "briefing_path": str(briefing_path),
                **contract_dispatch_fields,
            })
            if operation_payload.get("dependency_refs"):
                dispatched.payload["dependency_refs"] = list(
                    operation_payload["dependency_refs"]
                )
            if operation_payload.get("dependency_refs_skipped"):
                dispatched.payload["dependency_refs_skipped"] = list(
                    operation_payload["dependency_refs_skipped"]
                )
            if wave is not None:
                dispatched.payload["wave"] = wave
            self._copy_fanout_assignment_metadata(dispatched.payload, task_item)
            for key in _CONTRACT_HANDOFF_KEYS:
                value = operation_payload.get(key)
                if value not in (None, ""):
                    dispatched.payload[key] = value
            if self._claim_writer_fanout_task(
                task_id,
                role.instance_id,
                run_id=run_id,
            ):
                dispatched.payload["task_status"] = "in_progress"
                dispatched.payload["assigned_to"] = role.instance_id
            try:
                snapshot_result, snapshot_payload = (
                    self._write_fanout_child_runtime_snapshot(
                        role=role,
                        payload=dispatched.payload,
                        briefing_path=briefing_path,
                    )
                )
                dispatched.payload["snapshot_ref"] = snapshot_result.snapshot_ref
                self.event_writer.append(ZfEvent(
                    type="runtime.snapshot.recorded",
                    actor="zf-cli",
                    task_id=task_id or None,
                    payload=snapshot_payload,
                    causation_id=dispatched.causation_id,
                    correlation_id=context.trace_id,
                ))
            except Exception as snapshot_exc:
                self.event_writer.append(ZfEvent(
                    type="runtime.snapshot.invalid",
                    actor="zf-cli",
                    task_id=task_id or None,
                    payload={
                        "source": "fanout_child",
                        "reason": str(snapshot_exc),
                        "fanout_id": context.fanout_id,
                        "child_id": child.child_id,
                        "run_id": run_id,
                    },
                    causation_id=dispatched.causation_id,
                    correlation_id=context.trace_id,
                ))
            self.event_writer.append(dispatched)
            self._set_worker_state(
                role.instance_id,
                "busy",
                reason=f"dispatched fanout child {context.fanout_id}/{child.child_id}",
                task_id=task_id,
            )
            return True
        except Exception as exc:
            payload = {
                "fanout_id": context.fanout_id,
                "trace_id": context.trace_id,
                "stage_id": context.stage_id,
                "child_id": child.child_id,
                "run_id": run_id,
                "role_instance": child.role_instance,
                "task_id": str(task_item.get("task_id") or ""),
                "scope": str(task_item.get("scope") or ""),
                "pdd_id": pdd_id,
                "feature_id": feature_id,
                "task_map_ref": task_map_ref,
                "source_index_ref": source_index_ref,
                "reason": str(exc),
            }
            dispatch_failure_kind = classify_dispatch_exception(exc)
            if dispatch_failure_kind:
                payload["failure_kind"] = dispatch_failure_kind
            self._copy_fanout_assignment_metadata(payload, task_item)
            self.event_writer.append(ZfEvent(
                type="fanout.child.failed",
                actor="zf-cli",
                payload=payload,
                causation_id=causation_id,
                correlation_id=context.trace_id,
            ))
            return False

    def _resolve_orphan_reader_fanout_child(
        self,
        event: ZfEvent,
        payload: dict,
    ) -> tuple[str, str] | None:
        """Re-bind a child result that lost its fanout context (B-STUCK-1b).

        Restart / regular re-dispatch can strip ``fanout_id``/``child_id`` from a
        reader-fanout child: the worker emits a bare domain completion carrying
        only ``actor`` (its role instance) + ``dispatch_id``, which
        ``_maybe_update_reader_fanout`` would silently drop -> the fanout barrier
        never resolves -> stage times out (the ledgerlite prd-refine livelock).

        Reconcile by the one key that survives a bare re-dispatch: the emitting
        role instance. WIP=1 means a role runs exactly one thing, so a result
        from role R unambiguously belongs to the single non-terminal reader
        child whose ``role_instance == R`` -- but only when exactly one such
        child exists and its manifest treats this event as a child result.
        Zero or multiple matches -> do not guess.
        """
        status = str(payload.get("status") or "")
        result_statuses = {
            "completed", "passed", "approved", "success",
            "failed", "failure", "rejected",
        }
        # Cheap gate: skip the manifest scan for the vast majority of events
        # (heartbeats, usage, decisions) that are not child results.
        if status not in result_statuses and not isinstance(
            payload.get("report"), dict
        ):
            return None
        role_instance = str(payload.get("role_instance") or "") or str(
            getattr(event, "actor", "") or ""
        )
        if not role_instance:
            return None
        fanout_root = self.state_dir / "fanouts"
        if not fanout_root.exists():
            return None
        terminal = {"completed", "failed", "timed_out", "cancelled"}
        matches: list[tuple[str, str]] = []
        for manifest_path in fanout_root.glob("*/manifest.json"):
            fanout_id = manifest_path.parent.name
            manifest = self._fanout_manifest(fanout_id)
            if not manifest or manifest.get("topology") != "fanout_reader":
                continue
            aggregate = (
                manifest.get("aggregate")
                if isinstance(manifest.get("aggregate"), dict)
                else {}
            )
            if (
                str(manifest.get("status") or "") in terminal
                or str(aggregate.get("status") or "") in terminal
            ):
                continue
            # A superseded (non-current) generation is as dead as a terminal one
            # for re-binding: a superseded fanout is abandoned mid-flight when a
            # newer instance of the same logical stage starts, so its manifest
            # status is NOT terminal yet its children are orphans. Binding a fresh
            # completion to it only gets that completion dropped as
            # fanout.child.stale_completion. Observed in the feishu e2e: a resident
            # prd-author re-used across rounds emitted a bare completion that bound
            # to a 1.5h-old superseded prd-refine fanout -> stale-dropped -> the new
            # task's prd stage stalled. Skip superseded generations here so the
            # completion lands on the current generation (or none), never a stale one.
            stale_reason, _superseded_by = self._fanout_identity_stale_reason(fanout_id)
            if stale_reason:
                continue
            aggregate_config = manifest.get("aggregate_config") or {}
            success_event = str(aggregate_config.get("success_event") or "")
            failure_event = str(aggregate_config.get("failure_event") or "")
            child_success_event, child_failure_event = (
                self._fanout_child_result_events(aggregate_config)
            )
            is_result = (
                event.type in {
                    child_success_event,
                    child_failure_event,
                    success_event,
                    failure_event,
                }
                or status in result_statuses
            )
            if not is_result:
                continue
            for child in manifest.get("children", []) or []:
                if not isinstance(child, dict):
                    continue
                if str(child.get("role_instance") or "") != role_instance:
                    continue
                if str(child.get("status") or "") in {"completed", "failed"}:
                    continue
                child_id = str(child.get("child_id") or "")
                if child_id:
                    matches.append((fanout_id, child_id))
        if len(matches) == 1:
            return matches[0]
        return None

    def _maybe_update_reader_fanout(self, event: ZfEvent) -> None:
        if event.type == "fanout.synth.completed":
            self._handle_fanout_synth_completed(event)
            return
        if event.type in {
            "fanout.requested",
            "fanout.started",
            "fanout.child.queued",
            "fanout.child.dispatched",
            "fanout.child.completed",
            "fanout.child.failed",
            "fanout.slot.assigned",
            "fanout.slot.released",
            "fanout.assignment.override",
            "fanout.aggregate.started",
            "fanout.aggregate.completed",
            "fanout.synth.dispatched",
            "fanout.timed_out",
            "fanout.cancelled",
        }:
            return
        payload = self._fanout_result_payload(event)
        fanout_id = str(payload.get("fanout_id") or "")
        child_id = str(payload.get("child_id") or payload.get("child_run") or "")
        if not fanout_id or not child_id:
            resolved = self._resolve_orphan_reader_fanout_child(event, payload)
            if resolved is None:
                return
            fanout_id, child_id = resolved
            payload = {**payload, "fanout_id": fanout_id, "child_id": child_id}
        manifest = self._fanout_manifest(fanout_id)
        if not manifest:
            return
        if manifest.get("topology") != "fanout_reader":
            return
        if self._cancel_candidate_superseded_replan_fanout(manifest):
            return
        child = self._fanout_child(manifest, child_id)
        stale_reason, superseded_by = self._fanout_identity_stale_reason(fanout_id)
        if stale_reason:
            self._emit_fanout_identity_stale_completion(
                event=event,
                payload=payload,
                manifest=manifest,
                child=child,
                reason=stale_reason,
                superseded_by=superseded_by,
            )
            return
        aggregate_config = manifest.get("aggregate_config") or {}
        success_event = str(aggregate_config.get("success_event") or "")
        failure_event = str(aggregate_config.get("failure_event") or "")
        child_success_event, child_failure_event = self._fanout_child_result_events(
            aggregate_config,
        )
        if child and child.get("status") in {"completed", "failed"}:
            return
        run_id = str(payload.get("run_id") or (child or {}).get("run_id") or "")
        base_payload = {
            "fanout_id": fanout_id,
            "trace_id": manifest.get("trace_id", ""),
            "stage_id": manifest.get("stage_id", ""),
            "child_id": child_id,
            "run_id": run_id,
            "role_instance": str(payload.get("role_instance") or (child or {}).get("role_instance") or ""),
            "target_ref": manifest.get("target_ref", ""),
        }
        for key in _FANOUT_AFFINITY_METADATA_KEYS:
            value = self._fanout_payload_metadata_value(payload, child, key)
            if value:
                base_payload[key] = value
        child_payload = (
            (child or {}).get("payload")
            if isinstance((child or {}).get("payload"), dict)
            else {}
        )
        for key in _CONTRACT_HANDOFF_KEYS:
            value = payload.get(key) or (child or {}).get(key) or child_payload.get(key)
            if value not in (None, ""):
                base_payload[key] = value
        status = str(payload.get("status") or "")
        blocked_event_type = str(payload.get("blocked_event_type") or "")
        discriminator_terminal = (
            event.type == "discriminator.failed"
            and blocked_event_type in {
                child_success_event,
                child_failure_event,
                success_event,
                failure_event,
            }
        )
        failed_result = (
            event.type in {child_failure_event, failure_event}
            or status in {"failed", "failure", "rejected"}
            or discriminator_terminal
        )
        completed_result = (
            event.type in {child_success_event, success_event}
            or status in {"completed", "passed", "approved", "success"}
        )
        if not failed_result and not completed_result:
            return
        output_violation = self._fanout_output_path_violation(fanout_id, payload)
        if output_violation:
            self.event_writer.append(ZfEvent(
                type="fanout.child.failed",
                actor="zf-cli",
                payload={**base_payload, "reason": output_violation},
                causation_id=event.id,
                correlation_id=event.correlation_id or manifest.get("trace_id", ""),
            ))
            self._release_fanout_worker_if_terminal(
                role_instance=base_payload["role_instance"],
                fanout_id=fanout_id,
                child_id=child_id,
                run_id=run_id, task_id=str(base_payload.get("task_id") or ""),
            )
            self._evaluate_reader_fanout(fanout_id)
            return

        report_result = self._fanout_child_report(
            child_id=child_id,
            event=event,
            success=completed_result,
        )
        artifact_paths = self._write_fanout_child_output(
            fanout_id,
            child_id,
            event,
            report=report_result.report,
            diagnostics=report_result.diagnostics,
        )
        report_payload = {
            "report": report_result.report,
            "report_path": artifact_paths.get("report_path", ""),
            "report_diagnostics": report_result.diagnostics,
        }
        if isinstance(payload.get("verification_result"), dict):
            report_payload["verification_result"] = dict(
                payload["verification_result"],
            )
        if isinstance(payload.get("goal_closure_result"), dict):
            report_payload["goal_closure_result"] = dict(
                payload["goal_closure_result"],
            )
        for key in ("evidence_refs", "findings", "reproduction_commands"):
            value = payload.get(key)
            if value not in (None, ""):
                report_payload[key] = value
        from zf.runtime.call_result_admission import result_protocol_mode
        from zf.runtime.call_result_runtime import admit_runtime_call_result

        call_mode = result_protocol_mode(
            self.config,
            {**child_payload, **base_payload, **payload},
        )
        call_outcome = admit_runtime_call_result(
            self,
            event,
            merged_payload={
                **child_payload,
                **base_payload,
                **payload,
                **report_payload,
                "result_protocol_mode": call_mode,
            },
            mode=call_mode,
        )
        if call_outcome.repair_requested:
            # Result shape correction is attempt-local. Keep this child live
            # and do not consume a semantic rework attempt.
            return
        if call_outcome.status == "superseded":
            return
        if call_outcome.admitted:
            base_payload.update({
                "operation_id": call_outcome.operation_id,
                "request_hash": call_outcome.request_hash,
                "result_protocol_mode": call_outcome.mode,
                "admitted_call_result_ref": dict(call_outcome.envelope_ref or {}),
                "control_result_ref": dict(call_outcome.control_result_ref or {}),
            })
            if call_mode in {"warning", "blocking"}:
                from zf.runtime.call_result_runtime import hydrate_admitted_control_result

                control_result = hydrate_admitted_control_result(
                    self.state_dir,
                    call_outcome.envelope_ref or {},
                )
                control_verdict = str(control_result.get("verdict") or "").lower()
                base_payload["semantic_verdict"] = control_verdict
                control_schema = str(control_result.get("schema_version") or "")
                base_payload["control_result_schema"] = control_schema
                if (
                    control_schema != "goal-closure-result.v1"
                    and control_verdict in {"rejected", "blocked", "abstained"}
                ):
                    failed_result = True
                    completed_result = False
        # U20(finding 13):审角色报告带判决却零证据引用 → 观测事件;
        # LB-4(2026-07-08):verification.report_evidence_gate=fail_closed
        # 时升级为 child 失败,并入既有 malformed-report 返工轨道(一次
        # rework 语义与上限由既有 rework cap/升级链承担,不新造循环)。
        from zf.runtime.report_evidence_gate import (
            REPORT_EVIDENCE_MISSING_EVENT,
            is_verification_stage,
            report_evidence_gap,
        )

        evidence_gap = ""
        if (
            base_payload.get("control_result_schema") != "goal-closure-result.v1"
            and is_verification_stage(
                stage_id=str(manifest.get("stage_id") or ""),
                event_type=event.type,
            )
        ):
            evidence_gap = report_evidence_gap(report_result.report)
        if evidence_gap:
            self.event_writer.append(ZfEvent(
                type=REPORT_EVIDENCE_MISSING_EVENT,
                actor="zf-cli",
                task_id=base_payload.get("task_id") or None,
                payload={
                    **{k: base_payload.get(k) for k in (
                        "fanout_id", "stage_id", "child_id", "task_id",
                    )},
                    "reason": evidence_gap,
                    "report_status": str(
                        (report_result.report or {}).get("status") or ""
                    ),
                    "result_event_id": event.id,
                },
                causation_id=event.id,
                correlation_id=event.correlation_id or manifest.get("trace_id", ""),
            ))
        evidence_fail_closed = bool(
            evidence_gap
            and completed_result
            and str(getattr(
                getattr(self.config, "verification", None),
                "report_evidence_gate",
                "signal",
            ) or "signal") == "fail_closed"
        )
        if failed_result or not report_result.valid or evidence_fail_closed:
            reason = (
                "malformed_report"
                if not report_result.valid
                else "report_evidence_missing"
                if evidence_fail_closed and not failed_result
                else str(
                    payload.get("reason")
                    or (
                        f"semantic verdict: {base_payload.get('semantic_verdict')}"
                        if base_payload.get("semantic_verdict")
                        else event.type
                    )
                )
            )
            self.event_writer.append(ZfEvent(
                type="fanout.child.failed",
                actor="zf-cli",
                payload={
                    **base_payload,
                    **report_payload,
                    "reason": reason,
                    "evidence": {
                        "result_event_id": event.id,
                        "report_diagnostics": report_result.diagnostics,
                    },
                },
                causation_id=event.id,
                correlation_id=event.correlation_id or manifest.get("trace_id", ""),
            ))
        else:
            self.event_writer.append(ZfEvent(
                type="fanout.child.completed",
                actor="zf-cli",
                payload={
                    **base_payload,
                    **report_payload,
                    "status": "completed",
                    "result_event_id": event.id,
                },
                causation_id=event.id,
                correlation_id=event.correlation_id or manifest.get("trace_id", ""),
            ))
        self._release_fanout_worker_if_terminal(
            role_instance=base_payload["role_instance"],
            fanout_id=fanout_id,
            child_id=child_id,
            run_id=run_id, task_id=str(base_payload.get("task_id") or ""),
        )
        self._evaluate_reader_fanout(fanout_id)
    def _emit_writer_fanout_stale_completion(
        self,
        *,
        event: ZfEvent,
        payload: dict,
        manifest: dict,
        child: dict,
        expected_run_id: str,
    ) -> None:
        fanout_id = str(manifest.get("fanout_id") or "")
        child_id = str(child.get("child_id") or "")
        if self._fanout_stale_completion_recorded(
            fanout_id=fanout_id,
            child_id=child_id,
            source_event_id=event.id,
        ):
            return
        self.event_writer.append(ZfEvent(
            type="fanout.child.stale_completion",
            actor="zf-cli",
            task_id=event.task_id or str(payload.get("task_id") or "") or None,
            payload={
                "fanout_id": fanout_id,
                "trace_id": str(manifest.get("trace_id") or ""),
                "stage_id": str(manifest.get("stage_id") or ""),
                "child_id": child_id,
                "task_id": str(event.task_id or payload.get("task_id") or ""),
                "role_instance": str(
                    payload.get("role_instance")
                    or child.get("role_instance")
                    or event.actor
                    or ""
                ),
                "expected_run_id": expected_run_id,
                "actual_run_id": str(payload.get("run_id") or ""),
                "result_event_id": event.id,
                "source_event_type": event.type,
                "reason": "fanout child run_id does not match active run",
            },
            causation_id=event.id,
            correlation_id=event.correlation_id or manifest.get("trace_id", ""),
        ))

    def _emit_writer_fanout_completion_adopted(
        self,
        *,
        event: ZfEvent,
        manifest: dict,
        child: dict,
        adopted_from: str,
        reason: str,
    ) -> None:
        """BF-1 审计事件:一笔携带旧身份的完成被当前代收编。"""
        fanout_id = str(manifest.get("fanout_id") or "")
        child_id = str(child.get("child_id") or "")
        try:
            already_recorded = any(
                item.type == "fanout.child.completion_adopted"
                and str((item.payload or {}).get("fanout_id") or "") == fanout_id
                and str((item.payload or {}).get("child_id") or "") == child_id
                and str((item.payload or {}).get("result_event_id") or "") == event.id
                for item in reversed(self.event_log.read_all())
                if isinstance(item.payload, dict)
            )
        except OSError:
            already_recorded = False
        if already_recorded:
            return
        self.event_writer.append(ZfEvent(
            type="fanout.child.completion_adopted",
            actor="zf-cli",
            task_id=event.task_id or str(child.get("task_id") or "") or None,
            payload={
                "fanout_id": fanout_id,
                "trace_id": str(manifest.get("trace_id") or ""),
                "stage_id": str(manifest.get("stage_id") or ""),
                "child_id": child_id,
                "task_id": str(event.task_id or child.get("task_id") or ""),
                "adopted_from": adopted_from,
                "result_event_id": event.id,
                "source_event_type": event.type,
                "reason": reason,
            },
            causation_id=event.id,
            correlation_id=event.correlation_id or manifest.get("trace_id", ""),
        ))

    def _fanout_stale_completion_recorded(
        self,
        *,
        fanout_id: str,
        child_id: str,
        source_event_id: str,
    ) -> bool:
        if not fanout_id or not source_event_id:
            return False
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        for event in reversed(events):
            if event.type != "fanout.child.stale_completion":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("fanout_id") or "") != fanout_id:
                continue
            if child_id and str(payload.get("child_id") or "") != child_id:
                continue
            # One stale_completion per (fanout, child) is the record; keying on
            # the source event id let a re-emitting lane amplify 1:1 — r10: one
            # child re-sent verify.child.completed every ~7s (no ack loop) and
            # this sweep answered each with a fresh stale_completion, 1954 rows
            # for a single child (53% of the day's event log with its twin).
            return True
        return False
    def _emit_fanout_identity_stale_completion(
        self,
        *,
        event: ZfEvent,
        payload: dict,
        manifest: dict,
        child: dict | None,
        reason: str,
        superseded_by: str,
    ) -> None:
        fanout_id = str(manifest.get("fanout_id") or payload.get("fanout_id") or "")
        child_id = str(
            payload.get("child_id")
            or payload.get("child_run")
            or (child or {}).get("child_id")
            or ("synth" if event.type == "fanout.synth.completed" else "")
        )
        if self._fanout_stale_completion_recorded(
            fanout_id=fanout_id,
            child_id=child_id,
            source_event_id=event.id,
        ):
            return
        task_id = str(
            event.task_id
            or payload.get("task_id")
            or (child or {}).get("task_id")
            or ""
        )
        stale_payload = {
            "fanout_id": fanout_id,
            "trace_id": str(manifest.get("trace_id") or payload.get("trace_id") or ""),
            "stage_id": str(manifest.get("stage_id") or payload.get("stage_id") or ""),
            "child_id": child_id,
            "task_id": task_id,
            "role_instance": str(
                payload.get("role_instance")
                or (child or {}).get("role_instance")
                or event.actor
                or ""
            ),
            "run_id": str(payload.get("run_id") or (child or {}).get("run_id") or ""),
            "result_event_id": event.id,
            "source_event_type": event.type,
            "reason": reason,
            "superseded_by": superseded_by,
        }
        self.event_writer.append(ZfEvent(
            type="fanout.child.stale_completion",
            actor="zf-cli",
            task_id=task_id or None,
            payload=stale_payload,
            causation_id=event.id,
            correlation_id=event.correlation_id or manifest.get("trace_id", ""),
        ))
    @staticmethod
    def _writer_fanout_aggregate_recoverable(manifest: dict) -> bool:
        aggregate = manifest.get("aggregate") if isinstance(manifest.get("aggregate"), dict) else {}
        status = str(aggregate.get("status") or "")
        reason = str(aggregate.get("reason") or "")
        if status not in {"failed", "timed_out"}:
            return False
        if status != "timed_out" and reason != "timeout":
            return False
        children = [
            child for child in manifest.get("children", []) or []
            if isinstance(child, dict)
        ]
        return bool(children) and all(
            str(child.get("status") or "") == "completed"
            for child in children
        )
    def _maybe_update_writer_fanout(self, event: ZfEvent) -> None:
        if event.type.startswith("fanout."):
            return
        if (
            event.type in {"dev.build.done", "dev.failed", "dev.blocked"}
            and self._writer_source_event_already_terminal(event.id)
        ):
            return
        from zf.runtime.writer_fanout_admission import writer_completion_admission

        payload = self._fanout_result_payload(event)
        fanout_id = str(payload.get("fanout_id") or "")
        child_id = str(payload.get("child_id") or payload.get("child_run") or "")
        if not fanout_id or not child_id:
            return
        if self._writer_fanout_child_result_recorded(
            fanout_id=fanout_id,
            child_id=child_id,
            source_event_id=event.id,
        ):
            return
        manifest = self._fanout_manifest(fanout_id)
        if not manifest or manifest.get("topology") != "fanout_writer_scoped":
            return
        child = self._fanout_child(manifest, child_id)
        from zf.runtime.terminal_events import successful_terminal_before_event

        terminal = successful_terminal_before_event(
            self.event_log.read_all(),
            event,
        )
        if terminal is not None:
            self._emit_fanout_identity_stale_completion(
                event=event,
                payload=payload,
                manifest=manifest,
                child=child,
                reason="run_terminal",
                superseded_by=terminal.id,
            )
            return
        adopted_dispatch_id = str(
            payload.get("_writer_fanout_adopted_dispatch_id") or ""
        )
        adopted = bool(adopted_dispatch_id)
        if adopted and event.type not in {"task.ref.updated", "task.ref.rejected"}:
            self._emit_writer_fanout_completion_adopted(
                event=event,
                manifest=manifest,
                child=child or {},
                adopted_from=f"task_dispatch:{adopted_dispatch_id}",
                reason=str(
                    payload.get("_writer_fanout_adoption_reason")
                    or "active_task_attempt_fence"
                ),
            )
        # BF-1 修补(断点续跑 00:57 实弹):收编只对携带结果的事件开闸。
        # heartbeat 等观察型事件也带旧 fanout 身份,曾触发假审计且无
        # 去重(RF-7A 刷屏家族);非结果事件走原 stale 路径(有去重)。
        adoptable_event = event.type in {
            "dev.build.done",
            "task.ref.updated",
            "task.ref.rejected",
        }
        stale_reason, superseded_by = self._fanout_identity_stale_reason(fanout_id)
        if stale_reason:
            # BF-1(r6.1 断点复盘):fanout 换代期间到达的真交付先尝试
            # 跨代收编——当前代同 task 的 child 仍未终局时这份工作仍被
            # 等待,丢弃即死锁;内容好坏归 review 判。
            from zf.runtime.fanout_completion_adoption import (
                find_writer_adoption_target,
            )

            target = None if not adoptable_event else find_writer_adoption_target(
                fanout_id=fanout_id,
                task_id=str(
                    event.task_id
                    or payload.get("task_id")
                    or (child or {}).get("task_id")
                    or ""
                ),
                current_sibling_lookup=self._fanout_identity_current_sibling,
                manifest_loader=self._fanout_manifest,
                source_manifest=manifest,
                source_child=child or {},
            )
            if target is None:
                self._emit_fanout_identity_stale_completion(
                    event=event,
                    payload=payload,
                    manifest=manifest,
                    child=child,
                    reason=stale_reason,
                    superseded_by=superseded_by,
                )
                return
            self._emit_writer_fanout_completion_adopted(
                event=event,
                manifest=target.manifest,
                child=target.child,
                adopted_from=fanout_id,
                reason=stale_reason,
            )
            manifest = target.manifest
            child = target.child
            fanout_id = str(manifest.get("fanout_id") or fanout_id)
            child_id = str(child.get("child_id") or child_id)
            payload = {**payload, "fanout_id": fanout_id, "child_id": child_id}
            adopted = True
        recovery = False
        if child and child.get("status") in {"completed", "failed"}:
            if child.get("status") == "failed" and event.type in {
                "dev.build.done",
                "task.ref.updated",
            }:
                recovery = True
            elif self._writer_fanout_completed_child_repair_allowed(
                event=event,
                payload=payload,
                child=child,
            ):
                recovery = True
            else:
                target = self._writer_fanout_recovery_target(
                    event=event,
                    payload=payload,
                    current_fanout_id=fanout_id,
                )
                if target is None:
                    return
                manifest, child = target
                fanout_id = str(manifest.get("fanout_id") or fanout_id)
                child_id = str(child.get("child_id") or child_id)
                recovery = True
        run_id = str(payload.get("run_id") or (child or {}).get("run_id") or "")
        expected_run_id = str((child or {}).get("run_id") or "")
        actual_run_id = str(payload.get("run_id") or "")
        stale_run = bool(
            child
            and expected_run_id
            and actual_run_id
            and actual_run_id != expected_run_id
        )
        if stale_run:
            from zf.runtime.fanout_completion_adoption import (
                TERMINAL_CHILD_STATUSES,
            )

            child_status = str((child or {}).get("status") or "")
            if adopted or (
                adoptable_event and child_status not in TERMINAL_CHILD_STATUSES
            ):
                # BF-1:run_id 轮换(restart/重派)但该 child 仍未终局
                # ——工作仍被等待,按当前代身份收编而非丢弃。
                if not adopted:
                    self._emit_writer_fanout_completion_adopted(
                        event=event,
                        manifest=manifest,
                        child=child or {},
                        adopted_from=fanout_id,
                        reason="run_id_rotated",
                    )
                run_id = expected_run_id
            else:
                self._emit_writer_fanout_stale_completion(
                    event=event,
                    payload=payload,
                    manifest=manifest,
                    child=child,
                    expected_run_id=expected_run_id,
                )
                if not recovery:
                    return
                run_id = expected_run_id
        base_payload = {
            "fanout_id": fanout_id,
            "trace_id": manifest.get("trace_id", ""),
            "stage_id": manifest.get("stage_id", ""),
            "child_id": child_id,
            "run_id": run_id,
            "role_instance": str(payload.get("role_instance") or (child or {}).get("role_instance") or event.actor or ""),
            "task_id": event.task_id or str(payload.get("task_id") or (child or {}).get("task_id") or ""),
            "scope": str(payload.get("scope") or (child or {}).get("scope") or ""),
            "workdir": str(payload.get("workdir") or (child or {}).get("workdir") or ""),
            "source_branch": str(payload.get("source_branch") or (child or {}).get("source_branch") or ""),
            "pdd_id": str(payload.get("pdd_id") or manifest.get("pdd_id") or ""),
            "feature_id": str(payload.get("feature_id") or payload.get("pdd_id") or manifest.get("pdd_id") or ""),
            "task_map_ref": str(payload.get("task_map_ref") or (child or {}).get("task_map_ref") or manifest.get("task_map_ref") or ""),
            "source_index_ref": str(payload.get("source_index_ref") or (child or {}).get("source_index_ref") or manifest.get("source_index_ref") or ""),
        }
        child_payload = (
            (child or {}).get("payload")
            if isinstance((child or {}).get("payload"), dict)
            else {}
        )
        for key in _CONTRACT_HANDOFF_KEYS:
            value = payload.get(key) or (child or {}).get(key) or child_payload.get(key)
            if value not in (None, ""):
                base_payload[key] = value
        for key in _FANOUT_AFFINITY_METADATA_KEYS:
            value = self._fanout_payload_metadata_value(payload, child, key)
            if value:
                base_payload[key] = value
        if event.type in {"dev.build.done", "task.ref.updated"}:
            try:
                self._ensure_writer_completion_contract_identity(
                    event=event,
                    payload=payload,
                    child=child or {},
                    base_payload=base_payload,
                )
            except TaskContractSnapshotError as exc:
                self._record_writer_fanout_child_failed(
                    fanout_id=fanout_id,
                    base_payload=base_payload,
                    failure_payload={
                        "reason": f"writer contract handoff snapshot failed: {exc}",
                        "failure_class": "verifier_contract_failure",
                    },
                    event=event,
                    manifest=manifest,
                )
                return
        if event.type in {"dev.blocked", "dev.failed", "task.ref.rejected"} or str(payload.get("status") or "") in {
            "failed",
            "blocked",
        }:
            failure_payload = {
                "reason": str(
                    payload.get("reason")
                    or payload.get("failure_reason")
                    or payload.get("summary")
                    or event.type
                ),
            }
            if isinstance(payload.get("report"), dict):
                failure_payload["report"] = payload["report"]
            if isinstance(payload.get("findings"), list):
                failure_payload["findings"] = payload["findings"]
            for key in (
                "verification_command",
                "evidence_refs",
                "files_or_scope",
                "expected_behavior",
                "failure_reason",
                "failure_classification",
                "blocked_rework_findings",
                "blockers",
                "protected_paths_required_for_fix",
                "allowed_paths",
                "checks",
                "artifact_integrity",
                "task_map_sha256",
                "summary",
            ):
                if payload.get(key) not in (None, ""):
                    failure_payload[key] = payload.get(key)
            self._record_writer_fanout_child_failed(
                fanout_id=fanout_id,
                base_payload=base_payload,
                failure_payload=failure_payload,
                event=event,
                manifest=manifest,
            )
            return
        if event.type not in {"dev.build.done", "task.ref.updated"}:
            return
        completion_gate = writer_completion_admission(
            task_store=self.task_store,
            task_id=base_payload["task_id"],
            task_map_ref=base_payload["task_map_ref"],
        )
        skip_terminal_admission_for_repair = (
            recovery
            and str((child or {}).get("status") or "") == "completed"
            and completion_gate.reason == "terminal_task"
        )
        skip_terminal_admission_for_adopted_attempt = (
            adopted and completion_gate.reason == "terminal_task"
        )
        if not completion_gate.passed and not (
            skip_terminal_admission_for_repair
            or skip_terminal_admission_for_adopted_attempt
        ):
            self._record_writer_fanout_child_failed(
                fanout_id=fanout_id,
                base_payload=base_payload,
                failure_payload=completion_gate.failure_payload(),
                event=event,
                manifest=manifest,
            )
            return
        protected_write = self._writer_protected_write(payload)
        if protected_write:
            self._record_writer_fanout_child_failed(
                fanout_id=fanout_id,
                base_payload=base_payload,
                failure_payload={
                    "reason": f"writer touched protected path {protected_write!r}",
                },
                event=event,
                manifest=manifest,
            )
            return
        task_ref = self._writer_current_task_ref(
            self._task_ref_entry(base_payload["task_id"]), event=event, payload=payload,
        )
        if not task_ref:
            # Task-ref creation is a separate mechanical projection and may
            # settle a few seconds after this event when recovery/watcher
            # processes overlap. Keep the writer child pending until the
            # canonical task.ref.updated/rejected signal arrives; treating
            # this interval as a semantic failure wastes a rework attempt.
            if event.type == "dev.build.done":
                return
            self._record_writer_fanout_child_failed(
                fanout_id=fanout_id,
                base_payload=base_payload,
                failure_payload={
                    "reason": f"missing task ref in {event.type}",
                },
                event=event,
                manifest=manifest,
            )
            return
        from zf.runtime.call_result_admission import result_protocol_mode
        from zf.runtime.call_result_runtime import admit_runtime_call_result

        call_mode = result_protocol_mode(
            self.config,
            {**child_payload, **base_payload, **payload},
        )
        call_outcome = admit_runtime_call_result(
            self,
            event,
            merged_payload={
                **child_payload,
                **base_payload,
                **payload,
                "task_ref": str(task_ref.get("task_ref") or ""),
                "target_commit": str(
                    payload.get("target_commit")
                    or payload.get("source_commit")
                    or task_ref.get("source_commit")
                    or ""
                ),
                "result_protocol_mode": call_mode,
            },
            mode=call_mode,
        )
        if call_outcome.repair_requested:
            return
        if call_outcome.status == "superseded":
            return
        if call_outcome.admitted:
            base_payload.update({
                "operation_id": call_outcome.operation_id,
                "request_hash": call_outcome.request_hash,
                "result_protocol_mode": call_outcome.mode,
                "admitted_call_result_ref": dict(call_outcome.envelope_ref or {}),
                "control_result_ref": dict(call_outcome.control_result_ref or {}),
            })
        result_event_id = event.id
        if event.type == "task.ref.updated":
            result_event_id = str(
                payload.get("trigger_event_id")
                or event.causation_id
                or event.id
            )
        recovery_payload = {}
        if recovery:
            recovered_status = str((child or {}).get("status") or "")
            recovery_reason = "late_dev_build_done_after_child_failed"
            if recovered_status == "completed":
                recovery_reason = "late_dev_build_done_replaces_completed_child"
            if event.type == "task.ref.updated":
                recovery_reason = "late_task_ref_updated_after_child_failed"
                if recovered_status == "completed":
                    recovery_reason = "late_task_ref_updated_replaces_completed_child"
            recovery_payload = {
                "recovered_from_status": recovered_status,
                "recovery_reason": recovery_reason,
                "recovery_source_event_id": event.id,
            }
            if actual_run_id and actual_run_id != run_id:
                recovery_payload["recovered_from_run_id"] = actual_run_id
        completed_event = self.event_writer.append(ZfEvent(
            type="fanout.child.completed",
            actor="zf-cli",
            payload={
                **base_payload,
                **recovery_payload,
                "status": "completed",
                "result_event_id": result_event_id,
                "source_event_type": event.type,
                "task_ref": str(task_ref.get("task_ref") or ""),
                "source_commit": str(task_ref.get("source_commit") or ""),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id or manifest.get("trace_id", ""),
        ))
        self._emit_lane_stage_result(
            event_type=LANE_STAGE_HANDOFF_SUCCESS_EVENT,
            status="completed",
            source_event=completed_event,
            manifest=manifest,
            child_payload=dict(completed_event.payload),
        )
        self._release_fanout_worker_if_terminal(
            role_instance=base_payload["role_instance"],
            fanout_id=fanout_id,
            child_id=base_payload["child_id"],
            run_id=base_payload["run_id"], task_id=str(base_payload.get("task_id") or ""),
        )
        if base_payload.get("assignment_strategy") == "affinity_stage_slots":
            self._release_affinity_writer_slot_and_dispatch_next(
                fanout_id=fanout_id,
                completed_payload=base_payload,
                causation_id=completed_event.id,
            )
        self._evaluate_writer_fanout(fanout_id, force_retry=recovery)

    def _writer_fanout_completed_child_repair_allowed(
        self,
        *,
        event: ZfEvent,
        payload: dict,
        child: dict,
    ) -> bool:
        """Allow a late task-ref repair to replace a completed writer child.

        A repair dispatch may emit a fresh ``dev.build.done`` without fanout
        identity after TaskRefManager has rejected the original completion.
        That is valid only when the task ref already points to this exact
        completion event and source commit; otherwise a duplicate completion
        for an already completed child must remain ignored.
        """
        if event.type != "dev.build.done":
            return False
        task_id = str(event.task_id or payload.get("task_id") or "").strip()
        if not task_id or task_id != str(child.get("task_id") or ""):
            return False
        source_commit = str(payload.get("source_commit") or "").strip()
        if not source_commit:
            return False
        if source_commit == str(child.get("source_commit") or "").strip():
            return False
        task_ref = self._task_ref_entry(task_id)
        if not isinstance(task_ref, dict):
            return False
        if str(task_ref.get("trigger_event_id") or "") != event.id:
            return False
        if str(task_ref.get("source_commit") or "") != source_commit:
            return False
        return True

    def _note_prompt_sent(self, instance_id: str, run_key: str) -> None:
        """ZF-E2E-PRDCTL-P2-7-3:记录 (run_key, ts) 供简报吞没守卫查询。"""
        if not hasattr(self, "_last_prompt_sent_at"):
            self._last_prompt_sent_at = {}
        self._last_prompt_sent_at[instance_id] = (run_key, time.monotonic())

    def _task_completed_by_events(self, task_id: str) -> bool:
        """Event-evidence completion check for manifest-status staleness
        (ZF-E2E-PRDCTL-P2-7-5; index-backed, cheap for a handful of tasks)."""
        try:
            for progressed in self.event_log.events_for_task(task_id):
                if progressed.type in {
                    "dev.build.done",
                    "fanout.child.completed",
                    "lane.stage.completed",
                    "task.done",
                }:
                    return True
        except Exception:
            return False
        return False

    def _record_writer_fanout_child_failed(
        self,
        *,
        fanout_id: str,
        base_payload: dict,
        failure_payload: dict,
        event: ZfEvent,
        manifest: dict,
    ) -> None:
        failed_event = self.event_writer.append(ZfEvent(
            type="fanout.child.failed",
            actor="zf-cli",
            payload={
                **base_payload,
                **failure_payload,
            },
            causation_id=event.id,
            correlation_id=event.correlation_id or manifest.get("trace_id", ""),
        ))
        self._emit_lane_stage_result(
            event_type=LANE_STAGE_HANDOFF_FAILURE_EVENT,
            status="failed",
            source_event=failed_event,
            manifest=manifest,
            child_payload=dict(failed_event.payload),
            extra_payload=failure_payload,
        )
        self._release_fanout_worker_if_terminal(
            role_instance=base_payload["role_instance"],
            fanout_id=fanout_id,
            child_id=base_payload["child_id"],
            run_id=base_payload["run_id"], task_id=str(base_payload.get("task_id") or ""),
        )
        # A terminally-failed affinity child must free its lane exactly like
        # a completed one; otherwise the slot stays pinned to a dead child
        # and queued overflow children starve until the stage timeout
        # (2026-06-10 review P1-9).
        if base_payload.get("assignment_strategy") == "affinity_stage_slots":
            self._release_affinity_writer_slot_and_dispatch_next(
                fanout_id=fanout_id,
                completed_payload=base_payload,
                causation_id=failed_event.id,
            )
        self._evaluate_writer_fanout(fanout_id)

    def _writer_fanout_child_result_recorded(
        self,
        *,
        fanout_id: str,
        child_id: str,
        source_event_id: str,
    ) -> bool:
        if not fanout_id or not child_id or not source_event_id:
            return False
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        for event in reversed(events):
            if event.type not in {"fanout.child.completed", "fanout.child.failed"}:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("fanout_id") or "") != fanout_id:
                continue
            if str(payload.get("child_id") or "") != child_id:
                continue
            if (
                str(payload.get("result_event_id") or "") == source_event_id
                or str(event.causation_id or "") == source_event_id
            ):
                return True
        return False

    def _fanout_failure_findings(
        self,
        manifest: dict,
        *,
        extra_payloads: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Collect structured findings from failed fanout children.

        Rework/candidate recovery should not have to infer reviewer feedback
        from terse ``reason`` strings. Children may report findings either at
        top level or under ``report.findings``; failed children without
        findings still contribute a compact reason finding.
        """
        child_payloads = {
            str(payload.get("child_id") or ""): payload
            for payload in self._fanout_child_payloads(manifest)
            if isinstance(payload, dict)
        }
        payloads: list[dict] = []
        for child in manifest.get("children", []) or []:
            if not isinstance(child, dict):
                continue
            if str(child.get("status") or "") != "failed":
                continue
            child_id = str(child.get("child_id") or "")
            enriched = dict(child)
            enriched.update(child_payloads.get(child_id, {}))
            payloads.append(enriched)
        payloads.extend(
            payload for payload in (extra_payloads or []) if isinstance(payload, dict)
        )
        try:
            events = self.event_log.read_all()
        except Exception:
            events = []
        fanout_id = str(manifest.get("fanout_id") or "")
        for event in events:
            if event.type != "fanout.child.failed":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("fanout_id") or "") != fanout_id:
                continue
            payloads.append(payload)

        findings: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for payload in payloads:
            for finding in self._findings_from_payload(payload):
                key = (
                    str(finding.get("child_id") or ""),
                    str(finding.get("task_id") or ""),
                    str(finding.get("message") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                findings.append(finding)
        return findings

    @staticmethod
    def _findings_from_payload(payload: dict) -> list[dict[str, Any]]:
        raw_findings = payload.get("findings")
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        if not isinstance(raw_findings, list):
            raw_findings = report.get("findings")
        if not isinstance(raw_findings, list):
            raw_findings = payload.get("blocked_rework_findings")
        if not isinstance(raw_findings, list):
            raw_findings = []

        out: list[dict[str, Any]] = []
        child_id = str(payload.get("child_id") or "")
        task_id = str(payload.get("task_id") or "")
        for index, raw in enumerate(raw_findings):
            if isinstance(raw, dict):
                message = str(
                    raw.get("message")
                    or raw.get("summary")
                    or raw.get("title")
                    or raw.get("reason")
                    or ""
                ).strip()
                if not message:
                    continue
                item = dict(raw)
                item.setdefault("finding_id", f"{child_id or task_id or 'child'}-{index + 1}")
                item.setdefault("severity", "high")
                item.setdefault("category", "verification")
                item.setdefault("child_id", child_id)
                item.setdefault("task_id", task_id)
                item["message"] = message
                out.append(item)
            else:
                message = str(raw).strip()
                if message:
                    out.append({
                        "finding_id": f"{child_id or task_id or 'child'}-{index + 1}",
                        "severity": "high",
                        "category": "verification",
                        "child_id": child_id,
                        "task_id": task_id,
                        "message": message,
                    })
        if out:
            return out
        reason = str(
            payload.get("reason")
            or payload.get("failure_reason")
            or payload.get("summary")
            or report.get("summary")
            or ""
        ).strip()
        if not reason:
            return []
        return [{
            "finding_id": f"{child_id or task_id or 'child'}-reason",
            "severity": "high",
            "category": "runtime_failure",
            "child_id": child_id,
            "task_id": task_id,
            "message": reason,
        }]

    @staticmethod
    def _candidate_failure_findings(
        candidate_payload: dict,
        *,
        status: str,
        failed_children: list[str],
    ) -> list[dict[str, Any]]:
        if status not in {"conflict", "quality_failed", "stale"} and not failed_children:
            return []
        findings: list[dict[str, Any]] = []
        for item in candidate_payload.get("stale_tasks") or []:
            if not isinstance(item, dict):
                continue
            findings.append({
                "finding_id": f"{item.get('task_id') or 'task'}-stale-task-ref",
                "severity": "high",
                "category": "stale_task_ref",
                "task_id": str(item.get("task_id") or ""),
                "message": (
                    "candidate task ref is stale: "
                    + str(item.get("reason") or "task_index_mismatch")
                ),
            })
        if status == "conflict":
            findings.append({
                "finding_id": "candidate-conflict",
                "severity": "high",
                "category": "candidate_integration",
                "message": str(candidate_payload.get("error") or "candidate conflict"),
                "files_or_scope": list(candidate_payload.get("conflict_files") or []),
            })
        if status == "quality_failed":
            from zf.runtime.candidate_rework import candidate_quality_failure_message
            quality = (
                candidate_payload.get("quality")
                if isinstance(candidate_payload.get("quality"), dict)
                else {}
            )
            findings.append({
                "finding_id": "candidate-quality-failed",
                "severity": "high",
                "category": "candidate_quality",
                "message": candidate_quality_failure_message(quality),
                "verification_command": "; ".join(
                    str(command)
                    for commands in (quality.get("failure_details") or {}).values()
                    for command in (commands if isinstance(commands, list) else [])
                ),
                "evidence_refs": list(quality.get("gates_failed") or []),
            })
        for child in failed_children:
            if child.startswith("candidate:"):
                findings.append({
                    "finding_id": "candidate-exception",
                    "severity": "high",
                    "category": "candidate_integration",
                    "message": child,
                })
        return findings
    def _evaluate_reader_fanout(self, fanout_id: str) -> None:
        manifest = self._fanout_manifest(fanout_id)
        if not manifest:
            return
        if self._cancel_candidate_superseded_replan_fanout(manifest):
            return
        stale_reason, _superseded_by = self._fanout_identity_stale_reason(fanout_id)
        if stale_reason:
            return
        aggregate = manifest.get("aggregate") if isinstance(manifest.get("aggregate"), dict) else {}
        if aggregate.get("status") in {"completed", "failed", "timed_out", "cancelled"}:
            return
        children = [
            child for child in manifest.get("children", [])
            if isinstance(child, dict)
        ]
        if not children:
            return
        statuses = [str(child.get("status") or "") for child in children]
        failed = any(status == "failed" for status in statuses)
        all_terminal = all(status in {"completed", "failed"} for status in statuses)
        config = manifest.get("aggregate_config") or {}
        mode = str(config.get("mode") or "wait_for_all")
        synth_role = str(config.get("synth_role") or "")
        if synth_role:
            if not all_terminal:
                return
            if self._fanout_synth_dispatched(manifest):
                return
            self._dispatch_fanout_synth(fanout_id, manifest, mode, synth_role)
            return
        if mode == "any_failed_fail" and failed:
            final_status = "failed"
        elif all_terminal:
            final_status = "failed" if failed else "completed"
        else:
            return
        trace_id = str(manifest.get("trace_id") or "")
        stage_id = str(manifest.get("stage_id") or "")
        success_event = str(config.get("success_event") or "")
        failure_event = str(config.get("failure_event") or "")
        artifact_payload: dict = {}
        if final_status == "completed":
            artifact_projection = self._project_refactor_success_artifacts(
                manifest=manifest,
                success_event=success_event,
            )
            if artifact_projection is not None:
                artifact_payload = artifact_projection.payload
                if not artifact_projection.ok:
                    final_status = "failed"
                elif success_event == "zaofu.refactor.plan.ready":
                    self._publish_refactor_plan_manifest(
                        manifest=manifest,
                        projection_payload=artifact_payload,
                        trace_id=trace_id,
                    )
            else:
                artifact_payload = self._generic_fanout_success_payload(
                    manifest=manifest,
                    success_event=success_event,
                )
        elif (
            success_event == "flow.discovery.completed"
            and failure_event == "flow.discovery.failed"
        ):
            # A discovery child may reject the candidate while still completing
            # its semantic job by returning bounded gap_tasks. Preserve that
            # result so the kernel can schedule the gaps instead of retrying the
            # same read-only scan against an unchanged candidate.
            artifact_payload = self._generic_fanout_success_payload(
                manifest=manifest,
                success_event=success_event,
            )
        artifact_payload = {
            **self._fanout_flow_identity_payload(manifest),
            **artifact_payload,
        }
        if final_status == "completed":
            contract_failure = self._success_payload_contract_failure(
                success_event,
                artifact_payload,
            )
            if contract_failure:
                final_status = "failed"
                artifact_payload = self._contract_failure_payload(
                    artifact_payload,
                    contract_failure,
                )
        if final_status == "completed":
            criteria_failure = evaluate_fanout_stage_success_criteria_for_orchestrator(
                self, manifest=manifest, artifact_payload=artifact_payload)
            if criteria_failure:
                final_status = "failed"
                artifact_payload = criteria_failure.artifact_payload
        self.event_writer.append(ZfEvent(
            type="fanout.aggregate.started",
            actor="zf-cli",
            payload={"fanout_id": fanout_id, "trace_id": trace_id, "stage_id": stage_id, "mode": mode},
            correlation_id=trace_id,
        ))
        publish_event = failure_event if final_status == "failed" else success_event
        # B-FIX-07 (R32 stall): failure path 的 aggregate.completed 与 publish_event
        # (review.rejected 等)必带 pdd_id/feature_id —— 否则 candidate_rework
        # 从 publish_event 推不出 pdd,rework 无法路由 → stall。从 manifest 回填。
        pdd_id = str(manifest.get("pdd_id") or "")
        feature_id = str(manifest.get("feature_id") or "")
        failure_findings = (
            self._fanout_failure_findings(manifest)
            if final_status == "failed"
            else []
        )
        aggregate_payload = {
            "fanout_id": fanout_id,
            "trace_id": trace_id,
            "stage_id": stage_id,
            "pdd_id": pdd_id,
            "feature_id": feature_id,
            "status": final_status,
            "success_event": success_event if final_status == "completed" else "",
            "failure_event": failure_event if final_status == "failed" else "",
            **artifact_payload,
        }
        if final_status == "failed":
            aggregate_payload.setdefault("findings", failure_findings)
            aggregate_payload.setdefault(
                "failed_children",
                [
                    str(child.get("child_id") or "")
                    for child in children
                    if str(child.get("status") or "") == "failed"
                ],
            )
            derived_failure_kind = aggregate_failure_kind([
                child for child in children
                if str(child.get("status") or "") == "failed"
            ])
            if derived_failure_kind:
                aggregate_payload.setdefault("failure_kind", derived_failure_kind)
        aggregate_event = self.event_writer.append(ZfEvent(
            type="fanout.aggregate.completed",
            actor="zf-cli",
            payload=aggregate_payload,
            correlation_id=trace_id,
        ))
        self._consume_durable_fanout_aggregate_result(aggregate_event)
        emit_fanout_channel_state_update(
            writer=self.event_writer,
            terminal_event=aggregate_event,
            manifest={
                **manifest,
                "aggregate": aggregate_event.payload,
                "artifact_refs": artifact_payload.get(
                    "artifact_refs",
                    manifest.get("artifact_refs", []),
                ),
            },
        )
        if publish_event:
            if publish_event in {
                LANE_STAGE_HANDOFF_SUCCESS_EVENT,
                LANE_STAGE_HANDOFF_FAILURE_EVENT,
            }:
                terminal_children = [
                    child for child in children
                    if str(child.get("status") or "") in {"completed", "failed"}
                ]
                if final_status == "failed":
                    failed_children = [
                        child for child in terminal_children
                        if str(child.get("status") or "") == "failed"
                    ]
                    if failed_children:
                        terminal_children = failed_children
                for child in terminal_children:
                    child_payload = {
                        **child,
                        "fanout_id": fanout_id,
                        "trace_id": trace_id,
                        "stage_id": stage_id,
                        "pdd_id": pdd_id,
                        "feature_id": feature_id,
                        "result_event_id": aggregate_event.id,
                    }
                    extra_payload = dict(artifact_payload)
                    if final_status == "failed":
                        extra_payload.setdefault("findings", failure_findings)
                        extra_payload.setdefault(
                            "failed_children",
                            aggregate_payload.get("failed_children", []),
                        )
                    self._emit_lane_stage_result(
                        event_type=publish_event,
                        status=final_status,
                        source_event=aggregate_event,
                        manifest=manifest,
                        child_payload=child_payload,
                        extra_payload=extra_payload,
                    )
                return
            publish_payload = {
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "pdd_id": pdd_id,
                "feature_id": feature_id,
                "status": final_status,
                "target_ref": manifest.get("target_ref", ""),
                "child_count": len(children),
                **artifact_payload,
            }
            if final_status == "failed":
                publish_payload.setdefault("findings", failure_findings)
                publish_payload.setdefault(
                    "failed_children",
                    aggregate_payload.get("failed_children", []),
                )
                # R5: judge.failed reached the operator with an empty reason.
                # The aggregate knows which children failed and what they
                # reported — say so instead of shipping "".
                if not str(publish_payload.get("reason") or "").strip():
                    failed_children = publish_payload.get("failed_children") or []
                    first_message = ""
                    if failure_findings and isinstance(failure_findings[0], dict):
                        first_message = str(
                            failure_findings[0].get("message") or "",
                        )[:160]
                    publish_payload["reason"] = (
                        f"{len(failed_children)}/{len(children)} children failed"
                        + (f": {first_message}" if first_message else "")
                    )
                # publish_event is config-driven (the stage's failure_event may
                # name any contract event, e.g. lane.stage.failed). Forge the
                # contract's required keys present — value may be empty, same
                # rule as the lane-pipeline emitter — else a blocking
                # discriminator replaces the failure with discriminator.failed
                # and rework never sees it (R6: verify-timeout wedged 25min).
                from zf.core.config.schema_profiles import union_required_keys

                target_ref = str(publish_payload.get("target_ref") or "")
                for key in union_required_keys(publish_event):
                    if key == "task_id" and target_ref.startswith("task/"):
                        publish_payload.setdefault(
                            "task_id", target_ref.split("/", 1)[1],
                        )
                    else:
                        publish_payload.setdefault(key, "")
            published = self.event_writer.append(ZfEvent(
                type=publish_event,
                actor="zf-cli",
                payload=publish_payload,
                correlation_id=trace_id,
            ))
            if final_status == "completed" and publish_event == "verify.passed":
                self._bridge_verify_passed_to_parity_scan(published)
            elif (
                final_status == "completed"
                and is_module_parity_scan_completed_event(publish_event)
            ):
                self._bridge_module_parity_scan_completed(published)
            elif (
                final_status == "completed"
                and publish_event in {
                    "gap_plan.ready",
                    "goal.gap_plan.ready",
                    "flow.gap_plan.ready",
                }
            ):
                self._bridge_gap_plan_ready_to_task_map(published)
        # P0-2 (2026-06-19 e2e): plan.ready -> task_map.ready is a deterministic
        # driver hop. The reduced refactor-flow profile lists task_map.ready as
        # an external_trigger, so on a FRESH plan (not a candidate rework, which
        # the self-heal sweep already re-emits) nothing converted a gate-passed
        # plan.ready into the first task_map.ready — the orchestrator livelocked
        # re-waking on plan.ready with no forward transition. Bridge it here on
        # the same gate-passed projection, then start the writer (impl) fanout.
        if (
            final_status == "completed"
            and success_event == "zaofu.refactor.plan.ready"
            and not (manifest.get("rework_of") or manifest.get("rework_attempt"))
            and str(artifact_payload.get("task_map_ref") or "").strip()
        ):
            self._bridge_refactor_plan_ready_to_task_map(
                manifest=manifest,
                projection_payload=artifact_payload,
                trace_id=trace_id,
            )

    def _evaluate_writer_fanout(
        self,
        fanout_id: str,
        *,
        force_retry: bool = False,
    ) -> None:
        manifest = self._fanout_manifest(fanout_id)
        if not manifest or manifest.get("topology") != "fanout_writer_scoped":
            return
        stale_reason, _superseded_by = self._fanout_identity_stale_reason(fanout_id)
        if stale_reason:
            return
        aggregate = manifest.get("aggregate") if isinstance(manifest.get("aggregate"), dict) else {}
        aggregate_status = str(aggregate.get("status") or "")
        recovered_aggregate = (
            force_retry or self._writer_fanout_aggregate_recoverable(manifest)
        )
        if (
            aggregate_status in {"completed", "failed", "timed_out", "cancelled"}
            and not recovered_aggregate
        ):
            return
        children = [
            child for child in manifest.get("children", [])
            if isinstance(child, dict)
        ]
        if not children:
            return
        statuses = [str(child.get("status") or "") for child in children]
        if not all(status in {"completed", "failed"} for status in statuses):
            return
        config = manifest.get("aggregate_config") or {}
        if str(config.get("mode") or "") != "candidate_integration":
            return
        trace_id = str(manifest.get("trace_id") or "")
        stage_id = str(manifest.get("stage_id") or "")
        pdd_id = str(manifest.get("pdd_id") or "default")
        feature_id = str(manifest.get("feature_id") or pdd_id)
        task_map_ref = str(manifest.get("task_map_ref") or "")
        source_index_ref = str(manifest.get("source_index_ref") or "")
        completed_task_ids = [
            str(child.get("task_id") or "")
            for child in children
            if child.get("status") == "completed" and child.get("task_id")
        ]
        failed_children = [
            str(child.get("child_id") or "")
            for child in children
            if child.get("status") == "failed"
        ]
        self.event_writer.append(ZfEvent(
            type="fanout.aggregate.started",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "mode": "candidate_integration",
            },
            correlation_id=trace_id,
        ))
        result = None
        if completed_task_ids:
            try:
                from zf.runtime.candidates import CandidateRebuilder

                result = CandidateRebuilder(
                    state_dir=self.state_dir,
                    project_root=self.project_root,
                    config=self.config,
                    event_log=self.event_log,
                ).rebuild(
                    pdd_id,
                    event_writer=self.event_writer,
                    task_ids=completed_task_ids,
                )
            except Exception as exc:
                result = None
                failed_children.append(f"candidate: {exc}")
        final_status = (
            "completed"
            if result is not None
            and result.status == "updated"
            and not failed_children
            else "failed"
        )
        success_event = str(config.get("success_event") or "")
        failure_event = str(config.get("failure_event") or "")
        publish_event = success_event if final_status == "completed" else failure_event
        candidate_payload = result.payload if result is not None else {}
        failure_findings = (
            [
                *self._fanout_failure_findings(manifest),
                *self._candidate_failure_findings(
                    candidate_payload,
                    status=str(candidate_payload.get("status") or ""),
                    failed_children=failed_children,
                ),
            ]
            if final_status == "failed"
            else []
        )
        candidate_contract_payload = self._candidate_ready_contract_payload(
            candidate_payload=candidate_payload,
            pdd_id=pdd_id,
            feature_id=feature_id,
            task_map_ref=task_map_ref,
            source_index_ref=source_index_ref,
            completed_task_ids=completed_task_ids,
        )
        candidate_contract_payload = {
            **self._fanout_flow_identity_payload(manifest),
            **candidate_contract_payload,
        }
        from zf.runtime.candidate_integration import (
            candidate_aggregate_event_payload,
            candidate_terminal_event_payload,
            prepare_candidate_integration_attempt,
        )

        if prepare_candidate_integration_attempt(
            candidate_contract_payload=candidate_contract_payload,
            candidate_payload=candidate_payload, manifest=manifest,
            events=self.event_log.read_all(), event_writer=self.event_writer,
            final_status=final_status, publish_event=publish_event,
            failed_children=failed_children, fanout_id=fanout_id, trace_id=trace_id,
        ):
            return
        aggregate_event = self.event_writer.append(ZfEvent(
            type="fanout.aggregate.completed",
            actor="zf-cli",
            payload=candidate_aggregate_event_payload(
                contract_payload=candidate_contract_payload,
                candidate_payload=candidate_payload, fanout_id=fanout_id,
                trace_id=trace_id, stage_id=stage_id, status=final_status,
                success_event=success_event, failure_event=failure_event,
                pdd_id=pdd_id, feature_id=feature_id, task_map_ref=task_map_ref,
                source_index_ref=source_index_ref,
                completed_task_ids=completed_task_ids, failed_children=failed_children,
                findings=failure_findings,
                recovered_status=aggregate_status if recovered_aggregate else "",
                recovered_reason=("retry_requested" if force_retry else
                                  str(aggregate.get("reason") or "")
                                  if recovered_aggregate else ""),
            ),
            correlation_id=trace_id,
        ))
        self._consume_durable_fanout_aggregate_result(aggregate_event)
        emit_fanout_channel_state_update(
            writer=self.event_writer,
            terminal_event=aggregate_event,
            manifest={**manifest, "aggregate": aggregate_event.payload},
        )
        if publish_event:
            self.event_writer.append(ZfEvent(
                type=publish_event,
                actor="zf-cli",
                payload=candidate_terminal_event_payload(
                    contract_payload=candidate_contract_payload,
                    candidate_payload=candidate_payload, fanout_id=fanout_id,
                    trace_id=trace_id, stage_id=stage_id, status=final_status,
                    failed_children=failed_children, findings=failure_findings,
                ),
                causation_id=aggregate_event.id,
                correlation_id=trace_id,
            ))
    def _check_fanout_timeouts(self) -> None:
        root = self.state_dir / "fanouts"
        if not root.exists():
            return
        try:
            events = self.event_log.read_all()
        except Exception:
            return
        for manifest_path in root.glob("*/manifest.json"):
            fanout_id = manifest_path.parent.name
            manifest = self._fanout_manifest(fanout_id)
            if not manifest:
                continue
            if (
                manifest.get("topology") == "fanout_reader"
                and self._cancel_candidate_superseded_replan_fanout(
                    manifest,
                    events=events,
                )
            ):
                continue
            stale_reason, _superseded_by = self._fanout_identity_stale_reason(
                fanout_id,
            )
            if stale_reason:
                continue
            aggregate = manifest.get("aggregate") if isinstance(manifest.get("aggregate"), dict) else {}
            terminal_statuses = {"completed", "failed", "timed_out", "cancelled"}
            manifest_status = str(manifest.get("status") or "")
            aggregate_status = str(aggregate.get("status") or "")
            if (
                manifest_status in terminal_statuses
                or aggregate_status in terminal_statuses
            ):
                if (
                    manifest_status == "timed_out"
                    or aggregate_status == "timed_out"
                ):
                    self._backfill_timed_out_fanout_failure(manifest, events)
                continue
            timeout_seconds = self._fanout_timeout_seconds(str(manifest.get("stage_id") or ""))
            if timeout_seconds <= 0:
                continue
            dispatches = [
                event for event in events
                if event.type == "fanout.child.dispatched"
                and isinstance(event.payload, dict)
                and event.payload.get("fanout_id") == fanout_id
            ]
            # Baseline for assigned children that never got a dispatch, and
            # for queue-only scheduler stalls handled by the queue policy.
            fanout_started = [
                event for event in events
                if event.type == "fanout.started"
                and isinstance(event.payload, dict)
                and event.payload.get("fanout_id") == fanout_id
            ]
            fanout_epoch = (
                self._event_epoch(fanout_started[0]) if fanout_started else None
            )
            from zf.runtime.fanout_timeout_policy import close_expired_queued_wait

            now = self._now()
            eligible_queued_child_ids: set[str] | None = None
            stage = self._fanout_stage_by_id(str(manifest.get("stage_id") or ""))
            if (
                manifest.get("topology") == "fanout_writer_scoped"
                and stage is not None
                and self._fanout_assignment_strategy(stage)
                == "affinity_stage_slots"
            ):
                stage_slot = str(
                    getattr(getattr(stage, "assignment", None), "stage_slot", "")
                    or ""
                )
                completed_task_ids: set[str] = set()
                if per_lane_flow_match(self.config, stage.id, stage_slot) is None:
                    completed_task_ids = {
                        str(child.get("task_id") or "")
                        for child in manifest.get("children", []) or []
                        if isinstance(child, dict)
                        and str(child.get("status") or "") == "completed"
                        and str(child.get("task_id") or "")
                    }
                eligible_queued_child_ids = {
                    str(child.get("child_id") or "")
                    for child in manifest.get("children", []) or []
                    if isinstance(child, dict)
                    and str(child.get("status") or "") == "queued"
                    and _writer_task_dependencies_satisfied(
                        self.task_store,
                        child,
                        completed_task_ids=completed_task_ids,
                    )
                    and str(child.get("child_id") or "")
                }
            if close_expired_queued_wait(
                self.event_writer,
                manifest=manifest,
                fanout_epoch=fanout_epoch,
                now=now,
                timeout_seconds=timeout_seconds,
                eligible_queued_child_ids=eligible_queued_child_ids,
            ):
                continue
            timed_out: list[dict] = []
            timed_out_reason: dict[str, str] = {}
            for child in manifest.get("children", []) or []:
                if not isinstance(child, dict):
                    continue
                child_id = str(child.get("child_id") or "")
                if not child_id or child.get("status") in {"completed", "failed"}:
                    continue
                if str(child.get("status") or "") == "queued":
                    continue
                child_dispatches = [
                    event for event in dispatches
                    if event.payload.get("child_id") == child_id
                ]
                max_retries = int(
                    (manifest.get("aggregate_config") or {}).get("max_retries") or 0
                )
                if child_dispatches:
                    last_dispatch = child_dispatches[-1]
                    dispatch_epoch = self._event_epoch(last_dispatch)
                    # Two independent staleness signals: the stage budget (time
                    # since dispatch) and per-child liveness (time since the
                    # child's last sign of life). A reader that engages then
                    # freezes — e.g. on a transient backend API error — is caught
                    # by the tighter idle threshold instead of waiting out the
                    # full stage timeout (R15: a frozen scan reader stranded the
                    # fanout ~26min before the 30min budget would have fired).
                    stale_reason = ""
                    if now - dispatch_epoch >= timeout_seconds:
                        stale_reason = "timeout"
                    else:
                        idle_threshold = self._fanout_child_idle_threshold(child)
                        if idle_threshold > 0:
                            last_activity = self._fanout_child_last_activity(
                                child, events, dispatch_epoch
                            )
                            if now - last_activity >= idle_threshold:
                                # E5/F15(avbs-r5 首航 7 分钟误杀):thinking
                                # backend 的长回合天然零事件,hook 沉默 ≠ 死亡。
                                # 与 worker 侧 _provider_stuck_grace_active 对齐:
                                # codex child 自派发起享 max(threshold, 900s)
                                # 宽限,宽限外才按事件沉默判闲置。
                                if not self._fanout_child_idle_grace_active(
                                    child,
                                    dispatch_epoch=dispatch_epoch,
                                    idle_threshold=idle_threshold,
                                    now=now,
                                ):
                                    stale_reason = "idle"
                    if not stale_reason:
                        continue
                    if len(child_dispatches) <= max_retries:
                        self._retry_fanout_child(
                            manifest=manifest,
                            child=child,
                            previous_dispatch=last_dispatch,
                            attempt=len(child_dispatches),
                        )
                    else:
                        timed_out.append(child)
                        timed_out_reason[child_id] = stale_reason
                elif (
                    fanout_epoch is not None
                    and self._now() - fanout_epoch >= timeout_seconds
                ):
                    # No dispatch event to retry from → time it out directly so
                    # the aggregate can converge to its failure event.
                    timed_out.append(child)
                    timed_out_reason[child_id] = "timeout"
            if not timed_out:
                # R23: children may all be terminal while the SYNTH phase
                # hangs (stuck pane) — that fanout is invisible to the child
                # walk above and sat past its stage budget for 6h+. Cover the
                # synth with the same budget.
                self._check_fanout_synth_timeout(
                    fanout_id=fanout_id,
                    manifest=manifest,
                    events=events,
                    timeout_seconds=timeout_seconds,
                )
                continue
            pending_children = [str(child.get("child_id") or "") for child in timed_out]
            for child in timed_out:
                self.event_writer.append(ZfEvent(
                    type="fanout.child.failed",
                    actor="zf-cli",
                    payload={
                        "fanout_id": fanout_id,
                        "trace_id": manifest.get("trace_id", ""),
                        "stage_id": manifest.get("stage_id", ""),
                        "child_id": str(child.get("child_id") or ""),
                        "run_id": str(child.get("run_id") or ""),
                        "role_instance": str(child.get("role_instance") or ""),
                        "task_id": str(child.get("task_id") or ""),
                        "reason": timed_out_reason.get(
                            str(child.get("child_id") or ""), "timeout"
                        ),
                        "timeout_seconds": timeout_seconds,
                    },
                    correlation_id=str(manifest.get("trace_id") or ""),
                ))
                self._release_fanout_worker_if_terminal(
                    role_instance=str(child.get("role_instance") or ""),
                    fanout_id=fanout_id,
                    child_id=str(child.get("child_id") or ""),
                    run_id=str(child.get("run_id") or ""),
                    task_id=str(child.get("task_id") or ""),
                )
            timed_out_event = self.event_writer.append(ZfEvent(
                type="fanout.timed_out",
                actor="zf-cli",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": manifest.get("trace_id", ""),
                    "stage_id": manifest.get("stage_id", ""),
                    "pending_children": pending_children,
                    "timeout_seconds": timeout_seconds,
                },
                correlation_id=str(manifest.get("trace_id") or ""),
            ))
            self._publish_fanout_timeout_failure(
                manifest=manifest,
                pending_children=pending_children,
                timeout_seconds=timeout_seconds,
                causation_event=timed_out_event,
                existing_events=events,
            )

    def _cancel_candidate_superseded_replan_fanout(
        self,
        manifest: dict,
        *,
        events: list[ZfEvent] | None = None,
    ) -> bool:
        terminal_statuses = {"completed", "failed", "timed_out", "cancelled"}
        aggregate = (
            manifest.get("aggregate")
            if isinstance(manifest.get("aggregate"), dict)
            else {}
        )
        if (
            str(manifest.get("status") or "") in terminal_statuses
            or str(aggregate.get("status") or "") in terminal_statuses
        ):
            return False
        trigger = (
            manifest.get("trigger_payload")
            if isinstance(manifest.get("trigger_payload"), dict)
            else {}
        )
        from zf.runtime.stage_failure_replan import (
            rework_source_from_payload,
            superseding_candidate_ready,
        )

        source = rework_source_from_payload(trigger)
        if events is None:
            try:
                events = self.event_log.read_all()
            except Exception:
                return False
        ready = superseding_candidate_ready(
            events,
            rework_source=source,
            correlation_id=str(manifest.get("trace_id") or ""),
            pdd_id=str(manifest.get("pdd_id") or manifest.get("feature_id") or ""),
        )
        if ready is None:
            return False
        fanout_id = str(manifest.get("fanout_id") or "")
        for event in reversed(events):
            payload = event.payload if isinstance(event.payload, dict) else {}
            if (
                event.type == "fanout.cancelled"
                and str(payload.get("fanout_id") or "") == fanout_id
            ):
                return True
        self.event_writer.append(ZfEvent(
            type="fanout.cancelled",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": str(manifest.get("trace_id") or ""),
                "stage_id": str(manifest.get("stage_id") or ""),
                "trigger_event_id": str(manifest.get("trigger_event_id") or ""),
                "target_ref": str(manifest.get("target_ref") or ""),
                "pdd_id": str(manifest.get("pdd_id") or ""),
                "feature_id": str(manifest.get("feature_id") or ""),
                "reason": "candidate_replan_superseded_by_candidate_ready",
                "superseded_by": ready.id,
                "source": source,
            },
            causation_id=ready.id,
            correlation_id=str(manifest.get("trace_id") or "") or None,
        ))
        return True

    def _check_fanout_synth_timeout(
        self,
        *,
        fanout_id: str,
        manifest: dict,
        events: list[ZfEvent],
        timeout_seconds: int,
    ) -> None:
        """R23: the timeout sweep only walked manifest children, so a fanout
        whose children were all terminal but whose synth never completed
        (pane stuck on an interactive prompt) hung past its stage budget
        forever. Time the synth out into the existing synth-failure path so
        the aggregate converges to its failure event instead of hanging."""
        synth = manifest.get("synth")
        if not isinstance(synth, dict) or synth.get("status") != "dispatched":
            return
        dispatched = [
            event for event in events
            if event.type == "fanout.synth.dispatched"
            and isinstance(event.payload, dict)
            and event.payload.get("fanout_id") == fanout_id
        ]
        if not dispatched:
            return
        # B3 (R25 ISSUE-005): dedicated synth budget when configured;
        # and when the verdict is already decided (any child failed under
        # a reject-on-fail strategy) the synth only owes a synthesis
        # report — don't sit out the full stage budget (R25: 40min idle
        # after a 5/6-failed round whose outcome could not change).
        config = manifest.get("aggregate_config") or {}
        configured = int(config.get("synth_timeout_seconds") or 0)
        effective_timeout = configured if configured > 0 else timeout_seconds
        strategy = str(
            config.get("review_strategy") or config.get("mode") or ""
        )
        children = [
            child for child in manifest.get("children", [])
            if isinstance(child, dict)
        ]
        any_failed = any(
            str(child.get("status") or "") == "failed" for child in children
        )
        if any_failed and strategy in {
            "all_approve_or_one_rejects",
            "any_failed_fail",
        }:
            effective_timeout = min(effective_timeout, _SYNTH_DECIDED_TIMEOUT_S)
        if self._now() - self._event_epoch(dispatched[-1]) < effective_timeout:
            return
        failure_event = self.event_writer.append(ZfEvent(
            type="fanout.synth.completed",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": str(manifest.get("trace_id") or ""),
                "stage_id": str(manifest.get("stage_id") or ""),
                # B-FIX-07 (R32 stall): 降级路径必产 well-formed 事件 —— 从 manifest
                # 复制 pdd_id/feature_id,否则下游 review.rejected 失去 pdd,
                # candidate_rework 无法路由 → stall。
                "pdd_id": str(manifest.get("pdd_id") or ""),
                "feature_id": str(manifest.get("feature_id") or ""),
                "run_id": str(synth.get("run_id") or ""),
                "role_instance": str(synth.get("role_instance") or ""),
                "status": "failed",
                "recommendation": "reject",
                "reason": "synth_timeout",
                "summary": (
                    f"synth produced no terminal event within "
                    f"{timeout_seconds}s of dispatch; failing the aggregate "
                    "so the stage converges instead of hanging (R23)"
                ),
            },
            correlation_id=str(manifest.get("trace_id") or ""),
        ))
        self._finalize_fanout_synth(failure_event)
    def _backfill_timed_out_fanout_failure(
        self,
        manifest: dict,
        events: list[ZfEvent],
    ) -> None:
        fanout_id = str(manifest.get("fanout_id") or "")
        if not fanout_id:
            return
        if any(
            event.type == "fanout.aggregate.completed"
            and isinstance(event.payload, dict)
            and event.payload.get("fanout_id") == fanout_id
            for event in events
        ):
            return
        timed_out_event = None
        for event in reversed(events):
            if (
                event.type == "fanout.timed_out"
                and isinstance(event.payload, dict)
                and event.payload.get("fanout_id") == fanout_id
            ):
                timed_out_event = event
                break
        aggregate = (
            manifest.get("aggregate")
            if isinstance(manifest.get("aggregate"), dict)
            else {}
        )
        pending_children = [
            str(child_id or "")
            for child_id in (
                (timed_out_event.payload if timed_out_event else {}).get(
                    "pending_children",
                    aggregate.get("pending_children", []),
                )
                or []
            )
            if str(child_id or "")
        ]
        timeout_seconds = int(
            (
                (timed_out_event.payload if timed_out_event else {}).get(
                    "timeout_seconds",
                    aggregate.get("timeout_seconds", 0),
                )
            )
            or 0
        )
        self._publish_fanout_timeout_failure(
            manifest=manifest,
            pending_children=pending_children,
            timeout_seconds=timeout_seconds,
            causation_event=timed_out_event,
            existing_events=events,
        )
    def _publish_fanout_timeout_failure(
        self,
        *,
        manifest: dict,
        pending_children: list[str],
        timeout_seconds: int,
        causation_event: ZfEvent | None,
        existing_events: list[ZfEvent],
    ) -> None:
        fanout_id = str(manifest.get("fanout_id") or "")
        if not fanout_id:
            return
        stale_reason, _superseded_by = self._fanout_identity_stale_reason(fanout_id)
        if stale_reason:
            return
        aggregate_config = (
            manifest.get("aggregate_config")
            if isinstance(manifest.get("aggregate_config"), dict)
            else {}
        )
        trace_id = str(manifest.get("trace_id") or "")
        stage_id = str(manifest.get("stage_id") or "")
        failure_event = str(aggregate_config.get("failure_event") or "")
        mode = str(aggregate_config.get("mode") or "wait_for_all")
        has_aggregate_started = any(
            event.type == "fanout.aggregate.started"
            and isinstance(event.payload, dict)
            and event.payload.get("fanout_id") == fanout_id
            for event in existing_events
        )
        causation_id = causation_event.id if causation_event is not None else None
        if not has_aggregate_started:
            self.event_writer.append(ZfEvent(
                type="fanout.aggregate.started",
                actor="zf-cli",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": trace_id,
                    "stage_id": stage_id,
                    "mode": mode,
                },
                causation_id=causation_id,
                correlation_id=trace_id,
            ))
        aggregate_event = self.event_writer.append(ZfEvent(
            type="fanout.aggregate.completed",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "status": "failed",
                "reason": "timeout",
                "success_event": "",
                "failure_event": failure_event,
                "failed_children": pending_children,
                "pending_children": pending_children,
                "timeout_seconds": timeout_seconds,
            },
            causation_id=causation_id,
            correlation_id=trace_id,
        ))
        self._consume_durable_fanout_aggregate_result(aggregate_event)
        emit_fanout_channel_state_update(
            writer=self.event_writer,
            terminal_event=aggregate_event,
            manifest={**manifest, "aggregate": aggregate_event.payload},
        )
        failure_already_published = any(
            event.type == failure_event
            and isinstance(event.payload, dict)
            and event.payload.get("fanout_id") == fanout_id
            for event in existing_events
        )
        if failure_event and not failure_already_published:
            children = [
                child for child in manifest.get("children", []) or []
                if isinstance(child, dict)
            ]
            self.event_writer.append(ZfEvent(
                type=failure_event,
                actor="zf-cli",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": trace_id,
                    "stage_id": stage_id,
                    "status": "failed",
                    "reason": "timeout",
                    "target_ref": manifest.get("target_ref", ""),
                    "child_count": len(children),
                    "failed_children": pending_children,
                    "pending_children": pending_children,
                    "timeout_seconds": timeout_seconds,
                },
                causation_id=aggregate_event.id,
                correlation_id=trace_id,
            ))
    def _retry_fanout_child(
        self,
        *,
        manifest: dict,
        child: dict,
        previous_dispatch: ZfEvent,
        attempt: int,
    ) -> None:
        fanout_id = str(manifest.get("fanout_id") or "")
        child_id = str(child.get("child_id") or "")
        role_instance = str(child.get("role_instance") or "")
        role = next(iter(self._fanout_roles([role_instance])), None)
        if role is None:
            return
        run_id = f"run-{fanout_id}-{child_id}-retry-{attempt}"
        trace_id = str(manifest.get("trace_id") or "")
        stage_id = str(manifest.get("stage_id") or "")
        task_id = str(child.get("task_id") or "")
        if str(manifest.get("topology") or "") == "fanout_writer_scoped":
            fence_reason = self._writer_task_dispatch_fence_reason(
                task_id,
                role_instance=role.instance_id,
                run_id=run_id,
            )
            if fence_reason:
                self._defer_writer_fanout_dispatch(
                    context=type("RetryContext", (), {
                        "fanout_id": fanout_id,
                        "trace_id": trace_id,
                        "stage_id": stage_id,
                    })(),
                    child=type("RetryChild", (), {"child_id": child_id})(),
                    task_item=child,
                    role=role,
                    run_id=run_id,
                    causation_id=previous_dispatch.id,
                    reason=fence_reason,
                    release_slot=False,
                )
                return
        if not self._ensure_fanout_role_dispatchable(
            role=role,
            fanout_id=fanout_id,
            skip_send_window=False,
            stage_id=stage_id,
            child_id=child_id,
            run_id=run_id,
            trace_id=trace_id,
            causation_id=previous_dispatch.id,
            prompt_kind="fanout_child",
        ):
            return
        briefing_path = self._write_fanout_retry_briefing(
            role=role,
            manifest=manifest,
            child=child,
            run_id=run_id,
        )
        prompt = build_task_prompt(
            role.instance_id,
            briefing_path,
            prompt_kind="fanout_child",
        )
        dispatch_context = self._dispatch_context(
            role=role,
            briefing_path=briefing_path,
            trace_id=trace_id,
        )
        try:
            self._send_transport_task(
                role.instance_id,
                briefing_path,
                prompt,
                dispatch_context,
            )
            self._note_prompt_sent(role.instance_id, run_id)
        except Exception as exc:
            # ZF-E2E-PRDCTL-P0-1: the primary dispatch path already converts
            # send failures (incl. BudgetExceededError) into
            # fanout.child.failed; the retry path let them propagate and kill
            # the reactor turn.
            failure_payload = {
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "child_id": child_id,
                "run_id": run_id,
                "role_instance": role.instance_id,
                "task_id": str(child.get("task_id") or ""),
                "retry_of_run_id": str(previous_dispatch.payload.get("run_id") or ""),
                "attempt": attempt + 1,
                "reason": str(exc),
            }
            retry_failure_kind = classify_dispatch_exception(exc)
            if retry_failure_kind:
                failure_payload["failure_kind"] = retry_failure_kind
            self.event_writer.append(ZfEvent(
                type="fanout.child.failed",
                actor="zf-cli",
                payload=failure_payload,
                causation_id=previous_dispatch.id,
                correlation_id=trace_id,
            ))
            return
        payload = {
            "fanout_id": fanout_id,
            "trace_id": trace_id,
            "stage_id": stage_id,
            "child_id": child_id,
            "run_id": run_id,
            "role_instance": role.instance_id,
            "target_ref": str(child.get("target_ref") or manifest.get("target_ref") or ""),
            "retry_of_run_id": str(previous_dispatch.payload.get("run_id") or ""),
            "attempt": attempt + 1,
            "skills": list(role.skills),
            "briefing_path": str(briefing_path),
        }
        for key in ("task_id", "scope", "workdir", "source_branch", "pdd_id", "feature_id"):
            value = str(child.get(key) or "")
            if value:
                payload[key] = value
        child_payload = child.get("payload")
        if isinstance(child_payload, dict) and child_payload:
            payload["payload"] = dict(child_payload)
        if str(manifest.get("topology") or "") == "fanout_writer_scoped":
            task_id = str(payload.get("task_id") or "")
            if self._claim_writer_fanout_task(
                task_id,
                role.instance_id,
                run_id=run_id,
            ):
                payload["task_status"] = "in_progress"
                payload["assigned_to"] = role.instance_id
        try:
            snapshot_result, snapshot_payload = (
                self._write_fanout_child_runtime_snapshot(
                    role=role,
                    payload=payload,
                    briefing_path=briefing_path,
                )
            )
            payload["snapshot_ref"] = snapshot_result.snapshot_ref
            self.event_writer.append(ZfEvent(
                type="runtime.snapshot.recorded",
                actor="zf-cli",
                task_id=payload.get("task_id") or None,
                payload=snapshot_payload,
                causation_id=previous_dispatch.id,
                correlation_id=str(manifest.get("trace_id") or ""),
            ))
        except Exception as snapshot_exc:
            self.event_writer.append(ZfEvent(
                type="runtime.snapshot.invalid",
                actor="zf-cli",
                task_id=payload.get("task_id") or None,
                payload={
                    "source": "fanout_child",
                    "reason": str(snapshot_exc),
                    "fanout_id": fanout_id,
                    "child_id": child_id,
                    "run_id": run_id,
                },
                causation_id=previous_dispatch.id,
                correlation_id=str(manifest.get("trace_id") or ""),
            ))
        self.event_writer.append(ZfEvent(
            type="fanout.child.dispatched",
            actor="zf-cli",
            payload=payload,
            causation_id=previous_dispatch.id,
            correlation_id=trace_id,
        ))
        self._set_worker_state(
            role.instance_id,
            "busy",
            reason=f"dispatched fanout child {fanout_id}/{child_id}",
            task_id=str(payload.get("task_id") or ""),
        )

    def _reader_child_infra_dispatch_deferrals(
        self,
        fanout_id: str,
        child_id: str,
    ) -> int:
        """Count prior infra dispatch deferrals for one reader child."""
        try:
            events = self.event_log.read_all()
        except Exception:
            return 0
        count = 0
        for event in events:
            if event.type != "fanout.child.dispatch_deferred":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if (
                str(payload.get("fanout_id") or "") == fanout_id
                and str(payload.get("child_id") or "") == child_id
            ):
                count += 1
        return count

    def _release_fanout_worker_if_terminal(
        self,
        *,
        role_instance: str,
        fanout_id: str,
        child_id: str, run_id: str, task_id: str,
    ) -> None:
        if not role_instance:
            return
        # ZF-E2E-PRDCTL-P2-7-3:child 终局 → 清发送窗,合法的"完成后立即
        # 续派同 lane"不受简报吞没守卫影响。
        try:
            getattr(self, "_last_prompt_sent_at", {}).pop(role_instance, None)
        except Exception:
            pass
        try:
            active = self._active_fanout_child_for_instance(role_instance)
        except Exception:
            active = None
        if active:
            if (
                str(active.get("fanout_id") or "") != fanout_id
                or str(active.get("child_id") or "") != child_id
                or (
                    run_id
                    and str(active.get("run_id") or "")
                    and str(active.get("run_id") or "") != run_id
                )
            ):
                return
        self._set_worker_state(
            role_instance,
            "idle",
            reason=f"fanout child {fanout_id}/{child_id} terminal",
            task_id=task_id,
        )
    def _fanout_timeout_seconds(self, stage_id: str) -> int:
        # E6(prd-goal e2e finding-15):stage 未配 timeout 时 sweep 直接
        # 跳过 → fanout 起后永不终局(zombie ×2 实弹,还挡 G1 idle
        # 判定)。"fanout 起必有终局":未配置走保守地板。
        for stage in getattr(self.config.workflow, "stages", []):
            if stage.id == stage_id:
                return int(stage.timeout_seconds or DEFAULT_FANOUT_TIMEOUT_S)
        return DEFAULT_FANOUT_TIMEOUT_S
    def _finalize_fanout_synth(self, event: ZfEvent) -> None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        fanout_id = str(payload.get("fanout_id") or "")
        if not fanout_id:
            return
        manifest = self._fanout_manifest(fanout_id)
        if not manifest:
            return
        stale_reason, superseded_by = self._fanout_identity_stale_reason(fanout_id)
        if stale_reason:
            self._emit_fanout_identity_stale_completion(
                event=event,
                payload=payload,
                manifest=manifest,
                child=None,
                reason=stale_reason,
                superseded_by=superseded_by,
            )
            return
        aggregate = manifest.get("aggregate") if isinstance(manifest.get("aggregate"), dict) else {}
        if aggregate.get("status") in {"completed", "failed", "timed_out", "cancelled"}:
            return
        from zf.runtime.fanout import recommendation_is_success, validate_fanout_report

        raw_report = payload.get("report") if isinstance(payload.get("report"), dict) else payload
        report_result = validate_fanout_report(
            raw_report,
            child_id="synth",
            default_status="passed",
            default_recommendation=str(payload.get("recommendation") or "abstain"),
            default_summary=str(payload.get("summary") or ""),
        )
        recommendation = str(
            payload.get("recommendation")
            or report_result.report.get("recommendation")
            or "abstain"
        )
        if not report_result.valid:
            recommendation = "reject"
        final_status = "completed" if recommendation_is_success(recommendation) else "failed"
        config = manifest.get("aggregate_config") or {}
        success_event = str(config.get("success_event") or "")
        failure_event = str(config.get("failure_event") or "")
        trace_id = str(manifest.get("trace_id") or payload.get("trace_id") or "")
        stage_id = str(manifest.get("stage_id") or payload.get("stage_id") or "")
        artifact_payload: dict = {}
        if final_status == "completed":
            artifact_projection = self._project_refactor_success_artifacts(
                manifest=manifest,
                success_event=success_event,
                synth_event=event,
            )
            if artifact_projection is not None:
                artifact_payload = artifact_projection.payload
                if not artifact_projection.ok:
                    final_status = "failed"
                    recommendation = "reject"
            else:
                artifact_payload = self._generic_fanout_success_payload(
                    manifest=manifest,
                    success_event=success_event,
                    extra_payloads=[payload],
                )
        elif (
            success_event == "flow.discovery.completed"
            and failure_event == "flow.discovery.failed"
        ):
            artifact_payload = self._generic_fanout_success_payload(
                manifest=manifest,
                success_event=success_event,
                extra_payloads=[payload],
            )
        artifact_payload = {
            **self._fanout_flow_identity_payload(manifest, payloads=[payload]),
            **artifact_payload,
        }
        if final_status == "completed":
            contract_failure = self._success_payload_contract_failure(
                success_event,
                artifact_payload,
            )
            if contract_failure:
                final_status = "failed"
                recommendation = "reject"
                artifact_payload = self._contract_failure_payload(
                    artifact_payload,
                    contract_failure,
                )
        if final_status == "completed":
            criteria_failure = evaluate_fanout_stage_success_criteria_for_orchestrator(
                self, manifest=manifest, artifact_payload=artifact_payload)
            if criteria_failure:
                final_status = "failed"
                recommendation = "reject"
                artifact_payload = criteria_failure.artifact_payload
        from zf.runtime.plan_artifact_package_runtime import (
            admit_synthesized_plan_package,
        )
        final_status, recommendation, artifact_payload = (
            admit_synthesized_plan_package(
                self, event=event, manifest=manifest, stage_id=stage_id,
                trace_id=trace_id, success_event=success_event,
                final_status=final_status, recommendation=recommendation,
                artifact_payload=artifact_payload,
            )
        )
        if not self._fanout_aggregate_started(manifest):
            self.event_writer.append(ZfEvent(
                type="fanout.aggregate.started",
                actor="zf-cli",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": trace_id,
                    "stage_id": stage_id,
                    "mode": str(config.get("mode") or "wait_for_all"),
                },
                causation_id=event.id,
                correlation_id=trace_id,
            ))
        publish_event = success_event if final_status == "completed" else failure_event
        # B-FIX-07 (R32 stall): reader-synth finalize 的 failure publish_event
        # (review.rejected 等)必带 manifest 的 pdd_id/feature_id —— 否则
        # candidate_rework 从中推不出 pdd,rework 无法路由 → stall(synth_timeout
        # 走的就是此路径)。
        pdd_id = str(manifest.get("pdd_id") or "")
        feature_id = str(manifest.get("feature_id") or "")
        # ZF-E2E-PRDCTL-P2-7-4:reader-synth 发布路径此前不转发 candidate_ref
        # (lane final-ready 路径有,此路径无——路径不对称),下游 judge 段的
        # ${candidate_ref} 模板解析失败秒败(deepwater 实证)。
        synth_payload = event.payload if isinstance(event.payload, dict) else {}
        trigger_payload = (
            manifest.get("trigger_payload")
            if isinstance(manifest.get("trigger_payload"), dict) else {}
        )
        candidate_ref = ""
        for source in (manifest, trigger_payload, synth_payload):
            candidate_ref = str(source.get("candidate_ref") or "").strip()
            if candidate_ref:
                break
        candidate_ref_payload = (
            {"candidate_ref": candidate_ref} if candidate_ref else {}
        )
        aggregate_event = self.event_writer.append(ZfEvent(
            type="fanout.aggregate.completed",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "pdd_id": pdd_id,
                "feature_id": feature_id,
                "status": final_status,
                "success_event": success_event if final_status == "completed" else "",
                "failure_event": failure_event if final_status == "failed" else "",
                "recommendation": recommendation,
                "synth_event_id": event.id,
                **candidate_ref_payload,
                **artifact_payload,
            },
            causation_id=event.id,
            correlation_id=trace_id,
        ))
        self._consume_durable_fanout_aggregate_result(aggregate_event)
        emit_fanout_channel_state_update(
            writer=self.event_writer,
            terminal_event=aggregate_event,
            manifest={
                **manifest,
                "aggregate": aggregate_event.payload,
                "artifact_refs": artifact_payload.get(
                    "artifact_refs",
                    manifest.get("artifact_refs", []),
                ),
            },
            synth_event=event,
        )
        if publish_event:
            children = [
                child for child in manifest.get("children", [])
                if isinstance(child, dict)
            ]
            published = self.event_writer.append(ZfEvent(
                type=publish_event,
                actor="zf-cli",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": trace_id,
                    "stage_id": stage_id,
                    "pdd_id": pdd_id,
                    "feature_id": feature_id,
                    "status": final_status,
                    "target_ref": manifest.get("target_ref", ""),
                    "child_count": len(children),
                    "recommendation": recommendation,
                    "synth_role": str(config.get("synth_role") or ""),
                    **candidate_ref_payload,
                    **artifact_payload,
                },
                causation_id=event.id,
                correlation_id=trace_id,
            ))
            if final_status == "completed" and publish_event == "verify.passed":
                self._bridge_verify_passed_to_parity_scan(published)
            elif (
                final_status == "completed"
                and is_module_parity_scan_completed_event(publish_event)
            ):
                self._bridge_module_parity_scan_completed(published)
            elif (
                final_status == "completed"
                and publish_event in {
                    "gap_plan.ready",
                    "goal.gap_plan.ready",
                    "flow.gap_plan.ready",
                }
            ):
                self._bridge_gap_plan_ready_to_task_map(published)
    def _render_fanout_target(self, template: str, event: ZfEvent) -> str:
        values = {}
        if isinstance(event.payload, dict):
            values.update({str(key): str(value) for key, value in event.payload.items()})
        if event.task_id:
            values.setdefault("task_id", event.task_id)
        candidate_ref = str(values.get("candidate_ref") or "").strip()
        if candidate_ref and self._is_candidate_ready_default_target(template, event):
            return candidate_ref
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace("${" + key + "}", value)
        if candidate_ref and self._is_candidate_ready_default_target(rendered, event):
            return candidate_ref
        if rendered:
            return rendered
        return str(values.get("target_ref") or values.get("candidate_ref") or values.get("task_ref") or "")

    @staticmethod
    def _is_candidate_ready_default_target(value: str, event: ZfEvent) -> bool:
        if event.type != "candidate.ready":
            return False
        normalized = str(value or "").strip()
        return (
            not normalized
            or normalized == "HEAD"
            or "ZF_CANDIDATE_REF" in normalized
        )

    def _refactor_scan_target_ref_error(
        self,
        *,
        stage,
        event: ZfEvent,
        target_ref: str,
    ) -> str:
        if getattr(event, "type", "") != "refactor.scan.requested":
            return ""
        if str(getattr(stage, "trigger", "") or "") != "refactor.scan.requested":
            return ""
        target_ref = str(target_ref or "").strip()
        if not target_ref:
            return ""
        try:
            proc = subprocess.run(
                [
                    "git",
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    f"{target_ref}^{{commit}}",
                ],
                cwd=self.project_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return "target_ref_not_a_git_ref"
        if proc.returncode == 0:
            return ""
        return "target_ref_not_a_git_ref"

    def _emit_refactor_scan_target_ref_failed(
        self,
        *,
        event: ZfEvent,
        stage_id: str,
        trace_id: str,
        fanout_id: str,
        target_ref: str,
        reason: str,
    ) -> None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        base_payload = {
            "fanout_id": fanout_id,
            "trace_id": trace_id,
            "stage_id": stage_id,
            "trigger_event_id": event.id,
            "target_ref": target_ref,
            "reason": reason,
            "failure_classification": "operator_config",
            "remediation": "use_branch_or_commit_for_target_ref",
            "source_event_id": event.id,
        }
        for key in ("pdd_id", "feature_id", "run_tag"):
            value = str(payload.get(key) or "").strip()
            if value:
                base_payload[key] = value
        self.event_writer.append(ZfEvent(
            type="fanout.cancelled",
            actor="zf-cli",
            payload=dict(base_payload),
            causation_id=event.id,
            correlation_id=trace_id,
        ))
        self.event_writer.append(ZfEvent(
            type="refactor.scan.failed",
            actor="zf-cli",
            payload={
                **base_payload,
                # v3 EventSchemaD requires child_id + status on
                # refactor.scan.failed (kernel_aggregate_child). This is a
                # pre-dispatch operator-config cancellation (no real child), so
                # name the scan stage as the failing unit; without these the
                # blocking discriminator rejects the fail-fast and the scan
                # wedges for hours instead of surfacing the clear error.
                "child_id": stage_id or "refactor-scan",
                "status": "failed",
                "error": (
                    f"target_ref {target_ref!r} is not a git branch, tag, "
                    "or commit; file paths belong in objective/prompt/artifacts"
                ),
            },
            causation_id=event.id,
            correlation_id=trace_id,
        ))

    def _delta_gate_allows(
        self,
        *,
        stage,
        event: ZfEvent,
        target_ref: str,
        trace_id: str,
    ) -> bool:
        """FIX-15②③(bizsim r4):判审收敛门。

        ② 同一审计对象 commit 已有本段驳回记录 → 抑制重开审
          (fanout.retrigger.suppressed;r4 两次必败审的实锚);
        ③ 本段连续 ≥3 次驳回未收敛 → 升级 owner(human.escalate,
          judge_nonconvergence,带驳回链摘要;有 delta 时仍放行新审)。
        依赖 FIX-9 的 pin-commit:历史审计对象取 fanout.child.dispatched
        里记录的 target_commit。
        """
        failure_event = str(getattr(stage.aggregate, "failure_event", "") or "")
        if not failure_event:
            return True
        try:
            events = self.event_log.read_all()
        except Exception:
            return True
        stage_fanouts: set[str] = set()
        pinned: dict[str, str] = {}
        failures: list[tuple[str, str]] = []  # (fanout_id, reason)
        escalated_counts: set[int] = set()
        success_event = str(getattr(stage.aggregate, "success_event", "") or "")
        outcomes: list[str] = []
        for existing in events:
            payload = (
                existing.payload if isinstance(existing.payload, dict) else {}
            )
            fid = str(payload.get("fanout_id") or "")
            if existing.type == "fanout.started" and str(
                payload.get("stage_id") or ""
            ) == stage.id:
                stage_fanouts.add(fid)
            elif existing.type == "fanout.child.dispatched" and fid in stage_fanouts:
                child_payload = payload.get("payload")
                commit = (
                    str(child_payload.get("target_commit") or "")
                    if isinstance(child_payload, dict) else ""
                )
                if commit:
                    pinned[fid] = commit
            elif existing.type == failure_event and fid in stage_fanouts:
                failures.append((
                    fid,
                    str(payload.get("reason") or payload.get("summary") or "")[:160],
                ))
                outcomes.append("fail")
            elif success_event and existing.type == success_event and fid in stage_fanouts:
                outcomes.append("pass")
            elif existing.type == "human.escalate" and str(
                payload.get("stage_id") or ""
            ) == stage.id and str(payload.get("reason") or "") == "judge_nonconvergence":
                try:
                    escalated_counts.add(int(payload.get("failure_count") or 0))
                except (TypeError, ValueError):
                    pass
        if not failures:
            return True
        trailing = 0
        for outcome in reversed(outcomes):
            if outcome != "fail":
                break
            trailing += 1
        current_commit = ""
        cleaned = str(target_ref or "").strip()
        if cleaned and "${" not in cleaned and "{{" not in cleaned:
            try:
                proc = subprocess.run(
                    ["git", "rev-parse", "--verify", "--quiet",
                     f"{cleaned}^{{commit}}"],
                    cwd=self.project_root,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
                if proc.returncode == 0:
                    current_commit = proc.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                current_commit = ""
        if trailing >= 3 and trailing not in escalated_counts:
            self.event_writer.append(ZfEvent(
                type="human.escalate",
                actor="zf-cli",
                payload={
                    "reason": "judge_nonconvergence",
                    "stage_id": stage.id,
                    "failure_count": trailing,
                    "failure_chain": [
                        {"fanout_id": fid, "reason": reason}
                        for fid, reason in failures[-3:]
                    ],
                    "blocking_scope": "run",
                    "suggested_options": [
                        "inspect failure_chain", "fix and retrigger",
                        "safe_halt",
                    ],
                },
                causation_id=event.id,
                correlation_id=trace_id,
            ))
        failed_commits = {
            pinned[fid] for fid, _ in failures if pinned.get(fid)
        }
        if current_commit and current_commit in failed_commits:
            self.event_writer.append(ZfEvent(
                type="fanout.retrigger.suppressed",
                actor="zf-cli",
                payload={
                    "stage_id": stage.id,
                    "target_ref": cleaned,
                    "target_commit": current_commit,
                    "reason": "no_delta_since_failure",
                    "failure_count": trailing,
                },
                causation_id=event.id,
                correlation_id=trace_id,
            ))
            return False
        return True

    _SCHEMA_EDU_PLACEHOLDERS = {
        "requirement_understanding": (
            "How you understood the requirement (1-3 sentences)."
        ),
        "requirement_coverage_matrix": [{
            "requirement_id": "<acceptance-id-from-task-contract>",
            "source_ref": "<prd-or-contract-path#section>",
            "status": "covered",
            "evidence_refs": ["<test-or-log-path>"],
            "gap_summary": "",
            "replan_action": "continue",
        }],
        "gap_findings": [],
        "replan_recommendation": "continue",
        "evidence_refs": ["<primary-evidence-path>"],
        "summary": (
            "One-line reviewable outcome with numbers "
            "(N files / M tests / gate X of Y)."
        ),
    }

    def _schema_education_toplevel_fields(
        self,
        event_type: str,
        *,
        existing: dict,
    ) -> dict:
        """LB-4:child 完成事件的**顶层** required/non_empty 字段也进样例。

        FIX-14 只镜像 report.* 子字段;canonical-dag/v3 给读者 child 完成
        事件加了顶层 non_empty(summary/evidence_refs),默认骨架不含它们 →
        blocking 档下即便合规 agent 照抄模板也会被拦一道。顶层字段与 report
        字段同样机械教育,避免执法在 happy path 制造返工。
        """
        if not event_type:
            return {}
        try:
            from zf.core.verification.event_schema import EventSchemaRegistry

            rule = EventSchemaRegistry.from_config(self.config).rule_for(
                event_type,
            )
        except Exception:
            return {}
        if rule is None:
            return {}
        out: dict = {}
        for field_name in (*rule.required, *rule.non_empty):
            if field_name == "report" or field_name in existing or field_name in out:
                continue
            out[field_name] = self._SCHEMA_EDU_PLACEHOLDERS.get(
                field_name, f"<{field_name}>",
            )
        return out

    def _schema_education_report_fields(
        self,
        event_type: str,
        *,
        existing: dict,
    ) -> dict:
        """FIX-14:按配置的 event schema 反推 report 样例必须携带的字段。

        schema 要求(required/non_empty)而默认骨架没有的字段,以占位值
        补进 briefing 样例——合约与教育机械配对,不靠 agent 试错 422。
        """
        if not event_type:
            return {}
        try:
            from zf.core.verification.event_schema import EventSchemaRegistry

            rule = EventSchemaRegistry.from_config(self.config).rule_for(
                event_type,
            )
        except Exception:
            return {}
        report_rule = (
            rule.nested_rules.get("report") if rule is not None else None
        )
        if report_rule is None:
            return {}
        wanted = [*report_rule.required, *report_rule.non_empty]
        out: dict = {}
        for field_name in wanted:
            if field_name in existing or field_name in out:
                continue
            out[field_name] = self._SCHEMA_EDU_PLACEHOLDERS.get(
                field_name, f"<{field_name}>",
            )
        return out

    def _pin_reader_target_or_reject(
        self,
        *,
        role: RoleConfig,
        target_ref: str,
        context,
        child,
        run_id: str,
        causation_id: str,
    ) -> bool:
        """FIX-9/15①(bizsim r4 F9):reader child 派发前锁定审计对象。

        stage 声明了 target_ref 但渲染为空 → 拒派发(r4 judge 五审即
        审基线树);checkout/HEAD 校验失败 → 拒派发。锁定成功把
        target_commit 写进 child payload 供 briefing/证据绑定消费。
        """
        config = getattr(self, "config", None)
        workflow = getattr(config, "workflow", None)
        stages = getattr(workflow, "stages", []) or []
        stage = next(
            (
                s for s in stages
                if getattr(s, "id", "") == context.stage_id
            ),
            None,
        )
        declared = str(getattr(stage, "target_ref", "") or "").strip() if stage else ""
        cleaned = str(target_ref or "").strip()
        reason = ""
        pinned = ""
        unrendered = "${" in cleaned or "{{" in cleaned
        if declared and (not cleaned or unrendered):
            reason = "target_ref_unresolved"
            cleaned = "" if unrendered else cleaned
        elif cleaned and not self._fanout_target_ref_is_project_path(cleaned):
            from zf.runtime.workdirs import WorkdirManager

            try:
                pinned = WorkdirManager(
                    state_dir=self.state_dir,
                    project_root=self.project_root,
                    config=self.config,
                ).pin_reader_target(role, cleaned)
            except Exception as exc:
                reason = f"pin_failed: {exc}"
        if reason:
            failure_payload = {
                "fanout_id": context.fanout_id,
                "trace_id": context.trace_id,
                "stage_id": context.stage_id,
                "child_id": child.child_id,
                "run_id": run_id,
                "role_instance": role.instance_id,
                "target_ref": cleaned,
                "reason": reason,
            }
            self.event_writer.append(ZfEvent(
                type="fanout.child.workdir_mismatch",
                actor="zf-cli",
                payload=failure_payload,
                causation_id=causation_id,
                correlation_id=context.trace_id,
            ))
            self.event_writer.append(ZfEvent(
                type="fanout.child.failed",
                actor="zf-cli",
                payload={
                    **failure_payload,
                    "failure_class": "reader_workdir_mismatch",
                },
                causation_id=causation_id,
                correlation_id=context.trace_id,
            ))
            return False
        if pinned and isinstance(child.payload, dict):
            child.payload["target_commit"] = pinned
        return True

    def _checkout_fanout_reader(self, role: RoleConfig, target_ref: str) -> None:
        if not target_ref:
            return
        if self._fanout_target_ref_is_project_path(target_ref):
            return
        from zf.runtime.workdirs import WorkdirManager

        WorkdirManager(
            state_dir=self.state_dir,
            project_root=self.project_root,
            config=self.config,
        ).checkout_reader_ref(role, target_ref)

    def _fanout_target_ref_is_project_path(self, target_ref: str) -> bool:
        """Return true when target_ref is a project-local file/dir input.

        PRD/Issue scan stages use target_ref for source documents such as
        docs/prd/foo.md. Candidate/judge stages use target_ref as a git audit
        ref. Existing project paths should be passed through to the reader
        briefing instead of being pinned as git refs.
        """
        raw = str(target_ref or "").strip()
        if not raw or "${" in raw or "{{" in raw or "://" in raw:
            return False
        try:
            root = self.project_root.resolve(strict=False)
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = root / candidate
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return False
        return resolved.exists()
    def _write_writer_fanout_briefing(
        self,
        *,
        role: RoleConfig,
        context,
        child,
        task_item: dict,
        run_id: str,
        pdd_id: str,
        workdir_plan,
        skill_entries: list | None = None,
        rework_feedback: list | None = None,
        rework_attempt: int = 0,
        rework_summary: dict | None = None,
    ) -> Path:
        import json
        import shlex

        task_id = str(task_item.get("task_id") or "")
        task_scope = str(task_item.get("scope") or "")
        task_payload = (
            task_item.get("payload")
            if isinstance(task_item.get("payload"), dict)
            else {}
        )
        from zf.runtime.injection import materialize_instruction_refs
        task_payload = materialize_instruction_refs(
            task_payload, project_root=self.project_root,
        )
        workflow_ref_keys = (
            "workflow_input_manifest_ref",
            "workflow_prompt_ref",
            "workflow_run_id",
            "source_inventory_ref",
            "capability_matrix_ref",
            "acceptance_matrix_ref",
            "test_matrix_ref",
            "real_e2e_matrix_ref",
            "skill_adapter_plan_ref",
            "intake_json_ref",
        )

        def _workflow_ref_bundle(*sources: dict) -> dict:
            bundle: dict = {}
            source_refs: dict = {}
            artifact_refs: list = []
            for source in sources:
                if not isinstance(source, dict):
                    continue
                for key in workflow_ref_keys:
                    value = str(source.get(key) or "").strip()
                    if value and key not in bundle:
                        bundle[key] = value
                raw_source_refs = source.get("source_refs")
                if isinstance(raw_source_refs, dict):
                    source_refs.update({
                        str(k): str(v)
                        for k, v in raw_source_refs.items()
                        if str(v or "").strip()
                    })
                raw_artifact_refs = source.get("artifact_refs")
                if isinstance(raw_artifact_refs, list):
                    artifact_refs.extend(raw_artifact_refs)
            if source_refs:
                bundle["source_refs"] = source_refs
            if artifact_refs:
                seen: set[str] = set()
                deduped: list = []
                for item in artifact_refs:
                    key = (
                        json.dumps(item, sort_keys=True, ensure_ascii=False)
                        if isinstance(item, dict) else str(item or "")
                    )
                    if key and key not in seen:
                        seen.add(key)
                        deduped.append(item)
                bundle["artifact_refs"] = deduped
            return bundle

        task_instruction = str(
            task_payload.get("instruction")
            or task_payload.get("summary")
            or ""
        )
        contract_src = (
            task_item.get("raw_task")
            if isinstance(task_item.get("raw_task"), dict)
            else task_item
        )
        contract_snapshot: dict = {}
        try:
            canonical_task = self.task_store.get(task_id)
            expected_contract = {"task_id": task_id}
            if canonical_task is not None:
                expected_contract.update(current_task_contract_identity(
                    canonical_task,
                    task_map_ref=str(task_item.get("task_map_ref") or ""),
                ))
            contract_snapshot = hydrate_task_contract_snapshot(
                self.state_dir,
                contract_descriptor_from_payload(task_item),
                expected=expected_contract,
            )
        except TaskContractSnapshotError:
            contract_snapshot = {}
        briefings_dir = self.state_dir / "briefings"
        briefings_dir.mkdir(parents=True, exist_ok=True)
        path = briefings_dir / f"{role.instance_id}-{context.fanout_id}-{child.child_id}.md"
        source_branch = str(workdir_plan.branch_or_ref)
        workdir = str(workdir_plan.project_path)
        # Lane branches keep prior-round commits that never merge back to the
        # candidate base; without the dispatch-time workdir HEAD the task-ref
        # scope gate diffs the whole lane history and rejects every handoff
        # (HIC-6B747D9856). The writer echoes this field in dev.build.done.
        from zf.runtime.orchestrator_dispatch import _capture_head
        base_git_head = str(task_item.get("base_commit") or "") or _capture_head(Path(workdir))
        completion_payload = {
            "fanout_id": context.fanout_id,
            "stage_id": context.stage_id,
            "child_id": child.child_id,
            "run_id": run_id,
            "pdd_id": pdd_id,
            "feature_id": str(task_item.get("feature_id") or pdd_id),
            "task_map_ref": str(task_item.get("task_map_ref") or ""),
            "source_index_ref": str(task_item.get("source_index_ref") or ""),
            "scope": task_scope,
            "source_branch": source_branch,
            "workdir": workdir,
            "base_git_head": base_git_head,
            "source_commit": "<HEAD commit>",
            "files_touched": [],
            "contract_snapshot_ref": str(task_item.get("contract_snapshot_ref") or ""),
            "contract_snapshot_digest": str(task_item.get("contract_snapshot_digest") or ""),
            "workflow_run_id": str(task_item.get("workflow_run_id") or ""),
            "contract_revision": str(task_item.get("contract_revision") or ""),
            "task_map_generation": str(task_item.get("task_map_generation") or ""),
            "base_commit": base_git_head,
            **_workflow_ref_bundle(task_item, task_payload, contract_src),
        }
        for key in _CONTRACT_HANDOFF_KEYS:
            value = task_item.get(key)
            if value not in (None, "", [], {}):
                completion_payload[key] = value
        if contract_snapshot:
            from zf.runtime.impl_self_check import completion_payload_template
            completion_payload.update(completion_payload_template(
                contract_snapshot=contract_snapshot,
                task_item=task_item,
                task_id=task_id, run_id=run_id, child_id=child.child_id,
            ))
        completion_command = " ".join([
            *[shlex.quote(part) for part in shlex.split(zf_cli_cmd()) or ["zf"]],
            "emit",
            "dev.build.done",
            "--task",
            shlex.quote(task_id),
            "--actor",
            shlex.quote(role.instance_id),
            "--state-dir",
            shlex.quote(str(self.state_dir)),
            "--payload",
            shlex.quote(json.dumps(completion_payload, ensure_ascii=False)),
        ])
        blocked_payload = {
            key: completion_payload[key]
            for key in (
                "fanout_id",
                "stage_id",
                "child_id",
                "run_id",
                "pdd_id",
                "feature_id",
                "task_map_ref",
                "source_index_ref",
                "workflow_run_id",
                "contract_revision",
                "task_map_generation",
            )
            if completion_payload.get(key)
        }
        blocked_payload.update({
            "status": "failed",
            "reason": "<concise reproducible blocker>",
            "failure_class": "task_contract_unsatisfiable",
            "recommended_action": "replan",
            "blocker_task_ids": [],
            "required_paths": [],
            "evidence_refs": ["<reproduction command or artifact ref>"],
            "report": {
                "status": "failed",
                "summary": "<what is impossible inside the current contract>",
                "findings": [],
                "recommendation": "reject",
            },
        })
        blocked_command = " ".join([
            *[shlex.quote(part) for part in shlex.split(zf_cli_cmd()) or ["zf"]],
            "emit",
            "dev.blocked",
            "--task",
            shlex.quote(task_id),
            "--actor",
            shlex.quote(role.instance_id),
            "--state-dir",
            shlex.quote(str(self.state_dir)),
            "--payload",
            shlex.quote(json.dumps(blocked_payload, ensure_ascii=False)),
        ])
        rework_section: list[str] = []
        if rework_feedback:
            rework_section = [
                f"## ⚠️ REWORK (attempt {rework_attempt}) — prior candidate was REJECTED",
                "",
                "The previous candidate for this PDD failed review/verification. You MUST",
                "address EVERY finding below; re-submitting without fixing them will be",
                "rejected again. If a finding mentions npm/workspace/lockfile, regenerate",
                "`package-lock.json` (run `npm install`) and ensure every declared",
                "`@scope/*` workspace dependency has a real providing package.",
                "",
                "Reviewer findings to resolve:",
                *[f"- {line}" for line in rework_feedback],
                "",
            ]
            if rework_summary:
                rework_section.extend([
                    "Rework summary:",
                    "```json",
                    json.dumps(rework_summary, indent=2, ensure_ascii=False),
                    "```",
                    "",
                ])
        candidate_preflight_section = [
            "## Candidate-ready preflight",
            "",
            "Before emitting `dev.build.done`, run the lane verification named by the task contract and any root checks required by this repository's candidate quality gates. Candidate integration may block `candidate.ready` until those gates pass.",
            "For Node.js/TypeScript refactors, package or workspace changes require a frozen lockfile install plus root typecheck/test where available, not only package-local tests.",
            "Contract fixtures must be exact golden fixtures: assert complete fields, complete lists, offsets, fallback/ignored behavior, and snake_case/camelCase parity where the source system supports both. Shape-only fixture checks are not enough.",
            "",
        ]
        workflow_input_section = render_workflow_input_briefing_section(
            _workflow_ref_bundle(task_item, task_payload, contract_src),
        ).strip()
        workflow_input_lines = (
            [*workflow_input_section.splitlines(), ""]
            if workflow_input_section
            else []
        )
        from zf.runtime.artifact_read_ledger import render_attempt_source_briefing

        controlled_input_section = render_attempt_source_briefing(
            task_item,
            state_dir=self.state_dir,
        ).strip()
        controlled_input_lines = (
            [*controlled_input_section.splitlines(), ""]
            if controlled_input_section
            else []
        )
        # 2026-06-19 (e2e context-continuity audit): the plan's per-task
        # contract content (acceptance / verification / summary-scope-guard /
        # source_refs) must be inlined into the briefing so the impl agent does
        # not have to dereference task_map_ref to learn it. writer_task_items
        # normalizes the task (renaming acceptance→acceptance_criteria and
        # stringifying verification) but preserves the untouched plan task under
        # ``raw_task``, so read the original list-typed contract from there;
        # fall back to task_item for the truly-raw dispatch path.
        workflow_scope_refs = _workflow_ref_bundle(
            task_item,
            task_payload,
            contract_src,
        )
        scope_contract = {
            "task_id": task_id,
            "pdd_id": pdd_id,
            "feature_id": str(task_item.get("feature_id") or pdd_id),
            "task_map_ref": str(task_item.get("task_map_ref") or ""),
            "source_index_ref": str(task_item.get("source_index_ref") or ""),
            "allowed_paths": (
                contract_snapshot.get("allowed_paths")
                or task_item.get("allowed_paths", [])
            ),
            "allowed_paths_reason": str(task_item.get("allowed_paths_reason") or contract_src.get("allowed_paths_reason") or ""),
            "protected_paths": task_item.get("protected_paths", [".zf/**"]),
            "base_ref": str(
                task_item.get("dispatch_base_commit")
                or child.target_ref
                or self.config.runtime.git.candidate_base_ref
            ),
            "worker_branch": source_branch,
            "handoff_ref": f"{self.config.runtime.git.task_ref_prefix}/{task_id}",
            "expected_outputs": [
                "source_commit",
                "tests",
                "files_changed",
            ],
            # Inline the plan's per-task contract content (summary holds
            # any SCOPE GUARD) from the preserved raw plan task — see
            # contract_src above.
            "summary": str(
                contract_snapshot.get("behavior")
                or contract_src.get("summary")
                or ""
            ),
            "acceptance": (
                contract_snapshot.get("acceptance_criteria")
                or contract_src.get("acceptance", [])
            ),
            "verification": (
                contract_snapshot.get("verification_commands")
                or contract_src.get("verification", [])
            ),
            "source_refs": (
                contract_src.get("source_refs")
                or task_item.get("source_refs", [])
            ),
            "contract_snapshot_ref": str(task_item.get("contract_snapshot_ref") or ""),
            "contract_snapshot_digest": str(task_item.get("contract_snapshot_digest") or ""),
            "contract_snapshot": contract_snapshot,
        }
        scope_contract.update({
            key: value
            for key, value in workflow_scope_refs.items()
            if key != "source_refs"
        })
        if workflow_scope_refs.get("source_refs"):
            scope_contract["workflow_source_refs"] = workflow_scope_refs["source_refs"]
        display_task_payload = dict(task_payload)
        for contract_key in (
            "acceptance",
            "acceptance_criteria",
            "allowed_paths",
            "contract_revision",
            "raw_task",
            "scope",
            "task_map_generation",
            "validation",
            "verification",
            "verification_tiers",
        ):
            display_task_payload.pop(contract_key, None)
        briefing_text = "\n".join([
                f"# Fanout Writer Child: {task_id}",
                "",
                f"- fanout_id: `{context.fanout_id}`",
                f"- stage_id: `{context.stage_id}`",
                f"- child_id: `{child.child_id}`",
                f"- run_id: `{run_id}`",
                f"- task_id: `{task_id}`",
                f"- pdd_id: `{pdd_id}`",
                f"- scope: `{task_scope}`",
                f"- workdir: `{workdir}`",
                f"- worker_branch: `{source_branch}`",
                f"- handoff_ref: `{self.config.runtime.git.task_ref_prefix}/{task_id}`",
                "",
                "Work only in the assigned workdir and branch.",
                "Do not update main, candidate refs, or .zf truth files directly.",
                "",
                *rework_section,
                *candidate_preflight_section,
                *self._skill_briefing_section(role, skill_entries),
                *workflow_input_lines,
                *controlled_input_lines,
                "Task instruction:",
                task_instruction or "Implement the assigned task scope inside allowed_paths.",
                "",
                "Compatibility task payload (contract fields are intentionally omitted):",
                "```json",
                json.dumps(display_task_payload, indent=2, ensure_ascii=False),
                "```",
                "",
                # U10(r6.1-F1 实弹:dev 有权限却自称无权 idle 等待)——
                # 权限面必须醒目且免歧义,先于 JSON 正文一句话拍死。
                "## ⚠️ YOUR WRITE PERMISSIONS (read this before assuming a blocker)",
                "",
                "You ARE allowed to create/edit EVERY path in `allowed_paths` below "
                "(including root-level entry files if listed) — but ONLY those exact "
                "paths: do NOT invent an alternative layout (e.g. `src/`) or write "
                "outside `allowed_paths` (rejected by verify/quality, blocked at write "
                "time). If a path you need is truly absent, emit completion with the blocker.",
                "",
                "Scope contract:",
                "```json",
                json.dumps(scope_contract, indent=2),
                "```",
                "",
                "## Completion discipline (candidate integration depends on it)",
                "1. COMMIT only this task's `files_touched` before emitting dev.build.done. "
                "Stage them with explicit pathspecs (`git add -- <path>...`); never use "
                "`git add -A`, `git add .`, or `git commit -a`. Materialized runtime files "
                "such as `.claude/` and `.zf-setup.done` are not task output. An uncommitted "
                "task file is rejected at integration (\"workdir has uncommitted\").",
                "2. The `source_commit` you report MUST be the current branch HEAD. Do NOT touch files "
                "or commit again after emitting dev.build.done — a later commit makes the reported "
                "source_commit stale (\"source_commit is not HEAD\") and the ref is rejected.",
                "3. Stay strictly inside `allowed_paths`. Do NOT create or edit files another slice "
                "owns — overlapping a sibling's paths is rejected (\"changes outside contract scope\") "
                "and conflicts at cherry-pick integration.",
                "4. Identity fields (`fanout_id`/`run_id`/`child_id`) are kernel audit fields, "
                "pre-filled by this command — you never need to manage or update them. If you "
                "re-emit after a re-dispatch, the kernel may adopt it only when the canonical "
                "contract revision and task-map generation are unchanged. A stale contract is "
                "superseded and cannot advance the current child.",
                "5. Fill `impl_self_check` after running each declared command. Replace every "
                "placeholder with the current HEAD, exact command receipt evidence, and one "
                "result for every mandatory AC. Do not claim that a command or AC passed "
                "without a durable artifact/event ref.",
                "",
                "When finished, update `<HEAD commit>` and `files_touched`, then emit dev.build.done with the runtime state dir explicitly:",
                "```bash",
                completion_command,
                "```",
                "",
                "If the contract cannot be satisfied inside `allowed_paths`, do not "
                "search runtime source or emit success. Fill the blocker evidence, "
                "upstream `blocker_task_ids`, and `required_paths`, then emit:",
                "```bash",
                blocked_command,
                "```",
                "",
            ])
        write_briefing_with_metrics(
            path, briefing_text, state_dir=self.state_dir,
            stage=str(context.stage_id or ""),
            role=role.instance_id, payload=task_item,
            indexed_skills=list(role.skills),
            auto_injected_skills=list(skill_entries or []),
        )
        return path
    def _write_fanout_retry_briefing(
        self,
        *,
        role: RoleConfig,
        manifest: dict,
        child: dict,
        run_id: str,
    ) -> Path:
        import json
        import shlex

        briefings_dir = self.state_dir / "briefings"
        briefings_dir.mkdir(parents=True, exist_ok=True)
        fanout_id = str(manifest.get("fanout_id") or "")
        child_id = str(child.get("child_id") or "")
        path = briefings_dir / f"{role.instance_id}-{fanout_id}-{child_id}-retry.md"
        aggregate_config = (
            manifest.get("aggregate_config")
            if isinstance(manifest.get("aggregate_config"), dict)
            else {}
        )
        child_payload = (
            child.get("payload")
            if isinstance(child.get("payload"), dict)
            else {}
        )
        child_success_event, child_failure_event = self._fanout_child_result_events(
            aggregate_config,
        )
        success_payload = {
            "fanout_id": fanout_id,
            "stage_id": str(manifest.get("stage_id") or ""),
            "child_id": child_id,
            "run_id": run_id,
            "role_instance": role.instance_id,
            "status": "completed",
            "report": {
                "child_id": child_id,
                "status": "passed",
                "summary": "Short retry outcome summary.",
                "findings": [],
                "recommendation": "approve",
            },
        }
        failure_payload = {
            **success_payload,
            "status": "failed",
            "reason": "Retry could not complete the assigned child scope.",
            "report": {
                "child_id": child_id,
                "status": "failed",
                "summary": "Short retry failure summary.",
                "findings": [],
                "recommendation": "reject",
            },
        }

        def _emit_command(event_type: str, payload: dict) -> str:
            if not event_type:
                return "# no event configured"
            cli_parts = shlex.split(zf_cli_cmd()) or ["zf"]
            return " ".join([
                *[shlex.quote(part) for part in cli_parts],
                "emit",
                shlex.quote(event_type),
                "--actor",
                shlex.quote(role.instance_id),
                "--state-dir",
                shlex.quote(str(self.state_dir)),
                "--payload",
                shlex.quote(json.dumps(payload, ensure_ascii=False)),
            ])

        lines = [
            f"# Fanout Retry: {child_id}",
            "",
            f"- fanout_id: `{fanout_id}`",
            f"- stage_id: `{manifest.get('stage_id', '')}`",
            f"- child_id: `{child_id}`",
            f"- run_id: `{run_id}`",
            f"- target_ref: `{manifest.get('target_ref', '')}`",
            "",
            "This is a retry of the same fanout child. Keep the same child_id and use the new run_id.",
        ]
        if manifest.get("topology") == "fanout_writer_scoped":
            lines.extend([
                f"- task_id: `{child.get('task_id', '')}`",
                f"- workdir: `{child.get('workdir', '')}`",
                f"- worker_branch: `{child.get('source_branch', '')}`",
                "",
                f"Emit dev.build.done with `--state-dir {self.state_dir}` and the same fanout_id and child_id when finished.",
            ])
        else:
            lines.extend([
                "",
                "Child payload:",
                "```json",
                json.dumps(child_payload, indent=2, ensure_ascii=False),
                "```",
                "",
                "Aggregate contract:",
                "```json",
                json.dumps(aggregate_config, indent=2, ensure_ascii=False),
                "```",
                "",
                "Success command:",
                "```bash",
                _emit_command(child_success_event, success_payload),
                "```",
                "",
                "Failure command:",
                "```bash",
                _emit_command(child_failure_event, failure_payload),
                "```",
                "",
                "Do not emit the aggregate success/failure event directly; the kernel publishes it after the fanout barrier or synth role finishes.",
                "Emit-once protocol: the result event is consumed asynchronously — you will",
                "NOT receive an acknowledgement. Emitting succeeds when the command exits 0.",
                "NEVER re-emit the same completion (no retry loops, no periodic re-sends):",
                "if this fanout generation was superseded, every duplicate is marked",
                "stale_completion and discarded, and re-sending floods the event log",
                "(r10 forensics: one lane re-emitting every ~7s produced 4.5k junk rows).",
                "After emitting once, stop and wait for new instructions.",
                "`fanout_id`, `stage_id`, `child_id`, `run_id`, `role_instance`, and `status` must stay as top-level payload fields.",
                "Finding schema: use `severity` = info|low|medium|high|critical, `path`, `message`, and optional integer `line`.",
            ])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
    def _write_fanout_synth_briefing(
        self,
        *,
        role: RoleConfig,
        manifest: dict,
        run_id: str,
        skill_entries: list | None = None,
        call_payload: dict[str, Any] | None = None,
    ) -> Path:
        import json
        import shlex

        fanout_id = str(manifest.get("fanout_id") or "")
        call_payload = dict(call_payload or {})
        reports = self._fanout_reports(manifest)
        from zf.runtime.fanout_briefing_scope import (
            render_fanout_scope_briefing_lines,
        )

        current_status = None
        try:
            from zf.runtime.fanout_identity import fanout_current_status

            current_status = fanout_current_status(
                self.event_log.read_all(),
                fanout_id,
            )
        except Exception:
            current_status = None
        scope_summary_lines = render_fanout_scope_briefing_lines(
            manifest,
            reports,
            current_status=current_status,
        )
        from zf.core.workflow.runner_policy import pure_aggregator_policy_plan

        runner_policy = pure_aggregator_policy_plan(self.config, role)
        runner_policy_lines: list[str] = []
        if runner_policy.get("applies"):
            runner_policy_lines = [
                "## Runner Policy",
                "",
                f"- policy_id: `{runner_policy.get('policy_id', '')}`",
                "- pure_aggregator: true",
                "- permitted_inputs: child reports, fanout manifest fields, "
                "event refs, and artifact refs listed in this briefing",
                "- project_source_write: prohibited",
                "- source_fact_creation: prohibited; do not inspect project "
                "source to invent facts for missing children",
            ]
            if runner_policy.get("applied"):
                runner_policy_lines.extend([
                    "- runner_permissions_narrowed: true",
                    "```json",
                    json.dumps(
                        runner_policy.get("effective", {}),
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "```",
                ])
            runner_policy_lines.append("")
        aggregate_config = (
            manifest.get("aggregate_config")
            if isinstance(manifest.get("aggregate_config"), dict)
            else {}
        )
        success_event = str(aggregate_config.get("success_event") or "")
        is_refactor_plan = success_event in {
            "zaofu.refactor.plan.ready",
            "refactor.plan.ready",
        }
        is_plan_artifact_stage = self._is_plan_artifact_stage(
            role=role,
            stage_id=str(manifest.get("stage_id") or ""),
            success_event=success_event,
            child_success_event="fanout.synth.completed",
        )
        briefings_dir = self.state_dir / "briefings"
        briefings_dir.mkdir(parents=True, exist_ok=True)
        path = briefings_dir / f"{role.instance_id}-{fanout_id}-synth.md"
        completion_payload = {
            "fanout_id": fanout_id,
            "stage_id": str(manifest.get("stage_id", "")),
            "child_id": "synth",
            "run_id": run_id,
            "role_instance": role.instance_id,
            "status": "completed",
            "recommendation": "approve",
            "summary": "Short synthesis summary.",
            "report": {
                "child_id": "synth",
                "status": "passed",
                "summary": "Short synthesis summary.",
                "findings": [],
                "recommendation": "approve",
            },
        }
        completion_payload.update({
            key: call_payload[key]
            for key in PLAN_SYNTH_HANDOFF_KEYS
            if key in call_payload
        })
        if is_refactor_plan:
            trigger_payload = (
                manifest.get("trigger_payload")
                if isinstance(manifest.get("trigger_payload"), dict)
                else {}
            )
            review_artifact_ref = str(
                trigger_payload.get("review_artifact_ref")
                or "Path to the review artifact used."
            )
            plan_intent = str(trigger_payload.get("plan_intent") or "")
            safe_fanout_id = "".join(
                ch if ch.isalnum() or ch in "._-" else "-"
                for ch in fanout_id
            )
            artifact_dir = self.state_dir / "artifacts" / (safe_fanout_id or "fanout")
            plan_ref = str(artifact_dir / "refactor-plan.md")
            task_map_ref = str(artifact_dir / "task_map.json")
            risk_register_ref = str(artifact_dir / "risk-register.json")
            backlog_candidates_ref = str(artifact_dir / "backlog-candidates.json")
            scan_quality_audit_ref = str(artifact_dir / "scan-quality-audit.json")
            artifact_refs = [
                plan_ref,
                task_map_ref,
                risk_register_ref,
                backlog_candidates_ref,
                scan_quality_audit_ref,
            ]
            completion_payload.update({
                "artifact_refs": artifact_refs,
                "artifact_digests": {},
                "plan_artifact_ref": plan_ref,
                "task_map_ref": task_map_ref,
                "risk_register_ref": risk_register_ref,
                "backlog_candidates_ref": backlog_candidates_ref,
                "scan_quality_audit_ref": scan_quality_audit_ref,
                "evidence_refs": [],
            })
            completion_payload["report"].update({
                "review_artifact_ref": review_artifact_ref,
                "plan_intent": plan_intent,
                "plan_artifact_ref": plan_ref,
                "task_map_ref": task_map_ref,
                "risk_register_ref": risk_register_ref,
                "backlog_candidates_ref": backlog_candidates_ref,
                "scan_quality_audit_ref": scan_quality_audit_ref,
                "refactor_plan_md": "## Refactor Plan\n\nReplace with the final plan.",
                "task_map": {"tasks": []},
                "gates": [],
                "risk_register": [],
                "backlog_candidates": [],
                "artifact_refs": artifact_refs,
                "evidence_refs": [],
            })
        elif is_plan_artifact_stage:
            plan_ref = f"docs/plans/{manifest.get('stage_id', '')}-synth-plan.md"
            completion_payload.update({
                "plan_artifact_ref": plan_ref,
                "artifact_refs": [plan_ref],
                "evidence_refs": [],
            })
            completion_payload["report"].update({
                "plan_artifact_ref": plan_ref,
                "plan_md": "## Plan\n\nReplace with the durable plan artifact content.",
                "task_map_ref": "",
                "backlog_ref": "",
                "source_index_ref": "",
                "evidence_refs": [],
            })
        if call_payload:
            from zf.runtime.plan_synth_handoff import (
                render_plan_synth_completion_command,
            )

            completion_command = render_plan_synth_completion_command(
                cli_command=zf_cli_cmd(),
                actor=role.instance_id,
                state_dir=self.state_dir,
                payload=completion_payload,
            )
        else:
            completion_command = " ".join([
                *[shlex.quote(part) for part in shlex.split(zf_cli_cmd()) or ["zf"]],
                "emit",
                "fanout.synth.completed",
                "--actor",
                shlex.quote(role.instance_id),
                "--state-dir",
                shlex.quote(str(self.state_dir)),
                "--payload",
                shlex.quote(json.dumps(completion_payload, ensure_ascii=False)),
            ])
        if call_payload:
            from zf.runtime.artifact_read_ledger import render_attempt_source_briefing

            source_lines = render_attempt_source_briefing(
                call_payload,
                state_dir=self.state_dir,
            ).rstrip().splitlines()
            child_report_lines = [
                "Child report bodies are canonical required inputs in the Source Manifest.",
                "Read every required child result with `zf artifact read`; do not rely on copied inline JSON.",
                "",
            ]
        else:
            source_lines = []
            child_report_lines = [
                "Child reports:",
                "```json",
                json.dumps([report.get("report", {}) for report in reports], indent=2),
                "```",
                "",
            ]
        briefing_text = "\n".join([
                f"# Fanout Synth: {fanout_id}",
                "",
                f"- fanout_id: `{fanout_id}`",
                f"- stage_id: `{manifest.get('stage_id', '')}`",
                f"- run_id: `{run_id}`",
                f"- target_ref: `{manifest.get('target_ref', '')}`",
                "",
                "Synthesize the child reports. Do not modify project source files.",
                "Do not emit the configured success or failure event directly.",
                "",
                *runner_policy_lines,
                *scope_summary_lines,
                *source_lines,
                *( [""] if source_lines else [] ),
                *self._skill_briefing_section(role, skill_entries),
                *(
                    [
                        "## Plan Artifact Contract",
                        "",
                        *self._plan_artifact_contract_lines(),
                        "",
                    ]
                    if is_plan_artifact_stage
                    else []
                ),
                "Child report paths:",
                *[
                    f"- `{report.get('report_path', '')}`"
                    for report in reports
                    if report.get("report_path")
                ],
                "",
                *child_report_lines,
                "Admitted call-result envelopes (selected protocol only):",
                "```json",
                json.dumps([
                    {
                        "child_id": report.get("child_id", ""),
                        "operation_id": report.get("operation_id", ""),
                        "request_hash": report.get("request_hash", ""),
                        "envelope_ref": report.get("admitted_call_result_ref", {}),
                        "control_result": report.get("control_result", {}),
                    }
                    for report in reports
                    if report.get("admitted_call_result_ref")
                ], indent=2, ensure_ascii=False),
                "```",
                "",
                "When finished, emit exactly one fanout.synth.completed event with the runtime state dir explicitly:",
                "```bash",
                completion_command,
                "```",
                "",
            ])
        write_briefing_with_metrics(
            path, briefing_text, state_dir=self.state_dir,
            stage=str(manifest.get("stage_id") or ""),
            role=role.instance_id, payload={**manifest, **call_payload},
            indexed_skills=list(role.skills),
            auto_injected_skills=list(skill_entries or []),
        )
        return path
    def _write_fanout_child_output(
        self,
        fanout_id: str,
        child_id: str,
        event: ZfEvent,
        *,
        report: dict | None = None,
        diagnostics: list[str] | None = None,
    ) -> dict[str, str]:
        paths: dict[str, str] = {}
        try:
            from zf.core.state.atomic_io import atomic_write_text
            from zf.core.safety import PathGuard

            child_dir = (
                self.state_dir
                / "fanouts"
                / fanout_id
                / "children"
                / child_id
            )
            PathGuard.assert_under(
                child_dir,
                self.state_dir / "fanouts" / fanout_id / "children",
            )
            path = child_dir / "result.json"
            atomic_write_text(path, event.to_json() + "\n")
            paths["result_path"] = str(path)
            if report is not None:
                import json

                report_path = child_dir / "report.json"
                atomic_write_text(
                    report_path,
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                )
                paths["report_path"] = str(report_path)
            if diagnostics:
                import json

                diagnostics_path = child_dir / "report-diagnostics.json"
                atomic_write_text(
                    diagnostics_path,
                    json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n",
                )
                paths["report_diagnostics_path"] = str(diagnostics_path)
        except Exception:
            pass
        return paths
