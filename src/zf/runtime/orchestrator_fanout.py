"""FanoutCoordinationMixin — reader/writer fanout, synth and child
coordination (49 methods moved verbatim from orchestrator.py, P3).

Same shape as the other Orchestrator mixins: methods share the
Orchestrator instance state via self; composed in orchestrator.py.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.runtime.channel_workflow_bridge import emit_fanout_channel_state_update
from zf.runtime.fanout_stage_criteria import evaluate_fanout_stage_success_criteria_for_orchestrator
from zf.runtime.injection import build_task_prompt
from zf.runtime.workflow_inputs import render_workflow_input_briefing_section
from zf.runtime.writer_fanout_data import _FANOUT_AFFINITY_METADATA_KEYS

# B3: when a reject-on-fail aggregate already has a failed child the
# verdict cannot flip — cap the synth wait at this budget instead of
# the full stage timeout (R25: 40min idle on a decided round).
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


def _contract_handoff_ref_fields(config, success_event: str) -> list[str]:
    dag = getattr(getattr(config, "workflow", None), "dag", None)
    schemas = getattr(dag, "event_schemas", {}) or {}
    rule = schemas.get(success_event)
    required = rule.get("required", []) if isinstance(rule, dict) else []
    fields = [f for f in required if f in _HANDOFF_REF_FIELDS]
    if fields:
        return fields
    return list(_PRD_STAGE_LOOSE_FALLBACK.get(success_event, ()))

class FanoutCoordinationMixin:
    def _recover_unrecorded_writer_fanout_results(self) -> None:
        try:
            from zf.runtime.event_window import read_runtime_events

            events = read_runtime_events(self.event_log, self.state_dir)
        except Exception:
            return
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
        self._recover_incomplete_writer_fanout_aggregates(events)
    def _recover_pending_writer_fanout_dispatches(
        self,
        events: list[ZfEvent],
    ) -> bool:
        """Dispatch writer-fanout children that were deferred before send_task.

        Dead/shell workers are recorded as ``fanout.child.dispatch_deferred`` so
        the child remains pending instead of becoming a business failure. After
        the worker respawns, this restart-safe sweep reuses the canonical writer
        dispatch path to send the original child briefing.
        """
        from zf.runtime.fanout import FanoutChild, FanoutContext

        terminal_statuses = {"completed", "failed", "timed_out", "cancelled"}
        active_children: set[tuple[str, str]] = set()
        events_by_id = {event.id: event for event in events}
        for event in events:
            if event.type not in {
                "fanout.child.dispatched",
                "fanout.child.completed",
                "fanout.child.failed",
            }:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            fanout_id = str(payload.get("fanout_id") or "")
            child_id = str(payload.get("child_id") or "")
            if fanout_id and child_id:
                active_children.add((fanout_id, child_id))

        recovered = False
        fanout_root = self.state_dir / "fanouts"
        if not fanout_root.exists():
            return False
        for manifest_path in fanout_root.glob("*/manifest.json"):
            fanout_id = manifest_path.parent.name
            manifest = self._fanout_manifest(fanout_id)
            if not manifest or manifest.get("topology") != "fanout_writer_scoped":
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
            stage_id = str(manifest.get("stage_id") or "")
            if self._fanout_stage_by_id(stage_id) is None:
                continue
            context = FanoutContext(
                fanout_id=fanout_id,
                stage_id=stage_id,
                topology=str(manifest.get("topology") or "fanout_writer_scoped"),
                trace_id=str(manifest.get("trace_id") or ""),
                trigger_event_id=str(manifest.get("trigger_event_id") or ""),
                target_ref=str(
                    manifest.get("target_ref")
                    or self.config.runtime.git.candidate_base_ref
                ),
                expected_children=[],
            )
            trigger_event = events_by_id.get(str(manifest.get("trigger_event_id") or ""))
            trigger_payload = (
                trigger_event.payload
                if trigger_event is not None and isinstance(trigger_event.payload, dict)
                else {}
            )
            rework_feedback = [
                str(item)
                for item in (trigger_payload.get("rework_feedback") or [])
                if str(item).strip()
            ]
            try:
                rework_attempt = int(trigger_payload.get("rework_attempt") or 0)
            except (TypeError, ValueError):
                rework_attempt = 0
            rework_summary = (
                trigger_payload.get("rework_summary")
                if isinstance(trigger_payload.get("rework_summary"), dict)
                else {}
            )
            for raw_child in manifest.get("children", []) or []:
                if not isinstance(raw_child, dict):
                    continue
                child_id = str(raw_child.get("child_id") or "")
                if not child_id or (fanout_id, child_id) in active_children:
                    continue
                status = str(raw_child.get("status") or "")
                if status not in {"", "pending"}:
                    continue
                role_instance = str(raw_child.get("role_instance") or "")
                task_id = str(raw_child.get("task_id") or "")
                if not role_instance or not task_id:
                    continue
                role = next(iter(self._fanout_roles([role_instance])), None)
                if role is None:
                    continue
                task_item = (
                    dict(raw_child.get("payload"))
                    if isinstance(raw_child.get("payload"), dict)
                    else {}
                )
                task_item.setdefault("task_id", task_id)
                for key in (
                    "scope",
                    "pdd_id",
                    "feature_id",
                    "task_map_ref",
                    "source_index_ref",
                    *_FANOUT_AFFINITY_METADATA_KEYS,
                ):
                    value = raw_child.get(key)
                    if value not in (None, ""):
                        task_item.setdefault(key, value)
                task_item.setdefault("role_instance", role.instance_id)
                child = FanoutChild(
                    child_id=child_id,
                    role_instance=role.instance_id,
                    target_ref=str(raw_child.get("target_ref") or context.target_ref),
                    payload=task_item,
                )
                before = len(self.event_log.read_all())
                self._dispatch_writer_fanout_child(
                    context=context,
                    child=child,
                    task_item=task_item,
                    role=role,
                    pdd_id=str(
                        raw_child.get("pdd_id")
                        or manifest.get("pdd_id")
                        or manifest.get("feature_id")
                        or "default"
                    ),
                    feature_id=str(
                        raw_child.get("feature_id")
                        or manifest.get("feature_id")
                        or raw_child.get("pdd_id")
                        or manifest.get("pdd_id")
                        or "default"
                    ),
                    task_map_ref=str(
                        raw_child.get("task_map_ref")
                        or manifest.get("task_map_ref")
                        or ""
                    ),
                    source_index_ref=str(
                        raw_child.get("source_index_ref")
                        or manifest.get("source_index_ref")
                        or ""
                    ),
                    wave=self._fanout_child_wave(raw_child),
                    causation_id=str(raw_child.get("last_event_id") or "")
                    or str(manifest.get("trigger_event_id") or ""),
                    rework_feedback=rework_feedback,
                    rework_attempt=rework_attempt,
                    rework_summary=rework_summary,
                )
                recovered = recovered or len(self.event_log.read_all()) > before
        return recovered
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
    def _recover_writer_fanout_task_bindings(self) -> None:
        """Re-project active writer fanout dispatches into canonical tasks."""
        try:
            events = self.event_log.read_all()
        except Exception:
            events = []
        self._cancel_orphan_active_fanout_manifests(events)
        fanout_root = self.state_dir / "fanouts"
        if not fanout_root.exists():
            return
        terminal_statuses = {"completed", "failed", "timed_out", "cancelled"}
        for manifest_path in fanout_root.glob("*/manifest.json"):
            fanout_id = manifest_path.parent.name
            manifest = self._fanout_manifest(fanout_id)
            if not manifest or manifest.get("topology") != "fanout_writer_scoped":
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
            stale_reason, superseded_by = self._fanout_identity_stale_reason(fanout_id)
            if stale_reason:
                self._cancel_superseded_writer_fanout_manifest(
                    fanout_id=fanout_id,
                    manifest=manifest,
                    reason=stale_reason,
                    superseded_by=superseded_by,
                )
                continue
            for child in manifest.get("children", []) or []:
                if not isinstance(child, dict):
                    continue
                if str(child.get("status") or "") != "dispatched":
                    continue
                task_id = str(child.get("task_id") or "")
                role_instance = str(child.get("role_instance") or "")
                run_id = str(child.get("run_id") or "")
                if not task_id or not role_instance or not run_id:
                    continue
                task = self.task_store.get(task_id)
                if task is None or task.status in {"done", "cancelled", "blocked"}:
                    continue
                if (
                    task.assigned_to == role_instance
                    and task.active_dispatch_id == run_id
                    and task.status == "in_progress"
                ):
                    continue
                if not self._claim_writer_fanout_task(
                    task_id,
                    role_instance,
                    run_id=run_id,
                ):
                    continue
                self.event_writer.append(ZfEvent(
                    type="task.dispatch_context.bound",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "dispatch_id": run_id,
                        "role_instance": role_instance,
                        "fanout_id": fanout_id,
                        "child_id": str(child.get("child_id") or ""),
                        "source": "writer_fanout_task_binding_recovery",
                    },
                    causation_id=str(child.get("last_event_id") or "") or None,
                    correlation_id=str(manifest.get("trace_id") or "") or None,
                ))

    def _cancel_superseded_writer_fanout_manifest(
        self,
        *,
        fanout_id: str,
        manifest: dict,
        reason: str,
        superseded_by: str,
    ) -> None:
        if not fanout_id:
            return
        try:
            events = self.event_log.read_all()
        except Exception:
            events = []
        for event in reversed(events):
            if event.type != "fanout.cancelled":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("fanout_id") or "") == fanout_id:
                return
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
                "task_map_ref": str(manifest.get("task_map_ref") or ""),
                "reason": reason,
                "superseded_by": superseded_by,
                "source": "superseded_writer_fanout_manifest_closeout",
            },
            correlation_id=str(manifest.get("trace_id") or "") or None,
        ))

    def _cancel_orphan_active_fanout_manifests(
        self,
        events: list[ZfEvent],
    ) -> bool:
        """Fail-closed active fanout manifests that have no started event.

        ``events.jsonl`` is the runtime truth. A crash between manifest
        materialization and ``fanout.started`` append can leave a manifest at
        ``status=started`` forever. Recovery sweeps must not keep binding or
        redispatching those half-written projections.
        """
        fanout_root = self.state_dir / "fanouts"
        if not fanout_root.exists():
            return False
        terminal_statuses = {"completed", "failed", "timed_out", "cancelled"}
        started_ids: set[str] = set()
        terminal_ids: set[str] = set()
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            fanout_id = str(payload.get("fanout_id") or "")
            if not fanout_id:
                continue
            if event.type == "fanout.started":
                started_ids.add(fanout_id)
            elif event.type in {
                "fanout.cancelled",
                "fanout.timed_out",
                "fanout.aggregate.completed",
            }:
                terminal_ids.add(fanout_id)

        recovered = False
        for manifest_path in fanout_root.glob("*/manifest.json"):
            fanout_id = manifest_path.parent.name
            if fanout_id in started_ids or fanout_id in terminal_ids:
                continue
            manifest = self._fanout_manifest(fanout_id)
            if not manifest:
                continue
            topology = str(manifest.get("topology") or "")
            if topology not in {"fanout_writer_scoped", "fanout_reader"}:
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
                    "task_map_ref": str(manifest.get("task_map_ref") or ""),
                    "reason": "fanout_manifest_without_started_event",
                    "source": "orphan_active_fanout_manifest_recovery",
                },
                correlation_id=str(manifest.get("trace_id") or "") or None,
            ))
            recovered = True
        return recovered
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

    def _fanout_dispatch_deferred_recently(
        self,
        *,
        fanout_id: str,
        child_id: str,
        role_instance: str,
        window_s: float = 60.0,
    ) -> bool:
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        now = self._now()
        for event in reversed(events):
            if event.type != "fanout.child.dispatch_deferred":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if (
                str(payload.get("fanout_id") or "") != fanout_id
                or str(payload.get("child_id") or "") != child_id
                or str(payload.get("role_instance") or "") != role_instance
            ):
                continue
            try:
                return now - self._event_epoch(event) < window_s
            except Exception:
                return True
        return False

    def _ensure_fanout_role_dispatchable(
        self,
        *,
        role: RoleConfig,
        fanout_id: str,
        stage_id: str,
        child_id: str,
        run_id: str,
        trace_id: str,
        causation_id: str | None = None,
        prompt_kind: str = "fanout_child",
    ) -> bool:
        """Return True when a fanout role can receive a prompt now.

        Fanout paths historically bypassed normal task dispatch's worker
        availability guard. If a pane had fallen back to a shell, send_task
        failed and the child was marked as a business failure. Treat that as
        infrastructure: respawn/defer now, and let the next run_once dispatch
        the still-pending child.
        """
        state = getattr(self, "_last_worker_state", {}).get(role.instance_id, "idle")
        dispatchable = True
        try:
            dispatchable = bool(self._worker_dispatchable(role.instance_id))
        except Exception:
            dispatchable = True
        alive = True
        alive_error = ""
        try:
            alive = bool(self.transport.is_alive(role.instance_id))
        except Exception as exc:  # noqa: BLE001 - transport health is best effort
            alive = False
            alive_error = str(exc)
        if alive and dispatchable:
            return True

        if self._fanout_dispatch_deferred_recently(
            fanout_id=fanout_id,
            child_id=child_id,
            role_instance=role.instance_id,
        ):
            return False

        reason_parts: list[str] = []
        if not alive:
            reason_parts.append("worker_transport_not_alive")
        if alive_error:
            reason_parts.append(alive_error)
        if not dispatchable:
            reason_parts.append(f"worker_state_not_dispatchable:{state}")
        reason = "; ".join(reason_parts) or "worker_not_dispatchable"

        respawn_action = ""
        respawn_reason = ""
        if not alive and state != "respawning":
            try:
                decision = self._respawn_instance(role)
                respawn_action = str(getattr(decision, "action", "") or "")
                respawn_reason = str(getattr(decision, "reason", "") or "")
            except Exception as exc:  # noqa: BLE001
                respawn_action = "respawn_exception"
                respawn_reason = str(exc)

        self.event_writer.append(ZfEvent(
            type="fanout.child.dispatch_deferred",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "child_id": child_id,
                "run_id": run_id,
                "role_instance": role.instance_id,
                "prompt_kind": prompt_kind,
                "reason": reason,
                "worker_state": state,
                "transport_alive": alive,
                "dispatchable": dispatchable,
                "respawn_action": respawn_action,
                "respawn_reason": respawn_reason,
            },
            causation_id=causation_id,
            correlation_id=trace_id,
        ))
        return False

    def _dispatch_reader_fanout_child(
        self,
        *,
        context,
        child,
        role: RoleConfig,
        aggregate,
        causation_id: str,
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
            self._checkout_fanout_reader(role, child.target_ref or context.target_ref)
            skill_entries = self._record_skill_provenance(role=role)
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
            dispatched = context.child_dispatched_event(child, run_id=run_id)
            dispatched.payload["skills"] = list(role.skills)
            dispatched.payload["briefing_path"] = str(briefing_path)
            if child.payload:
                dispatched.payload["payload"] = dict(child.payload)
                self._copy_fanout_assignment_metadata(
                    dispatched.payload,
                    child.payload,
                )
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

    def _recover_pending_reader_fanout_dispatches(
        self,
        events: list[ZfEvent],
    ) -> bool:
        from zf.runtime.fanout import FanoutChild, FanoutContext

        terminal_statuses = {"completed", "failed", "timed_out", "cancelled"}
        active_children: set[tuple[str, str]] = set()
        for event in events:
            if event.type not in {
                "fanout.child.dispatched",
                "fanout.child.completed",
                "fanout.child.failed",
            }:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            fanout_id = str(payload.get("fanout_id") or "")
            child_id = str(payload.get("child_id") or "")
            if fanout_id and child_id:
                active_children.add((fanout_id, child_id))

        recovered = False
        fanout_root = self.state_dir / "fanouts"
        if not fanout_root.exists():
            return False
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
                str(manifest.get("status") or "") in terminal_statuses
                or str(aggregate.get("status") or "") in terminal_statuses
            ):
                continue
            stage_id = str(manifest.get("stage_id") or "")
            stage = self._fanout_stage_by_id(stage_id)
            if stage is None:
                continue
            context = FanoutContext(
                fanout_id=fanout_id,
                stage_id=stage_id,
                topology=str(manifest.get("topology") or "fanout_reader"),
                trace_id=str(manifest.get("trace_id") or ""),
                trigger_event_id=str(manifest.get("trigger_event_id") or ""),
                target_ref=str(manifest.get("target_ref") or ""),
                expected_children=[],
            )
            for raw_child in manifest.get("children", []) or []:
                if not isinstance(raw_child, dict):
                    continue
                child_id = str(raw_child.get("child_id") or "")
                if not child_id or (fanout_id, child_id) in active_children:
                    continue
                status = str(raw_child.get("status") or "")
                if status not in {"", "pending", "queued"}:
                    continue
                role_instance = str(raw_child.get("role_instance") or "")
                role = next(iter(self._fanout_roles([role_instance])), None)
                if role is None:
                    continue
                child = FanoutChild(
                    child_id=child_id,
                    role_instance=role.instance_id,
                    target_ref=str(raw_child.get("target_ref") or context.target_ref),
                    payload=(
                        dict(raw_child.get("payload"))
                        if isinstance(raw_child.get("payload"), dict)
                        else {}
                    ),
                )
                before = len(self.event_log.read_all())
                self._dispatch_reader_fanout_child(
                    context=context,
                    child=child,
                    role=role,
                    aggregate=stage.aggregate,
                    causation_id=str(manifest.get("trigger_event_id") or ""),
                )
                recovered = recovered or len(self.event_log.read_all()) > before
        return recovered

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
                if self._reader_role_has_activity_after_dispatch(
                    events,
                    role_instance,
                    latest_index,
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
        if getattr(event, "type", "") != "workflow.invoke.requested":
            return True
        payload = event.payload if isinstance(event.payload, dict) else {}
        pattern_id = str(
            payload.get("pattern_id")
            or payload.get("stage_id")
            or ""
        ).strip()
        if not pattern_id:
            return False
        return pattern_id == str(getattr(stage, "id", "") or "")

    def _maybe_start_reader_fanout(self, event: ZfEvent) -> None:
        stages = [
            stage for stage in getattr(self.config.workflow, "stages", [])
            if stage.topology == "fanout_reader" and stage.trigger == event.type
            and self._fanout_stage_matches_trigger_event(stage, event)
        ]
        if not stages:
            return
        from zf.runtime.fanout import FanoutChild, FanoutContext

        for stage in stages:
            if self._fanout_started(stage.id, event.id):
                continue
            trace_id = event.correlation_id or (
                event.payload.get("trace_id", "") if isinstance(event.payload, dict) else ""
            ) or event.id
            target_ref = self._render_fanout_target(stage.target_ref, event)
            trigger_payload = event.payload if isinstance(event.payload, dict) else {}
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
            if self._fanout_assignment_strategy(stage) == "affinity_stage_slots":
                base_context = FanoutContext.create(
                    stage_id=stage.id,
                    topology=stage.topology,
                    trace_id=trace_id,
                    trigger_event_id=event.id,
                    target_ref=target_ref,
                    role_instances=[],
                )
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
                stage_slot = str(
                    getattr(getattr(stage, "assignment", None), "stage_slot", "")
                    or ""
                )
                for upstream_child in upstream_manifest.get("children", []) or []:
                    if not isinstance(upstream_child, dict):
                        continue
                    if str(upstream_child.get("status") or "") != "completed":
                        continue
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
            role_by_instance = {role.instance_id: role for role in roles}
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
                )
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
                self.event_writer.append(ZfEvent(
                    type="fanout.cancelled",
                    actor="zf-cli",
                    payload={
                        **base_payload,
                        "reason": "source_index_gap",
                        "missing_anchor_task_ids":
                            result.missing_anchor_task_ids,
                        "note": result.note,
                    },
                    causation_id=trigger_event.id,
                    correlation_id=trace_id,
                ))
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
            # B-93-03 (doc 93 §4): 投影 plan-digest 落 artifact,payload 带
            # digest_ref —— CLI/Web/Feishu 共用同一份人读摘要,不各自重算。
            task_items = list(getattr(loaded, "task_items", []) or [])
            digest_ref = self._write_plan_digest(plan_id, task_items, base_payload["task_map_ref"])
            self.event_writer.append(ZfEvent(
                type="plan.approval.requested",
                actor="zf-cli",
                payload={
                    **base_payload,
                    "task_count": len(task_items),
                    "digest_ref": digest_ref,
                },
                causation_id=plan_id,
                correlation_id=trace_id,
            ))
        return False

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
            admit_writer_fanout,
            load_writer_task_map,
        )

        for stage in stages:
            if self._fanout_started(stage.id, event.id):
                continue
            if self._equivalent_rework_fanout_started(stage.id, event):
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
                )
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
                            "wave": loaded.wave,
                            "task_ids": [
                                str(item.get("task_id") or "")
                                for item in loaded.task_items
                            ],
                            **admission.failure_payload(),
                        },
                        causation_id=event.id,
                        correlation_id=trace_id,
                    ))
                    continue
                task_items = admission.task_items
            except Exception as exc:
                self.event_writer.append(ZfEvent(
                    type="fanout.cancelled",
                    actor="zf-cli",
                    payload={
                        "stage_id": stage.id,
                        "trigger_event_id": event.id,
                        "trace_id": trace_id,
                        "pdd_id": pdd_id,
                        "task_map_ref": (
                            loaded.task_map_ref if loaded is not None else ""
                        ),
                        "reason": str(exc),
                    },
                    causation_id=event.id,
                    correlation_id=trace_id,
                ))
                continue
            if not task_items or not roles:
                continue

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

            base_context = FanoutContext.create(
                stage_id=stage.id,
                topology=stage.topology,
                trace_id=trace_id,
                trigger_event_id=event.id,
                target_ref=self.config.runtime.git.candidate_base_ref,
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
                for index, raw_task_item in enumerate(task_items):
                    if use_affinity:
                        if index < len(lane_roles):
                            lane_id, role = lane_roles[index]
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
                                target_ref=self.config.runtime.git.candidate_base_ref,
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
                                target_ref=self.config.runtime.git.candidate_base_ref,
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
                            target_ref=self.config.runtime.git.candidate_base_ref,
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
                )
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
    ) -> None:
        run_id = f"run-{context.fanout_id}-{child.child_id}"
        try:
            from zf.runtime.affinity_review_scope import affinity_scope_identity_errors
            from zf.runtime.workdirs import WorkdirManager

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
                return
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
            plan = WorkdirManager(
                state_dir=self.state_dir,
                project_root=self.project_root,
                config=self.config,
            ).prepare(role)
            skill_entries = self._record_skill_provenance(
                role=role,
                task_id=task_id,
            )
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
            })
            if wave is not None:
                dispatched.payload["wave"] = wave
            self._copy_fanout_assignment_metadata(dispatched.payload, task_item)
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
            )
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
            self._copy_fanout_assignment_metadata(payload, task_item)
            self.event_writer.append(ZfEvent(
                type="fanout.child.failed",
                actor="zf-cli",
                payload=payload,
                causation_id=causation_id,
                correlation_id=context.trace_id,
            ))
    def _park_writer_fanout_queued_task(
        self,
        *,
        task_id: str,
        fanout_id: str,
        child_id: str,
    ) -> None:
        if not task_id:
            return
        task = self.task_store.get(task_id)
        if task is None or task.status != "backlog":
            return
        if not self._move_task(
            task_id,
            "blocked",
            trigger_event="fanout.child.queued",
        ):
            return
        self.task_store.update(
            task_id,
            assigned_to="",
            blocked_reason=f"fanout_queue:{fanout_id}:{child_id}",
        )
    def _block_writer_fanout_tasks(
        self,
        *,
        task_items: list[dict],
        reason: str,
    ) -> None:
        for task_item in task_items:
            task_id = str(task_item.get("task_id") or "")
            if not task_id:
                continue
            task = self.task_store.get(task_id)
            if task is None or task.status != "backlog":
                continue
            if not self._move_task(
                task_id,
                "blocked",
                trigger_event="fanout.cancelled",
            ):
                continue
            self.task_store.update(
                task_id,
                assigned_to="",
                blocked_reason=f"fanout_affinity_cancelled:{reason}",
            )
    def _unpark_writer_fanout_queued_task(self, task_id: str) -> None:
        if not task_id:
            return
        task = self.task_store.get(task_id)
        if task is None:
            return
        if task.status != "blocked":
            return
        if not str(task.blocked_reason or "").startswith("fanout_queue:"):
            return
        if not self._move_task(
            task_id,
            "backlog",
            trigger_event="fanout.slot.assigned",
        ):
            return
        self.task_store.update(task_id, assigned_to="", blocked_reason="")
    def _claim_writer_fanout_task(
        self,
        task_id: str,
        role_instance: str,
        *,
        run_id: str = "",
    ) -> bool:
        """Reserve a seeded kanban task for a writer fanout child."""
        if not task_id:
            return False
        task = self.task_store.get(task_id)
        if task is None:
            return False
        if task.status == "backlog":
            if not self._move_task(
                task_id,
                "in_progress",
                trigger_event="fanout.child.dispatched",
            ):
                return False
        latest = self.task_store.get(task_id)
        if latest is None or latest.status != "in_progress":
            return False
        updates = {"assigned_to": role_instance}
        if run_id:
            updates["active_dispatch_id"] = run_id
        self.task_store.update(task_id, **updates)
        return True
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
        status = str(payload.get("status") or "")
        failed_result = (
            event.type in {child_failure_event, failure_event}
            or status in {"failed", "failure", "rejected"}
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
                run_id=run_id,
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
        if failed_result or not report_result.valid:
            reason = (
                "malformed_report"
                if not report_result.valid
                else str(payload.get("reason") or event.type)
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
            run_id=run_id,
        )
        self._evaluate_reader_fanout(fanout_id)
    def _fanout_result_payload(self, event: ZfEvent) -> dict:
        payload = event.payload if isinstance(event.payload, dict) else {}
        report = payload.get("report")
        if not isinstance(report, dict):
            promoted = dict(payload)
        else:
            promoted = dict(payload)
            for key in (
                "fanout_id",
                "stage_id",
                "child_id",
                "child_run",
                "run_id",
                "role_instance",
                "status",
                "reason",
                "summary",
                "recommendation",
            ):
                if (
                    promoted.get(key) in (None, "")
                    and report.get(key) not in (None, "")
                ):
                    promoted[key] = report.get(key)
        if promoted.get("fanout_id") and (
            promoted.get("child_id") or promoted.get("child_run")
        ):
            return promoted
        target = self._writer_fanout_completion_target(
            event=event,
            payload=promoted,
            current_fanout_id="",
            statuses={"dispatched", "failed", "completed"},
        )
        if target is None:
            return promoted
        manifest, child = target
        enriched = dict(promoted)
        enriched.setdefault("fanout_id", str(manifest.get("fanout_id") or ""))
        enriched.setdefault("stage_id", str(manifest.get("stage_id") or ""))
        enriched.setdefault("trace_id", str(manifest.get("trace_id") or ""))
        enriched.setdefault("child_id", str(child.get("child_id") or ""))
        enriched.setdefault("run_id", str(child.get("run_id") or ""))
        enriched.setdefault("role_instance", str(child.get("role_instance") or ""))
        enriched.setdefault("task_id", str(child.get("task_id") or ""))
        enriched.setdefault("task_map_ref", str(
            child.get("task_map_ref") or manifest.get("task_map_ref") or ""
        ))
        enriched.setdefault("source_index_ref", str(
            child.get("source_index_ref") or manifest.get("source_index_ref") or ""
        ))
        enriched.setdefault("pdd_id", str(manifest.get("pdd_id") or ""))
        enriched.setdefault("feature_id", str(manifest.get("feature_id") or ""))
        enriched.setdefault("scope", str(child.get("scope") or ""))
        enriched.setdefault("workdir", str(child.get("workdir") or ""))
        enriched.setdefault("source_branch", str(child.get("source_branch") or ""))
        return enriched
    def _writer_fanout_completion_target(
        self,
        *,
        event: ZfEvent,
        payload: dict,
        current_fanout_id: str,
        statuses: set[str],
    ) -> tuple[dict, dict] | None:
        if event.type not in {
            "dev.build.done",
            "dev.blocked",
            "dev.failed",
            "task.ref.updated",
        }:
            return None
        task_id = str(event.task_id or payload.get("task_id") or "").strip()
        if not task_id:
            return None
        event_task_map_ref = str(payload.get("task_map_ref") or "").strip()
        role_instance = str(
            payload.get("role_instance")
            or (
                payload.get("actor")
                if event.type == "task.ref.updated"
                else None
            )
            or event.actor
            or ""
        ).strip()
        root = self.state_dir / "fanouts"
        if not root.exists():
            return None
        candidates: list[tuple[float, dict, dict]] = []
        for manifest_path in root.glob("*/manifest.json"):
            fanout_id = manifest_path.parent.name
            manifest = self._fanout_manifest(fanout_id)
            if not manifest or manifest.get("topology") != "fanout_writer_scoped":
                continue
            for child in manifest.get("children", []) or []:
                if not isinstance(child, dict):
                    continue
                if str(child.get("task_id") or "") != task_id:
                    continue
                status = str(child.get("status") or "")
                if status not in statuses:
                    continue
                child_role = str(child.get("role_instance") or "")
                if role_instance and child_role and role_instance != child_role:
                    continue
                child_task_map_ref = str(
                    child.get("task_map_ref")
                    or manifest.get("task_map_ref")
                    or ""
                ).strip()
                if (
                    event_task_map_ref
                    and child_task_map_ref
                    and event_task_map_ref != child_task_map_ref
                ):
                    continue
                try:
                    mtime = manifest_path.stat().st_mtime
                except OSError:
                    mtime = 0.0
                if status == "dispatched":
                    mtime += 1000.0
                # Prefer the current fanout when it is recoverable; otherwise
                # take the newest matching fanout for the same logical task.
                if fanout_id == current_fanout_id:
                    mtime += 1_000_000_000.0
                candidates.append((mtime, manifest, child))
        if not candidates:
            return None
        _, manifest, child = sorted(
            candidates,
            key=lambda item: item[0],
        )[-1]
        return manifest, child
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
            if (
                str(payload.get("result_event_id") or "") == source_event_id
                or str(event.causation_id or "") == source_event_id
            ):
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
        for key in _FANOUT_AFFINITY_METADATA_KEYS:
            value = self._fanout_payload_metadata_value(payload, child, key)
            if value:
                base_payload[key] = value
        if event.type in {"dev.blocked", "dev.failed"} or str(payload.get("status") or "") in {
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
        if not completion_gate.passed and not skip_terminal_admission_for_repair:
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
        task_ref = self._task_ref_entry(base_payload["task_id"])
        if not task_ref and event.type == "task.ref.updated":
            task_ref = payload
        if not task_ref:
            self._record_writer_fanout_child_failed(
                fanout_id=fanout_id,
                base_payload=base_payload,
                failure_payload={
                    "reason": "missing task ref after dev.build.done",
                },
                event=event,
                manifest=manifest,
            )
            return
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
        self._release_fanout_worker_if_terminal(
            role_instance=base_payload["role_instance"],
            fanout_id=fanout_id,
            child_id=base_payload["child_id"],
            run_id=base_payload["run_id"],
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
        self._release_fanout_worker_if_terminal(
            role_instance=base_payload["role_instance"],
            fanout_id=fanout_id,
            child_id=base_payload["child_id"],
            run_id=base_payload["run_id"],
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
            quality = (
                candidate_payload.get("quality")
                if isinstance(candidate_payload.get("quality"), dict)
                else {}
            )
            findings.append({
                "finding_id": "candidate-quality-failed",
                "severity": "high",
                "category": "candidate_quality",
                "message": "candidate quality gates failed",
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
        aggregate_event = self.event_writer.append(ZfEvent(
            type="fanout.aggregate.completed",
            actor="zf-cli",
            payload=aggregate_payload,
            correlation_id=trace_id,
        ))
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
                and publish_event == "cangjie.module.parity.scan.completed"
            ):
                self._bridge_module_parity_scan_completed(published)
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
        aggregate_event = self.event_writer.append(ZfEvent(
            type="fanout.aggregate.completed",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "status": final_status,
                "success_event": success_event if final_status == "completed" else "",
                "failure_event": failure_event if final_status == "failed" else "",
                "pdd_id": pdd_id,
                "feature_id": feature_id,
                "task_map_ref": task_map_ref,
                "source_index_ref": source_index_ref,
                "completed_task_ids": completed_task_ids,
                "failed_children": failed_children,
                "candidate_status": str(candidate_payload.get("status") or ""),
                "candidate_ref": str(candidate_payload.get("branch") or ""),
                "findings": failure_findings if final_status == "failed" else [],
                "recovered_from_aggregate_status": (
                    aggregate_status if recovered_aggregate else ""
                ),
                "recovered_from_aggregate_reason": (
                    "retry_requested"
                    if force_retry
                    else str(aggregate.get("reason") or "")
                    if recovered_aggregate
                    else ""
                ),
                **candidate_contract_payload,
            },
            correlation_id=trace_id,
        ))
        emit_fanout_channel_state_update(
            writer=self.event_writer,
            terminal_event=aggregate_event,
            manifest={**manifest, "aggregate": aggregate_event.payload},
        )
        if publish_event:
            published = self.event_writer.append(ZfEvent(
                type=publish_event,
                actor="zf-cli",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": trace_id,
                    "stage_id": stage_id,
                    "status": final_status,
                    **candidate_contract_payload,
                    "commit": str(candidate_payload.get("commit") or ""),
                    "failed_children": failed_children,
                    "findings": failure_findings,
                },
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
            # Baseline for a child that was assigned/queued but never got a
            # dispatch event (e.g. an affinity overflow child whose lane never
            # freed): without this it is invisible to the timeout sweep and
            # strands a wait_for_all aggregate forever. Fall back to the fanout
            # start so it still times out by stage age.
            fanout_started = [
                event for event in events
                if event.type == "fanout.started"
                and isinstance(event.payload, dict)
                and event.payload.get("fanout_id") == fanout_id
            ]
            fanout_epoch = (
                self._event_epoch(fanout_started[0]) if fanout_started else None
            )
            timed_out: list[dict] = []
            timed_out_reason: dict[str, str] = {}
            for child in manifest.get("children", []) or []:
                if not isinstance(child, dict):
                    continue
                child_id = str(child.get("child_id") or "")
                if not child_id or child.get("status") in {"completed", "failed"}:
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
                    now = self._now()
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
        if not self._ensure_fanout_role_dispatchable(
            role=role,
            fanout_id=fanout_id,
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
        self._send_transport_task(
            role.instance_id,
            briefing_path,
            prompt,
            dispatch_context,
        )
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
        child_id: str,
        run_id: str,
    ) -> None:
        if not role_instance:
            return
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
        )
    def _fanout_timeout_seconds(self, stage_id: str) -> int:
        for stage in getattr(self.config.workflow, "stages", []):
            if stage.id == stage_id:
                return int(stage.timeout_seconds or 0)
        return 0
    def _dispatch_fanout_synth(
        self,
        fanout_id: str,
        manifest: dict,
        mode: str,
        synth_role: str,
    ) -> None:
        trace_id = str(manifest.get("trace_id") or "")
        stage_id = str(manifest.get("stage_id") or "")
        role = next(iter(self._fanout_roles([synth_role])), None)
        if role is None:
            failure_event = self.event_writer.append(ZfEvent(
                type="fanout.synth.completed",
                actor="zf-cli",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": trace_id,
                    "stage_id": stage_id,
                    "status": "failed",
                    "recommendation": "reject",
                    "summary": f"synth role {synth_role!r} not found",
                },
                correlation_id=trace_id,
            ))
            self._finalize_fanout_synth(failure_event)
            return
        if not self._fanout_aggregate_started(manifest):
            self.event_writer.append(ZfEvent(
                type="fanout.aggregate.started",
                actor="zf-cli",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": trace_id,
                    "stage_id": stage_id,
                    "mode": mode,
                },
                correlation_id=trace_id,
            ))
        run_id = f"run-{fanout_id}-synth"
        if not self._ensure_fanout_role_dispatchable(
            role=role,
            fanout_id=fanout_id,
            stage_id=stage_id,
            child_id="synth",
            run_id=run_id,
            trace_id=trace_id,
            causation_id=str(manifest.get("trigger_event_id") or "") or None,
            prompt_kind="fanout_synth",
        ):
            return
        try:
            self._checkout_fanout_reader(role, str(manifest.get("target_ref") or ""))
            skill_entries = self._record_skill_provenance(role=role)
            briefing_path = self._write_fanout_synth_briefing(
                role=role,
                manifest=manifest,
                run_id=run_id,
                skill_entries=skill_entries,
            )
            prompt = build_task_prompt(
                role.instance_id,
                briefing_path,
                prompt_kind="fanout_synth",
            )
            dispatch_context = self._dispatch_context(
                role=role,
                briefing_path=briefing_path,
                trace_id=trace_id,
            )
            self._send_transport_task(
                role.instance_id,
                briefing_path,
                prompt,
                dispatch_context,
            )
            reports = self._fanout_reports(manifest)
            from zf.core.workflow.runner_policy import pure_aggregator_policy_plan

            runner_policy = pure_aggregator_policy_plan(self.config, role)
            self.event_writer.append(ZfEvent(
                type="fanout.synth.dispatched",
                actor="zf-cli",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": trace_id,
                    "stage_id": stage_id,
                    "role_instance": role.instance_id,
                    "run_id": run_id,
                    "target_ref": str(manifest.get("target_ref") or ""),
                    "briefing_path": str(briefing_path),
                    "report_paths": [
                        str(report.get("report_path") or "")
                        for report in reports
                    ],
                    "runner_policy": (
                        runner_policy if runner_policy.get("applies") else {}
                    ),
                },
                correlation_id=trace_id,
            ))
        except Exception as exc:
            failure_event = self.event_writer.append(ZfEvent(
                type="fanout.synth.completed",
                actor="zf-cli",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": trace_id,
                    "stage_id": stage_id,
                    "role_instance": role.instance_id,
                    "run_id": run_id,
                    "status": "failed",
                    "recommendation": "reject",
                    "summary": str(exc),
                },
                correlation_id=trace_id,
            ))
            self._finalize_fanout_synth(failure_event)
    def _handle_fanout_synth_completed(self, event: ZfEvent) -> None:
        self._finalize_fanout_synth(event)
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
            if final_status == "completed" and publish_event == "verify.passed":
                self._bridge_verify_passed_to_parity_scan(published)
            elif (
                final_status == "completed"
                and publish_event == "cangjie.module.parity.scan.completed"
            ):
                self._bridge_module_parity_scan_completed(published)
        publish_event = success_event if final_status == "completed" else failure_event
        # B-FIX-07 (R32 stall): reader-synth finalize 的 failure publish_event
        # (review.rejected 等)必带 manifest 的 pdd_id/feature_id —— 否则
        # candidate_rework 从中推不出 pdd,rework 无法路由 → stall(synth_timeout
        # 走的就是此路径)。
        pdd_id = str(manifest.get("pdd_id") or "")
        feature_id = str(manifest.get("feature_id") or "")
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
                **artifact_payload,
            },
            causation_id=event.id,
            correlation_id=trace_id,
        ))
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
            self.event_writer.append(ZfEvent(
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
                    **artifact_payload,
                },
                causation_id=event.id,
                correlation_id=trace_id,
            ))
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
                "error": (
                    f"target_ref {target_ref!r} is not a git branch, tag, "
                    "or commit; file paths belong in objective/prompt/artifacts"
                ),
            },
            causation_id=event.id,
            correlation_id=trace_id,
        ))

    def _checkout_fanout_reader(self, role: RoleConfig, target_ref: str) -> None:
        if not target_ref:
            return
        from zf.runtime.workdirs import WorkdirManager

        WorkdirManager(
            state_dir=self.state_dir,
            project_root=self.project_root,
            config=self.config,
        ).checkout_reader_ref(role, target_ref)
    def _write_fanout_briefing(
        self,
        *,
        role: RoleConfig,
        context,
        child_id: str,
        run_id: str,
        aggregate,
        child_payload: dict | None = None,
        skill_entries: list | None = None,
    ) -> Path:
        import json
        import shlex

        briefings_dir = self.state_dir / "briefings"
        briefings_dir.mkdir(parents=True, exist_ok=True)
        path = briefings_dir / f"{role.instance_id}-{context.fanout_id}-{child_id}.md"
        child_payload = child_payload if isinstance(child_payload, dict) else {}
        from zf.runtime.affinity_review_scope import (
            affinity_scope_briefing_lines,
            affinity_scope_identity_errors,
        )

        identity_errors = affinity_scope_identity_errors(
            child_payload,
            role_instance=role.instance_id,
        )
        if identity_errors:
            raise RuntimeError(
                "fanout affinity child identity invalid: "
                + ", ".join(identity_errors)
            )

        trigger_payload = (
            child_payload.get("trigger_payload")
            if isinstance(child_payload.get("trigger_payload"), dict)
            else {}
        )
        success_payload = {
            "fanout_id": context.fanout_id,
            "stage_id": context.stage_id,
            "child_id": child_id,
            "run_id": run_id,
            "role_instance": role.instance_id,
            "status": "completed",
            "report": {
                "child_id": child_id,
                "status": "passed",
                "summary": "Short outcome summary.",
                "findings": [],
                "recommendation": "approve",
            },
        }
        failure_payload = {
            **success_payload,
            "status": "failed",
            "reason": "Blocking finding.",
            "report": {
                "child_id": child_id,
                "status": "failed",
                "summary": "Short failure summary.",
                "findings": [],
                "recommendation": "reject",
            },
        }
        success_event = str(getattr(aggregate, "success_event", "") or "")
        failure_event = str(getattr(aggregate, "failure_event", "") or "")
        child_success_event, child_failure_event = self._fanout_child_result_events(
            aggregate,
        )
        is_refactor_review = success_event == "zaofu.refactor.review.ready"
        is_refactor_plan = success_event in {
            "zaofu.refactor.plan.ready",
            "refactor.plan.ready",
        }
        is_plan_artifact_stage = self._is_plan_artifact_stage(
            role=role,
            stage_id=str(context.stage_id),
            success_event=success_event,
            child_success_event=child_success_event,
        )
        if is_refactor_review:
            success_payload["report"].update({
                "coverage_matrix": [{
                    "subsystem": child_id,
                    "inspected_paths": [],
                    "evidence_refs": [],
                    "coverage": "partial",
                    "uncovered": [],
                }],
                "evidence_refs": [],
                "uncovered": [],
                "confidence": "confirmed|inferred|uncovered",
            })
            failure_payload["reason"] = (
                "Unable to produce a coverage/evidence-backed review report."
            )
            failure_payload["report"].update({
                "coverage_matrix": [],
                "evidence_refs": [],
                "uncovered": ["Unable to inspect assigned scope."],
            })
        elif is_refactor_plan:
            review_artifact_ref = str(
                trigger_payload.get("review_artifact_ref")
                or "Path to the review artifact used."
            )
            plan_intent = str(trigger_payload.get("plan_intent") or "")
            scan_quality_audit_ref = (
                "Path to scan-quality-audit.json proving scan inputs were "
                "consumed before task_map synthesis."
            )
            success_payload.update({
                "scan_quality_audit_ref": scan_quality_audit_ref,
                "artifact_refs": [scan_quality_audit_ref],
                "artifact_digests": {},
            })
            success_payload["report"].update({
                "review_artifact_ref": review_artifact_ref,
                "plan_intent": plan_intent,
                "scan_quality_audit_ref": scan_quality_audit_ref,
                "refactor_plan_md": "## Refactor Plan\n\nReplace with the final plan.",
                "task_map": {"tasks": []},
                "gates": [],
                "risk_register": [],
                "backlog_candidates": [],
                "artifact_refs": [scan_quality_audit_ref],
                "evidence_refs": [],
            })
            failure_payload["reason"] = (
                "Unable to produce a plan artifact from the review artifact."
            )
            failure_payload["report"].update({
                "review_artifact_ref": review_artifact_ref,
                "plan_intent": plan_intent,
                "missing_fields": [],
            })

        def _emit_command(event_type: str, payload: dict) -> str:
            if not event_type:
                return "# no event configured"
            return " ".join([
                "zf",
                "emit",
                shlex.quote(event_type),
                "--actor",
                shlex.quote(role.instance_id),
                "--state-dir",
                shlex.quote(str(self.state_dir)),
                "--payload",
                shlex.quote(json.dumps(payload, ensure_ascii=False)),
            ])

        payload_section: list[str] = []
        if child_payload:
            from zf.runtime.injection import materialize_instruction_refs
            child_payload = materialize_instruction_refs(
                child_payload, project_root=self.project_root,
            )
            instruction = str(
                child_payload.get("instruction")
                or child_payload.get("summary")
                or ""
            ).strip()
            payload_section.extend([
                "## Child-Specific Context",
                "",
                "Treat this workflow child payload as the verification scope for this run:",
                "```json",
                json.dumps(child_payload, ensure_ascii=False, indent=2),
                "```",
                "",
            ])
            if instruction:
                payload_section.extend([
                    "Instruction:",
                    instruction,
                "",
            ])
        workflow_input_section = render_workflow_input_briefing_section(
            child_payload,
        ).strip()
        workflow_input_lines = (
            [*workflow_input_section.splitlines(), ""]
            if workflow_input_section
            else []
        )

        result_guidance = [
            "Finding schema: use `severity` = info|low|medium|high|critical, `path`, `message`, and optional integer `line`.",
            "`fanout_id`, `stage_id`, `child_id`, `run_id`, `role_instance`, and `status` must stay as top-level payload fields; do not place them only inside `report`.",
        ]
        if is_refactor_review:
            result_guidance.extend([
                "For this refactor review workflow, finding severity describes planning risk.",
                "Emit the success event when the review report is complete, even if findings include `high` or `critical` items.",
                "For a complete review report, keep `report.status` as `passed` and `report.recommendation` as `approve`; put caveats in findings, risks, refactor_slices, and summary.",
                "Do not invent custom recommendation values; valid values are `approve`, `reject`, `needs_rework`, and `abstain`.",
                "Emit the failure event only when you cannot inspect the assigned scope or cannot provide `coverage_matrix` / `evidence_refs`.",
                "Replace placeholder arrays in the success payload with actual coverage, evidence, uncovered areas, findings, and refactor slices.",
            ])
        elif is_refactor_plan:
            result_guidance.extend([
                "For this refactor plan workflow, emit the success event only when `refactor_plan_md`, `task_map`, and `gates` are complete.",
                "For a complete plan artifact, keep `report.status` as `passed` and `report.recommendation` as `approve`.",
                "Do not invent custom recommendation values; valid values are `approve`, `reject`, `needs_rework`, and `abstain`.",
                "Use the provided `review_artifact_ref` and `plan_intent`; do not invent facts for uncovered review areas.",
                "Emit the failure event only when the plan artifact cannot be produced.",
                *self._plan_artifact_contract_lines(),
            ])
        elif is_plan_artifact_stage:
            plan_ref = f"docs/plans/{context.stage_id}-{child_id}-plan.md"
            success_payload.update({
                "plan_artifact_ref": plan_ref,
                "artifact_refs": [plan_ref],
                "evidence_refs": [],
            })
            success_payload["report"].update({
                "plan_artifact_ref": plan_ref,
                "plan_md": "## Plan\n\nReplace with the durable plan artifact content.",
                "task_map_ref": "",
                "backlog_ref": "",
                "source_index_ref": "",
                "evidence_refs": [],
            })
            result_guidance.extend(self._plan_artifact_contract_lines())
        else:
            # Planning/gate aggregates (prd.ready/prd.approved/task_map.ready,
            # and any DAG flow whose contract declares handoff refs) fall through
            # the plan/refactor branches above with a plain reader template that
            # has no prd_ref/evidence_refs slot. The aggregate
            # `_generic_fanout_success_payload` COLLECTS evidence_refs from the
            # children — finding none it synthesizes `evidence_refs: []` and the
            # gate loops on `prd.blocked: requires evidence_refs` (ledger e2e
            # 2026-06-20). Derive the required slot from the event contract
            # (event_schemas) instead of hardcoding event names: this stays a
            # single source of truth with the gate and generalizes to custom DAG
            # flows; loose-mode falls back to the PRD-stage defaults.
            handoff_refs = _contract_handoff_ref_fields(self.config, success_event)
            if handoff_refs:
                prd_ref = str(
                    trigger_payload.get("prd_ref") or "docs/prd/<product>.md"
                )
                task_map_ref = str(
                    trigger_payload.get("task_map_ref")
                    or f"{self.state_dir}/artifacts/task_map.json"
                )
                primary_ref = (
                    task_map_ref if "task_map_ref" in handoff_refs else prd_ref
                )
                seed: dict = {}
                for field_name in handoff_refs:
                    if field_name == "prd_ref":
                        seed["prd_ref"] = prd_ref
                    elif field_name == "task_map_ref":
                        seed["task_map_ref"] = task_map_ref
                    else:  # artifact_refs / evidence_refs / source_index_ref
                        seed[field_name] = [primary_ref]
                success_payload.update(seed)
                success_payload["report"].update(seed)
                result_guidance.append(
                    f"This is a planning/gate aggregate. Success event "
                    f"`{success_event}` REQUIRES non-empty {handoff_refs} per the "
                    f"workflow contract — empty makes the kernel emit "
                    f"`{failure_event}` and route rework. Replace the placeholders "
                    f"with the real artifact you wrote, keep the primary ref inside "
                    f"`artifact_refs`, and put concrete pointers in `evidence_refs` "
                    f"(git:<sha>, the artifact path, the source document, or the "
                    f"trigger event id); never leave them empty."
                )
            else:
                result_guidance.append(
                    "Use `high` or `critical` for blocking findings; do not invent new severity names."
                )

        path.write_text(
            "\n".join([
                f"# Fanout Reader Child: {child_id}",
                "",
                f"- fanout_id: `{context.fanout_id}`",
                f"- stage_id: `{context.stage_id}`",
                f"- run_id: `{run_id}`",
                f"- target_ref: `{context.target_ref}`",
                "",
                "Evaluate the target ref as a read-only fanout child.",
                "Do not modify project source files.",
                "",
                # B3 (R20): affinity lanes inspect ONLY their slice — not the full
                # candidate — so a large candidate doesn't exhaust review context.
                *affinity_scope_briefing_lines(child_payload),
                *self._skill_briefing_section(role, skill_entries),
                *workflow_input_lines,
                *payload_section,
                "Use the runtime state dir explicitly because this role may run from a detached workdir.",
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
                "",
                *result_guidance,
                "",
                "When finished, emit exactly one result event with this payload:",
                "```json",
                json.dumps(success_payload, indent=2),
                "```",
                f"Child success event: `{child_success_event}`",
                f"Child failure event: `{child_failure_event}`",
                f"Aggregate success event: `{success_event}`",
                f"Aggregate failure event: `{failure_event}`",
                "",
            ]),
            encoding="utf-8",
        )
        return path
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
        task_instruction = str(
            task_payload.get("instruction")
            or task_payload.get("summary")
            or ""
        )
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
        base_git_head = _capture_head(Path(workdir))
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
        }
        completion_command = " ".join([
            "zf",
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
            task_item,
        ).strip()
        workflow_input_lines = (
            [*workflow_input_section.splitlines(), ""]
            if workflow_input_section
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
        contract_src = (
            task_item.get("raw_task")
            if isinstance(task_item.get("raw_task"), dict)
            else task_item
        )
        path.write_text(
            "\n".join([
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
                "Task instruction:",
                task_instruction or "Implement the assigned task scope inside allowed_paths.",
                "",
                "Task payload:",
                "```json",
                json.dumps(task_payload, indent=2, ensure_ascii=False),
                "```",
                "",
                "Scope contract:",
                "```json",
                json.dumps({
                    "task_id": task_id,
                    "pdd_id": pdd_id,
                    "feature_id": str(task_item.get("feature_id") or pdd_id),
                    "task_map_ref": str(task_item.get("task_map_ref") or ""),
                    "source_index_ref": str(task_item.get("source_index_ref") or ""),
                    "allowed_paths": task_item.get("allowed_paths", []),
                    "protected_paths": task_item.get("protected_paths", [".zf/**"]),
                    "base_ref": self.config.runtime.git.candidate_base_ref,
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
                    "summary": str(contract_src.get("summary") or ""),
                    "acceptance": contract_src.get("acceptance", []),
                    "verification": contract_src.get("verification", []),
                    "source_refs": (
                        contract_src.get("source_refs")
                        or task_item.get("source_refs", [])
                    ),
                }, indent=2),
                "```",
                "",
                "## Completion discipline (candidate integration depends on it)",
                "1. COMMIT everything before emitting dev.build.done: `git add -A && git commit`. "
                "An uncommitted workdir is rejected at integration (\"workdir has uncommitted\").",
                "2. The `source_commit` you report MUST be the current branch HEAD. Do NOT touch files "
                "or commit again after emitting dev.build.done — a later commit makes the reported "
                "source_commit stale (\"source_commit is not HEAD\") and the ref is rejected.",
                "3. Stay strictly inside `allowed_paths`. Do NOT create or edit files another slice "
                "owns — overlapping a sibling's paths is rejected (\"changes outside contract scope\") "
                "and conflicts at cherry-pick integration.",
                "",
                "When finished, update `<HEAD commit>` and `files_touched`, then emit dev.build.done with the runtime state dir explicitly:",
                "```bash",
                completion_command,
                "```",
                "",
                "Completion payload shape:",
                "```json",
                json.dumps(completion_payload, indent=2),
                "```",
                "",
            ]),
            encoding="utf-8",
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
            return " ".join([
                "zf",
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
    ) -> Path:
        import json
        import shlex

        fanout_id = str(manifest.get("fanout_id") or "")
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
        completion_command = " ".join([
            "zf",
            "emit",
            "fanout.synth.completed",
            "--actor",
            shlex.quote(role.instance_id),
            "--state-dir",
            shlex.quote(str(self.state_dir)),
            "--payload",
            shlex.quote(json.dumps(completion_payload, ensure_ascii=False)),
        ])
        path.write_text(
            "\n".join([
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
                "Child reports:",
                "```json",
                json.dumps([report.get("report", {}) for report in reports], indent=2),
                "```",
                "",
                "When finished, emit exactly one fanout.synth.completed event with the runtime state dir explicitly:",
                "```bash",
                completion_command,
                "```",
                "",
                "Completion payload shape:",
                "```json",
                json.dumps(completion_payload, indent=2),
                "```",
                "",
            ]),
            encoding="utf-8",
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
