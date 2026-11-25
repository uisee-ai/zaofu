"""Mechanical contracts for causal Skill evaluation and adoption claims."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping, Sequence

from zf.runtime.evolution_contracts import (
    EvolutionContractError,
    normalize_digest,
    stable_digest,
)


SKILL_EVAL_SUITE_SCHEMA = "skill-eval-suite.v1"
SKILL_TREATMENT_IDENTITY_SCHEMA = "skill-treatment-identity.v2"
LEGACY_SKILL_TREATMENT_IDENTITY_SCHEMA = "skill-treatment-identity.v1"
EVALUATION_PURPOSES = frozenset({
    "treatment_smoke",
    "content_lift",
    "natural_lift",
    "routing_stress",
    "adoption_lift",
})
CASE_KINDS = frozenset({
    "explicit",
    "implicit",
    "contextual",
    "negative",
    "confusable",
})
TREATMENT_EXPECTATIONS = frozenset({"required", "optional", "forbidden"})
TREATMENT_ARMS = frozenset({"raw", "current", "candidate"})
_FORCED_PURPOSES = frozenset({"treatment_smoke", "content_lift"})
_LEGACY_COMMON_IDENTITY_DIGESTS = (
    "support_skill_inventory_digest",
    "role_profile_digest",
    "briefing_digest",
    "prompt_digest",
    "workspace_fixture_digest",
    "tool_policy_digest",
    "eval_suite_generation_digest",
)
_COMMON_IDENTITY_DIGESTS = (
    "runtime_commit_digest",
    "provider_digest",
    "model_digest",
    "support_skill_inventory_digest",
    "role_profile_digest",
    "briefing_digest",
    "prompt_digest",
    "workspace_fixture_digest",
    "tool_policy_digest",
    "sandbox_policy_digest",
    "network_policy_digest",
    "budget_digest",
    "eval_suite_generation_digest",
)


def skill_evaluation_policy(raw: Mapping[str, Any]) -> dict[str, str]:
    """Normalize an evaluator-selected purpose without creating config truth."""

    purpose = str(raw.get("evaluation_purpose") or "treatment_smoke").strip()
    if purpose not in EVALUATION_PURPOSES:
        raise EvolutionContractError(
            f"skill evaluation_purpose has unsupported value {purpose!r}"
        )
    expected_mode = "forced" if purpose in _FORCED_PURPOSES else "natural"
    routing_mode = str(raw.get("routing_mode") or expected_mode).strip()
    if routing_mode != expected_mode:
        raise EvolutionContractError(
            f"{purpose} requires routing_mode={expected_mode}"
        )
    return {"evaluation_purpose": purpose, "routing_mode": routing_mode}


def validate_skill_eval_suite(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate public case identity while leaving semantic grading external."""

    body = deepcopy(dict(raw))
    if body.get("schema_version") != SKILL_EVAL_SUITE_SCHEMA:
        raise EvolutionContractError(
            f"schema_version must be {SKILL_EVAL_SUITE_SCHEMA}"
        )
    suite_id = str(body.get("suite_id") or "").strip()
    if not suite_id:
        raise EvolutionContractError("skill eval suite_id is required")
    policy = skill_evaluation_policy(body)
    cases = body.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvolutionContractError("skill eval suite requires cases")
    normalized_cases: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    for item in cases:
        if not isinstance(item, Mapping):
            raise EvolutionContractError("skill eval cases must be objects")
        case_id = str(item.get("case_id") or "").strip()
        case_kind = str(item.get("case_kind") or "").strip()
        treatment = str(item.get("treatment") or "").strip()
        if not case_id or case_id in case_ids:
            raise EvolutionContractError("skill eval case ids must be unique")
        if case_kind not in CASE_KINDS:
            raise EvolutionContractError(
                f"skill eval case {case_id} has unsupported case_kind"
            )
        if treatment not in TREATMENT_EXPECTATIONS:
            raise EvolutionContractError(
                f"skill eval case {case_id} has unsupported treatment"
            )
        case_ids.add(case_id)
        normalized_case = {
            "case_id": case_id,
            "case_kind": case_kind,
            "treatment": treatment,
        }
        for ref_key, digest_key in (
            ("fixture_ref", "fixture_digest"),
            ("grader_ref", "grader_digest"),
        ):
            ref = str(item.get(ref_key) or "").strip()
            digest = str(item.get(digest_key) or "").strip()
            if bool(ref) != bool(digest):
                raise EvolutionContractError(
                    f"skill eval case {case_id} requires {ref_key}/{digest_key} together"
                )
            normalized_case[ref_key] = ref
            normalized_case[digest_key] = (
                normalize_digest(digest, field=f"case {case_id} {digest_key}")
                if digest
                else ""
            )
        normalized_cases.append(normalized_case)
    pool = _validate_routing_pool(body.get("routing_pool") or {})
    if policy["evaluation_purpose"] == "routing_stress":
        if not pool["sizes"]:
            raise EvolutionContractError(
                "routing_stress requires an executed routing pool declaration"
            )
        if not any(
            row["case_kind"] in {"negative", "confusable"}
            for row in normalized_cases
        ):
            raise EvolutionContractError(
                "routing_stress requires negative or confusable cases"
            )
        if not pool["decoy_skills"] and not pool["confusable_skills"]:
            raise EvolutionContractError(
                "routing_stress requires decoy or confusable Skills"
            )
    body.update(policy)
    body["suite_id"] = suite_id
    body["cases"] = normalized_cases
    body["routing_pool"] = pool
    supplied_digest = str(body.pop("suite_digest", "") or "").strip()
    body["suite_digest"] = stable_digest(body)
    if supplied_digest and normalize_digest(
        supplied_digest, field="skill eval suite_digest"
    ) != body["suite_digest"]:
        raise EvolutionContractError("skill eval suite digest drift")
    return body


def validate_skill_treatment_identity(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Freeze the single treatment while keeping common conditions comparable."""

    body = deepcopy(dict(raw))
    schema_version = str(body.get("schema_version") or "")
    if schema_version not in {
        LEGACY_SKILL_TREATMENT_IDENTITY_SCHEMA,
        SKILL_TREATMENT_IDENTITY_SCHEMA,
    }:
        raise EvolutionContractError(
            "schema_version must be a supported Skill treatment identity"
        )
    arm = str(body.get("arm") or "").strip()
    if arm not in TREATMENT_ARMS:
        raise EvolutionContractError("skill treatment arm is invalid")
    policy = skill_evaluation_policy(body)
    target = body.get("target_skill")
    if not isinstance(target, Mapping):
        raise EvolutionContractError("skill treatment target_skill is required")
    normalized_target = deepcopy(dict(target))
    name = str(normalized_target.get("name") or "").strip()
    if not name:
        raise EvolutionContractError("skill treatment target name is required")
    available = bool(normalized_target.get("available"))
    expected_available = arm != "raw"
    if available != expected_available:
        raise EvolutionContractError(
            f"skill treatment arm {arm} has inconsistent availability"
        )
    if "loading_mode" in normalized_target:
        if policy["evaluation_purpose"] != "natural_lift":
            raise EvolutionContractError("explicit loading-mode comparisons require natural_lift")
        allowed_modes = ("required", "on_demand") if available else ("absent",)
        if normalized_target["loading_mode"] not in allowed_modes:
            raise EvolutionContractError("invalid Skill loading mode for trial arm")
    normalized_target["name"] = name
    normalized_target["available"] = available
    if available:
        version = str(normalized_target.get("version") or "").strip()
        if not version:
            raise EvolutionContractError("available Skill treatment needs version")
        normalized_target["version"] = version
        normalized_target["digest"] = normalize_digest(
            normalized_target.get("digest"), field="target_skill.digest"
        )
        normalized_target["materialized_path_digest"] = normalize_digest(
            normalized_target.get("materialized_path_digest"),
            field="target_skill.materialized_path_digest",
        )
    else:
        for key in ("version", "digest", "materialized_path_digest"):
            if str(normalized_target.get(key) or "").strip():
                raise EvolutionContractError(
                    f"raw Skill treatment must not declare target {key}"
                )
            normalized_target[key] = ""
    common: dict[str, str] = {}
    common_fields = (
        _LEGACY_COMMON_IDENTITY_DIGESTS
        if schema_version == LEGACY_SKILL_TREATMENT_IDENTITY_SCHEMA
        else _COMMON_IDENTITY_DIGESTS
    )
    for key in common_fields:
        common[key] = normalize_digest(body.get(key), field=key)
        body[key] = common[key]
    body.update(policy)
    body["schema_version"] = schema_version
    body["arm"] = arm
    body["target_skill"] = normalized_target
    body["common_fingerprint"] = stable_digest(common)
    body["treatment_fingerprint"] = stable_digest({
        "arm": arm,
        "target_skill": normalized_target,
        "common": common,
        **policy,
    })
    return body


def compare_skill_treatment_identities(
    values: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Check common inputs and isolate a target content change or loading policy change."""

    try:
        rows = [validate_skill_treatment_identity(item) for item in values]
    except EvolutionContractError as exc:
        return _treatment_comparison(False, str(exc), [])
    if not rows:
        return _treatment_comparison(False, "no Skill treatment identities", [])
    by_arm: dict[str, dict[str, Any]] = {}
    for row in rows:
        arm = str(row["arm"])
        prior = by_arm.get(arm)
        if prior and prior["treatment_fingerprint"] != row["treatment_fingerprint"]:
            return _treatment_comparison(
                False, f"Skill treatment identity drift within arm {arm}", rows
            )
        by_arm[arm] = row
    arms = set(by_arm)
    if arms not in ({"raw", "candidate"}, {"raw", "current", "candidate"}):
        return _treatment_comparison(
            False, "Skill treatment requires raw/candidate or raw/current/candidate", rows
        )
    common = {str(row["common_fingerprint"]) for row in by_arm.values()}
    if len(common) != 1:
        return _treatment_comparison(
            False, "non-target Skill treatment identity differs", rows
        )
    purposes = {str(row["evaluation_purpose"]) for row in by_arm.values()}
    modes = {str(row["routing_mode"]) for row in by_arm.values()}
    names = {str(row["target_skill"]["name"]) for row in by_arm.values()}
    if len(purposes) != 1 or len(modes) != 1 or len(names) != 1:
        return _treatment_comparison(
            False, "Skill treatment purpose, routing, or target name differs", rows
        )
    if "current" in by_arm:
        current = by_arm["current"]["target_skill"]
        candidate = by_arm["candidate"]["target_skill"]
        loading_modes = (current.get("loading_mode"), candidate.get("loading_mode"))
        if (loading_modes[0] is None) != (loading_modes[1] is None):
            return _treatment_comparison(False, "target loading mode missing from one arm", rows)
        if loading_modes[0] != loading_modes[1] and (
            by_arm["raw"]["target_skill"].get("loading_mode") != "absent"
            or current["version"] != candidate["version"]
            or current["materialized_path_digest"] != candidate["materialized_path_digest"]
        ):
            return _treatment_comparison(False, "loading trial must freeze target version and location", rows)
        same_content = current["digest"] == candidate["digest"]
        if same_content and loading_modes[0] == loading_modes[1]:
            return _treatment_comparison(False, "candidate Skill digest does not differ from current", rows)
        if not same_content and loading_modes[0] != loading_modes[1]:
            return _treatment_comparison(False, "content and loading mode both changed", rows)
    return {
        "schema_version": "skill-treatment-comparison.v1",
        "comparable": True,
        "reason": "common treatment identity matches",
        "arms": sorted(arms),
        "common_fingerprint": next(iter(common)),
        "evaluation_purpose": next(iter(purposes)),
        "routing_mode": next(iter(modes)),
        "target_skill": next(iter(names)),
    }


def classify_skill_treatment(
    *,
    identity: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    loaded_case_ids: Sequence[str] = (),
    behavior_by_case: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """Build ITT and mechanism observations without inferring application."""

    treatment_identity = validate_skill_treatment_identity(identity)
    available = bool(treatment_identity["target_skill"]["available"])
    loaded_ids = {str(item) for item in loaded_case_ids if str(item)}
    behavior = dict(behavior_by_case or {})
    case_rows: list[dict[str, Any]] = []
    for item in cases:
        if not isinstance(item, Mapping):
            raise EvolutionContractError("Skill treatment cases must be objects")
        case_id = str(item.get("case_id") or "").strip()
        case_kind = str(item.get("case_kind") or "").strip()
        expected = str(item.get("treatment") or "").strip()
        if not case_id or case_kind not in CASE_KINDS:
            raise EvolutionContractError("Skill treatment case identity is invalid")
        if expected not in TREATMENT_EXPECTATIONS:
            raise EvolutionContractError("Skill treatment expectation is invalid")
        loaded = bool(available and case_id in loaded_ids)
        applied = behavior.get(case_id) if case_id in behavior else None
        if not available:
            status = "unavailable"
        elif expected == "required":
            status = "loaded" if loaded else "missed"
        elif expected == "forbidden":
            status = "overtriggered" if loaded else "avoided"
        else:
            status = "loaded" if loaded else "not_loaded"
        case_rows.append({
            "case_id": case_id,
            "case_kind": case_kind,
            "expected": expected,
            "available": available,
            "loaded": loaded,
            "applied": applied,
            "behavior_followed": applied,
            "status": status,
        })
    required = [row for row in case_rows if row["expected"] == "required"]
    forbidden = [row for row in case_rows if row["expected"] == "forbidden"]
    loaded_count = sum(bool(row["loaded"]) for row in case_rows)
    overtrigger_count = sum(row["status"] == "overtriggered" for row in forbidden)
    forced = treatment_identity["routing_mode"] == "forced"
    admission_valid = not (
        available and forced and any(not bool(row["loaded"]) for row in required)
    )
    if not available:
        observation = "not_applicable"
    elif overtrigger_count:
        observation = "overtriggered"
    elif not admission_valid:
        observation = "treatment_not_applied"
    elif forbidden and len(forbidden) == len(case_rows) and not loaded_count:
        observation = "correctly_not_invoked"
    elif loaded_count:
        observation = "invoked" if forced else "loaded"
    else:
        observation = "available_not_loaded"
    observed = [
        row["behavior_followed"]
        for row in case_rows
        if isinstance(row["behavior_followed"], bool)
    ]
    return {
        "evaluation_purpose": treatment_identity["evaluation_purpose"],
        "routing_mode": treatment_identity["routing_mode"],
        "intent_to_treat": True,
        "identity": treatment_identity,
        "required": bool(available and required),
        "available": available,
        "loaded": bool(loaded_count),
        "applied": all(observed) if observed and len(observed) == len(case_rows) else None,
        "application_observed": bool(observed and len(observed) == len(case_rows)),
        "admission_valid": admission_valid,
        "observation": observation,
        "overtrigger_count": overtrigger_count,
        "cases": case_rows,
    }


def validate_skill_treatment_observation(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a provider/evaluator-produced treatment observation."""

    body = deepcopy(dict(raw))
    identity = body.get("identity")
    if not isinstance(identity, Mapping):
        raise EvolutionContractError("Skill treatment observation needs identity")
    normalized_identity = validate_skill_treatment_identity(identity)
    if body.get("evaluation_purpose") != normalized_identity["evaluation_purpose"]:
        raise EvolutionContractError("Skill treatment purpose drift")
    if body.get("routing_mode") != normalized_identity["routing_mode"]:
        raise EvolutionContractError("Skill treatment routing drift")
    if body.get("intent_to_treat") is not True:
        raise EvolutionContractError("Skill treatment must preserve intent_to_treat")
    for key in (
        "required",
        "available",
        "loaded",
        "application_observed",
        "admission_valid",
    ):
        if not isinstance(body.get(key), bool):
            raise EvolutionContractError(f"Skill treatment {key} must be boolean")
    if bool(body["available"]) != bool(
        normalized_identity["target_skill"]["available"]
    ):
        raise EvolutionContractError("Skill treatment availability drift")
    applied = body.get("applied")
    if applied is not None and not isinstance(applied, bool):
        raise EvolutionContractError("Skill treatment applied must be bool or null")
    observation = str(body.get("observation") or "")
    if observation not in {
        "invoked",
        "loaded",
        "treatment_not_applied",
        "not_applicable",
        "available_not_loaded",
        "correctly_not_invoked",
        "overtriggered",
    }:
        raise EvolutionContractError("Skill treatment observation is unsupported")
    cases = body.get("cases")
    if not isinstance(cases, list):
        raise EvolutionContractError("Skill treatment cases must be a list")
    case_specs: list[dict[str, str]] = []
    loaded_case_ids: list[str] = []
    behavior_by_case: dict[str, bool] = {}
    for item in cases:
        if not isinstance(item, Mapping):
            raise EvolutionContractError(
                "Skill treatment observation cases must be objects"
            )
        case_id = str(item.get("case_id") or "").strip()
        case_kind = str(item.get("case_kind") or "").strip()
        expected = str(item.get("expected") or "").strip()
        if not case_id or case_kind not in CASE_KINDS:
            raise EvolutionContractError(
                "Skill treatment observation case identity is invalid"
            )
        if expected not in TREATMENT_EXPECTATIONS:
            raise EvolutionContractError(
                "Skill treatment observation case expectation is invalid"
            )
        if not isinstance(item.get("available"), bool) or not isinstance(
            item.get("loaded"), bool
        ):
            raise EvolutionContractError(
                "Skill treatment observation case availability must be boolean"
            )
        case_specs.append({
            "case_id": case_id,
            "case_kind": case_kind,
            "treatment": expected,
        })
        if bool(item["loaded"]):
            loaded_case_ids.append(case_id)
        followed = item.get("behavior_followed")
        if isinstance(followed, bool):
            behavior_by_case[case_id] = followed
    derived = classify_skill_treatment(
        identity=normalized_identity,
        cases=case_specs,
        loaded_case_ids=loaded_case_ids,
        behavior_by_case=behavior_by_case,
    )
    derived_cases = {
        str(item["case_id"]): item for item in derived["cases"]
    }
    for item in cases:
        expected_case = derived_cases[str(item["case_id"])]
        for key in (
            "available",
            "loaded",
            "applied",
            "behavior_followed",
            "status",
        ):
            if item.get(key) != expected_case[key]:
                raise EvolutionContractError(
                    "Skill treatment observation case "
                    f"{item['case_id']} {key} is inconsistent"
                )
    for key in (
        "required",
        "available",
        "loaded",
        "applied",
        "application_observed",
        "admission_valid",
        "observation",
        "overtrigger_count",
    ):
        if body.get(key) != derived[key]:
            raise EvolutionContractError(
                f"Skill treatment observation {key} is inconsistent with case facts"
            )
    body["identity"] = normalized_identity
    body["observation"] = observation
    body["cases"] = list(derived_cases.values())
    body["overtrigger_count"] = derived["overtrigger_count"]
    return body


def validate_case_results(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise EvolutionContractError("case_results must be a list")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise EvolutionContractError("case_results entries must be objects")
        case_id = str(item.get("case_id") or "").strip()
        case_kind = str(item.get("case_kind") or "").strip()
        if not case_id or case_id in seen:
            raise EvolutionContractError("case_results ids must be unique")
        if case_kind not in CASE_KINDS:
            raise EvolutionContractError(
                f"case_result {case_id} has unsupported case_kind"
            )
        try:
            score = float(item.get("score"))
        except (TypeError, ValueError) as exc:
            raise EvolutionContractError(
                f"case_result {case_id} score must be numeric"
            ) from exc
        if not math.isfinite(score):
            raise EvolutionContractError(
                f"case_result {case_id} score must be finite"
            )
        if not isinstance(item.get("gate_passed"), bool):
            raise EvolutionContractError(
                f"case_result {case_id} gate_passed must be boolean"
            )
        row = {
            "case_id": case_id,
            "case_kind": case_kind,
            "score": score,
            "gate_passed": bool(item["gate_passed"]),
        }
        if isinstance(item.get("behavior_followed"), bool):
            row["behavior_followed"] = bool(item["behavior_followed"])
        rows.append(row)
        seen.add(case_id)
    return rows


def _validate_routing_pool(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise EvolutionContractError("routing_pool must be an object")
    sizes: list[int] = []
    for value in raw.get("sizes") or []:
        try:
            size = int(value)
        except (TypeError, ValueError) as exc:
            raise EvolutionContractError("routing pool sizes must be integers") from exc
        if size < 1:
            raise EvolutionContractError("routing pool sizes must be positive")
        if size not in sizes:
            sizes.append(size)
    return {
        "sizes": sorted(sizes),
        "support_skills": _skill_digest_rows(raw.get("support_skills") or []),
        "decoy_skills": _skill_digest_rows(raw.get("decoy_skills") or []),
        "confusable_skills": _skill_digest_rows(raw.get("confusable_skills") or []),
    }


def _skill_digest_rows(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise EvolutionContractError("routing pool Skill inventories must be lists")
    rows: list[dict[str, str]] = []
    names: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise EvolutionContractError("routing pool Skill entries must be objects")
        name = str(item.get("name") or "").strip()
        if not name or name in names:
            raise EvolutionContractError("routing pool Skill names must be unique")
        rows.append({
            "name": name,
            "digest": normalize_digest(item.get("digest"), field=f"Skill {name} digest"),
        })
        names.add(name)
    return rows


def _treatment_comparison(
    comparable: bool,
    reason: str,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "skill-treatment-comparison.v1",
        "comparable": comparable,
        "reason": reason,
        "arms": sorted({str(row.get("arm") or "") for row in rows if row.get("arm")}),
        "common_fingerprint": "",
    }


def build_skill_treatment_identity(
    *,
    arm: str,
    target_skill: Mapping[str, Any],
    common_identity: Mapping[str, Any],
    evaluation_purpose: str,
) -> dict[str, Any]:
    """Build and immediately validate one treatment identity."""

    return validate_skill_treatment_identity({
        "schema_version": SKILL_TREATMENT_IDENTITY_SCHEMA,
        "arm": arm,
        "evaluation_purpose": evaluation_purpose,
        "target_skill": dict(target_skill),
        **dict(common_identity),
    })


__all__ = [
    "CASE_KINDS",
    "EVALUATION_PURPOSES",
    "SKILL_EVAL_SUITE_SCHEMA",
    "SKILL_TREATMENT_IDENTITY_SCHEMA",
    "build_skill_treatment_identity",
    "classify_skill_treatment",
    "compare_skill_treatment_identities",
    "skill_evaluation_policy",
    "validate_case_results",
    "validate_skill_eval_suite",
    "validate_skill_treatment_identity",
    "validate_skill_treatment_observation",
]
