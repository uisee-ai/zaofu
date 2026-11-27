from copy import deepcopy
from types import SimpleNamespace

from tests.test_orchestrator_agent_run_plan import _runtime
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.oa_initial_verify_scope import REF, initial_verify_errors, pin_initial_verify_scope
from zf.runtime.oa_task_verify_assignment import current_scope, validate_verify_units
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.writer_fanout_admission import LoadedWriterTaskMap, writer_task_items


def test_catalog_command_and_local_acceptance_match_materialized_dispatch_scope(tmp_path):
    fixture, _, _, prior_package, _ = _runtime(tmp_path)
    runtime = Orchestrator(fixture.state_dir, fixture.config, SimpleNamespace(), project_root=tmp_path)
    data = {"tasks": [
        {"task_id": key, "scope": "catalog entry", "allowed_paths": [f"catalog/{key}.py"],
         "acceptance_criteria": [{"id": f"AC-{key}", "statement": "catalog remains searchable",
                                 "mandatory": True, "verification_owner": owner}],
         "validation": {"commands": [{"id": f"CMD-{key}", "owner": "task_verify", "tier": "runtime",
                                      "command": f"python -m tests.{key}", "timeout_seconds": 30}]}}
        for key, owner in [("index", "candidate_verify"), ("stock", "task_verify")]
    ]}
    st = runtime.state_dir
    desc = write_immutable_json_sidecar(st, data, root="fixtures", kind="task_map",
                                       schema_version="v1", created_by="test")
    run = prior_package["workflow_run_id"]
    pkg = build_plan_artifact_package(
        workflow_run_id=run, flow_kind="prd", producer_stage_id="plan", plan_revision="2", task_map_generation="g2",
        run_contract={"ref": prior_package["run_contract_ref"], "sha256": prior_package["run_contract_sha256"],
                      "contract_digest": prior_package["run_contract_digest"]},
        produced=[{"logical_name": "task_map", "artifact_kind": "task_map", "schema_version": "v1",
                   "producer_stage_id": "plan", "ref": desc["ref"], "sha256": desc["sha256"]}],
        required_ports=["task_map"])
    binding = write_plan_artifact_package(st, pkg)
    loaded = LoadedWriterTaskMap(task_items=writer_task_items(data), task_map_ref=desc["ref"], task_map_path=st/desc["ref"],
        feature_id="catalog", flow_kind="prd", workflow_run_id=run, task_map_generation="g2",
        plan_artifact_package_ref=binding["ref"], plan_artifact_package_digest=binding["sha256"],
        plan_artifact_package_id=binding["package_id"])
    identity = {"workflow_run_id": run, "task_map_generation": "g2", "plan_artifact_package_ref": binding["ref"],
                "plan_artifact_package_digest": binding["sha256"]}
    pinned = pin_initial_verify_scope(runtime, loaded, identity, "initial-plan")
    facts = hydrate_sidecar_ref(st, pinned).payload
    assert facts["task_map_sha256"] == desc["sha256"] and not facts["unresolved_task_ids"]
    assert not runtime.task_store.list_all()
    units = [{"work_unit_id": key, "stage_ids": ["verify"], "task_ids": [key],
              "verification_scope": {"acceptance_ids": [f"AC-{key}"], "required_evidence": ["local command receipt"]}}
             for key in ("index", "stock")]
    plan = {"graph": {"work_units": units}, "delegation": [
        {"work_unit_id": key, "preferred_role_refs": ["reader"]} for key in ("index", "stock")]}
    request = {**identity, REF: pinned, "verify_assignment_mode": "oa_run_plan", "verify_assignment_seats": ["reader"]}
    errors = initial_verify_errors(st, request, plan)
    assert [(e["task_id"], e["other_owners"]) for e in errors] == [("index", {"AC-index": "candidate_verify"})]
    assert errors[0]["task_verify_commands"] == [{"id": "CMD-index", "owner": "task_verify", "command": "python -m tests.index"}]
    tasks = runtime._ensure_writer_tasks_canonical(loaded, preview_only=True)
    runtime.task_store.add_many(list(tasks.values()))
    context = {"task_contract_revisions": {k: v["contract_revision"] for k, v in facts["tasks"].items()}}
    assert current_scope(runtime, "index", context, {}, units[0], ["reader"]) is None
    assert current_scope(runtime, "stock", context, {}, units[1], ["reader"])
    valid = deepcopy(plan)
    valid["graph"]["work_units"][0]["verification_scope"]["acceptance_ids"] = ["*"]
    validate_verify_units(None, valid, request)
    assert initial_verify_errors(st, request, valid) == []
    assert current_scope(runtime, "index", context, {}, valid["graph"]["work_units"][0], ["reader"])
    assert initial_verify_errors(st, identity, plan) == []  # Old request has no new comparison obligation.
    assert initial_verify_errors(st, {**request, "task_map_generation": "some-other-generation"}, plan)[0]["code"] == "invalid_task_verify_scope_reference"
    assert initial_verify_errors(st, {**request, REF: {**pinned, "sha256": "0"*64}}, plan)[0]["code"] == "invalid_task_verify_scope_reference"
