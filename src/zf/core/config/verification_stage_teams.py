"""Compile approved verification teams into the existing WRC, Reader and Judge routes."""

from __future__ import annotations

from zf.core.config.stage_teams import _text, active_members, author_team, member_child
from zf.core.config.task_pipeline_profile import profile_digest


def normalize_validation_teams(teams: dict) -> dict:
    result = {}
    for stage, spec in teams.items():
        if stage == "judge":
            result[stage] = author_team(spec, stage=stage)
            continue
        field = {"task_verify": "mode", "candidate_verify": "width"}.get(stage)
        if not field or not isinstance(spec, dict) or set(spec) != {"reason", field}:
            raise ValueError(f"stage_teams.{stage} requires reason and {field}")
        value = spec[field]
        if stage == "task_verify" and value != "oa_run_plan":
            raise ValueError("task_verify.mode must be oa_run_plan")
        if stage == "candidate_verify" and (type(value) is not int or not 1 <= value <= 32):
            raise ValueError("candidate_verify.width must be a positive registered Reader count")
        result[stage] = {"reason": _text(spec["reason"], "reason"), field: value}
    return result


def apply_validation_teams(expansion: dict, teams: dict) -> None:
    meta = expansion["metadata"]
    pipeline = expansion["pipelines"][0]
    policy = meta.get("task_pipeline") or {}
    profiles = meta["result_protocol"]["semantic_submit_profiles"]
    if "task_verify" in teams or "candidate_verify" in teams:
        if policy.get("mode") != "blocking":
            raise ValueError("OA verification assignments require blocking Task Pipeline")
        if "task_verify" in teams:
            policy["verify_assignment_mode"] = "oa_run_plan"
        if "candidate_verify" in teams:
            width = teams["candidate_verify"]["width"]
            registered = policy["candidate"].get("verify_width", pipeline["lane_count"])
            if width > min(registered, len(policy["pools"]["verify"]["role_instances"])):
                raise ValueError("candidate_verify.width conflicts with the registered Candidate Reader seats")
            if meta["product_acceptance"]["mode"] != "blocking":
                raise ValueError("Candidate Reader assignments require blocking product acceptance")
            policy["candidate"]["verify_width"] = width
            policy["candidate"]["require_oa_assignments"] = True
            planner = next(s for s in expansion["stages"] if s["id"] == "prd-plan")
            planner["criteria"]["instructions"] = [*planner["criteria"]["instructions"],
                f"Declare exactly {width} candidate_reader_assignments in product_acceptance_spec. "
                "Select independent registered verify seats and assign each its task-qualified acceptance IDs, "
                "mandatory journeys and cross-task integration checks. Declare integration_checks as check_id, task_ids "
                "and observable assertion; assign every check ID via integration_check_ids and declare the needed Plan reads. "
                "The OA will review the exact assignment on this Plan revision."]
        policy["profile_digest"] = profile_digest(policy)
    judge = teams.get("judge")
    if judge:
        meta["registered_judge_role"] = pipeline["final"]["role"]
        pipeline["final"]["role"] = judge["author"]
        plan_stage = next(s for s in expansion["stages"] if s["id"] == "prd-plan")
        if judge["author"] in plan_stage["roles"]:
            raise ValueError("formal Judge must be independent of the Plan author")
        discovery = next((s for s in expansion["stages"] if s["id"] == "prd-post-verify-discovery"), None)
        members = active_members(judge)
        if members:
            if not discovery or meta.get("post_verify_discovery") != "product_completeness":
                raise ValueError("required Judge specialists need a bound product discovery stage")
            primary, = discovery["roles"]
            roles = [member["role"] for member in members]
            if primary in roles:
                raise ValueError("Judge specialists must be independent of product discovery")
            required = [m for m in members if m.get("required", True)]
            if required:
                discovery["roles"] = [primary, *[m["role"] for m in required]]
                discovery["fanout"] = {"children": [{"role_instance": primary},
                    *[member_child(m, judge["author"]) for m in required]]}
            advisory = [m for m in members if not m.get("required", True)]
            if advisory:
                expansion["stages"].append({
                    "id": "prd-judge-advisory", "trigger": discovery["trigger"], "topology": "fanout_reader",
                    "result_semantics": "artifact_production", "roles": [m["role"] for m in advisory],
                    "fanout": {"children": [member_child(m, judge["author"]) for m in advisory]},
                    "aggregate": {"mode": "wait_for_all", "child_success_event": "workflow.child.completed",
                                  "child_failure_event": "workflow.child.failed"},
                })
    required_profiles = set()
    if "candidate_verify" in teams or (judge and judge["members"]):
        required_profiles.update(("candidate-verify", "workflow-read"))
    if judge:
        required_profiles.add("thin-judge-goal-closure")
    for profile in required_profiles:
        if profiles.setdefault(profile, "blocking") != "blocking":
            raise ValueError(f"stage team requires blocking {profile} result admission")
