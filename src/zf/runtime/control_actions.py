"""Shared controlled runtime actions for Web and external bridges."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from zf.core.config.schema import ZfConfig
from zf.core.events import EventWriter, ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.schema import Task, TaskContract, TaskEvidence
from zf.core.task.store import TaskStore
from zf.runtime.action_orchestrator import ControlledActionOrchestrator
from zf.runtime.channel_adapter import dispatch_pending_replies
from zf.runtime.channel_contracts import (
    CHANNEL_DISCUSSION_MODES,
    normalize_channel_skill_refs,
    normalize_channel_role,
    normalize_member_type,
    normalize_permission_profile,
    normalize_permissions,
    normalize_provider,
    normalize_visibility_profile,
    permission_profile_write_policy,
    validate_channel_member_contract,
)
from zf.runtime.channel_handoff import request_channel_handoff
from zf.runtime.channel_owner_report import build_owner_report_payload
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_router import (
    detect_channel_mention_tokens,
    resolve_channel_mentions,
    routable_backing_worker_member,
    route_channel_message,
)
from zf.runtime.channel_openclaw import prepare_openclaw_member_connection
from zf.runtime.openclaw_provider import (
    OpenClawGatewayClient,
)
from zf.runtime.channel_roles import normalize_role_context_ref
from zf.runtime.operator_intent import (
    infer_operator_intent,
    validate_operator_intent_payload,
)
from zf.runtime.automation_projection import AUTOMATIONS, project_automations
from zf.runtime.workflow_inputs import (
    normalize_artifact_refs,
    normalize_source_refs,
    workflow_input_manifest_ref,
    workflow_run_id_for,
    write_workflow_input_manifest,
)

from zf.runtime.control_actions_channel_msg import ChannelMessageActionsMixin
from zf.runtime.control_actions_channel_admin import ChannelAdminActionsMixin
from zf.runtime.control_actions_plan import PlanApprovalActionsMixin
from zf.runtime.control_actions_product import ProductActionsMixin
from zf.runtime.control_actions_ops import OpsActionsMixin
from zf.runtime.control_actions_recovery import RECOVERY_ACTIONS, RecoveryActionsMixin
from zf.runtime.control_actions_surgery import SurgeryActionsMixin
from zf.runtime.control_actions_emit import ActionEmitMixin
from zf.runtime.control_actions_workflow_resume import WorkflowResumeActionsMixin
from zf.runtime.control_actions_candidate_rework import CandidateReworkActionsMixin
from zf.runtime.control_actions_workflow_request import WorkflowRequestActionsMixin
from zf.runtime.control_actions_helpers import (  # noqa: F401 — re-export moved helpers
    _approval_ref,
    _automation_output_summary,
    _channel_member_can_receive,
    _compact_automation_outputs,
    _dedupe_ids,
    _normal_channel_id,
    _optional_str,
    _proposal_id,
    _provider_binding_id,
    _requested_fanout_id,
    _required_text,
    _runtime_impact_summary,
    _safe_channel_permissions,
    _safe_int,
    _stable_control_id,
    _string_list,
    _synthesis_target_member,
    _task_contract_from_payload,
    _task_evidence_from_payload,
    _task_id_from_payload,
    _task_metadata_payload,
    _task_priority,
    _task_updates_from_payload,
    _workflow_stage,
    validate_shared_action_payload,
)


class ControlledActionService(
    ChannelMessageActionsMixin,
    ChannelAdminActionsMixin,
    ProductActionsMixin,
    PlanApprovalActionsMixin,
    OpsActionsMixin,
    WorkflowResumeActionsMixin,
    CandidateReworkActionsMixin,
    WorkflowRequestActionsMixin,
    SurgeryActionsMixin,
    RecoveryActionsMixin,
    ActionEmitMixin,
):
    """Execute deterministic action requests from trusted control surfaces."""

    def __init__(
        self,
        state_dir: Path,
        writer: EventWriter,
        *,
        config: ZfConfig | None = None,
        project_root: Path | None = None,
        actor: str = "web",
        source: str = "kanban-agent",
        surface: str = "web",
        openclaw_client: OpenClawGatewayClient | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.writer = writer
        self.config = config
        self.project_root = Path(project_root) if project_root is not None else None
        self.actor = actor
        self.source = source
        self.surface = surface
        self.openclaw_client = openclaw_client

    def execute(
        self,
        *,
        action: str,
        requested_action: str,
        payload: dict,
        requested: ZfEvent,
    ) -> dict:
        result = ControlledActionOrchestrator(
            writer=self.writer,
            actor=self.actor,
            surface=self.surface,
        ).run(
            action=action,
            requested_action=requested_action,
            payload=payload,
            requested=requested,
            task_id=_task_id_from_payload(payload),
            handler=lambda: self._execute_action(
                action=action,
                requested_action=requested_action,
                payload=payload,
                requested=requested,
            ),
        )
        # frontend-stress (2026-07-15): ANY accepted proposal must clear its
        # Triage card, not just create-task. The Web Accept threads
        # proposal_event_id on every proposal action; when the action succeeds,
        # emit the resolved event so pending_kanban_proposals collapses the card.
        # create-task already clears via task.created, and the dismiss action
        # emits its own resolved event — skip those to avoid a redundant event.
        proposal_event_id = str(payload.get("proposal_event_id") or "").strip()
        if (
            proposal_event_id
            and bool(result.get("ok"))
            and action not in {"create-task", "kanban-proposal-dismiss"}
        ):
            self.writer.emit(
                "kanban.agent.proposal.resolved",
                actor=self.actor,
                causation_id=requested.id,
                correlation_id=requested.correlation_id,
                payload={
                    "proposal_event_id": proposal_event_id,
                    "resolution": "executed",
                    "action": action,
                    "source": self.source,
                },
            )
        return result

    def _execute_action(
        self,
        *,
        action: str,
        requested_action: str,
        payload: dict,
        requested: ZfEvent,
    ) -> dict:
        if action == "create-task":
            return self._create_task(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "kanban-proposal-dismiss":
            return self._kanban_proposal_dismiss(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "capture-regression-case":
            return self._capture_regression_case(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "replay-regression-case":
            return self._replay_regression_case(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "update-task":
            return self._update_task(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "request-fanout":
            return self._request_fanout(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action in ("plan-approve", "plan-reject"):
            return self._plan_approval_action(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "channel-post-message":
            return self._channel_post_message(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "channel-create":
            return self._channel_create(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "channel-invite-member":
            return self._channel_invite_member(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "channel-update-member-permission":
            return self._channel_update_member_permission(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "channel-remove-member":
            return self._channel_remove_member(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "channel-delete":
            return self._channel_delete(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "channel-clear-history":
            return self._channel_clear_history(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "channel-mark-read":
            return self._channel_mark_read(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "channel-synthesis":
            return self._channel_synthesis(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "channel-synthesis-request":
            return self._channel_synthesis_request(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "workflow-request":
            return self._workflow_request(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "workflow-submit":
            return self._workflow_submit(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "workflow-invoke":
            return self._workflow_invoke(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "channel-drain-replies":
            return self._channel_drain_replies(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "channel-handoff":
            return self._channel_handoff(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "channel-discussion-mode":
            return self._channel_discussion_mode(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "channel-owner-report":
            return self._channel_owner_report(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "automation-run":
            return self._automation_run(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "maintenance-prepare":
            return self._maintenance_prepare(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action.startswith("inbox-"):
            return self._inbox_read(requested=requested, action=action, requested_action=requested_action, payload=payload)
        if action in {
            "attention-ack",
            "attention-snooze",
            "attention-resolve",
            "attention-feedback",
            "attention-escalate",
        }:
            return self._attention_lifecycle(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "operator-intent-create":
            return self._operator_intent_create(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action in {"operator-intent-approve", "operator-intent-reject"}:
            return self._operator_intent_lifecycle(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action in {"replan-approve", "replan-defer", "replan-reject"}:
            return self._replan_owner_decision(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "idea-to-product":
            return self._idea_to_product(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action in {
            "provider-dev-chat-start",
            "provider-dev-chat-send",
            "provider-dev-chat-stop",
        }:
            return self._provider_dev_chat(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action in {
            "workflow-config-propose",
            "workflow-config-validate",
            "workflow-config-apply",
        }:
            return self._workflow_config_action(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action in {"runtime-stop", "runtime-restart", "runtime-resume"}:
            return self._runtime_lifecycle_action(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "failure-closeout":
            return self._failure_closeout_action(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "failure-closeout-activate":
            return self._failure_closeout_activate_action(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "task-requeue":
            return self._task_requeue_action(
                requested=requested, action=action,
                requested_action=requested_action, payload=payload,
            )
        if action == "child-rebuild":
            return self._child_rebuild_action(
                requested=requested, action=action,
                requested_action=requested_action, payload=payload,
            )
        if action == "stage-retrigger":
            return self._stage_retrigger_action(
                requested=requested, action=action,
                requested_action=requested_action, payload=payload,
            )
        if action == "rescan-grant":
            return self._rescan_grant_action(
                requested=requested, action=action,
                requested_action=requested_action, payload=payload,
            )
        if action == "payload-repair-reemit":
            return self._payload_repair_reemit_action(
                requested=requested, action=action,
                requested_action=requested_action, payload=payload,
            )
        if action == "briefing-redeliver":
            return self._briefing_redeliver_action(
                requested=requested, action=action,
                requested_action=requested_action, payload=payload,
            )
        if action == "human-decision-dismiss":
            return self._human_decision_dismiss_action(
                requested=requested, action=action,
                requested_action=requested_action, payload=payload,
            )
        if action == "ship-retry":
            return self._ship_retry_action(
                requested=requested, action=action,
                requested_action=requested_action, payload=payload,
            )
        if action == "real-e2e-run":
            return self._real_e2e_run_action(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "run-contract-review":
            return self._run_contract_review_action(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "workflow-batch-resume":
            return self._workflow_batch_resume(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        if action == "candidate-rework-apply":
            return self._candidate_rework_apply(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )
        return self._failed(
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=_task_id_from_payload(payload),
            reason="controlled action is not implemented by shared service",
            status_code=501,
            status="not_implemented",
        )
