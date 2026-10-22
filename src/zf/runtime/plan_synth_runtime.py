"""Plan-synthesis dispatch and call-result admission."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.injection import build_task_prompt
from zf.runtime.run_admission import RunDispatchBlocked


PLAN_SYNTH_HANDOFF_KEYS = (
    "workflow_run_id",
    "child_id",
    "operation_id",
    "request_hash",
    "attempt_id",
    "result_protocol_mode",
    "output_profile_id",
    "output_profile_revision",
    "attempt_source_manifest_ref",
    "attempt_source_manifest_digest",
    "attempt_source_manifest",
    "input_consumption_policy_ref",
    "input_consumption_policy_digest",
    "input_consumption_policy",
    "required_reads",
    "result_scratch_ref",
    "semantic_result_submit_mode",
    "plan_revision",
    "plan_synth_contract_ref",
    "plan_synth_contract_digest",
    "reviewed_plan_candidate_digest",
)
PLAN_SYNTH_SEMANTIC_FIELDS = (
    "artifact_refs",
    "evidence_refs",
    "findings",
    "fix_items",
    "owner_decision_items",
    "plan_ports",
    "review_artifact_ref",
    "plan_artifact_ref",
    "task_map_ref",
    "risk_register_ref",
    "backlog_candidates_ref",
    "scan_quality_audit_ref",
    "refactor_plan_md",
    "plan_md",
    "plan_intent",
    "task_map",
    "gates",
    "risk_register",
    "backlog_candidates",
)


class PlanSynthRuntimeMixin:
    """Dispatch selected plan synthesis through the profiled call protocol."""

    def _recover_lost_fanout_synth_dispatches(
        self,
        events: list[ZfEvent],
        *,
        recovery_snapshot=None,
    ) -> bool:
        """Re-send a synth briefing lost when its provider session restarted."""

        event_index = {event.id: index for index, event in enumerate(events)}
        dispatches: dict[str, list[ZfEvent]] = {}
        terminal: set[str] = set()
        lost_by_role: dict[str, list[tuple[int, ZfEvent]]] = {}
        recovery_count: dict[str, int] = {}
        for index, event in enumerate(events):
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.type == "fanout.synth.dispatched":
                fanout_id = str(payload.get("fanout_id") or "")
                if fanout_id:
                    dispatches.setdefault(fanout_id, []).append(event)
                continue
            if event.type == "fanout.synth.completed":
                fanout_id = str(payload.get("fanout_id") or "")
                if fanout_id:
                    terminal.add(fanout_id)
                continue
            if (
                event.type == "fanout.child.dispatch_lost"
                and str(payload.get("child_id") or "") == "synth"
            ):
                fanout_id = str(payload.get("fanout_id") or "")
                if fanout_id:
                    recovery_count[fanout_id] = recovery_count.get(fanout_id, 0) + 1
                continue
            role_instance = self._reader_dispatch_lost_role(event)
            if role_instance:
                lost_by_role.setdefault(role_instance, []).append((index, event))

        if recovery_snapshot is not None:
            manifests = recovery_snapshot.manifests
        else:
            fanout_root = self.state_dir / "fanouts"
            if not fanout_root.exists():
                return False
            manifests = (
                (path.parent.name, self._fanout_manifest(path.parent.name))
                for path in fanout_root.glob("*/manifest.json")
            )
        for fanout_id, manifest in manifests:
            if fanout_id in terminal:
                continue
            if not manifest or manifest.get("topology") != "fanout_reader":
                continue
            synth = manifest.get("synth")
            if not isinstance(synth, dict) or synth.get("status") != "dispatched":
                continue
            if recovery_snapshot is None:
                stale_reason, _superseded_by = (
                    self._fanout_identity_stale_reason(fanout_id)
                )
            else:
                stale_reason, _superseded_by = (
                    self._fanout_identity_stale_reason(
                        fanout_id,
                        currentness=recovery_snapshot.currentness,
                    )
                )
            if stale_reason:
                continue
            role_instance = str(synth.get("role_instance") or "")
            synth_dispatches = dispatches.get(fanout_id, [])
            if not role_instance or not synth_dispatches:
                continue
            allowed_recoveries = max(
                1,
                int((manifest.get("aggregate_config") or {}).get("max_retries") or 0),
            )
            latest_dispatch = synth_dispatches[-1]
            latest_index = event_index.get(latest_dispatch.id, -1)
            if latest_index < 0:
                continue
            if self._fanout_dispatch_has_authoritative_result(
                events,
                latest_dispatch,
            ):
                continue
            lost_event = self._reader_dispatch_lost_event_after(
                lost_by_role.get(role_instance, []),
                latest_index,
                events=events, dispatch_event=latest_dispatch,
            )
            if lost_event is None:
                continue
            lost_index = event_index.get(lost_event.id, latest_index)
            activity_index = (
                latest_index
                if lost_event.type == "cost.usage.capture_miss"
                else lost_index
            )
            if self._fanout_role_has_activity_after_signal(
                events,
                role_instance,
                activity_index,
            ):
                continue
            recovery_attempts = recovery_count.get(fanout_id, 0)
            if recovery_attempts >= allowed_recoveries:
                self._fail_lost_fanout_synth_dispatch(
                    manifest=manifest,
                    latest_dispatch=latest_dispatch,
                    lost_event=lost_event,
                )
                return True
            if self._redispatch_lost_fanout_synth(
                manifest=manifest,
                latest_dispatch=latest_dispatch,
                lost_event=lost_event,
                attempt=recovery_attempts + 1,
            ):
                return True
        return False

    def _fail_lost_fanout_synth_dispatch(
        self,
        *,
        manifest: dict,
        latest_dispatch: ZfEvent,
        lost_event: ZfEvent,
    ) -> None:
        payload = (
            dict(latest_dispatch.payload)
            if isinstance(latest_dispatch.payload, dict)
            else {}
        )
        fanout_id = str(payload.get("fanout_id") or manifest.get("fanout_id") or "")
        role_instance = str(payload.get("role_instance") or "")
        run_id = str(payload.get("run_id") or f"run-{fanout_id}-synth")
        trace_id = str(payload.get("trace_id") or manifest.get("trace_id") or "")
        stage_id = str(payload.get("stage_id") or manifest.get("stage_id") or "")
        provider_turn_closed = lost_event.type == "provider.turn.closed"
        loss_reason = (
            "provider_turn_closed_without_synth_result"
            if provider_turn_closed
            else "reader_synth_session_replaced_after_dispatch"
        )
        self._set_worker_state(
            role_instance,
            "idle",
            reason=f"{loss_reason}_recovery_exhausted",
            force=True,
        )
        lost = self.event_writer.append(ZfEvent(
            type="fanout.child.dispatch_lost",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "child_id": "synth",
                "run_id": run_id,
                "role_instance": role_instance,
                "reason": loss_reason,
                "lost_signal_event_id": lost_event.id,
                "lost_signal_type": lost_event.type,
                "semantic_attempt_consumed": False,
            },
            causation_id=lost_event.id,
            correlation_id=trace_id,
        ))
        failure_event = self.event_writer.append(ZfEvent(
            type="fanout.synth.completed",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "run_id": run_id,
                "role_instance": role_instance,
                "status": "failed",
                "recommendation": "reject",
                "reason": f"{loss_reason}_recovery_exhausted",
                "failure_class": "worker_noop_or_terminal_missing",
                "summary": (
                    "synth provider session ended without a canonical result "
                    "after bounded recovery"
                ),
            },
            causation_id=lost.id,
            correlation_id=trace_id,
        ))
        self._finalize_fanout_synth(failure_event)

    def _redispatch_lost_fanout_synth(
        self,
        *,
        manifest: dict,
        latest_dispatch: ZfEvent,
        lost_event: ZfEvent,
        attempt: int,
    ) -> bool:
        payload = (
            dict(latest_dispatch.payload)
            if isinstance(latest_dispatch.payload, dict)
            else {}
        )
        fanout_id = str(payload.get("fanout_id") or manifest.get("fanout_id") or "")
        role_instance = str(payload.get("role_instance") or "")
        run_id = str(payload.get("run_id") or f"run-{fanout_id}-synth")
        trace_id = str(payload.get("trace_id") or manifest.get("trace_id") or "")
        stage_id = str(payload.get("stage_id") or manifest.get("stage_id") or "")
        briefing_path = Path(str(payload.get("briefing_path") or ""))
        provider_turn_closed = lost_event.type == "provider.turn.closed"
        loss_reason = (
            "provider_turn_closed_without_synth_result"
            if provider_turn_closed
            else "reader_synth_session_replaced_after_dispatch"
        )
        role = next(iter(self._fanout_roles([role_instance])), None)
        if role is None or not briefing_path.is_file():
            return False
        self._set_worker_state(
            role_instance,
            "idle",
            reason=loss_reason,
            force=True,
        )
        if not self._ensure_fanout_role_dispatchable(
            role=role,
            fanout_id=fanout_id,
            stage_id=stage_id,
            child_id="synth",
            run_id=run_id,
            trace_id=trace_id,
            causation_id=lost_event.id,
            prompt_kind="fanout_synth",
            skip_send_window=True,
            provider_session_replaced=True,
        ):
            return False
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
        try:
            self._send_transport_task(
                role.instance_id,
                briefing_path,
                prompt,
                dispatch_context,
            )
        except RunDispatchBlocked:
            return
        except Exception as exc:
            self.event_writer.append(ZfEvent(
                type="fanout.child.dispatch_deferred",
                actor="zf-cli",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": trace_id,
                    "stage_id": stage_id,
                    "child_id": "synth",
                    "run_id": run_id,
                    "role_instance": role.instance_id,
                    "prompt_kind": "fanout_synth",
                    "reason": f"synth recovery dispatch failed: {exc}",
                },
                causation_id=lost_event.id,
                correlation_id=trace_id,
            ))
            return False
        lost = self.event_writer.append(ZfEvent(
            type="fanout.child.dispatch_lost",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "child_id": "synth",
                "run_id": run_id,
                "role_instance": role.instance_id,
                "reason": loss_reason,
                "lost_signal_event_id": lost_event.id,
                "lost_signal_type": lost_event.type,
                "semantic_attempt_consumed": False,
            },
            causation_id=lost_event.id,
            correlation_id=trace_id,
        ))
        self._note_prompt_sent(role.instance_id, run_id)
        self.event_writer.append(ZfEvent(
            type="fanout.synth.dispatched",
            actor="zf-cli",
            payload={
                **payload,
                "attempt": attempt,
                "retry_of_event_id": latest_dispatch.id,
                "recovery_kind": (
                    "provider_turn_closed"
                    if provider_turn_closed
                    else "provider_session_replaced"
                ),
                "semantic_attempt_consumed": False,
            },
            causation_id=lost.id,
            correlation_id=trace_id,
        ))
        return True

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
        try:
            reports = self._fanout_reports(manifest)
            from zf.runtime.fanout_artifact_refs import (
                prepare_fanout_synth_reports,
            )

            reports = prepare_fanout_synth_reports(
                reports=reports,
                manifest=manifest,
                state_dir=self.state_dir,
                project_root=self.project_root,
                config=self.config,
                roles=self.config.roles,
            )
            aggregate_config = (
                manifest.get("aggregate_config")
                if isinstance(manifest.get("aggregate_config"), dict)
                else {}
            )
            success_event = str(aggregate_config.get("success_event") or "")
            is_plan_synth = self._is_plan_artifact_stage(
                role=role,
                stage_id=stage_id,
                success_event=success_event,
                child_success_event="fanout.synth.completed",
            )
            stage = next(
                (
                    item
                    for item in getattr(self.config.workflow, "stages", []) or []
                    if str(getattr(item, "id", "") or "") == stage_id
                ),
                None,
            )
            execution_review = None
            if (
                is_plan_synth
                and str(getattr(stage, "attempt_domain", "") or "") == "plan"
            ):
                from zf.core.workflow.flow_metadata import flow_metadata_for
                from zf.runtime.call_result_envelope import write_immutable_json_sidecar
                from zf.runtime.plan_candidate_preflight import (
                    evaluate_plan_candidate_preflight,
                    plan_candidate_writer_policy,
                )

                trigger_payload = (
                    manifest.get("trigger_payload")
                    if isinstance(manifest.get("trigger_payload"), dict)
                    else {}
                )
                preflight = evaluate_plan_candidate_preflight(
                    state_dir=self.state_dir,
                    project_root=self.project_root,
                    reports=reports,
                    manifest=manifest,
                    metadata=flow_metadata_for(
                        self.config,
                        payload=trigger_payload,
                    ),
                    writer_policy=plan_candidate_writer_policy(self.config),
                )
                execution_review = preflight.get("execution_review")
                if preflight["status"] != "passed":
                    from zf.runtime.plan_synth_handoff import (
                        build_plan_candidate_input_refs,
                    )

                    candidate_refs, _candidate_bindings = (
                        build_plan_candidate_input_refs(
                            state_dir=self.state_dir,
                            project_root=self.project_root,
                            reports=reports,
                        )
                    )
                    descriptor = write_immutable_json_sidecar(
                        self.state_dir,
                        preflight,
                        root="plan-candidate-preflight",
                        kind="plan_candidate_preflight",
                        schema_version="plan-candidate-preflight.v1",
                        created_by="plan-candidate-preflight",
                        source_event_id=str(manifest.get("trigger_event_id") or ""),
                    )
                    findings = [
                        {
                            "severity": "high",
                            **dict(item),
                            "evidence_refs": [str(descriptor.get("ref") or "")],
                        }
                        for item in preflight["errors"]
                    ]
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
                            "summary": "plan candidate failed mechanical preflight",
                            "failure_class": "plan_candidate_preflight",
                            "attempt_domain": "protocol_repair",
                            "semantic_rework_cost": 0,
                            "plan_candidate_preflight_ref": descriptor,
                            "previous_plan_candidate_refs": [
                                *candidate_refs,
                                {
                                    **descriptor,
                                    "source_id": "plan-candidate-preflight",
                                    "artifact_id": "plan-candidate-preflight.json",
                                    "allowed_paths": ["$"],
                                },
                            ],
                            "report": {
                                "child_id": "synth",
                                "status": "failed",
                                "summary": "plan candidate failed mechanical preflight",
                                "findings": findings,
                                "recommendation": "reject",
                            },
                        },
                        causation_id=str(manifest.get("trigger_event_id") or "") or None,
                        correlation_id=trace_id,
                    ))
                    self._finalize_fanout_synth(failure_event)
                    return
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
            self._checkout_fanout_reader(role, str(manifest.get("target_ref") or ""))
            call_payload: dict[str, Any] = {}
            prepared_call = None
            if is_plan_synth:
                from zf.runtime.call_result_runtime import prepare_call_operation
                from zf.core.workflow.runner_policy import pure_aggregator_policy_plan
                from zf.runtime.fanout_artifact_refs import (
                    plan_artifact_workdir_write_scopes,
                )
                from zf.runtime.plan_synth_handoff import build_plan_synth_call_payload

                call_payload = build_plan_synth_call_payload(
                    state_dir=self.state_dir,
                    project_root=self.project_root,
                    manifest=manifest,
                    reports=reports,
                    run_id=run_id,
                    role_instance=role.instance_id,
                )
                if execution_review is not None:
                    from zf.runtime.plan_execution_review import attach_execution_review

                    attach_execution_review(
                        state_dir=self.state_dir, payload=call_payload,
                        review=execution_review,
                        source_event_id=str(manifest.get("trigger_event_id") or ""),
                    )
                workdir_write_scopes = []
                if not pure_aggregator_policy_plan(self.config, role).get("applies"):
                    workdir_write_scopes = plan_artifact_workdir_write_scopes(
                        fanout_id=fanout_id,
                        success_event=success_event,
                    )
                prepared_call = prepare_call_operation(
                    self,
                    payload=call_payload,
                    operation_type="fanout_synth",
                    operation_key=(
                        f"synth@trig:{str(manifest.get('trigger_event_id') or '')[:12]}"
                    ),
                    stage_id=stage_id,
                    task_id=str(call_payload.get("task_id") or ""),
                    dispatch_id=run_id,
                    causation_id=str(manifest.get("trigger_event_id") or ""),
                    correlation_id=trace_id,
                    workdir_write_scopes=workdir_write_scopes,
                )
                if not prepared_call.should_dispatch:
                    return
            trigger_payload = (
                manifest.get("trigger_payload")
                if isinstance(manifest.get("trigger_payload"), dict)
                else {}
            )
            skill_entries = self._record_skill_provenance(
                role=role,
                task_id=str(
                    call_payload.get("task_id")
                    or trigger_payload.get("task_id")
                    or manifest.get("task_id")
                    or ""
                )
                or None,
            )
            briefing_path = self._write_fanout_synth_briefing(
                role=role,
                manifest=manifest,
                run_id=run_id,
                skill_entries=skill_entries,
                call_payload=call_payload,
                reports=reports,
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
                task_id=str(call_payload.get("task_id") or "") or None,
                parent_task_id=str(
                    call_payload.get("parent_task_id") or ""
                )
                or None,
                run_id=str(call_payload.get("workflow_run_id") or "") or None,
                operation_id=(
                    prepared_call.operation_id
                    if prepared_call is not None
                    else None
                ),
                dispatch_id=run_id,
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
                    task_id=str(call_payload.get("task_id") or ""),
                    dispatch_id=run_id,
                    causation_id=str(manifest.get("trigger_event_id") or ""),
                    correlation_id=trace_id,
                )
            self._note_prompt_sent(role.instance_id, run_id)
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
                    **{
                        key: call_payload[key]
                        for key in PLAN_SYNTH_HANDOFF_KEYS
                        if key in call_payload
                    },
                },
                correlation_id=trace_id,
            ))
        except RunDispatchBlocked:
            return
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
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("output_profile_id") or "") == "plan-synth":
            fanout_id = str(payload.get("fanout_id") or "")
            stale_reason, _superseded_by = self._fanout_identity_stale_reason(fanout_id)
            if stale_reason:
                self._finalize_fanout_synth(event)
                return
            from zf.runtime.call_result_runtime import admit_runtime_call_result

            outcome = admit_runtime_call_result(
                self,
                event,
                merged_payload=payload,
                mode="blocking",
            )
            if outcome.repair_requested or outcome.status == "superseded":
                return
            if not outcome.admitted:
                return
            from zf.runtime.call_result_runtime import (
                hydrate_admitted_control_result,
            )

            control_result = hydrate_admitted_control_result(
                self.state_dir,
                outcome.envelope_ref or {},
            )
            semantic_payload = {
                key: control_result[key]
                for key in PLAN_SYNTH_SEMANTIC_FIELDS
                if key in control_result
            }
            report = (
                dict(payload["report"])
                if isinstance(payload.get("report"), dict)
                else {}
            )
            report.update(semantic_payload)
            payload = {
                **payload,
                **semantic_payload,
                "report": report,
                "admitted_call_result_ref": dict(outcome.envelope_ref or {}),
                "control_result_ref": dict(outcome.control_result_ref or {}),
                "admitted_call_result_digest": str(
                    (outcome.envelope_ref or {}).get("sha256") or ""
                ),
            }
            event = replace(event, payload=payload)
        from zf.runtime.plan_synth_owner_checkpoint import (
            apply_plan_synth_owner_checkpoint,
        )

        owner_disposition = apply_plan_synth_owner_checkpoint(
            self,
            event=event,
            payload=payload,
            manifest=self._fanout_manifest(str(payload.get("fanout_id") or "")) or {},
        )
        if owner_disposition.hold:
            return
        if owner_disposition.payload != payload:
            payload = owner_disposition.payload
            event = replace(event, payload=payload)
        self._finalize_fanout_synth(event)


__all__ = [
    "PLAN_SYNTH_HANDOFF_KEYS",
    "PLAN_SYNTH_SEMANTIC_FIELDS",
    "PlanSynthRuntimeMixin",
]
