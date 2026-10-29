from copy import deepcopy
import json

import pytest

from tests.test_plan_acceptance_rework import _prepare, _port
from zf.core.events.model import ZfEvent
from zf.runtime.plan_artifact_package import package_event_payload, prepare_plan_artifact_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@pytest.mark.parametrize("parent", ["T1", "unknown"])
def test_package_rebinds_all_inherited_partitioned_ports(tmp_path, parent):
    args, _, gap, previous = _prepare(tmp_path)
    state = args["state_dir"]
    spec = hydrate_sidecar_ref(state, _port(previous)).payload
    spec["task_input_policy"] = {
        "schema_version": "task-input-policy.v1",
        "shared_paths": [f"$.{key}" for key in spec if key != "user_journeys"],
        "tasks": [{"task_id": "T1", "paths": ["$.user_journeys[0]"]}],
    }
    execution = {"schema_version": "execution-spec.v1", "rules": ["deny invalid input"],
                 "task_input_policy": {"schema_version": "task-input-policy.v1",
                     "shared_paths": ["$.schema_version"],
                     "tasks": [{"task_id": "T1", "paths": ["$.rules[0]"]}]}}
    first, descriptor, _ = prepare_plan_artifact_package(events=[], payload={
        "task_map_ref": "r1.json", "task_map_generation": "g1",
        "requirement_spec_ref": "requirement.md", "plan_ports": [
            {"logical_name": "product_acceptance_spec", "body": spec},
            {"logical_name": "execution_spec", "body": execution}]}, **args)
    event = ZfEvent(type="plan.artifact_package.admitted", correlation_id="run-1",
                   payload=package_event_payload(first, descriptor, status="admitted"))
    (state / "r2.json").write_text(json.dumps({"tasks": [
        {"task_id": "T1", "acceptance_criteria": ["Exact JSON"]},
        {"task_id": "T2", "parent_task_id": parent, "acceptance_criteria": ["Exact JSON"]}],
        "amend": {"gap_task_ids": ["T2"]}}))
    if parent == "unknown":
        with pytest.raises(ValueError, match="source input partition"):
            prepare_plan_artifact_package(events=[event], payload=gap, **args)
        return
    package, _, _ = prepare_plan_artifact_package(events=[event], payload=gap, **args)
    for name in ("product_acceptance_spec", "execution_spec"):
        before_port = next(p for p in first["produced"] if p["logical_name"] == name)
        before = hydrate_sidecar_ref(state, before_port).payload
        port = next(p for p in package["produced"] + package["inherited"] if p["logical_name"] == name)
        after = hydrate_sidecar_ref(state, port).payload
        paths = {r["task_id"]: r["paths"] for r in after["task_input_policy"]["tasks"]}
        assert paths["T2"] == paths["T1"]
        expected = deepcopy(before)
        expected["task_input_policy"]["tasks"].append({"task_id": "T2", "paths": paths["T1"]})
        if name == "product_acceptance_spec":
            expected.update(plan_revision="g2", task_map_generation="g2")
        assert after == expected
        assert port["ref"] != before_port["ref"]
        assert hydrate_sidecar_ref(state, before_port).payload == before
