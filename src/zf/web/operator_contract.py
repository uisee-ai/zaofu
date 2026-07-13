"""Shared Kanban Agent operator contract projections."""

from __future__ import annotations

from pathlib import Path
from typing import Any


KANBAN_AGENT_ALLOWED_ACTIONS = (
    "chat-orchestrator",
    "operator-intent-create",
    "operator-intent-approve",
    "operator-intent-reject",
    "create-task",
    "update-task",
    "archive-task",
    "link-evidence",
    "request-fanout",
    "workflow-invoke",
    "workflow-batch-resume",
    "candidate-rework-apply",
    "idea-to-product",
    "start-collaboration",
    "start-operator-session",
    "dispatch-task",
    "request-verify",
    "request-review",
    "ship-candidate",
    "agent-session-cancel",
    "automation-run",
    "maintenance-prepare",
    "attention-ack",
    "attention-snooze",
    "attention-resolve",
    "attention-feedback",
    "attention-escalate",
    "inbox-item-read",
    "inbox-all-read",
    "provider-dev-chat-start",
    "provider-dev-chat-send",
    "provider-dev-chat-stop",
    "workflow-config-propose",
    "workflow-config-validate",
    "workflow-config-apply",
    "runtime-stop",
    "runtime-restart",
    "runtime-resume",
)

KANBAN_AGENT_CAPABILITIES = (
    "read_shared_project_context",
    "read_runtime_projections",
    "read_project_operator_summary",
    "read_skills_catalog",
    "explain_projection",
    "explain_status_evidence_split",
    "read_star_dag",
    "classify_operator_intent",
    "submit_intent",
    "propose_idea_to_product",
    "request_fanout",
    "request_workflow_invoke",
    "request_workflow_batch_resume",
    "request_candidate_rework",
    "request_collaboration",
    "request_supervisor_diagnosis",
    "request_autoresearch_diagnosis",
    "request_automation_run",
    "create_task",
    "update_task",
    "archive_task",
    "link_evidence",
    "propose_provider_dev_chat",
    "propose_workflow_config_change",
    "propose_runtime_restart",
)

KANBAN_AGENT_FORBIDDEN_CAPABILITIES = (
    "direct_zf_truth_write",
    "direct_git_mutation",
    "role_terminal_control",
    "orchestrator_terminal_control",
    "direct_role_dispatch",
    "direct_task_status_mutation",
    "direct_runtime_stop_restart",
    "direct_workflow_config_write",
    "transcript_as_business_truth",
)

RUNTIME_TRUTH_FILES = (
    "events.jsonl",
    "kanban.json",
    "session.yaml",
    "role_sessions.yaml",
    "feature_list.json",
)

RUNTIME_PROJECTIONS = (
    "runs",
    "traces",
    "fanouts",
    "workdirs",
    "skills",
    "cost",
    "diagnostics",
)


def empty_skills_available() -> dict[str, Any]:
    return {
        "pool_path": "",
        "pool_count": 0,
        "enabled_role_count": 0,
        "names": [],
        "enabled_by_role": [],
        "warnings": 0,
    }


def kanban_agent_shared_context(
    *,
    project_root: Path,
    state_dir: Path,
    operator_workdir: Path,
) -> dict[str, Any]:
    project_root = Path(project_root)
    state_dir = Path(state_dir)
    operator_workdir = Path(operator_workdir)
    return {
        "mode": "dedicated_operator_workdir_with_project_pointers",
        "project_root": str(project_root),
        "shared_project_workdir": str(project_root),
        "state_dir": str(state_dir),
        "zf_yaml": str(project_root / "zf.yaml"),
        "operator_workdir": str(operator_workdir),
        "context_files": {
            "project_root": str(operator_workdir / "PROJECT_ROOT"),
            "state_dir": str(operator_workdir / "STATE_DIR"),
            "shared_context": str(operator_workdir / "SHARED_CONTEXT.json"),
            "skills": str(operator_workdir / "SKILLS.md"),
        },
        "operator_summary": {
            "api": "/api/projects/{project_id}/kanban-agent/summary",
            "schema_version": "kanban-agent.project-summary.v0",
            "truth_write": False,
        },
        "intent_contract": {
            "schema_version": "operator.intent.v0",
            "high_risk_requires_owner_approval": True,
            "mutates_truth_directly": False,
        },
        "truth_files": [
            {"name": name, "path": str(state_dir / name)}
            for name in RUNTIME_TRUTH_FILES
        ],
        "projections": list(RUNTIME_PROJECTIONS),
    }


def kanban_agent_boundary() -> dict[str, Any]:
    return {
        "role": "operator_action_requester",
        "scheduler": False,
        "direct_truth_write": False,
        "direct_role_dispatch": False,
        "direct_role_terminal_control": False,
        "direct_runtime_stop_restart": False,
        "direct_workflow_config_write": False,
        "high_risk_actions_require_owner_approval": True,
        "proposal_only_actions": [
            "idea-to-product",
            "provider-dev-chat-start",
            "provider-dev-chat-send",
            "provider-dev-chat-stop",
            "workflow-config-propose",
            "workflow-config-validate",
        ],
        "transcript_is_truth": False,
    }


def kanban_agent_status_model() -> dict[str, Any]:
    return {
        "canonical_task_status": "task.status",
        "task_status_source": "TaskStore/EventWriter",
        "execution_status_source": "events/runs/role_sessions/operator_session",
        "interaction_status_source": "operator transcript/chat events",
        "run_completed_implies_task_done": False,
        "done_requires": "accepted update-task or archive-task action, or orchestrator/runtime task status transition",
    }


def kanban_agent_evidence_model() -> dict[str, Any]:
    return {
        "canonical": "task/card status is workflow truth",
        "execution": "run, trace, fanout, role, verify, and review events are evidence",
        "interaction": "Kanban Agent chat and PTY transcript are interaction evidence",
        "completion_rule": "operator/backend completion never moves a task to done by itself",
    }


# Alias -> canonical controlled-action name. Moved verbatim from
# web/server.py so non-fastapi consumers (Feishu specialist conversation,
# proposal extraction) canonicalize identically to the Web action surface.
CANONICAL_ACTIONS = {
    "dispatch": "dispatch-task",
    "rerun-verify": "request-verify",
    "ship": "ship-candidate",
    "suspend": "pause-agent",
    "resume": "resume-agent",
    "create-issue": "create-task",
    "update-issue": "update-task",
    "reply-worker": "worker-reply",
    "respawn-worker": "worker-respawn",
    "drain-worker": "worker-drain",
    "channel.create": "channel-create",
    "channel-new": "channel-create",
    "channel.add_member": "channel-invite-member",
    "channel-add-member": "channel-invite-member",
    "channel.member.permission": "channel-update-member-permission",
    "channel.member.permission.update": "channel-update-member-permission",
    "channel.member.remove": "channel-remove-member",
    "channel-remove-agent": "channel-remove-member",
    "channel.delete": "channel-delete",
    "channel.history.clear": "channel-clear-history",
    "channel.synthesis.request": "channel-synthesis-request",
    "channel.mark_read": "channel-mark-read",
    "channel.handoff": "channel-handoff",
    "channel.discussion_mode": "channel-discussion-mode",
    "channel.owner_report.request": "channel-owner-report",
    "channel-owner-report-request": "channel-owner-report",
    "cancel-agent-session": "agent-session-cancel",
    "agent.session.cancel": "agent-session-cancel",
    "assignment.propose": "assignment-propose",
    "assignment-intent": "assignment-propose",
    "automation.run": "automation-run",
    "automation.run.manual": "automation-run",
    "run-automation": "automation-run",
    "maintenance.prepare": "maintenance-prepare",
    "maintenance_prepare": "maintenance-prepare",
    "attention.ack": "attention-ack",
    "attention.snooze": "attention-snooze",
    "attention.resolve": "attention-resolve",
    "attention.feedback": "attention-feedback",
    "attention.escalate": "attention-escalate",
    "operator.intent.create": "operator-intent-create",
    "operator.intent.approve": "operator-intent-approve",
    "operator.intent.reject": "operator-intent-reject",
    "replan.approve": "replan-approve",
    "replan.defer": "replan-defer",
    "replan.reject": "replan-reject",
    "plan.approve": "plan-approve",
    "plan.reject": "plan-reject",
    "workflow.invoke": "workflow-invoke",
    "workflow.batch.resume": "workflow-batch-resume",
    "candidate.rework.apply": "candidate-rework-apply",
    "idea.to_product": "idea-to-product",
    "productize-idea": "idea-to-product",
    "provider.dev_chat.start": "provider-dev-chat-start",
    "provider.dev_chat.send": "provider-dev-chat-send",
    "provider.dev_chat.stop": "provider-dev-chat-stop",
    "workflow.config.propose": "workflow-config-propose",
    "workflow.config.validate": "workflow-config-validate",
    "workflow.config.apply": "workflow-config-apply",
    "runtime.stop": "runtime-stop",
    "runtime.restart": "runtime-restart",
    "runtime.resume": "runtime-resume",
    "failure.closeout": "failure-closeout",
    "failure.materialize.closeout": "failure-closeout",
    "failure.closeout.activate": "failure-closeout-activate",
    "failure.activate.closeout": "failure-closeout-activate",
    "real.e2e.run": "real-e2e-run",
    "real_e2e.run": "real-e2e-run",
    "run.contract.review": "run-contract-review",
}


def canonical_action(action_name: str) -> str:
    return CANONICAL_ACTIONS.get(action_name, action_name)


# Reply-output contract for kanban-agent channel members (Feishu surface).
# The Web panel teaches this through KanbanHeadlessAgent._system_prompt; a
# channel member's system prompt has no such section, so the Feishu inviter
# attaches this as the member's reply_contract. Shape rules mirror what
# normalize_proposed_task_contract expects (racing-e2e contract-shape fix).
KANBAN_AGENT_CHANNEL_PROPOSAL_CONTRACT = (
    "Action proposals: you are the ZaoFu Kanban Agent on this channel. "
    "Read-only requests (introduce, explain, analyze, debug, review, why) must "
    "be answered in plain text without action_proposal JSON, and never include "
    "example action_proposal JSON in explanations. Only when the operator "
    "explicitly asks to create, track, or schedule work, end your reply with a "
    "compact fenced json block containing "
    '{"action_proposal": {"action": "create-task", "payload": {"title": ..., '
    '"contract": {"behavior": ..., "verification": ..., "acceptance": ...}}, '
    '"reason": ...}}. '
    "contract.behavior and contract.verification must each be a single string "
    "(join multiple checks with newlines, not a JSON list); contract.scope, if "
    "present, must contain only repo-relative path globs like src/** — put any "
    "non-path scope prose in the behavior text instead. For product ideas "
    "prefer action=idea-to-product with payload.objective. The operator must "
    "approve every proposal before it runs; never claim the task was created."
)
