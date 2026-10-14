"""Shared Kanban Agent operator contract projections."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.runtime.task_workflow_plans import TASK_WORKFLOW_PARAMETER_KEYS


TASK_WORKFLOW_PARAMETER_KEYS_TEXT = ", ".join(
    sorted(TASK_WORKFLOW_PARAMETER_KEYS)
)


KANBAN_AGENT_ALLOWED_ACTIONS = (
    "chat-orchestrator",
    "operator-intent-create",
    "operator-intent-approve",
    "operator-intent-reject",
    "kanban-plan-apply",
    "create-task",
    "update-task",
    "archive-task",
    "link-evidence",
    "request-fanout",
    "workflow-request",
    "workflow-start",
    "task-workflow-start",
    "workflow-submit",
    "workflow-cancel",
    "run-pause",
    "run-resume",
    "run-cancel",
    "workflow-reject",
    "workflow-invoke",
    "research-start",
    "research-adopt",
    "workflow-batch-resume",
    "candidate-rework-apply",
    "replan-approve",
    "replan-defer",
    "replan-reject",
    "idea-to-product",
    "start-collaboration",
    "channel-create-from-template",
    "channel-create-and-start",
    "channel-discussion-start",
    "channel-set-leader",
    "channel-invite-member",
    "channel-profile-register",
    "channel-remove-member",
    "channel-pin-message",
    "channel-question-resolve",
    "channel-consensus-confirm",
    "channel-consensus-block",
    "channel-prd-evaluate",
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
    "stage-replan-new-generation",
)

PROJECT_OPERATOR_CONTROLLED_ACTIONS = frozenset({
    "channel-profile-register",
    "operator-intent-create",
    "operator-intent-approve",
    "operator-intent-reject",
    "replan-approve",
    "replan-defer",
    "replan-reject",
    "plan-approve",
    "plan-reject",
    "workflow-request",
    "workflow-start",
    "task-workflow-start",
    "workflow-submit",
    "workflow-cancel",
    "workflow-reject",
    "workflow-invoke",
    "run-pause",
    "run-resume",
    "run-cancel",
    "workflow-batch-resume",
    "candidate-rework-apply",
    "idea-to-product",
    "provider-dev-chat-start",
    "provider-dev-chat-send",
    "provider-dev-chat-stop",
    "workflow-config-propose",
    "workflow-config-validate",
    "workflow-config-apply",
    "runtime-stop",
    "runtime-restart",
    "runtime-resume",
    "failure-closeout",
    "failure-closeout-activate",
    "stage-replan-new-generation",
    "real-e2e-run",
    "run-contract-review",
})

KANBAN_AGENT_CAPABILITIES = (
    "read_shared_project_context",
    "read_runtime_projections",
    "read_project_operator_summary",
    "read_skills_catalog",
    "develop_in_selected_workspace",
    "run_project_verification",
    "explain_projection",
    "explain_status_evidence_split",
    "read_star_dag",
    "classify_operator_intent",
    "submit_intent",
    "propose_idea_to_product",
    "request_fanout",
    "request_workflow_invoke",
    "start_research_fanout",
    "adopt_research_result",
    "request_workflow_requirement",
    "start_task_workflow_route",
    "request_workflow_submit",
    "control_project_run",
    "reject_workflow_proposal",
    "request_workflow_batch_resume",
    "request_candidate_rework",
    "request_collaboration",
    "create_channel_from_template",
    "create_and_start_channel",
    "start_channel_discussion",
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
        "role": "resident_project_agent",
        "scheduler": False,
        "direct_project_code_write": "permission_profile_gated",
        "direct_truth_write": False,
        "direct_role_dispatch": False,
        "direct_role_terminal_control": False,
        "direct_runtime_stop_restart": False,
        "direct_workflow_config_write": False,
        "high_risk_actions_require_owner_approval": True,
        "proposal_only_actions": [
            "idea-to-product",
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
    "channel.create_from_template": "channel-create-from-template",
    "channel.template.create": "channel-create-from-template",
    "channel.create_and_start": "channel-create-and-start",
    "channel.setup.apply": "channel-create-and-start",
    "channel.discussion.start": "channel-discussion-start",
    "channel.leader.set": "channel-set-leader",
    "channel.add_member": "channel-invite-member",
    "channel-add-member": "channel-invite-member",
    "channel.member.permission": "channel-update-member-permission",
    "channel.member.permission.update": "channel-update-member-permission",
    "channel.member.remove": "channel-remove-member",
    "channel-remove-agent": "channel-remove-member",
    "channel.delete": "channel-delete",
    "channel.history.clear": "channel-clear-history",
    "channel.synthesis.request": "channel-synthesis-request",
    "channel.question.resolve": "channel-question-resolve",
    "channel.consensus.confirm": "channel-consensus-confirm",
    "channel.consensus.block": "channel-consensus-block",
    "channel.mark_read": "channel-mark-read",
    "channel.message.pin": "channel-pin-message",
    "channel.handoff": "channel-handoff",
    "channel.discussion_mode": "channel-discussion-mode",
    "channel.owner_report.request": "channel-owner-report",
    "channel.prd_evaluation.request": "channel-prd-evaluate",
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
    "workflow.start": "workflow-start",
    "task.workflow.start": "workflow-start",
    "workflow.route.start": "workflow-start",
    "research.start": "research-start",
    "research.fanout.start": "research-start",
    "research.adopt": "research-adopt",
    "workflow.request": "workflow-request",
    "workflow.submit": "workflow-submit",
    "workflow.cancel": "workflow-cancel",
    "run.pause": "run-pause",
    "run.resume": "run-resume",
    "run.cancel": "run-cancel",
    "workflow.reject": "workflow-reject",
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
    "stage.replan.new_generation": "stage-replan-new-generation",
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
    "Plan and Approve interactions: you are the ZaoFu Kanban Agent on this channel. "
    "Read-only requests (introduce, explain, analyze, debug, review, why) must "
    "be answered in plain text without plan_request or action_proposal JSON, and never include "
    "example action_proposal JSON in explanations. Only when the operator "
    "must choose one to three unresolved routes or parameters, end the reply with "
    "exactly one compact fenced json block containing plan_request. A single "
    "question may use header, id, question, options, allow_other, and reason; "
    "multiple pure clarification questions use a questions array with one to "
    "three entries. Set subject_type to exactly one of channel_setup, "
    "clarification, task_create, or task_workflow. Pure business, research, "
    "issue, or refactor questions always use clarification; never invent a "
    "family-specific subject_type. Each question has two or three mutually exclusive options "
    "with exactly one recommended option first. Multi-question Plans cannot bind "
    "an action. Never request secrets, never combine "
    "plan_request with action_proposal. Ordinary Plan requests are clarification, "
    "not permission or approval. A Channel setup Plan is the sole bounded "
    "exception: set submit_action=channel-create-and-start, include a clean "
    "discussion_seed containing only the business requirement, "
    "submit_label='Create & start', allow_other=false, and give every option "
    "a label, description, and exact option-level submit_payload containing "
    "template_id, an explicit mode, plus optional name/overrides. Include a "
    "non-empty top-level "
    "question. Do not wrap Channel options in effect or put submit_payload under "
    "effect. Every option must choose conversation for one default responder, "
    "clarification for facilitated relay, or multi_lens for bounded blind fanout "
    "and synthesis. Use mode=multi_lens only inside submit_payload when the operator "
    "explicitly requests independent multi-role input or parallel review, never "
    "as effect.mode or submit_mode; otherwise preserve the template mode; use "
    "clarification for an owner-question-led pass. Channel defaults use synthesis-adaptive "
    "passes: omit overrides.budget.max_rounds unless the owner explicitly requests a hard cap. "
    "When context contains channel_profile_catalog, choose only a listed Profile with the "
    "same template role through overrides.role_overrides.<template_role>.profile_id; never "
    "invent a Profile, role, skill, provider, prompt, permission, or capability. "
    "To disable an optional template role, use only "
    "overrides.role_overrides.<template_role>.enabled=false; never emit disabled_roles. "
    "The selected option atomically creates the Channel and members, posts the "
    "original requirement, and starts the discussion without a second proposal. "
    "When turn context contains plan_discussion, answer the visible user message "
    "against that exact pending Plan and keep it pending unless a revised Plan is "
    "needed. The signed answer otherwise continues in this channel "
    "thread. Only when the operator explicitly asks to create, track, or "
    "schedule work, end your reply with exactly one compact fenced json block "
    "containing the action proposal. Decide this semantically in the operator's "
    "language, not through English or Chinese keyword spelling. Negated "
    "requests, explanations, examples, and questions about creation are not "
    "create intent; use a Plan when ambiguous. The final envelope is "
    '{"action_proposal": {"action": "create-task", "intent": {'
    '"decision": "propose_action", "source_quote": "an exact verbatim user '
    'substring supporting the proposal"}, "payload": {"title": ..., '
    '"contract": {"behavior": ..., "verification": ..., '
    '"verification_tiers": ["runtime"], "acceptance": ..., '
    '"spec_skip_reason": ...}}, '
    '"reason": ...}}. '
    "For tracked execution, add payload.workflow_plan with a task-specific "
    "question and two or three options selected only from the active workflow "
    "route catalog. Executable options contain route_id, label, description, "
    "recommended, and optional parameters; a no-run option uses mode=defer. "
    "The runtime creates the Task before binding and showing that Plan. Task "
    "approval never starts a Workflow. Every create-task proposal must set "
    "payload.execution_mode=workflow by default. Use execution_mode=direct only "
    "when the operator explicitly requests immediate legacy/direct dispatch. For "
    "tracking without execution, keep execution_mode=workflow and omit "
    "workflow_plan. Do not include Channel creation as a "
    "workflow option; Channel is an independent collaboration surface. "
    "contract.behavior and contract.verification must each be a single string "
    "(join multiple checks with newlines, not a JSON list); contract.scope, if "
    "present, must contain only repo-relative path globs like src/** — put any "
    "non-path scope prose in the behavior text instead. verification must be "
    "an executable shell command from the repository root with no prose prefix. "
    "For workflow-managed Tasks, set contract.verification_tiers to one or more "
    "of static, runtime, e2e, or manual_evidence. When scope or affected_files "
    "changes product code, preserve an available product/spec/source ref; if no "
    "upstream artifact exists, set a non-empty contract.spec_skip_reason that "
    "explains why the bounded Task contract is the source authority. For product ideas "
    "prefer action=idea-to-product with payload.objective and the same intent "
    "object. Proposal JSON must be the final reply envelope, not an example "
    "embedded in prose. The operator must "
    "approve every proposal before it runs; never claim the task was created. "
    "When a requirement benefits from a new collaboration Channel, use the "
    "action-bound Channel setup Plan above. Explain the tradeoff in each description; "
    "the runtime displays exact mode, initial routing/fanout, member roles, member "
    "count, selected Profiles, and adaptive/capped pass policy from submit_payload. Do not ask the operator to create "
    "the Channel "
    "or post the first message manually. For a direct non-chat API request, use "
    "action=channel-create-and-start with template_id and the requirement. "
    "When asked to start a discussion in an existing Channel, use "
    "action=channel-discussion-start with payload.channel_id and "
    "payload.objective. For an exact confirmed Channel PRD Task handoff, return "
    "subject_type=task_create with two or three options. The recommended option "
    "uses mode=propose, action=create-task, and a flat submit payload limited "
    "to title, objective, acceptance, acceptance_criteria, scope, "
    "explicit_non_goals, skills_required, priority, optional task_id, and optional "
    "workflow_plan; priority must be an integer from 1 through 5. When the owner "
    "asks to take this PRD into delivery, workflow_plan must contain two or three "
    "route choices from the active workflow_route_catalog; executable choices use "
    "id, label, description, recommended, route_id, objective, and optional "
    "parameters, while a no-run choice uses mode=defer. Do not put task_id, "
    "config_digest, or task_contract_digest in workflow_plan: the Kernel binds the "
    "created Task and then publishes a separate Workflow Plan. Task confirmation "
    "never starts a Workflow; its route still needs a second owner confirmation. "
    "Put mode, action, and payload inside the option's effect object. Copy the selected canonical "
    "item's artifact_ref and artifact_digest into top-level plan_request fields "
    "channel_prd_ref and channel_prd_digest. Also set top-level "
    'channel_prd_intent={"decision":"bind_channel_prd","source_quote":'
    '"an exact verbatim substring from the current user requirement chain '
    'explicitly requesting this PRD handoff"}. Selectors and '
    "channel_prd_intent must appear together. Never add them for an unrelated "
    "Issue, Refactor, General, or Research Task merely because a canonical PRD "
    "is available. "
    "Do not nest contract or channel_authority; runtime binds the exact "
    "authority. Include a second no-action alternative with "
    "effect.mode=continue, not defer, and do not combine this Plan with "
    "action_proposal. task_create Plan is reserved for this authorized Channel "
    "PRD handoff. The flat scope must cover every mandatory acceptance "
    "criterion's implementation plus test, runner/config, and evidence-producer "
    "paths, including planned paths that do not exist yet. Do not bind browser "
    "acceptance only to unit/build files or omit Playwright/evidence paths. For "
    "an unrelated direct Task without an artifact, never use "
    "subject_type=task_create; emit a create-task action_proposal instead. For "
    "an existing Task, return a task_workflow Plan whose "
    "executable options use mode=propose, action=workflow-start, and exact "
    "task_id, route_id, objective, config_digest, and parameters. The selected "
    "option is bound to the current task_contract_digest and becomes a separate "
    "Approve proposal. Put the question and options directly in top-level "
    "plan_request; do not add payload.workflow_plan or plan_request.payload. "
    "A no-action alternative uses effect.mode=continue with no action or payload, "
    "never defer. parameters may contain only executable adapter keys from this "
    f"list: {TASK_WORKFLOW_PARAMETER_KEYS_TEXT}. Keep business requirements, "
    "non-goals, verification prose, and output descriptions in the Task contract "
    "or objective instead of inventing parameter keys. The selected option becomes a separate "
    "Approve proposal. workflow-invoke(pattern_id) is "
    "a compatibility adapter and must not be the normal Kanban product output. "
    "These are proposals, not completed effects, until owner approval succeeds."
)
