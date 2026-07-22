"""Durable call registration and replay helpers for fanout runtimes."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.runtime.task_contract_snapshot import snapshot_payload_fields
from zf.runtime.writer_fanout_data import _FANOUT_AFFINITY_METADATA_KEYS


class DurableCallFanoutMixin:
    """Host methods for selected durable reader/writer fanout calls."""

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
            pending: list[tuple[dict, RoleConfig, FanoutChild, dict]] = []
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
                pending.append((task_item, role, child, raw_child))
            if not pending:
                continue
            context = replace(
                context,
                expected_children=[
                    child for _item, _role, child, _raw in pending
                ],
            )
            prepared_dispatches = self._preregister_writer_fanout_operations(
                context=context,
                assignments=[
                    (task_item, role, child)
                    for task_item, role, child, _raw_child in pending
                ],
                causation_id=str(manifest.get("trigger_event_id") or ""),
            )
            for task_item, role, child, raw_child in pending:
                self._unpark_writer_fanout_deferred_task(
                    str(task_item.get("task_id") or "")
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
                    prepared_dispatch=prepared_dispatches.get(child.child_id),
                )
                recovered = recovered or len(self.event_log.read_all()) > before
        return recovered

    def _recover_durable_fanout_aggregate_results(
        self,
        events: list[ZfEvent],
    ) -> None:
        """Close selected parent operations after an aggregate append crash."""

        terminal = {
            str((event.payload or {}).get("operation_id") or "")
            for event in events
            if event.type in {
                "workflow.operation.settled",
                "workflow.operation.failed",
                "workflow.operation.blocked",
            }
            and isinstance(event.payload, dict)
        }
        for event in events:
            if event.type != "fanout.aggregate.completed":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            manifest = self._fanout_manifest(str(payload.get("fanout_id") or ""))
            trigger_payload = (
                manifest.get("trigger_payload")
                if isinstance(manifest, dict)
                and isinstance(manifest.get("trigger_payload"), dict)
                else {}
            )
            operation_id = str(
                trigger_payload.get("workflow_operation_id")
                or trigger_payload.get("parent_operation_id")
                or ""
            )
            if not operation_id or operation_id in terminal:
                continue
            self._consume_durable_fanout_aggregate_result(event)

    def _consume_durable_fanout_aggregate_result(self, event: ZfEvent) -> None:
        """Synchronously close a selected parent before cursor advancement."""

        try:
            self._on_durable_fanout_aggregate_completed(event)
        except Exception as exc:
            payload = event.payload if isinstance(event.payload, dict) else {}
            self.event_writer.append(ZfEvent(
                type="workflow.call.result.invalid",
                actor="zf-cli",
                task_id=event.task_id,
                payload={
                    "schema_version": "call-result-admission.v1",
                    "fanout_id": str(payload.get("fanout_id") or ""),
                    "stage_id": str(payload.get("stage_id") or ""),
                    "reason": "nested_aggregate_admission_failed",
                    "error": str(exc),
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))

    def _prepare_reader_fanout_child_operation(
        self,
        *,
        context,
        child,
        role: RoleConfig,
        causation_id: str,
    ) -> dict[str, Any]:
        """Persist one selected reader call before any sibling is sent."""

        run_id = f"run-{context.fanout_id}-{child.child_id}"
        if not self._pin_reader_target_or_reject(
            role=role,
            target_ref=child.target_ref or context.target_ref,
            context=context,
            child=child,
            run_id=run_id,
            causation_id=causation_id,
        ):
            return {"skip": True, "run_id": run_id}
        self._prepare_reader_contract_target(child)
        skill_entries = self._record_skill_provenance(role=role)
        child.payload.update({
            "fanout_id": context.fanout_id,
            "trace_id": context.trace_id,
            "stage_id": context.stage_id,
            "child_id": child.child_id,
            "run_id": run_id,
            "role_instance": role.instance_id,
            "target_ref": child.target_ref or context.target_ref,
            "skills": list(role.skills),
        })
        prepared_call = None
        from zf.runtime.call_result_admission import result_protocol_mode

        call_mode = result_protocol_mode(self.config, child.payload)
        if call_mode != "shadow" or bool(child.payload.get("durable_operation")):
            from zf.runtime.call_result_runtime import prepare_call_operation

            prepared_call = prepare_call_operation(
                self,
                payload=child.payload,
                operation_type="fanout_reader_child",
                # ZF-GEN-SCOPE-01(07-17 第 4 次实弹同墙):重触发派生同
                # child 键 → 与已注册 op 撞身份(request_hash_divergence)。
                # 键掺触发事件 id:同触发重放=同 id(replay 语义保持),
                # 新触发=天然新代。
                operation_key=(
                    f"{child.child_id}@trig:{context.trigger_event_id[:12]}"
                    if context.trigger_event_id else child.child_id
                ),
                stage_id=context.stage_id,
                task_id=str(child.payload.get("task_id") or ""),
                dispatch_id=run_id,
                causation_id=causation_id,
                correlation_id=context.trace_id,
            )
        return {
            "skip": False,
            "run_id": run_id,
            "skill_entries": skill_entries,
            "prepared_call": prepared_call,
        }

    def _preregister_reader_fanout_operations(
        self,
        *,
        context,
        roles_by_instance: dict[str, RoleConfig],
        causation_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Register the whole durable sibling batch before provider send."""

        prepared: dict[str, dict[str, Any]] = {}
        for child in context.expected_children:
            role = roles_by_instance.get(child.role_instance)
            if role is None:
                continue
            try:
                prepared[child.child_id] = self._prepare_reader_fanout_child_operation(
                    context=context,
                    child=child,
                    role=role,
                    causation_id=causation_id,
                )
            except Exception as exc:
                run_id = f"run-{context.fanout_id}-{child.child_id}"
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
                        "reason": f"durable operation preregistration failed: {exc}",
                    },
                    causation_id=causation_id,
                    correlation_id=context.trace_id,
                ))
                prepared[child.child_id] = {"skip": True, "run_id": run_id}
        return prepared

    def _replay_settled_fanout_call(
        self,
        *,
        context,
        child,
        prepared,
        topology: str,
        causation_id: str,
    ) -> None:
        """Project a previously settled call without re-running its provider."""

        from zf.runtime.call_result_envelope import hydrate_call_result_envelope
        from zf.runtime.call_result_runtime import hydrate_admitted_control_result

        descriptor = {
            "kind": "call_result_envelope",
            "schema_version": "call-result-envelope.v1",
            "ref": prepared.admitted_call_result_ref,
            "sha256": prepared.admitted_call_result_digest,
        }
        envelope = hydrate_call_result_envelope(self.state_dir, descriptor)
        control = hydrate_admitted_control_result(self.state_dir, descriptor)
        verdict = str(control.get("verdict") or "passed").lower()
        failed = verdict in {"rejected", "blocked", "abstained"}
        event_type = "fanout.child.failed" if failed else "fanout.child.completed"
        payload = {
            "fanout_id": context.fanout_id,
            "trace_id": context.trace_id,
            "stage_id": context.stage_id,
            "child_id": child.child_id,
            "run_id": str(child.payload.get("run_id") or ""),
            "role_instance": child.role_instance,
            "task_id": str(child.payload.get("task_id") or ""),
            "status": "failed" if failed else "completed",
            "reason": f"replayed admitted semantic verdict: {verdict}" if failed else "",
            "operation_id": prepared.operation_id,
            "request_hash": prepared.request_hash,
            "result_protocol_mode": prepared.mode,
            "admitted_call_result_ref": dict(descriptor),
            "control_result_ref": dict(
                envelope.get("control_result")
                or envelope.get("control_result_ref")
                or {}
            ),
            "semantic_verdict": verdict,
            "replayed_settled_operation": True,
        }
        self.event_writer.append(ZfEvent(
            type=event_type,
            actor="zf-cli",
            task_id=payload["task_id"] or None,
            payload=payload,
            causation_id=causation_id,
            correlation_id=context.trace_id,
        ))
        if topology == "fanout_writer_scoped":
            self._evaluate_writer_fanout(context.fanout_id)
        else:
            self._evaluate_reader_fanout(context.fanout_id)

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
        manifests = [
            (manifest_path.parent.name, self._fanout_manifest(manifest_path.parent.name))
            for manifest_path in fanout_root.glob("*/manifest.json")
        ]
        started_order = {
            str((event.payload or {}).get("fanout_id") or ""): index
            for index, event in enumerate(events)
            if event.type == "fanout.started" and isinstance(event.payload, dict)
        }

        # Close stale generations before attempting any current dispatch. The
        # filesystem does not guarantee glob order; interleaving closeout and
        # dispatch made current children wait an extra tick when their manifest
        # happened to sort before the stale owner of the same reader role.
        for fanout_id, manifest in manifests:
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
            stale_reason, superseded_by = self._reader_fanout_stale_status(
                fanout_id=fanout_id,
                manifest=manifest,
                manifests=manifests,
                started_order=started_order,
            )
            if not stale_reason:
                continue
            before = len(self.event_log.read_all())
            self._cancel_superseded_fanout_manifest(
                fanout_id=fanout_id,
                manifest=manifest,
                reason=stale_reason,
                superseded_by=superseded_by,
                source="superseded_reader_fanout_manifest_closeout",
            )
            recovered = recovered or len(self.event_log.read_all()) > before

        for fanout_id, manifest in manifests:
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
            stale_reason, superseded_by = self._reader_fanout_stale_status(
                fanout_id=fanout_id,
                manifest=manifest,
                manifests=manifests,
                started_order=started_order,
            )
            if stale_reason:
                before = len(self.event_log.read_all())
                self._cancel_superseded_fanout_manifest(
                    fanout_id=fanout_id,
                    manifest=manifest,
                    reason=stale_reason,
                    superseded_by=superseded_by,
                    source="superseded_reader_fanout_manifest_closeout",
                )
                recovered = recovered or len(self.event_log.read_all()) > before
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
            pending: list[tuple[FanoutChild, RoleConfig]] = []
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
                pending.append((child, role))
            if not pending:
                continue
            context = replace(
                context,
                expected_children=[child for child, _role in pending],
            )
            roles_by_instance = {
                role.instance_id: role for _child, role in pending
            }
            prepared_dispatches = self._preregister_reader_fanout_operations(
                context=context,
                roles_by_instance=roles_by_instance,
                causation_id=str(manifest.get("trigger_event_id") or ""),
            )
            for child, role in pending:
                before = len(self.event_log.read_all())
                self._dispatch_reader_fanout_child(
                    context=context,
                    child=child,
                    role=role,
                    aggregate=stage.aggregate,
                    causation_id=str(manifest.get("trigger_event_id") or ""),
                    prepared_dispatch=prepared_dispatches.get(child.child_id),
                )
                recovered = recovered or len(self.event_log.read_all()) > before
        return recovered

    def _reader_fanout_stale_status(
        self,
        *,
        fanout_id: str,
        manifest: dict,
        manifests: list[tuple[str, dict | None]],
        started_order: dict[str, int],
    ) -> tuple[str, str]:
        stale_reason, superseded_by = self._fanout_identity_stale_reason(
            fanout_id,
        )
        if stale_reason:
            return stale_reason, superseded_by
        superseded_by = self._newer_reader_replan_fanout(
            fanout_id=fanout_id,
            manifest=manifest,
            manifests=manifests,
            started_order=started_order,
        )
        if superseded_by:
            return "superseded_by_newer_replan_attempt", superseded_by
        return "", ""

    @staticmethod
    def _newer_reader_replan_fanout(
        *,
        fanout_id: str,
        manifest: dict,
        manifests: list[tuple[str, dict | None]],
        started_order: dict[str, int],
    ) -> str:
        aggregate_config = (
            manifest.get("aggregate_config")
            if isinstance(manifest.get("aggregate_config"), dict)
            else {}
        )
        success_event = str(aggregate_config.get("success_event") or "")
        if success_event != "task_map.ready" and not success_event.endswith(
            ".plan.ready"
        ):
            return ""
        trigger = (
            manifest.get("trigger_payload")
            if isinstance(manifest.get("trigger_payload"), dict)
            else {}
        )
        try:
            attempt = int(trigger.get("rework_attempt") or 0)
        except (TypeError, ValueError):
            return ""
        if attempt <= 0:
            return ""
        current_order = started_order.get(fanout_id, -1)
        if current_order < 0:
            return ""

        scope = (
            str(manifest.get("stage_id") or ""),
            str(
                trigger.get("workflow_run_id")
                or trigger.get("run_id")
                or manifest.get("workflow_run_id")
                or manifest.get("trace_id")
                or ""
            ),
            str(manifest.get("pdd_id") or ""),
            str(manifest.get("feature_id") or ""),
        )
        if not any(scope[1:]):
            return ""
        role_instances = {
            str(child.get("role_instance") or "")
            for child in manifest.get("children", []) or []
            if isinstance(child, dict) and child.get("role_instance")
        }
        newer: list[tuple[int, str]] = []
        for candidate_id, candidate in manifests:
            if candidate_id == fanout_id or not candidate:
                continue
            if started_order.get(candidate_id, -1) <= current_order:
                continue
            candidate_trigger = (
                candidate.get("trigger_payload")
                if isinstance(candidate.get("trigger_payload"), dict)
                else {}
            )
            try:
                candidate_attempt = int(
                    candidate_trigger.get("rework_attempt") or 0
                )
            except (TypeError, ValueError):
                continue
            candidate_scope = (
                str(candidate.get("stage_id") or ""),
                str(
                    candidate_trigger.get("workflow_run_id")
                    or candidate_trigger.get("run_id")
                    or candidate.get("workflow_run_id")
                    or candidate.get("trace_id")
                    or ""
                ),
                str(candidate.get("pdd_id") or ""),
                str(candidate.get("feature_id") or ""),
            )
            if candidate_attempt <= attempt or candidate_scope != scope:
                continue
            candidate_roles = {
                str(child.get("role_instance") or "")
                for child in candidate.get("children", []) or []
                if isinstance(child, dict) and child.get("role_instance")
            }
            if role_instances and candidate_roles and role_instances.isdisjoint(
                candidate_roles
            ):
                continue
            newer.append((candidate_attempt, candidate_id))
        return max(newer, default=(0, ""))[1]

    def _prepare_writer_fanout_child_operation(
        self,
        *,
        context,
        child,
        task_item: dict,
        role: RoleConfig,
        causation_id: str,
    ) -> dict[str, Any]:
        """Materialize writer inputs and operation identity before send."""

        from zf.runtime.workdirs import WorkdirManager

        task_id = str(task_item.get("task_id") or "")
        run_id = f"run-{context.fanout_id}-{child.child_id}"
        manager = WorkdirManager(
            state_dir=self.state_dir,
            project_root=self.project_root,
            config=self.config,
        )
        plan = manager.prepare(role)
        source_ref = str(
            task_item.get("dispatch_base_commit")
            or task_item.get("candidate_base_commit")
            or task_item.get("source_commit")
            or ""
        ).strip()
        workdir_sync = manager.sync_writer_to_source_ref(
            role,
            source_ref_override=source_ref or None,
        )
        dependency_task_ids = list(dict.fromkeys([
            str(task_id).strip()
            for task_id in (
                list(task_item.get("blocked_by") or [])
                + list(task_item.get("depends_on") or [])
            )
            if str(task_id or "").strip()
        ]))
        dependency_result = manager.apply_dependency_task_refs(
            role,
            dependency_task_ids,
        )
        skill_entries = self._record_skill_provenance(
            role=role,
            task_id=task_id,
        )
        contract_snapshot: dict = {}
        contract_descriptor: dict = {}
        contract_dispatch_fields: dict[str, str] = {}
        if self._typed_task_contract_handoff_enabled(task_item):
            contract_snapshot, contract_descriptor = self._prepare_writer_contract_snapshot(
                task_item=task_item,
                context=context,
                project_path=plan.project_path,
            )
            contract_dispatch_fields = {
                **snapshot_payload_fields(contract_descriptor),
                "workflow_run_id": str(contract_snapshot["workflow_run_id"]),
                "contract_revision": str(contract_snapshot["contract_revision"]),
                "task_map_generation": str(contract_snapshot["task_map_generation"]),
                "base_commit": str(contract_snapshot["base_commit"]),
            }
        operation_payload = {
            **(child.payload if isinstance(child.payload, dict) else {}),
            **task_item,
            **contract_dispatch_fields,
            "fanout_id": context.fanout_id,
            "trace_id": context.trace_id,
            "stage_id": context.stage_id,
            "child_id": child.child_id,
            "run_id": run_id,
            "role_instance": role.instance_id,
            "target_ref": child.target_ref or context.target_ref,
            "skills": list(role.skills),
            "workdir_sync": workdir_sync,
            "dependency_refs": list(
                dependency_result.get("applied_dependency_refs") or []
            ),
            "dependency_refs_skipped": list(
                dependency_result.get("skipped_dependency_refs") or []
            ),
        }
        prepared_call = None
        from zf.runtime.call_result_admission import result_protocol_mode

        call_mode = result_protocol_mode(self.config, operation_payload)
        if call_mode != "shadow" or bool(operation_payload.get("durable_operation")):
            from zf.runtime.call_result_runtime import prepare_call_operation

            prepared_call = prepare_call_operation(
                self,
                payload=operation_payload,
                operation_type="fanout_writer_child",
                # ZF-GEN-SCOPE-01:同上,writer 路径(task_map 重触发场景)
                operation_key=(
                    f"{child.child_id}@trig:{context.trigger_event_id[:12]}"
                    if context.trigger_event_id else child.child_id
                ),
                stage_id=context.stage_id,
                task_id=task_id,
                dispatch_id=run_id,
                causation_id=causation_id,
                correlation_id=context.trace_id,
            )
        task_item.update(operation_payload)
        if isinstance(child.payload, dict):
            child.payload.update(operation_payload)
        return {
            "skip": False,
            "run_id": run_id,
            "plan": plan,
            "skill_entries": skill_entries,
            "contract_dispatch_fields": contract_dispatch_fields,
            "operation_payload": operation_payload,
            "prepared_call": prepared_call,
            "workdir_sync": workdir_sync,
            "dependency_result": dependency_result,
        }

    def _preregister_writer_fanout_operations(
        self,
        *,
        context,
        assignments: list[tuple[dict, RoleConfig, Any]],
        causation_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Persist all selected writer siblings before the first send."""

        prepared: dict[str, dict[str, Any]] = {}
        for task_item, role, child in assignments:
            run_id = f"run-{context.fanout_id}-{child.child_id}"
            if self._writer_task_dispatch_fence_reason(
                str(task_item.get("task_id") or ""),
                role_instance=role.instance_id,
                run_id=run_id,
            ):
                prepared[child.child_id] = {"skip": True, "run_id": run_id}
                continue
            # Do not mint an immutable operation before the provider lane can
            # actually receive it. A deferred child is retried later; preparing
            # it twice may legitimately observe a different dependency merge
            # commit and contract snapshot, which would turn recovery into a
            # request_hash_divergence for the same operation id.
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
                prepared[child.child_id] = {"skip": True, "run_id": run_id}
                continue
            try:
                prepared[child.child_id] = self._prepare_writer_fanout_child_operation(
                    context=context,
                    child=child,
                    task_item=task_item,
                    role=role,
                    causation_id=causation_id,
                )
            except Exception as exc:
                self.event_writer.append(ZfEvent(
                    type="fanout.child.failed",
                    actor="zf-cli",
                    task_id=str(task_item.get("task_id") or "") or None,
                    payload={
                        "fanout_id": context.fanout_id,
                        "trace_id": context.trace_id,
                        "stage_id": context.stage_id,
                        "child_id": child.child_id,
                        "run_id": run_id,
                        "role_instance": role.instance_id,
                        "task_id": str(task_item.get("task_id") or ""),
                        "reason": f"durable operation preregistration failed: {exc}",
                    },
                    causation_id=causation_id,
                    correlation_id=context.trace_id,
                ))
                prepared[child.child_id] = {"skip": True, "run_id": run_id}
        return prepared
