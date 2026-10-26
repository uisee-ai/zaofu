"""Shared helpers for portable typed-flow configuration drafts."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


SEMANTIC_CONTROL_CHECKPOINTS = (
    "plan_candidate",
)


def orchestration_spec(*, tier: str) -> dict[str, Any]:
    """Return a route-sized OA policy for generated projects."""

    if tier == "full":
        policy = {
            **_semantic_control_policy(),
            "flow_policies": {
                "research": _exception_advisor_policy(),
                "workflow": _exception_advisor_policy(),
            },
        }
    elif tier == "light":
        policy = _exception_advisor_policy()
    elif tier == "multi":
        policy = {
            **_exception_advisor_policy(),
            "flow_policies": {
                "issue": _exception_advisor_policy(),
                "prd": _semantic_control_policy(include_limits=False),
                "refactor": _semantic_control_policy(include_limits=False),
                "workflow": _exception_advisor_policy(),
                "research": _exception_advisor_policy(),
            },
        }
    else:
        raise ValueError(f"unsupported orchestration tier {tier!r}")
    return {"workflow": {"orchestration": policy}}


def _semantic_control_policy(*, include_limits: bool = True) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "mode": "semantic_control",
        "checkpoints": list(SEMANTIC_CONTROL_CHECKPOINTS),
        "checkpoint_policies": {
            "plan_candidate": "shadow",
        },
    }
    if include_limits:
        policy.update({"max_plan_revisions": 2, "no_progress_limit": 2})
    return policy


def _exception_advisor_policy() -> dict[str, Any]:
    return {"mode": "exception_advisor"}


def default_tmux_session(project: str) -> str:
    slug = re.sub(
        r"[^a-zA-Z0-9_-]+",
        "-",
        str(project or "flow").strip(),
    ).strip("-")
    slug = slug.lower()[:48] or "flow"
    return f"zf-{slug}"


def explicit_orchestrator_spec(
    backend: str,
    *,
    semantic_control: bool = False,
) -> dict[str, Any]:
    triggers = [
        "dispatch.silent_stall",
        "orchestrator.rework.triage.requested",
    ]
    if semantic_control:
        triggers.append("orchestrator.semantic.checkpoint.requested")
    return {
        "orchestrator": {
            "backend": backend,
            "wake_min_interval_s": 5,
        },
        "roles": [{
            "name": "orchestrator",
            "instance_id": "orchestrator",
            "role_kind": "reader",
            "backend": backend,
            "permission_mode": "bypass",
            "transport": "tmux",
            "stuck_threshold_seconds": 900,
            "spawn_ready_timeout_seconds": 240,
            "triggers": triggers,
            "publishes": ["orchestrator.rework.triage.recorded"],
            "skills": [
                "zf-yoke-orchestrator-role-context",
                "zf-harness-state-sync",
            ],
        }],
    }


def draft_runtime_profile_doc(
    *,
    name: str,
    backend: str,
    kind: str = "",
    role_skill_bundles: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Return the executable runtime profile embedded in portable flow drafts."""

    run_manager_backend = backend if backend in {"codex", "claude-code"} else ""
    resident_enabled = bool(run_manager_backend)
    spec: dict[str, Any] = {
        "runtime": {
            "workdirs": {
                "enabled": True,
                "mode": "worktree",
            },
            "run_manager": {
                "backend": run_manager_backend,
                "resident_agent": {
                    "enabled": resident_enabled,
                    "session_mode": "dedicated",
                },
            },
            "autoresearch_resident": {
                "enabled": resident_enabled,
                "interval_seconds": 10,
                "max_actions_per_tick": 1,
            },
            "feishu_inbound": {
                "enabled": "${ZF_FEISHU_INBOUND_ENABLED:-false}",
                "mode": "bridge",
                "require_routing": True,
            },
        },
        "workflow": {
            "candidate_quality_source": "task_contract_required",
            "impl_self_check_required": True,
            "rework_routing": (
                {"static_gate.failed": "fix-lane-0"}
                if kind == "issue"
                else {"static_gate.failed": "dev-lane-0"}
                if kind in {"prd", "refactor"}
                else {}
            ),
            "work_units": {
                "enabled": True,
                "split_quality": {
                    "mode": "blocking",
                    "max_acceptance_criteria": 0,
                },
            },
        },
    }
    if kind in {"issue", "prd", "refactor"} and role_skill_bundles:
        spec["flow_defaults"] = {
            kind: {
                "roleSkillBundles": role_skill_bundles,
            },
        }
    return {
        "apiVersion": "zaofu.dev/v1",
        "kind": "ConfigProfile",
        "metadata": {"name": name},
        "spec": spec,
    }


def non_empty_mapping(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, item in value.items():
        if not isinstance(item, list):
            continue
        values = [str(entry) for entry in item if str(entry).strip()]
        if values:
            out[str(key)] = values
    return out


def skill_sources_from_adapter_plan(
    adapter_plan: dict[str, Any],
    *,
    project_root: Path,
) -> list[dict[str, str]]:
    loaded = adapter_plan.get("loaded_skills")
    if not isinstance(loaded, list):
        return []
    source_paths: dict[str, Path] = {}
    for item in loaded:
        if not isinstance(item, dict):
            continue
        source_name = str(item.get("source_name") or "")
        if source_name in {"", "project", "state", "yoke"}:
            continue
        source_ref = str(item.get("source_ref") or "")
        if not source_ref:
            continue
        skill_root = Path(source_ref).expanduser().parent.parent
        config_name = (
            "zaofu-skills"
            if source_name in {"zaofu", "skill-source:zaofu"}
            else source_name.removeprefix("skill-source:")
        )
        source_paths[config_name] = skill_root
    return [
        {
            "name": name,
            "path": _display_or_relative_path(root, project_root),
            "mode": "readonly",
        }
        for name, root in sorted(source_paths.items())
    ]


def _display_or_relative_path(path: Path, project_root: Path) -> str:
    root = project_root.expanduser().resolve()
    target = path.expanduser().resolve(strict=False)
    try:
        return os.path.relpath(target, root)
    except ValueError:
        return str(target)


__all__ = [
    "default_tmux_session",
    "draft_runtime_profile_doc",
    "explicit_orchestrator_spec",
    "non_empty_mapping",
    "orchestration_spec",
    "skill_sources_from_adapter_plan",
]
