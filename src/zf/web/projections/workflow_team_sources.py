"""Read only admitted delegation and frozen Reader slices needed by stage cards."""

from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload
from zf.runtime.orchestrator_agent_run_plan import EXECUTION_PLAN_SCHEMA, RUN_PLAN_ADMITTED
from zf.runtime.product_acceptance import product_acceptance_binding_from_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_pipeline_contexts import latest_task_pipeline_generation_context, task_pipeline_generation_contexts


def read_stage_team_inputs(state, inputs):
    run = inputs["scope"]["run_id"]
    if not run:
        return {}, []
    events = [e for _, e in inputs["events"] if e.origin == "kernel"]
    stages = inputs["compiled"].get("nodes", [])
    result, refs = {}, []
    context = latest_task_pipeline_generation_context(events, workflow_run_id=run) or {}
    contexts = task_pipeline_generation_contexts(events)
    keys = ("workflow_run_id", "task_map_generation", "plan_artifact_package_ref", "plan_artifact_package_digest")
    admissions = {}
    event_positions = {e.id: i for i, e in enumerate(events)}
    for e in events:
        if e.type == RUN_PLAN_ADMITTED and e.payload.get("workflow_run_id") == run:
            identity = tuple(e.payload.get(k) for k in keys)
            if all(identity):
                admissions[(identity, e.payload.get("verify_assignment_task") or "")] = e
    plans = {}
    for task, ctx in contexts.items():
        # A delta admission can replace B while A continues under its original plan.
        if ctx.get("workflow_run_id") != run:
            continue
        stage = ctx.get("stage_id", "")
        identity = tuple(ctx.get(k) for k in keys)
        for mode in ("impl", "verify"):
            base = admissions.get((identity, ""))
            chosen = base
            if mode == "verify":
                correction = admissions.get((identity, task))
                if correction and (not base or event_positions[correction.id] > event_positions[base.id]):
                    chosen = correction
            if not chosen:
                continue
            descriptor = chosen.payload.get("execution_plan_ref") or {}
            item = result.setdefault(stage, {"event_id": chosen.id, "members": [],
                                             "task_map_generation": context["task_map_generation"]})
            try:
                if chosen.id not in plans:
                    refs.append(descriptor)
                    body = hydrate_sidecar_ref(state, descriptor).payload
                    if body.get("schema_version") != EXECUTION_PLAN_SCHEMA or any(
                            body.get("identity", {}).get(k) != ctx[k] for k in keys):
                        raise ValueError("OA 分工与当前任务图绑定不一致")
                    units = {u["work_unit_id"]: u for u in body["work_units"]}
                    plans[chosen.id] = [(units[d["work_unit_id"]], d) for d in body["delegation"]]
                aliases = {mode, f"{ctx.get('flow_kind', '')}-lanes-{mode}"}
                if mode == "impl":
                    aliases.add("implementation")
                for unit, delegation in plans[chosen.id]:
                    if task not in unit.get("task_ids", []) or not aliases.intersection(unit.get("stage_ids", [])):
                        continue
                    scope = unit.get("verification_scope") or {}
                    signer = {"obligation_id": unit["work_unit_id"], "task_ids": [task],
                        "pipeline_stage": mode, "task_map_generation": ctx["task_map_generation"],
                        "role": " / ".join(delegation["preferred_role_refs"]),
                        "acceptance_ids": scope.get("acceptance_ids", []),
                        "expected_output": "、".join(scope.get("required_evidence", [])),
                        "contract_revision": chosen.payload.get("verify_assignment_revision")
                                             or (ctx.get("task_contract_revisions") or {}).get(task, ""),
                        "binding": {"execution_plan_ref": descriptor, "event_id": chosen.id,
                                    "task_map_generation": ctx["task_map_generation"]}}
                    item["members"].append(signer)
                    if mode == "verify":
                        for specialist in unit.get("specialists", []):
                            item["members"].append({**signer, **specialist, "specialist": True,
                                "recipient": signer["role"], "work_unit_id": unit["work_unit_id"]})
            except (OSError, KeyError, TypeError, ValueError):
                item["diagnostic"] = "本代 OA 分工凭证不可用"
    candidate_stages = [s["stage_id"] for s in stages if s.get("trigger") == "candidate.ready"
                        and s.get("success_event") in {"test.passed", "verify.passed"}]
    if candidate_stages:
        try:
            freeze = frozen_candidate_for_payload(events, run, payload={})
        except (ValueError, KeyError):
            for stage in candidate_stages:
                result[stage] = {"diagnostic": "冻结事件的授权链不可用"}
            return result, refs
        if freeze and (not context or freeze.payload.get("task_map_generation") == context["task_map_generation"]):
            f = freeze.payload
            descriptor = {"ref": f.get("plan_artifact_package_ref"), "sha256": f.get("plan_artifact_package_digest")}
            refs.append(descriptor)
            try:
                binding = product_acceptance_binding_from_package(state, descriptor, validate_ports=False)
                refs.append({"ref": binding.get("spec_ref")})
                spec = binding.get("spec", {})
                strategy = (inputs["compiled"].get("stage_teams") or {}).get("candidate_verify")
                if strategy:
                    from zf.runtime.candidate_reader_assignments import approved_roster_ref
                    approval = approved_roster_ref(events, f, spec)
                    if f.get("candidate_reader_approval") != approval or len(spec.get("candidate_reader_assignments", [])) != strategy["width"]:
                        raise ValueError("冻结 Reader 名单与 OA 获准版本不一致")
                    refs.append(approval["execution_plan_ref"])
                members = []
                for row in spec.get("candidate_reader_assignments", []):
                    if any(spec.get(k) != f.get(k) for k in ("workflow_run_id", "task_map_generation")):
                        raise ValueError("Reader 名单与冻结目标不一致")
                    members.append({"obligation_id": row["role_instance"], "role": row["role_instance"],
                                    "task_ids": list(dict.fromkeys(ac["task_id"] for ac in row["acceptance"])),
                                    "acceptance": row["acceptance"], "expected_output": "Candidate 判词"})
                for stage in candidate_stages:
                    result[stage] = {"event_id": freeze.id, "freeze_id": f["freeze_id"],
                                     "task_map_generation": f.get("task_map_generation"), "members": members}
            except (OSError, TypeError, ValueError, KeyError):
                for stage in candidate_stages:
                    result[stage] = {"diagnostic": "冻结 Reader 凭证不可用"}
    return result, refs
