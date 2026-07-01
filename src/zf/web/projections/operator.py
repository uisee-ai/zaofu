"""Projections layer: operator (moved verbatim from web/server.py)."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from zf.core.config.schema import ZfConfig
from zf.runtime.provider_capabilities import provider_capability_for_backend
from zf.web.operator_contract import KANBAN_AGENT_ALLOWED_ACTIONS
import hashlib
import json
import os
import shlex
import shutil
from zf.web.projections.common import _line_count, _read_json_file
from zf.web.projections.summaries import _safe_session_segment, _skills


def _operator_skills_available(
    state_dir: Path,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> dict:
    skills = _skills(state_dir, config=config, project_root=project_root)
    pool = skills.get("pool", []) if isinstance(skills, dict) else []
    enabled = skills.get("enabled", []) if isinstance(skills, dict) else []
    warnings = skills.get("warnings", []) if isinstance(skills, dict) else []
    names = {
        str(item.get("name") or "")
        for item in pool
        if isinstance(item, dict) and item.get("name")
    }
    for item in enabled:
        if not isinstance(item, dict):
            continue
        for skill in item.get("skills", []) or []:
            if skill:
                names.add(str(skill))
    return {
        "pool_path": str(skills.get("pool_path") or "") if isinstance(skills, dict) else "",
        "pool_count": len(pool) if isinstance(pool, list) else 0,
        "enabled_role_count": len(enabled) if isinstance(enabled, list) else 0,
        "names": sorted(names),
        "enabled_by_role": [
            {
                "role": str(item.get("role") or ""),
                "role_name": str(item.get("role_name") or ""),
                "backend": str(item.get("backend") or ""),
                "skills": list(item.get("skills", []) or []),
            }
            for item in enabled
            if isinstance(item, dict)
        ],
        "warnings": len(warnings) if isinstance(warnings, list) else 0,
    }


def _operator_task_evidence(state_dir: Path, task_id: str) -> dict:
    safe_task = _safe_session_segment(task_id)
    transcripts = []
    for path in sorted((state_dir / "operator" / "sessions").glob("*/kanban-agent.log")):
        session_key = path.parent.name
        if safe_task not in session_key and task_id not in session_key:
            continue
        transcripts.append({
            "kind": "operator_transcript",
            "path": str(path),
            "exists": path.exists(),
            "bytes": path.stat().st_size if path.exists() else 0,
            "lines": _line_count(path) if path.exists() else 0,
            "truth": "interaction_evidence_only",
        })
    current = _read_json_file(state_dir / "operator" / "kanban-agent.json")
    current_session = {}
    current_context_task_id = ""
    if isinstance(current, dict):
        current_context_task_id = str(current.get("context_task_id") or current.get("task_id") or "")
    if isinstance(current, dict) and current_context_task_id == task_id:
        current_session = {
            "session_id": str(current.get("session_id") or ""),
            "backend": str(current.get("backend") or ""),
            "status": str(current.get("status") or ""),
            "alive": bool(current.get("alive", False)),
            "scope": "project",
            "context_task_id": current_context_task_id,
            "transcript_path": str(current.get("transcript_path") or ""),
        }
    return {
        "transcript_truth": "interaction_evidence_only",
        "current_session": current_session,
        "transcripts": transcripts,
        "transcript_count": len(transcripts),
    }


def _operator_backend_options(*, configured_backend: str = "") -> list[dict[str, Any]]:
    return [
        {
            "id": "deterministic",
            "title": "deterministic",
            "available": True,
            "source": "builtin",
            "default": configured_backend not in {"codex", "claude-code"},
            "capabilities": _operator_backend_capabilities("deterministic"),
        },
        {
            "id": "codex",
            "title": "codex",
            "available": _operator_backend_command_available("codex"),
            "source": "cli",
            "default": configured_backend == "codex",
            "capabilities": _operator_backend_capabilities("codex"),
        },
        {
            "id": "claude-code",
            "title": "claude-code",
            "available": _operator_backend_command_available("claude-code"),
            "source": "cli",
            "default": configured_backend == "claude-code",
            "capabilities": _operator_backend_capabilities("claude-code"),
        },
        {
            "id": "claude-headless",
            "title": "claude headless",
            "available": _operator_backend_command_available("claude-headless"),
            "source": "headless",
            "default": configured_backend == "claude-headless",
            "capabilities": _operator_backend_capabilities("claude-headless"),
        },
        {
            "id": "codex-headless",
            "title": "codex headless",
            "available": _operator_backend_command_available("codex-headless"),
            "source": "headless",
            "default": configured_backend == "codex-headless",
            "capabilities": _operator_backend_capabilities("codex-headless"),
        },
    ]


def _operator_backend_capabilities(backend: str) -> dict[str, Any]:
    capabilities = provider_capability_for_backend(backend)
    if backend in {"claude-headless", "codex-headless"}:
        capabilities["cancel"] = "agent-session-cancel" in KANBAN_AGENT_ALLOWED_ACTIONS
    return capabilities


def _default_operator_backend(configured_backend: str = "") -> str:
    if configured_backend and _operator_backend_available(configured_backend):
        return configured_backend
    return "deterministic"


def _operator_backend_available(backend: str) -> bool:
    if backend == "deterministic":
        return True
    return _operator_backend_command_available(backend)


def _operator_backend_command_available(backend: str) -> bool:
    if backend == "codex":
        command = os.environ.get("ZF_KANBAN_AGENT_CODEX_CMD", "codex").strip()
    elif backend == "claude-code":
        command = os.environ.get("ZF_KANBAN_AGENT_CLAUDE_CMD", "claude").strip()
    elif backend == "codex-headless":
        command = os.environ.get(
            "ZF_KANBAN_AGENT_CODEX_HEADLESS_CMD",
            os.environ.get("ZF_KANBAN_AGENT_CODEX_CMD", "codex"),
        ).strip()
    elif backend == "claude-headless":
        command = os.environ.get(
            "ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD",
            os.environ.get("ZF_KANBAN_AGENT_CLAUDE_CMD", "claude"),
        ).strip()
    else:
        return False
    binary = shlex.split(command)[0] if command else ""
    return bool(binary and shutil.which(binary))


def _operator_session_id(
    state_dir: Path,
    *,
    backend: str,
    scope: str,
    task_id: str,
) -> str:
    project = hashlib.sha1(str(Path(state_dir).resolve()).encode("utf-8")).hexdigest()[:12]
    safe_backend = _safe_session_segment(backend or "deterministic")
    return f"kanban-agent:{project}:project:{safe_backend}"


def _operator_action_command(text: str) -> dict | None:
    raw = text.strip()
    if not raw.startswith("/action"):
        return None
    body = raw[len("/action"):].strip()
    if not body:
        return {
            "ok": False,
            "reason": "usage: /action ACTION_NAME {json_payload}",
        }
    try:
        if body.startswith("{"):
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError("action command JSON must be an object")
            action_name = str(data.get("action") or data.get("name") or "")
            if "payload" in data:
                payload = data.get("payload")
            else:
                payload = {key: value for key, value in data.items() if key not in {"action", "name"}}
        else:
            action_name, _, raw_payload = body.partition(" ")
            payload = json.loads(raw_payload) if raw_payload.strip() else {}
        if not action_name:
            raise ValueError("action name is required")
        if not isinstance(payload, dict):
            raise ValueError("action payload must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "reason": str(exc)}
    return {"ok": True, "action": action_name, "payload": payload}
