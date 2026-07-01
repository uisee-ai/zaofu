"""Stateless payload/parsing helpers for controlled actions (moved verbatim)."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any
from zf.core.config.schema import ZfConfig
from zf.core.security.redaction import redact_obj
from zf.core.task.schema import Task
from zf.core.task.schema import TaskContract
from zf.core.task.schema import TaskEvidence
from zf.core.task.store import TaskStore
from zf.runtime.automation_projection import AUTOMATIONS
from zf.runtime.channel_contracts import CHANNEL_DISCUSSION_MODES
from zf.runtime.channel_contracts import normalize_permissions
from zf.runtime.channel_contracts import validate_channel_member_contract
from zf.runtime.operator_intent import validate_operator_intent_payload
import hashlib
import json
import re
import yaml


def _approval_ref(payload: dict) -> str:
    return str(
        payload.get("owner_approval_event_id")
        or payload.get("approval_event_id")
        or payload.get("approval_ref")
        or payload.get("approved_by")
        or ""
    ).strip()


def _automation_output_summary(outputs: list[Any]) -> str:
    parts: list[str] = []
    for output in outputs[:3]:
        if not isinstance(output, dict):
            continue
        summary = str(output.get("summary") or "").strip()
        if summary:
            parts.append(summary)
    return "; ".join(parts)


def _channel_member_can_receive(member: dict[str, Any]) -> bool:
    member_id = str(member.get("member_id") or "")
    if not member_id:
        return False
    if str(member.get("member_type") or "") in {"observer", "readonly-reviewer"}:
        return False
    if str(member.get("status") or "").lower() in {"removed", "suspended", "rejected", "failed"}:
        return False
    permissions = _string_list(member.get("permissions"))
    return not permissions or "message" in permissions


def _compact_automation_outputs(outputs: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for output in outputs[:10]:
        if not isinstance(output, dict):
            continue
        row: dict[str, Any] = {
            "type": str(output.get("type") or "report"),
            "summary": str(output.get("summary") or ""),
        }
        for key in ("project_id", "window"):
            if output.get(key):
                row[key] = str(output.get(key))
        rows.append(row)
    return redact_obj(rows)


def _dedupe_ids(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _normal_channel_id(value: object) -> str:
    raw = str(value or "").strip()
    text = raw.lower().lstrip("#")
    text = re.sub(r"[^a-z0-9._-]+", "-", text).strip("-._")
    if not text:
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8] if raw else ""
        text = digest
    if not text:
        return ""
    if not text.startswith("ch-"):
        text = f"ch-{text}"
    return text[:80].strip("-._")


def _optional_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _proposal_id(action: str, payload: dict, requested_event_id: str) -> str:
    seed = {
        "action": action,
        "task_id": str(payload.get("task_id") or ""),
        "project_id": str(payload.get("project_id") or ""),
        "requested_event_id": requested_event_id,
        "objective": str(
            payload.get("objective")
            or payload.get("message")
            or payload.get("reason")
            or ""
        ),
    }
    encoded = json.dumps(
        seed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "oprop-" + hashlib.sha1(encoded).hexdigest()[:12]


def _provider_binding_id(payload: dict[str, Any]) -> str:
    return str(
        payload.get("provider_binding_id")
        or payload.get("binding_id")
        or payload.get("binding")
        or ""
    ).strip()


def _requested_fanout_id(stage_id: str, payload: dict) -> str:
    basis = "|".join([
        stage_id,
        str(payload.get("task_id") or ""),
        str(payload.get("pdd_id") or ""),
        str(payload.get("trace_id") or ""),
        str(payload.get("reason") or ""),
    ])
    return f"fanout-{hashlib.sha1(basis.encode('utf-8')).hexdigest()[:10]}"


def _required_text(payload: dict, key: str) -> str:
    return str(payload.get(key) or "").strip()


def _runtime_impact_summary(state_dir: Path) -> dict[str, Any]:
    tasks: list[dict[str, str]] = []
    try:
        for task in TaskStore(Path(state_dir) / "kanban.json").list_all():
            if str(task.status or "") in {"backlog", "todo", "in_progress", "blocked", "review", "verify"}:
                tasks.append({
                    "task_id": task.id,
                    "title": task.title,
                    "status": task.status,
                    "assigned_to": task.assigned_to,
                })
    except Exception:
        tasks = []

    role_rows: list[dict[str, str]] = []
    path = Path(state_dir) / "role_sessions.yaml"
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
        if isinstance(data, dict):
            for instance_id, row in sorted(data.items()):
                if not isinstance(row, dict):
                    continue
                role_rows.append({
                    "instance_id": str(instance_id),
                    "role": str(row.get("role") or row.get("role_name") or ""),
                    "state": str(row.get("state") or ""),
                    "current_task_id": str(row.get("current_task_id") or row.get("task_id") or ""),
                })
    return {
        "schema_version": "runtime.impact-summary.v0",
        "active_task_count": len(tasks),
        "active_tasks": tasks[:20],
        "role_session_count": len(role_rows),
        "role_sessions": role_rows[:30],
        "recovery_plan": [
            "capture current kanban/events/role_sessions snapshot",
            "pause or gracefully stop runtime through zf lifecycle",
            "requeue or recover in-progress work according to shutdown policy",
            "start runtime with same project config",
            "run supervisor inspection and emit recovery brief",
        ],
        "direct_tmux_kill_allowed": False,
    }


def _safe_channel_permissions(value: object) -> list[str]:
    return normalize_permissions(value)


def _safe_int(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _stable_control_id(prefix: str, *parts: object) -> str:
    basis = "|".join(str(part or "") for part in parts)
    return f"{prefix}-{hashlib.sha1(basis.encode('utf-8')).hexdigest()[:12]}"


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _synthesis_target_member(channel: dict[str, Any]) -> str:
    members = [
        item for item in list(channel.get("members") or [])
        if isinstance(item, dict)
    ]
    discussion = channel.get("discussion") if isinstance(channel.get("discussion"), dict) else {}
    default_member_id = str((discussion or {}).get("default_responder_id") or "").strip()
    if default_member_id:
        default_member = next(
            (item for item in members if str(item.get("member_id") or "") == default_member_id),
            {},
        )
        if _channel_member_can_receive(default_member):
            return default_member_id
    for role in ("synthesizer", "facilitator"):
        for member in members:
            if str(member.get("channel_role") or "") == role and _channel_member_can_receive(member):
                return str(member.get("member_id") or "")
    for member in members:
        permissions = set(_string_list(member.get("permissions")))
        if {"message", "summarize"}.issubset(permissions) and _channel_member_can_receive(member):
            return str(member.get("member_id") or "")
    return ""


def _task_contract_from_payload(value: object) -> TaskContract:
    if not isinstance(value, dict):
        return TaskContract()
    return TaskContract(
        behavior=str(value.get("behavior") or ""),
        verification=str(value.get("verification") or ""),
        verification_tiers=_string_list(value.get("verification_tiers")),
        validation=(
            value.get("validation") if isinstance(value.get("validation"), dict) else {}
        ),
        scope=_string_list(value.get("scope")),
        exclusions=_string_list(value.get("exclusions")),
        acceptance=str(value.get("acceptance") or "exit_code=0"),
        rework_to=str(value.get("rework_to") or ""),
    )


def _task_evidence_from_payload(task: Task, value: object) -> TaskEvidence | None:
    if not isinstance(value, dict):
        return None
    current = asdict(task.evidence) if task.evidence is not None else asdict(TaskEvidence())
    changed = False
    for key in ("commit", "output_summary", "verified_at"):
        if key in value:
            current[key] = str(value.get(key) or "")
            changed = True
    if "files_touched" in value:
        current["files_touched"] = _string_list(value.get("files_touched"))
        changed = True
    if "commits" in value:
        current["commits"] = _string_list(value.get("commits"))
        changed = True
    return TaskEvidence(**current) if changed else None


def _task_id_from_payload(payload: dict) -> str | None:
    task_id = str(payload.get("task_id") or "")
    return task_id or None


def _task_metadata_payload(payload: dict) -> dict:
    return {
        key: payload.get(key)
        for key in ("labels", "notes", "description", "pdd_id", "tdd_id", "trace_id", "fanout_id", "run_id")
        if key in payload
    }


def _task_priority(value: object) -> int:
    try:
        priority = int(value)
    except (TypeError, ValueError):
        priority = 3
    return max(0, min(5, priority))


def _task_updates_from_payload(task: Task, payload: dict) -> dict:
    updates: dict[str, Any] = {}
    if "status" in payload:
        status = str(payload.get("status") or "").strip()
        if status in {
            "backlog",
            "ready",
            "todo",
            "in_progress",
            "review",
            "testing",
            "blocked",
            "done",
            "cancelled",
        }:
            updates["status"] = "backlog" if status in {"ready", "todo"} else status
    for key in ("title", "blocked_reason"):
        if key in payload:
            updates[key] = str(payload.get(key) or "")
    if "priority" in payload:
        updates["priority"] = _task_priority(payload.get("priority"))
    if "assigned_to" in payload or "owner" in payload:
        updates["assigned_to"] = _optional_str(payload.get("assigned_to") or payload.get("owner"))
    if "skills_required" in payload or "skills" in payload:
        updates["skills_required"] = _string_list(payload.get("skills_required") or payload.get("skills"))
    if "blocked_by" in payload:
        updates["blocked_by"] = _string_list(payload.get("blocked_by"))
    if isinstance(payload.get("contract"), dict):
        current = asdict(task.contract)
        current.update(payload["contract"])
        updates["contract"] = _task_contract_from_payload(current)
    if isinstance(payload.get("evidence"), dict):
        evidence = _task_evidence_from_payload(task, payload["evidence"])
        if evidence is not None:
            updates["evidence"] = evidence
    return updates


def _workflow_stage(config: ZfConfig | None, stage_id: str) -> Any:
    if config is None:
        return None
    for stage in getattr(config.workflow, "stages", []) or []:
        if getattr(stage, "id", "") == stage_id:
            return stage
    return None


def validate_shared_action_payload(
    action: str,
    payload: dict,
    *,
    config: ZfConfig | None = None,
) -> str:
    if action == "create-task" and not str(payload.get("title") or "").strip():
        return "title is required"
    if action == "update-task" and not str(payload.get("task_id") or "").strip():
        return "task_id is required"
    if action == "request-fanout":
        stage_id = str(payload.get("stage_id") or "")
        if not stage_id:
            return "stage_id is required"
        stage = _workflow_stage(config, stage_id)
        if stage is None:
            return f"fanout stage {stage_id!r} is not declared in zf.yaml"
        if not str(stage.topology or "").startswith("fanout_"):
            return f"workflow stage {stage_id!r} is not a fanout topology"
    if action == "channel-post-message":
        if not _required_text(payload, "channel_id"):
            return "channel_id is required"
        if not str(payload.get("text") or payload.get("message") or "").strip():
            return "text is required"
    if action == "channel-create":
        name = _required_text(payload, "name") or _required_text(payload, "channel_name")
        if not name:
            return "name is required"
        channel_id = _normal_channel_id(payload.get("channel_id") or payload.get("id") or name)
        if not channel_id:
            return "channel_id must include a letter or number"
    if action == "channel-invite-member":
        if not _required_text(payload, "channel_id"):
            return "channel_id is required"
        if not _required_text(payload, "member_id"):
            return "member_id is required"
        contract_error = validate_channel_member_contract(payload)
        if contract_error:
            return contract_error
    if action == "channel-update-member-permission":
        if not _required_text(payload, "channel_id"):
            return "channel_id is required"
        if not _required_text(payload, "member_id"):
            return "member_id is required"
        if not _required_text(payload, "permission_profile"):
            return "permission_profile is required"
        contract_error = validate_channel_member_contract(payload)
        if contract_error:
            return contract_error
    if action == "channel-synthesis":
        if not _required_text(payload, "channel_id"):
            return "channel_id is required"
        if not str(payload.get("decision") or "").strip():
            return "decision is required"
        if not str(payload.get("summary") or "").strip():
            return "summary is required"
    if action == "channel-synthesis-request":
        if not _required_text(payload, "channel_id"):
            return "channel_id is required"
    if action == "workflow-invoke":
        if not _required_text(payload, "task_id"):
            return "task_id is required"
        pattern_id = _required_text(payload, "pattern_id")
        if not pattern_id:
            return "pattern_id is required"
        stage = _workflow_stage(config, pattern_id)
        if stage is None:
            return f"execution pattern {pattern_id!r} is not declared in zf.yaml"
    if action == "channel-drain-replies":
        if not _required_text(payload, "channel_id"):
            return "channel_id is required"
    if action == "channel-mark-read":
        if not _required_text(payload, "channel_id"):
            return "channel_id is required"
    if action == "channel-handoff":
        for key in ("channel_id", "message_id", "member_id", "target_member_id", "reason"):
            if not _required_text(payload, key):
                return f"{key} is required"
    if action == "channel-discussion-mode":
        if not _required_text(payload, "channel_id"):
            return "channel_id is required"
        mode = _required_text(payload, "mode")
        if mode not in CHANNEL_DISCUSSION_MODES:
            return "mode must be one of " + ", ".join(sorted(CHANNEL_DISCUSSION_MODES))
    if action == "channel-owner-report":
        if not _required_text(payload, "channel_id"):
            return "channel_id is required"
        if not _required_text(payload, "owner_id"):
            return "owner_id is required"
    if action == "automation-run":
        automation_id = _required_text(payload, "automation_id") or _required_text(payload, "id")
        if not automation_id:
            return "automation_id is required"
        if automation_id not in AUTOMATIONS:
            return "automation_id must be one of " + ", ".join(AUTOMATIONS)
        trigger = _required_text(payload, "trigger")
        if trigger and trigger not in {"manual", "schedule", "event-window", "webhook"}:
            return "trigger must be one of event-window, manual, schedule, webhook"
    if action == "maintenance-prepare":
        if not (
            _required_text(payload, "trigger_id")
            or _required_text(payload, "trigger")
            or _required_text(payload, "proposal_id")
        ):
            return "trigger_id is required"
        if (
            payload.get("checkpoint")
            or payload.get("create_checkpoint")
            or payload.get("checkpoint_required")
        ) and not _required_text(payload, "task_id"):
            return "task_id is required when checkpoint is requested"
    if action in {
        "attention-ack",
        "attention-snooze",
        "attention-resolve",
        "attention-feedback",
        "attention-escalate",
    }:
        if not (_required_text(payload, "attention_id") or _required_text(payload, "fingerprint")):
            return "attention_id or fingerprint is required"
        if action == "attention-snooze" and not _required_text(payload, "snooze_until"):
            return "snooze_until is required"
    if action == "operator-intent-create":
        return validate_operator_intent_payload(payload)
    if action in {"operator-intent-approve", "operator-intent-reject"}:
        if not _required_text(payload, "intent_id"):
            return "intent_id is required"
    if action == "workflow-batch-resume":
        if not _required_text(payload, "checkpoint_id"):
            return "checkpoint_id is required"
        if not _required_text(payload, "safe_resume_action"):
            return "safe_resume_action is required"
        if (
            _required_text(payload, "safe_resume_action") == "trigger_rework"
            and not bool(payload.get("mutating_resume_supported"))
        ):
            return "trigger_rework requires explicit mutating_resume_supported"
    if action == "candidate-rework-apply":
        rework_action = _required_text(payload, "candidate_rework_action")
        if rework_action not in {"retrigger", "replan", "escalate"}:
            return "candidate_rework_action must be retrigger, replan, or escalate"
        if not _required_text(payload, "checkpoint_id"):
            return "checkpoint_id is required"
        if not _required_text(payload, "pdd_id"):
            return "pdd_id is required"
        if not _required_text(payload, "source_event_id"):
            return "source_event_id is required"
        if rework_action == "retrigger":
            for key in ("task_map_ref", "source_commit", "candidate_base_commit"):
                if not _required_text(payload, key):
                    return f"{key} is required"
    if action == "idea-to-product":
        if not (
            _required_text(payload, "objective")
            or _required_text(payload, "message")
            or _required_text(payload, "title")
        ):
            return "objective or message is required"
    if action in {"provider-dev-chat-start", "provider-dev-chat-send"}:
        if not (
            _required_text(payload, "message")
            or _required_text(payload, "objective")
        ):
            return "message or objective is required"
    if action == "workflow-config-propose":
        if not (
            _required_text(payload, "objective")
            or _required_text(payload, "message")
            or _required_text(payload, "patch_ref")
        ):
            return "objective, message, or patch_ref is required"
    if action == "workflow-config-validate":
        if not (
            _required_text(payload, "proposal_id")
            or _required_text(payload, "patch_ref")
        ):
            return "proposal_id or patch_ref is required"
    if action == "workflow-config-apply":
        if not _required_text(payload, "patch_ref"):
            return "patch_ref is required"
        if not _required_text(payload, "validation_result_ref"):
            return "validation_result_ref is required"
        if not _approval_ref(payload):
            return "owner approval is required"
    if action in {"runtime-stop", "runtime-restart"}:
        if not (payload.get("proposal_only") or payload.get("dry_run") or _approval_ref(payload)):
            return "owner approval is required unless proposal_only is true"
    return ""
