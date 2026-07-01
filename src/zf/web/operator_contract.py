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
