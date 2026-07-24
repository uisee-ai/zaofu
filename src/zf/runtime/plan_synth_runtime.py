"""Plan-synthesis dispatch and call-result admission."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.injection import build_task_prompt


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
)


class PlanSynthRuntimeMixin:
    """Dispatch selected plan synthesis through the profiled call protocol."""

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
            reports = self._fanout_reports(manifest)
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
            call_payload: dict[str, Any] = {}
            prepared_call = None
            if is_plan_synth:
                from zf.runtime.call_result_runtime import prepare_call_operation
                from zf.runtime.plan_synth_handoff import build_plan_synth_call_payload

                call_payload = build_plan_synth_call_payload(
                    state_dir=self.state_dir,
                    project_root=self.project_root,
                    manifest=manifest,
                    reports=reports,
                    run_id=run_id,
                    role_instance=role.instance_id,
                )
                prepared_call = prepare_call_operation(
                    self,
                    payload=call_payload,
                    operation_type="fanout_synth",
                    operation_key=(
                        f"synth@trig:{str(manifest.get('trigger_event_id') or '')[:12]}"
                    ),
                    stage_id=stage_id,
                    task_id="",
                    dispatch_id=run_id,
                    causation_id=str(manifest.get("trigger_event_id") or ""),
                    correlation_id=trace_id,
                )
                if not prepared_call.should_dispatch:
                    return
            briefing_path = self._write_fanout_synth_briefing(
                role=role,
                manifest=manifest,
                run_id=run_id,
                skill_entries=skill_entries,
                call_payload=call_payload,
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
            if prepared_call is not None:
                from zf.runtime.call_result_runtime import mark_call_operation_started

                mark_call_operation_started(
                    self,
                    prepared_call,
                    task_id="",
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
            payload = {
                **payload,
                "admitted_call_result_ref": dict(outcome.envelope_ref or {}),
                "control_result_ref": dict(outcome.control_result_ref or {}),
                "admitted_call_result_digest": str(
                    (outcome.envelope_ref or {}).get("sha256") or ""
                ),
            }
            event = replace(event, payload=payload)
        self._finalize_fanout_synth(event)


__all__ = ["PLAN_SYNTH_HANDOFF_KEYS", "PlanSynthRuntimeMixin"]
