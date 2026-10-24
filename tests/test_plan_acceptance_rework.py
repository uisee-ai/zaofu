from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.plan_artifact_package import (
    package_event_payload,
    prepare_plan_artifact_package,
)
from zf.runtime.product_acceptance import ProductAcceptanceError
from zf.runtime.run_contract import stable_json_sha256, write_run_contract
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _prepare(tmp_path: Path):
    state = tmp_path / ".state"
    state.mkdir()
    task_map = {"tasks": [{"task_id": "T1", "acceptance_criteria": ["Exact JSON"]}]}
    for name in ("r1", "r2"):
        (state / f"{name}.json").write_text(json.dumps(task_map))
    (state / "requirement.md").write_text("Exact JSON\n")
    contract = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}}
    write_run_contract(state, {**contract, "contract_digest": stable_json_sha256(contract)})
    spec = {
        "schema_version": "product_acceptance_spec.v1",
        "assembly_owner": "T1",
        "entrypoints": [{"entrypoint_id": "cli", "start_command": "python cli.py",
                         "health_check": "python cli.py --help", "owner": "T1"}],
        "user_journeys": [{
            "journey_id": "exact", "title": "Exact JSON", "mandatory": True,
            "verification_tier": "e2e", "assertions": [{
                "assertion_id": "result", "statement": "Exact JSON output",
                "observation_kind": "cli", "mandatory": True,
            }],
        }],
        "provider_qualification": {"required_for_goal": False},
    }
    args = dict(state_dir=state, project_root=tmp_path, workflow_run_id="run-1",
                flow_kind="prd", producer_stage_id="prd-impl", goal_id="GOAL-1",
                metadata={"artifact_package": {"mode": "blocking"}})
    payload = {"task_map_ref": "r1.json", "task_map_generation": "g1",
               "requirement_spec_ref": "requirement.md", "plan_ports": [{
                   "logical_name": "product_acceptance_spec", "body": spec,
               }]}
    package, descriptor, _ = prepare_plan_artifact_package(events=[], payload=payload, **args)
    event = ZfEvent(type="plan.artifact_package.admitted", correlation_id="run-1",
                   payload=package_event_payload(package, descriptor, status="admitted"))
    gap = {"task_map_ref": "r2.json", "task_map_generation": "g2",
           "requirement_spec_ref": "requirement.md", "resume_scope": "gap_tasks_only",
           "amend_of": "r1.json", "supersedes_task_map_generation": "g1"}
    return args, event, gap, package


def _port(package):
    return next(p for p in [*package["produced"], *package["inherited"]]
                if p["logical_name"] == "product_acceptance_spec")


def test_gap_plan_rebinds_acceptance_identity_without_changing_requirements(tmp_path):
    args, event, gap, previous = _prepare(tmp_path)
    before_port = _port(previous)
    before = hydrate_sidecar_ref(args["state_dir"], before_port).payload
    package, _, claim = prepare_plan_artifact_package(events=[event], payload=gap, **args)
    port = _port(package)
    after = hydrate_sidecar_ref(args["state_dir"], port).payload
    assert after == {**before, "plan_revision": "g2", "task_map_generation": "g2"}
    assert port["ref"] != before_port["ref"]
    assert port["rebound_from_ref"] == before_port["ref"]
    assert port["rebound_from_digest"] == before_port["sha256"]
    assert hydrate_sidecar_ref(args["state_dir"], before_port).payload == before
    assert claim["goal_claim_continuity"]["status"] == "passed"
    assert not any(p["logical_name"] == "product_acceptance_spec" for p in package["inherited"])


@pytest.mark.parametrize("field,value", [
    ("amend_of", "unrelated.json"),
    ("supersedes_task_map_generation", "stale-generation"),
    ("resume_scope", "full_replan"),
])
def test_unrelated_replan_cannot_relabel_inherited_acceptance(tmp_path, field, value):
    args, event, gap, _ = _prepare(tmp_path)
    with pytest.raises(ProductAcceptanceError):
        prepare_plan_artifact_package(events=[event], payload={**gap, field: value}, **args)


def test_gap_plan_rejects_changed_base_artifact(tmp_path):
    args, event, gap, _ = _prepare(tmp_path)
    (args["state_dir"] / "r1.json").write_text('{"tasks": []}')
    with pytest.raises(ProductAcceptanceError, match="exact prior plan lineage"):
        prepare_plan_artifact_package(events=[event], payload=gap, **args)
