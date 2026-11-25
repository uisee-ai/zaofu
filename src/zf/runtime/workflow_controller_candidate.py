"""Materialize bounded controller changes for the existing proposal/apply path."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

import yaml

from zf.core.config.loader import load_config
from zf.core.config.schema import ZfConfig
from zf.core.state.atomic_io import atomic_write_text


def bind_verification_checkpoint(
    documents: list[dict], teams: Mapping[str, Any], registered: ZfConfig,
) -> None:
    """Project an existing shadow delegation into a candidate subject to owner approval."""
    if not {"task_verify", "candidate_verify"} & teams.keys():
        return
    from zf.core.config.stage_teams import verification_delegation_policy

    policy = verification_delegation_policy(registered)
    if policy.checkpoint_policies.get("pre_impl") != "shadow":
        return
    configs = [doc["spec"] for doc in documents if doc.get("kind") == "ZfConfig"]
    if len(configs) != 1:
        raise ValueError("verification checkpoint changes require exactly one ZfConfig")
    oa = configs[0].setdefault("workflow", {}).setdefault("orchestration", {})
    target = oa.setdefault("flow_policies", {}).setdefault("prd", {})
    target.setdefault("checkpoint_policies", {})["pre_impl"] = "blocking"


def materialize_controller_candidate(
    *, base_path: Path, request_id: str, short_spec: Mapping[str, Any],
    requirement: Mapping[str, Any] | None = None,
) -> Path:
    """Materialize bounded Flow inputs; never write canonical config.

    Strictness remains checked against the compiled delivery contract, and
    pattern_id remains a registered entry selection. Neither authorizes gate
    changes or arbitrary YAML from an agent. PRD v4 requires explicit typed
    profile, mode and independent pool capacities.
    """
    family = str(short_spec.get("flow_family") or "")
    parameters = short_spec.get("parameters") or {}
    if parameters.get("controller_id"):
        from zf.runtime.workflow_registered_controller import assemble_controller
        documents = assemble_controller(
            base_path=base_path, short_spec=short_spec, requirement=requirement or {},
        )
        return _write_candidate(base_path, request_id, documents)
    if ("plan_critic_enabled" in parameters or "plan_review" in parameters) and "lanes" not in parameters:
        raise ValueError("plan_critic_enabled requires explicit existing lanes")
    if "stage_teams" in parameters and family != "PrdFlow":
        raise ValueError("stage_teams requires PrdFlow")
    if family not in {"IssueFlow", "PrdFlow", "RefactorFlow"} or not ({"lanes", "stage_teams"} & parameters.keys()):
        return base_path
    documents = list(yaml.safe_load_all(base_path.read_text(encoding="utf-8")))
    matches = [d for d in documents if isinstance(d, dict) and d.get("kind") == family]
    if len(matches) != 1 or not isinstance(matches[0].get("spec"), dict):
        raise ValueError("controller synthesis requires exactly one existing selected Flow")
    spec = matches[0]["spec"]
    if "stage_teams" in parameters:
        from zf.core.config.stage_teams import normalize_stage_teams
        spec["stageTeams"] = normalize_stage_teams(parameters["stage_teams"])
        spec.pop("stage_teams", None)
        bind_verification_checkpoint(documents, spec["stageTeams"], load_config(base_path))
        if "lanes" not in parameters:
            if "task_pipeline" in parameters:
                raise ValueError("task_pipeline requires explicit lanes")
            return _write_candidate(base_path, request_id, documents)
    lanes = parameters["lanes"]
    if isinstance(lanes, bool) or not isinstance(lanes, int) or not 1 <= lanes <= 64:
        raise ValueError("controller lanes must be an integer between 1 and 64")
    critic_enabled = parameters.get("plan_critic_enabled")
    if "plan_critic_enabled" in parameters and (family != "PrdFlow" or type(critic_enabled) is not bool):
        raise ValueError("plan_critic_enabled requires PrdFlow and a boolean")
    critic_keys = [key for key in ("planCriticEnabled", "plan_critic_enabled") if key in spec]
    if len(critic_keys) > 1:
        raise ValueError("controller Flow has ambiguous plan critic aliases")
    critic_key = critic_keys[0] if critic_keys else "planCriticEnabled"
    critic_changed = critic_enabled is not None and spec.get(critic_key, True) != critic_enabled
    if critic_changed:
        spec[critic_key] = critic_enabled
    if "plan_review" in parameters:
        from zf.core.config.plan_review import resolve_plan_review
        if family != "PrdFlow":
            raise ValueError("plan_review requires PrdFlow")
        policy = resolve_plan_review(parameters)
        existing = [key for key in ("planReview", "plan_review") if key in spec]
        if len(existing) > 1:
            raise ValueError("controller Flow has ambiguous plan review aliases")
        review_key = existing[0] if existing else "planReview"
        critic_changed = critic_changed or spec.get(review_key) != policy
        spec[review_key] = policy
        # The explicit policy replaces the legacy switch in this candidate only.
        for old in critic_keys:
            spec.pop(old, None)
    elif "plan_critic_enabled" in parameters:
        # A legacy toggle must not silently override an existing scoped policy.
        if "planReview" in spec or "plan_review" in spec:
            raise ValueError("existing planReview requires an explicit plan_review revision")
    pipeline = parameters.get("task_pipeline")
    if pipeline is not None:
        from zf.runtime.workflow_task_pipeline_inputs import validate_prd_task_pipeline
        validate_prd_task_pipeline(pipeline, family=family, lanes=lanes)
        if spec.get("taskPipeline") or spec.get("task_pipeline") or spec.get("flowProfile") or spec.get("flow_profile"):
            raise ValueError("existing profile requires explicit config revision")
    if family in {"IssueFlow", "PrdFlow"}:
        topology = spec.get("topology", "light" if family == "IssueFlow" else "fanout")
        if topology == "light" and lanes != 1 and pipeline is None:
            raise ValueError("light topology cannot resize lanes; explicit topology revision is required")
    lane_keys = [key for key in ("lanes", "laneCount", "lane_count") if key in spec]
    if len(lane_keys) > 1:
        raise ValueError("controller Flow has ambiguous lane aliases")
    key = lane_keys[0] if lane_keys else "lanes"
    if spec.get(key) == lanes and pipeline is None:
        return _write_candidate(base_path, request_id, documents) if critic_changed or "stage_teams" in parameters else base_path
    if spec.get("taskPipeline") or spec.get("task_pipeline"):
        raise ValueError("Task Pipeline resizing requires an explicit pool/config revision, not lanes alone")
    spec[key] = lanes
    if pipeline is not None:
        spec["topology"] = "fanout"
        spec["flowProfile"] = pipeline["profile"]
        spec["taskPipeline"] = {
            "mode": pipeline["mode"],
            "maxActiveTaskPipelines": pipeline["max_active_task_pipelines"],
            "pools": {
                "impl": {"capacity": pipeline["impl_capacity"]},
                "verify": {"capacity": pipeline["verify_capacity"]},
            },
            "backpressure": {
                "maxUnverifiedTasks": pipeline["max_active_task_pipelines"],
                "maxIntegrationQueue": 1,
            },
            "workerLifecycle": {},
            "affinity": {},
            "integrationAdmission": {},
            "candidate": {},
        }
    return _write_candidate(base_path, request_id, documents)


def _write_candidate(base_path: Path, request_id: str, documents: list) -> Path:
    content = yaml.safe_dump_all(documents, sort_keys=False, allow_unicode=True)
    digest = hashlib.sha256(content.encode()).hexdigest()
    request_digest = hashlib.sha256(request_id.encode()).hexdigest()[:12]
    candidate = base_path.parent / f".{base_path.name}.workflow-candidate-{request_digest}-{digest[:16]}.tmp"
    if candidate.is_symlink():
        raise ValueError("controller candidate must not be a symlink")
    existed = candidate.exists()
    if existed and candidate.read_text(encoding="utf-8") != content:
        raise ValueError("controller candidate digest collision")
    if not existed:
        atomic_write_text(candidate, content)
        candidate.chmod(0o600)
    try:
        # Resolve profiles relative to the canonical config directory and keep
        # all normal schema/topology gates before a proposal can be approved.
        config = load_config(candidate)
        if config.runtime.skills.strict:
            from zf.core.skills import validate_skill_sources
            problems = validate_skill_sources(config=config, project_root=base_path.parent)
            if problems:
                raise ValueError("controller skill validation failed: " + "; ".join(problems))
    except Exception:
        if not existed:
            candidate.unlink()
        raise
    return candidate
