"""Strict compiler for immutable v4 Task Pipeline controller profiles."""

from __future__ import annotations

import hashlib
import json
from typing import Any


TASK_PIPELINE_PROFILE_SCHEMA = "task-pipeline-controller-profile.v1"
TASK_PIPELINE_PROFILE_IDS = {
    "issue": "issue-flow-v4-task-pipeline",
    "prd": "prd-flow-v4-task-pipeline",
    "refactor": "refactor-flow-v4-task-pipeline",
}


class TaskPipelineProfileError(ValueError):
    """A v4 Task Pipeline profile is invalid or semantically unsafe."""


_TOP_LEVEL_ALIASES = {
    "maxActiveTaskPipelines": "max_active_task_pipelines",
    "maxReworkAttempts": "max_rework_attempts",
    "integrationAdmission": "integration_admission",
    "workerLifecycle": "worker_lifecycle",
}
_TOP_LEVEL_KEYS = frozenset({
    "mode",
    "max_active_task_pipelines",
    "max_rework_attempts",
    "pools",
    "integration_admission",
    "backpressure",
    "worker_lifecycle",
    "affinity",
    "candidate",
})
_POOL_ALIASES = {
    "workerProfiles": "worker_profiles",
    "roleInstances": "role_instances",
}
_POOL_KEYS = frozenset({
    "capacity",
    "role",
    "role_instances",
    "skills",
    "capabilities",
    "worker_profiles",
})
_CANDIDATE_ALIASES = {
    "verifyWidth": "verify_width",
    "integrationCapacity": "integration_capacity",
    "rollingSmoke": "rolling_smoke",
    "incrementalEvent": "incremental_event",
    "freezeEvent": "freeze_event",
    "deliveryEvent": "delivery_event",
    "partialCandidateAutoShip": "partial_candidate_auto_ship",
    "finalVerifyTarget": "final_verify_target",
}
_CANDIDATE_KEYS = frozenset({
    "verify_width",
    "integration",
    "integration_capacity",
    "rolling_smoke",
    "incremental_event",
    "freeze_event",
    "delivery_event",
    "partial_candidate_auto_ship",
    "final_verify_target",
})


def compile_task_pipeline_profile(
    *,
    flow_kind: str,
    profile_id: str,
    raw: object,
    default_impl_roles: list[str],
    default_verify_roles: list[str],
) -> dict[str, Any] | None:
    """Compile one frozen policy without changing the legacy lane topology."""

    kind = str(flow_kind or "").strip().lower()
    selected = str(profile_id or "").strip()
    expected = TASK_PIPELINE_PROFILE_IDS.get(kind)
    if not selected and raw is None:
        return None
    if expected is None:
        raise TaskPipelineProfileError(f"unsupported Task Pipeline flow kind: {kind!r}")
    if selected != expected:
        raise TaskPipelineProfileError(
            f"{kind} Task Pipeline profile must be {expected!r}, got {selected!r}"
        )
    values = _normalize_mapping(
        raw,
        aliases=_TOP_LEVEL_ALIASES,
        allowed=_TOP_LEVEL_KEYS,
        context=f"{selected}.taskPipeline",
    )
    mode = str(values.get("mode") or "shadow").strip().lower()
    if mode not in {"shadow", "blocking"}:
        raise TaskPipelineProfileError(
            f"{selected}.taskPipeline.mode must be shadow or blocking"
        )
    max_active = _bounded_int(
        values.get("max_active_task_pipelines", 0),
        minimum=1,
        maximum=32,
        context=f"{selected}.maxActiveTaskPipelines",
    )
    pools_raw = _mapping(values.get("pools"), f"{selected}.pools")
    unknown_pools = sorted(set(pools_raw) - {"impl", "verify", "acceptance_review"})
    if unknown_pools:
        raise TaskPipelineProfileError(
            f"{selected}.pools: unknown pool(s) {unknown_pools}"
        )
    for required in ("impl", "verify"):
        if required not in pools_raw:
            raise TaskPipelineProfileError(f"{selected}.pools.{required} is required")
    pools = {
        "impl": _compile_pool(
            pools_raw["impl"],
            context=f"{selected}.pools.impl",
            default_roles=default_impl_roles,
        ),
        "verify": _compile_pool(
            pools_raw["verify"],
            context=f"{selected}.pools.verify",
            default_roles=default_verify_roles,
        ),
    }
    if "acceptance_review" in pools_raw:
        pools["acceptance_review"] = _compile_pool(
            pools_raw["acceptance_review"],
            context=f"{selected}.pools.acceptance_review",
            default_roles=[],
        )

    backpressure = _compile_backpressure(values.get("backpressure"), selected)
    lifecycle = _compile_worker_lifecycle(values.get("worker_lifecycle"), selected)
    affinity = _compile_affinity(values.get("affinity"), selected)
    admission = _compile_integration_admission(
        values.get("integration_admission"), selected
    )
    candidate = _compile_candidate(values.get("candidate"), selected)
    if "verify_width" in candidate:
        if mode != "blocking" or candidate["verify_width"] > len(default_verify_roles):
            raise TaskPipelineProfileError("candidate.verifyWidth requires blocking mode and must fit the configured verify roles")
    if admission["risk_review"]["enabled"] and "acceptance_review" not in pools:
        raise TaskPipelineProfileError(
            f"{selected}: enabled risk_review requires acceptance_review pool"
        )
    if admission["risk_review"]["enabled"]:
        review_pool = pools["acceptance_review"]
        review_skills = {
            *review_pool.get("skills", []),
            *(
                skill
                for profile in review_pool.get("worker_profiles", [])
                for skill in profile.get("skills", [])
            ),
        }
        if "zf-integration-acceptance-review" not in review_skills:
            raise TaskPipelineProfileError(
                f"{selected}: enabled risk_review requires "
                "zf-integration-acceptance-review skill"
            )
    normalized: dict[str, Any] = {
        "schema_version": TASK_PIPELINE_PROFILE_SCHEMA,
        "profile_id": selected,
        "flow_kind": kind,
        "mode": mode,
        "max_active_task_pipelines": max_active,
        "max_rework_attempts": _bounded_int(
            values.get("max_rework_attempts", 2),
            minimum=0,
            maximum=8,
            context=f"{selected}.maxReworkAttempts",
        ),
        "pools": pools,
        "integration_admission": admission,
        "backpressure": backpressure,
        "worker_lifecycle": lifecycle,
        "affinity": affinity,
        "candidate": candidate,
    }
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    normalized["profile_digest"] = hashlib.sha256(encoded).hexdigest()
    return normalized


def _compile_pool(
    raw: object,
    *,
    context: str,
    default_roles: list[str],
) -> dict[str, Any]:
    values = _normalize_mapping(
        raw,
        aliases=_POOL_ALIASES,
        allowed=_POOL_KEYS,
        context=context,
    )
    capacity = _bounded_int(
        values.get("capacity", 0),
        minimum=1,
        maximum=16,
        context=f"{context}.capacity",
    )
    roles = _string_list(values.get("role_instances"), f"{context}.roleInstances")
    role = str(values.get("role") or "").strip()
    if role:
        roles.append(role)
    worker_profiles_raw = values.get("worker_profiles") or []
    if not isinstance(worker_profiles_raw, list):
        raise TaskPipelineProfileError(f"{context}.workerProfiles must be a list")
    worker_profiles: list[dict[str, Any]] = []
    for index, item in enumerate(worker_profiles_raw):
        profile = _normalize_mapping(
            item,
            aliases={},
            allowed=frozenset({"name", "role", "skills", "capabilities"}),
            context=f"{context}.workerProfiles[{index}]",
        )
        name = str(profile.get("name") or "").strip()
        worker_role = str(profile.get("role") or "").strip()
        if not name or not worker_role:
            raise TaskPipelineProfileError(
                f"{context}.workerProfiles[{index}] requires name and role"
            )
        worker_profiles.append({
            "name": name,
            "role": worker_role,
            "skills": _string_list(
                profile.get("skills"),
                f"{context}.workerProfiles[{index}].skills",
            ),
            "capabilities": _string_list(
                profile.get("capabilities"),
                f"{context}.workerProfiles[{index}].capabilities",
            ),
        })
        roles.append(worker_role)
    roles = list(dict.fromkeys([*roles, *default_roles]))
    if not roles:
        raise TaskPipelineProfileError(f"{context} requires a role or worker profile")
    if capacity > len(roles):
        raise TaskPipelineProfileError(
            f"{context}.capacity exceeds predeclared role instances"
        )
    return {
        "capacity": capacity,
        "role_instances": roles,
        "skills": _string_list(values.get("skills"), f"{context}.skills"),
        "capabilities": _string_list(
            values.get("capabilities"), f"{context}.capabilities"
        ),
        "worker_profiles": worker_profiles,
    }


def _compile_backpressure(raw: object, profile_id: str) -> dict[str, int]:
    context = f"{profile_id}.backpressure"
    values = _normalize_mapping(
        raw,
        aliases={
            "maxUnverifiedTasks": "max_unverified_tasks",
            "maxIntegrationQueue": "max_integration_queue",
        },
        allowed=frozenset({"max_unverified_tasks", "max_integration_queue"}),
        context=context,
    )
    return {
        "max_unverified_tasks": _bounded_int(
            values.get("max_unverified_tasks", 0),
            minimum=1,
            maximum=64,
            context=f"{context}.maxUnverifiedTasks",
        ),
        "max_integration_queue": _bounded_int(
            values.get("max_integration_queue", 0),
            minimum=1,
            maximum=32,
            context=f"{context}.maxIntegrationQueue",
        ),
    }


def _compile_worker_lifecycle(raw: object, profile_id: str) -> dict[str, Any]:
    context = f"{profile_id}.workerLifecycle"
    values = _normalize_mapping(
        raw,
        aliases={"idleSeconds": "idle_seconds"},
        allowed=frozenset({"mode", "idle_seconds"}),
        context=context,
    )
    mode = str(values.get("mode") or "on_demand").strip()
    if mode != "on_demand":
        raise TaskPipelineProfileError(f"{context}.mode must be on_demand")
    return {
        "mode": mode,
        "idle_seconds": _bounded_int(
            values.get("idle_seconds", 120),
            minimum=10,
            maximum=86_400,
            context=f"{context}.idleSeconds",
        ),
    }


def _compile_affinity(raw: object, profile_id: str) -> dict[str, str]:
    context = f"{profile_id}.affinity"
    values = _normalize_mapping(
        raw,
        aliases={
            "implRework": "impl_rework",
            "verifyIndependence": "verify_independence",
            "sessionBinding": "session_binding",
            "crossTaskContext": "cross_task_context",
        },
        allowed=frozenset({
            "impl_rework",
            "verify_independence",
            "session_binding",
            "cross_task_context",
        }),
        context=context,
    )
    result = {
        "impl_rework": str(values.get("impl_rework") or "prefer_previous_session"),
        "verify_independence": str(values.get("verify_independence") or "different_role"),
        "session_binding": str(values.get("session_binding") or "task_stage"),
        "cross_task_context": str(values.get("cross_task_context") or "fresh"),
    }
    expected = {
        "impl_rework": "prefer_previous_session",
        "verify_independence": "different_role",
        "session_binding": "task_stage",
        "cross_task_context": "fresh",
    }
    for key, value in expected.items():
        if result[key] != value:
            raise TaskPipelineProfileError(f"{context}.{key} must be {value!r}")
    return result


def _compile_integration_admission(raw: object, profile_id: str) -> dict[str, Any]:
    context = f"{profile_id}.integrationAdmission"
    values = _normalize_mapping(
        raw,
        aliases={"riskReview": "risk_review"},
        allowed=frozenset({"default", "risk_review"}),
        context=context,
    )
    default = str(values.get("default") or "verify_admitted").strip()
    if default not in {"verify_admitted", "risk_review"}:
        raise TaskPipelineProfileError(f"{context}.default is unknown: {default!r}")
    risk = _normalize_mapping(
        values.get("risk_review") or {},
        aliases={
            "forRisks": "for_risks",
            "timeoutSeconds": "timeout_seconds",
            "maxTurns": "max_turns",
            "budgetUsd": "budget_usd",
        },
        allowed=frozenset({
            "enabled",
            "for_risks",
            "timeout_seconds",
            "max_turns",
            "budget_usd",
            "fallback",
        }),
        context=f"{context}.riskReview",
    )
    enabled_raw = risk.get("enabled", False)
    if not isinstance(enabled_raw, bool):
        raise TaskPipelineProfileError(
            f"{context}.riskReview.enabled must be a boolean"
        )
    enabled = enabled_raw
    for_risks = _string_list(
        risk.get("for_risks") or ["high", "critical"],
        f"{context}.riskReview.forRisks",
    )
    unknown_risks = sorted(set(for_risks) - {"high", "critical"})
    if not for_risks or unknown_risks:
        raise TaskPipelineProfileError(
            f"{context}.riskReview.forRisks only admits high/critical"
        )
    result = {
        "enabled": enabled,
        "for_risks": for_risks,
        "timeout_seconds": _bounded_int(
            risk.get("timeout_seconds", 180),
            minimum=1,
            maximum=3600,
            context=f"{context}.riskReview.timeoutSeconds",
        ),
        "max_turns": _bounded_int(
            risk.get("max_turns", 1),
            minimum=1,
            maximum=1,
            context=f"{context}.riskReview.maxTurns",
        ),
        "budget_usd": _bounded_float(
            risk.get("budget_usd", 1.0),
            minimum=0.01,
            maximum=100.0,
            context=f"{context}.riskReview.budgetUsd",
        ),
        "fallback": str(risk.get("fallback") or "fail_closed").strip(),
    }
    if result["fallback"] != "fail_closed":
        raise TaskPipelineProfileError(
            f"{context}.riskReview.fallback must be fail_closed"
        )
    if default == "risk_review" and not enabled:
        raise TaskPipelineProfileError(
            f"{context}.default risk_review requires riskReview.enabled"
        )
    return {"default": default, "risk_review": result}


def _compile_candidate(raw: object, profile_id: str) -> dict[str, Any]:
    context = f"{profile_id}.candidate"
    values = _normalize_mapping(
        raw,
        aliases=_CANDIDATE_ALIASES,
        allowed=_CANDIDATE_KEYS,
        context=context,
    )
    result = {
        "integration": str(values.get("integration") or "incremental_serial_cas"),
        "integration_capacity": _bounded_int(
            values.get("integration_capacity", 1),
            minimum=1,
            maximum=1,
            context=f"{context}.integrationCapacity",
        ),
        "rolling_smoke": str(values.get("rolling_smoke") or "required"),
        "incremental_event": str(
            values.get("incremental_event") or "integration.queue.integrated"
        ),
        "freeze_event": str(values.get("freeze_event") or "candidate.ready"),
        "delivery_event": str(values.get("delivery_event") or "run.delivery.completed"),
        "partial_candidate_auto_ship": str(
            values.get("partial_candidate_auto_ship") or "forbidden"
        ),
        "final_verify_target": str(
            values.get("final_verify_target") or "frozen_exact_commit"
        ),
    }
    required = {
        "integration": "incremental_serial_cas",
        "rolling_smoke": "required",
        "partial_candidate_auto_ship": "forbidden",
        "final_verify_target": "frozen_exact_commit",
    }
    if "verify_width" in values:
        if type(values["verify_width"]) is not int:
            raise TaskPipelineProfileError(f"{context}.verifyWidth must be an integer")
        result["verify_width"] = _bounded_int(
            values["verify_width"], minimum=1, maximum=32,
            context=f"{context}.verifyWidth",
        )
    for key, expected in required.items():
        if result[key] != expected:
            raise TaskPipelineProfileError(f"{context}.{key} must be {expected!r}")
    events = [
        result["incremental_event"],
        result["freeze_event"],
        result["delivery_event"],
    ]
    if any(not value for value in events) or len(set(events)) != len(events):
        raise TaskPipelineProfileError(
            f"{context}: incremental/freeze/delivery events must be non-empty and distinct"
        )
    return result


def _normalize_mapping(
    raw: object,
    *,
    aliases: dict[str, str],
    allowed: frozenset[str],
    context: str,
) -> dict[str, Any]:
    values = _mapping(raw, context)
    normalized: dict[str, Any] = {}
    for source, value in values.items():
        key = aliases.get(str(source), str(source))
        if key not in allowed:
            raise TaskPipelineProfileError(f"{context}: unknown field {source!r}")
        if key in normalized and normalized[key] != value:
            raise TaskPipelineProfileError(f"{context}: conflicting field {key!r}")
        normalized[key] = value
    return normalized


def _mapping(raw: object, context: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TaskPipelineProfileError(f"{context} must be a mapping")
    return {str(key): value for key, value in raw.items()}


def _string_list(raw: object, context: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TaskPipelineProfileError(f"{context} must be a list")
    return list(dict.fromkeys(
        str(item).strip() for item in raw if str(item).strip()
    ))


def _bounded_int(value: object, *, minimum: int, maximum: int, context: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise TaskPipelineProfileError(f"{context} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise TaskPipelineProfileError(
            f"{context} must be between {minimum} and {maximum}"
        )
    return parsed


def _bounded_float(
    value: object,
    *,
    minimum: float,
    maximum: float,
    context: str,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise TaskPipelineProfileError(f"{context} must be numeric") from exc
    if not minimum <= parsed <= maximum:
        raise TaskPipelineProfileError(
            f"{context} must be between {minimum} and {maximum}"
        )
    return parsed


__all__ = [
    "TASK_PIPELINE_PROFILE_IDS",
    "TASK_PIPELINE_PROFILE_SCHEMA",
    "TaskPipelineProfileError",
    "compile_task_pipeline_profile",
]
