"""Resource ceilings for a resident diagnosis bound to an admitted production Run."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path

from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.runtime.run_contract import hydrate_run_effective_config
from zf.runtime.run_scope import run_aliases
from zf.runtime.workflow_budget_guard import _effective_run_limits


METERED_LIMITS = ("timeout_seconds", "max_usage_samples", "token_budget", "cost_budget_usd")


def ceiling(*values: float | int | None) -> float | int:
    """In workflow/profile limits, zero denotes no ceiling."""
    return min((v for v in values if v is not None and v > 0), default=0)


def monetary_cap(*values: float | None) -> float | None:
    """Global and per-role caps allow zero spend; only None removes the cap."""
    return min((v for v in values if v is not None), default=None)


@dataclass(frozen=True)
class DiagnosticResourcePolicy:
    run: dict[str, float | int]
    profiles: dict[str, dict]
    roles: dict[str, dict]
    global_usd: float | None
    request_id: str
    admission_id: str
    explicit_template: bool = False


def resident_resource_policy(
    state: Path, parent: ZfConfig | None, seed: str, *, worktree: Path, explicit_template: bool
) -> DiagnosticResourcePolicy | None:
    """A seed identifies a request. Only matching kernel facts and their immutable config authorize it."""
    if not seed or parent is None:
        return None
    try:
        binding = json.loads(seed)
    except (TypeError, ValueError):
        return None
    if not isinstance(binding, dict) or binding.get("source_state_dir") != str(state.resolve()):
        return None
    request_id = binding.get("diagnostic_request_id")
    run_id = binding.get("workflow_run_id") or binding.get("run_id")
    if not request_id or not run_id:
        return None
    log = event_log_from_project(state, config=parent)
    try:
        events = log.read_all()
    finally:
        log.close()
    request = next(
        (
            e
            for e in reversed(events)
            if e.type == "autoresearch.loop.requested"
            and e.origin == "kernel"
            and e.payload.get("loop_request_id") == request_id
            and (e.payload.get("workflow_run_id") or e.payload.get("run_id")) == run_id
        ),
        None,
    )
    if not request or request.payload.get("scenarios") != ["incident-reproduction"]:
        return None
    position = events.index(request)
    lifecycle = next(
        (
            e
            for e in reversed(events[position + 1 :])
            if e.actor == "zf-autoresearch-resident"
            and e.origin in {"", "kernel"}
            and e.payload.get("loop_request_id") == request_id
            and e.type
            in {
                "autoresearch.loop.started",
                "autoresearch.loop.completed",
                "autoresearch.loop.failed",
                "autoresearch.loop.skipped",
            }
        ),
        None,
    )
    if (
        not lifecycle
        or lifecycle.type != "autoresearch.loop.started"
        or lifecycle.payload.get("worktree") != str(worktree.resolve())
    ):
        raise ValueError(
            "Diagnostic resource policy requires the current resident execution and its exact worktree"
        )
    admitted = next(
        (
            e
            for e in reversed(events[:position])
            if e.type == "run.admission.admitted"
            and e.origin == "kernel"
            and e.payload.get("run_id") == run_id
        ),
        None,
    )
    if admitted is None or not admitted.payload.get("effective_config_digest"):
        return None  # Legacy parents without snapshot authority retain their diagnostic template.
    invoke = next(
        (
            e
            for e in events
            if e.id == admitted.payload.get("source_event_id") and e.type == "workflow.invoke.requested"
        ),
        None,
    )
    if not invoke or invoke.payload.get("workflow_run_id") != run_id:
        raise ValueError("Diagnostic parent Run is missing its admitted invocation")
    snap = hydrate_run_effective_config(
        state,
        {
            "config": {
                "effective_snapshot_ref": invoke.payload.get("effective_config_ref"),
                "effective_snapshot_digest": admitted.payload["effective_config_digest"],
            }
        },
    )
    workflow = snap["workflow"]
    limits, _ = _effective_run_limits(
        events,
        admitted_event=admitted,
        workflow_run_id=run_id,
        configured_run_limits=workflow["run_limits"],
        aliases=run_aliases(events),
    )
    profiles = {
        name: {key: spec["limits"].get(key, 0) for key in METERED_LIMITS}
        for name, spec in workflow["execution_profiles"].items()
    }
    roles = {
        row.get("instance_id") or row["name"]: {
            "default_profile": (row.get("execution") or {}).get("default_profile"),
            "budget_usd": row.get("budget_usd"),
        }
        for row in snap["roles"]
    }
    return DiagnosticResourcePolicy(
        limits,
        profiles,
        roles,
        snap.get("global_budget_usd"),
        request_id,
        admitted.id,
        explicit_template=explicit_template,
    )


def apply_diagnostic_resource_policy(
    data: dict, policy: DiagnosticResourcePolicy, budget_usd: float | None
) -> None:
    workflow = data.setdefault("workflow", {})
    run = workflow.setdefault("run_limits", {})
    for key in ("timeout_seconds", "token_budget", "cost_budget_usd"):
        run[key] = ceiling(
            policy.run.get(key),
            run.get(key) if policy.explicit_template else 0,
            budget_usd if key == "cost_budget_usd" else 0,
        )
    data["global_budget_usd"] = monetary_cap(
        policy.global_usd, budget_usd, data.get("global_budget_usd") if policy.explicit_template else None
    )
    # Only meter settings are inheritable. Child count, depth, grants, strategy and tools stay local.
    profiles = workflow.get("execution_profiles", {})
    for name, profile in profiles.items():
        applicable = [policy.profiles[name]] if name in policy.profiles else []
        for role in data.get("roles", []):
            execution = role.get("execution") or {}
            if name not in {execution.get("default_profile"), *(execution.get("profile_allowlist") or [])}:
                continue
            parent_role = policy.roles.get(role.get("instance_id") or role.get("name"), {})
            parent_profile = parent_role.get("default_profile")
            if parent_profile in policy.profiles:
                applicable.append(policy.profiles[parent_profile])
        if not applicable:
            continue
        limits = profile.setdefault("limits", {})
        for key in METERED_LIMITS:
            limits[key] = ceiling(
                *(item.get(key) for item in applicable), limits.get(key) if policy.explicit_template else 0
            )
    for role in data.get("roles", []):
        bound = policy.roles.get(role.get("instance_id") or role.get("name"))
        if bound:
            role["budget_usd"] = monetary_cap(
                bound.get("budget_usd"), role.get("budget_usd") if policy.explicit_template else None
            )


class ExplicitLoopSetting(argparse.Action):
    """Distinguish a caller's diagnostic restriction from the standalone experiment defaults."""

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        setattr(namespace, self.dest + "_explicit", True)


def loop_resource_settings(
    args: argparse.Namespace,
    parent_state_dir: Path,
    parent: ZfConfig | None,
) -> tuple[DiagnosticResourcePolicy | None, float | None]:
    resources = None
    if args.scenarios == ["incident-reproduction"] and not args.bypass_autoresearch:
        resources = resident_resource_policy(
            parent_state_dir,
            parent,
            args.seed_text,
            worktree=args.worktree,
            explicit_template=getattr(args, "config_template_explicit", False),
        )
    if resources is not None:
        explicit_budget = args.budget_usd if getattr(args, "budget_usd_explicit", False) else None
        if explicit_budget is not None and (not math.isfinite(explicit_budget) or explicit_budget <= 0):
            raise ValueError("An explicitly funded diagnostic budget must be positive")
        budget = monetary_cap(explicit_budget, resources.global_usd, resources.run.get("cost_budget_usd") or None)
        if budget is not None and budget <= 0:
            raise ValueError("The admitted parent Run authorizes no monetary spend for diagnosis")
    else:
        budget = min(
            [args.budget_usd]
            + [
                cap
                for cap in (
                    (parent.workflow.run_limits.cost_budget_usd, parent.global_budget_usd)
                    if parent is not None
                    else ()
                )
                if cap is not None and cap > 0
            ]
        )
    return resources, budget
