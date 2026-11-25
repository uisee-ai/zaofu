"""OA-selected PRD assignments embedded in the approved Flow and fanout children."""

from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath
from typing import Any

from zf.core.config.schema import WorkflowOrchestrationFlowPolicyConfig, ZfConfig


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError(f"stage_teams {name} must be a non-empty single-line string")
    return value.strip()


def team_member(raw: Any, *, stage: str = "") -> dict:
    """Read the shared, non-executable contract of a stage specialist."""
    fields = {"obligation_id", "role", "question", "input_refs", "expected_output"}
    extra = {"required", "coverage_owner", "disposition"} if stage in {"plan", "judge"} else set()
    if stage in {"plan", "judge", "task_verify"}:
        extra |= {"selection_reason", "completion_condition"}
    if not isinstance(raw, dict) or not fields <= raw.keys() or set(raw) - fields - extra:
        raise ValueError("stage_teams member requires exactly " + ", ".join(sorted(fields)))
    result = {key: _text(raw[key], key) for key in ("obligation_id", "role", "question", "expected_output")}
    for key in ("selection_reason", "completion_condition"):
        if key in raw:
            result[key] = _text(raw[key], key)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", result["obligation_id"]):
        raise ValueError("stage_teams obligation_id must be a stable identifier")
    refs = raw["input_refs"]
    if not isinstance(refs, list) or not refs:
        raise ValueError("stage_teams member requires an input_refs slice")
    result["input_refs"] = list(dict.fromkeys(_text(ref, "input ref") for ref in refs))
    for ref in result["input_refs"]:
        path = ref.partition("#")[0]
        if (path.startswith(("/", "~")) or "\\" in path or ".." in Path(path).parts
                or PureWindowsPath(path).drive or "://" in path or path.lower().startswith("file:")):
            raise ValueError("stage_teams input_refs must be authorized labels or project-relative evidence references")
    if "required" in raw and type(raw["required"]) is not bool:
        raise ValueError(f"{stage} member required must be boolean")
    if raw.get("required", True):
        if "coverage_owner" in raw or "disposition" in raw:
            raise ValueError("only optional Plan members may transfer coverage")
        if "required" in raw:
            result["required"] = True
    else:
        result["required"] = False
        if stage == "plan" or "disposition" in raw or "coverage_owner" in raw:
            result["coverage_owner"] = _text(raw.get("coverage_owner"), "coverage_owner")
        if "disposition" in raw:
            d = raw["disposition"]
            keys = {"status", "reason", "previous_proposal_digest", "previous_run_id"}
            if (not isinstance(d, dict) or set(d) != keys or d["status"] != "reassigned"
                    or not isinstance(d["previous_proposal_digest"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", d["previous_proposal_digest"])):
                raise ValueError("Plan transfer requires the prior approved Proposal and Run")
            result["disposition"] = {k: _text(d[k], k) for k in keys}
    return result


def author_team(raw: Any, *, stage: str) -> dict:
    base = {"reason", "author", "members"}
    if not isinstance(raw, dict) or not base <= raw.keys() or set(raw) - base - ({"objectives", "questions"} if stage == "judge" else {"reserve_roles"}):
        raise ValueError(f"stage_teams.{stage} requires reason, author and members")
    if not isinstance(raw["members"], list):
        raise ValueError(f"stage_teams.{stage}.members must be a list")
    author = _text(raw["author"], "author")
    members = [team_member(member, stage=stage) for member in raw["members"]]
    if any(m.get("coverage_owner", author) != author for m in members):
        raise ValueError(f"optional {stage} coverage must transfer to the formal author")
    roles = [member["role"] for member in members] + [author]
    ids = [member["obligation_id"] for member in members] + [f"{stage}-author"]
    if len(set(roles)) != len(roles) or len(set(ids)) != len(ids):
        raise ValueError(f"stage_teams.{stage} requires distinct member identities and one author")
    team = {"reason": _text(raw["reason"], "reason"), "author": author, "members": members}
    if "reserve_roles" in raw:
        reserves = raw["reserve_roles"]
        if not isinstance(reserves, list) or (reserves and not active_members({"members": members})):
            raise ValueError("Plan reserve_roles require an active research stage and a list of readers")
        reserves = [_text(role, "reserve role") for role in reserves]
        if len(set(reserves)) != len(reserves) or set(reserves).intersection(roles):
            raise ValueError("Plan reserve roles require distinct independent seats")
        team["reserve_roles"] = reserves
    if "objectives" in raw:
        if not isinstance(raw["objectives"], list) or not raw["objectives"]:
            raise ValueError("Judge objectives must be a nonempty list of goals")
        team["objectives"] = [_text(goal, "objective") for goal in raw["objectives"]]
    if "questions" in raw:
        if not isinstance(raw["questions"], list):
            raise ValueError("Judge questions must be a list")
        questions = []
        for question in raw["questions"]:
            if not isinstance(question, dict) or set(question) != {"question_id", "question"}:
                raise ValueError("Judge question requires question_id and question")
            questions.append({k: _text(question[k], k) for k in ("question_id", "question")})
        if len({q["question_id"] for q in questions}) != len(questions):
            raise ValueError("Judge question IDs must be unique")
        team["questions"] = questions
    return team


def normalize_stage_teams(raw: Any) -> dict:
    if not isinstance(raw, dict) or not raw or set(raw) - {"plan", "task_verify", "candidate_verify", "judge"}:
        raise ValueError("stage_teams requires a supported PRD stage")
    teams = {"plan": author_team(raw["plan"], stage="plan")} if "plan" in raw else {}
    verification = {stage: spec for stage, spec in raw.items() if stage != "plan"}
    if verification:
        from zf.core.config.verification_stage_teams import normalize_validation_teams

        teams.update(normalize_validation_teams(verification))
    return teams


def team_roles(teams: dict) -> set[str]:
    selected = set()
    for stage in teams.values():
        if stage.get("author"):
            selected.add(stage["author"])
        selected.update(stage.get("reserve_roles", []))
        selected.update(member["role"] for member in stage.get("members", []) if not member.get("disposition"))
    return selected


def scope_team_roles(expansion: dict, role_map: dict[str, str]) -> None:
    """Namespace generated roles; registered project-wide roles retain their instance ids."""
    metadata = expansion["metadata"]
    teams = metadata.get("stage_teams") or {}
    if not teams:
        return
    for name in team_roles(teams):
        role_map.setdefault(name, name)

    def bind(row: dict, *fields: str) -> None:
        for key in fields:
            if key in row:
                row[key] = role_map.get(row[key], row[key])

    bind(metadata, "registered_judge_role")
    for team in teams.values():
        bind(team, "author")
        if "reserve_roles" in team:
            team["reserve_roles"] = [role_map[name] for name in team["reserve_roles"]]
        for item in team.get("members", []):
            bind(item, "role", "coverage_owner")
    for stage in expansion["stages"]:
        for child in (stage.get("fanout") or {}).get("children", []):
            payload = child.get("payload") or {}
            bind(payload.get("stage_obligation") or {}, "role", "recipient", "coverage_owner")
            for member in payload.get("transferred_obligations", []):
                bind(member, "role", "coverage_owner")


def member_child(member: dict, recipient: str) -> dict:
    """The original registered fanout carries one obligation per child on replay."""
    return {"role_instance": member["role"], "payload": {
        "stage_obligation": {**member, "recipient": recipient},
        "input_refs": list(member["input_refs"]),
    }}


def active_members(team: dict) -> list[dict]:
    return [member for member in team["members"] if not member.get("disposition")]


def planner_child(plan: dict, recipient: str) -> dict:
    member = {"obligation_id": "plan-author", "role": plan["author"], "question": plan["reason"],
              "input_refs": ["requirement", *[m["obligation_id"] for m in active_members(plan)]],
              "expected_output": "Plan/Task Map"}
    child = member_child(member, recipient)
    transferred = [m for m in plan["members"] if m.get("disposition")]
    if transferred:
        payload = child["payload"]
        payload["transferred_obligations"] = transferred
        refs = [ref for m in transferred for ref in m["input_refs"]]
        payload["input_refs"] = list(dict.fromkeys([*payload["input_refs"], *refs]))
    return child


def compile_plan_team(expansion: dict, plan: dict, entry: str) -> None:
    scan, synth = expansion["stages"][:2]
    critic = synth["aggregate"].get("synth_role")
    chosen = team_roles({"plan": plan})
    judge = expansion["pipelines"][0]["final"]["role"]
    if chosen.intersection({critic, judge}):
        raise ValueError("Plan authors and contributors must be independent of the configured reviewers")
    author = plan["author"]
    synth["roles"] = [author]
    synth["fanout"] = {"children": [planner_child(plan, critic or "workflow")]}
    if members := active_members(plan):
        scan["roles"] = [member["role"] for member in members]
        scan["fanout"] = {"children": [member_child(member, author) for member in members]}
    else:
        synth["criteria"]["instructions"] = [
            "Use the confirmed PRD and its bound sources to produce the Plan and Task Map.",
            *synth["criteria"]["instructions"][1:],
        ]
        synth["trigger"] = entry
        expansion["stages"].remove(scan)
        expansion["entry_stage_id"] = synth["id"]


def authorized_judges(config: Any, registered: str = "") -> list[str]:
    registered_roles = {registered} if registered else {
        r for stage in config.workflow.stages if stage.flow_kind == "prd" and stage.trigger == "flow.goal.closed"
        for r in stage.roles}
    return sorted({role.instance_id or role.name for role in config.roles
                   if role.role_kind == "reader" and role.flow_kind in {"", "prd"}
                   and ("judge" in role.stages or (role.instance_id or role.name) in registered_roles)
                   and (role.instance_id or role.name) != config.workflow.orchestration.oa_profile})


def verification_delegation_policy(config: ZfConfig) -> WorkflowOrchestrationFlowPolicyConfig:
    """Require the project's existing PRD OA delegation before binding verification."""
    oa = config.workflow.orchestration
    policy = oa.flow_policies.get("prd")
    if (not oa.oa_profile or not policy or policy.mode != "semantic_control"
            or "pre_impl" not in policy.checkpoints or "delegate" not in (policy.autonomous_actions or [])):
        raise ValueError("OA verification requires a registered blocking pre_impl OA delegation")
    return policy


def validate_effective_teams(config: Any, metadata: dict, project_root: Path) -> None:
    """Check the actual effective consumer after hand-written overrides and namespacing."""
    teams = normalize_stage_teams(metadata["stage_teams"])
    for team in teams.values():
        for member in team.get("members", []):
            for ref in member["input_refs"]:
                if not (project_root / ref.partition("#")[0]).resolve().is_relative_to(project_root.resolve()):
                    raise ValueError("stage_teams input reference resolves outside the project")
    for target in team_roles(teams):
        # Fanout accepts group names as well as instance ids. A charter binds
        # exactly one instance and may never fan out again via a name alias.
        matches = [r for r in config.roles if (r.instance_id or r.name) == target]
        if (len(matches) != 1 or matches[0].role_kind != "reader" or matches[0].flow_kind not in {"", "prd"}):
            raise ValueError("stage_teams requires distinct registered PRD reader instances")
    stages = {stage.id: stage for stage in config.workflow.stages}
    for key, stage_id in (("plan", "prd-scan"), ("judge", "prd-post-verify-discovery")):
        team = teams.get(key)
        if not team:
            continue
        members = active_members(team)
        expected = [member_child(member, team["author"]) for member in members
                    if key != "judge" or member.get("required", True)]
        if key == "plan":
            planner = stages.get("prd-plan")
            if not planner or planner.roles != [team["author"]] or len(planner.children) != 1:
                raise ValueError("stage_teams requires exactly one bound Plan author")
            independent = {s.roles[0] for s in stages.values() if s.trigger == "flow.goal.closed"
                           and s.flow_kind == "prd" and len(s.roles) == 1}
            independent.add(planner.aggregate.synth_role)
            if team_roles({"plan": team}) & independent:
                raise ValueError("stage_teams Plan contributors must be independent of its reviewers")
            author = planner.children[0]
            contract = planner_child(team, planner.aggregate.synth_role or "workflow")
            if author.role_instance != team["author"] or author.payload != contract["payload"]:
                raise ValueError("stage_teams Plan author contract was overridden")
            if not expected and stage_id in stages:
                raise ValueError("stage_teams selected direct planning but a scan stage is configured")
        else:
            advisory = [member_child(m, team["author"]) for m in members if not m.get("required", True)]
            side = stages.get("prd-judge-advisory")
            if advisory or side:
                if (not side or not advisory or side.trigger != "flow.discovery.requested"
                        or side.topology != "fanout_reader" or side.result_semantics != "artifact_production"
                        or side.roles != [c["role_instance"] for c in advisory]
                        or [{"role_instance": c.role_instance, "payload": c.payload} for c in side.children] != advisory
                        or side.aggregate.child_success_event != "workflow.child.completed"
                        or side.aggregate.child_failure_event != "workflow.child.failed"
                        or any((side.aggregate.success_event, side.aggregate.failure_event,
                                side.aggregate.pending_event, side.aggregate.synth_role))):
                    raise ValueError("Judge advisory stage must preserve its read-only, non-gating contract")
            finals = [s for s in stages.values() if s.trigger == "flow.goal.closed" and s.flow_kind == "prd"]
            if (len(finals) != 1 or finals[0].roles != [team["author"]]
                    or not metadata.get("registered_judge_role")
                    or team["author"] not in authorized_judges(config, metadata["registered_judge_role"])):
                raise ValueError("stage_teams.judge requires one independent configured formal Judge")
        if expected or (key == "judge" and members):
            stage = stages.get(stage_id)
            actual = [{"role_instance": c.role_instance, "payload": c.payload}
                      for c in (stage.children if stage else []) if c.payload.get("stage_obligation")]
            bound = ([c.role_instance for c in stage.children] or stage.roles) if stage else []
            primary = set(bound) - {m["role"] for m in active_members(team)}
            if (actual != expected or not stage or stage.roles != bound or len(set(bound)) != len(bound)
                    or len(primary) != int(key == "judge") or team["author"] in primary):
                raise ValueError(f"stage_teams.{key} specialist contracts differ from the effective stage")
    if {"task_verify", "candidate_verify"} & teams.keys():
        policy = verification_delegation_policy(config)
        if policy.checkpoint_policies.get("pre_impl", "blocking") != "blocking":
            raise ValueError("OA verification requires a registered blocking pre_impl OA delegation")


def briefing_obligation_lines(stage: Any, instance: str, *, assigned: dict | None = None) -> list[str]:
    if stage.id not in {"prd-scan", "prd-plan", "prd-post-verify-discovery", "prd-judge-advisory"}:
        return []
    obligations = [assigned] if assigned else [
        child.payload.get("stage_obligation") for child in stage.children if child.role_instance == instance]
    for obligation in obligations:
        if not isinstance(obligation, dict) or obligation.get("role") != instance:
            continue
        if not {"obligation_id", "question", "input_refs", "expected_output", "recipient"} <= obligation.keys():
            continue
        return [
            "## Assigned stage obligation", "",
            f"- Obligation: {obligation['obligation_id']}",
            f"- Question: {obligation['question']}",
            *([f"- Selected because: {obligation['selection_reason']}"] if obligation.get("selection_reason") else []),
            *([f"- Completion condition: {obligation['completion_condition']}"] if obligation.get("completion_condition") else []),
            f"- Inputs within the authorized scope: {', '.join(obligation['input_refs'])}",
            f"- Deliver {obligation['expected_output']} to {obligation['recipient']}.", "",
        ]
    return []
