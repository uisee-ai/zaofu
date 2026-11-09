"""Explicit input changes must not disappear when an admitted generation replays."""
import json
from copy import deepcopy

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import (
    PlanArtifactPackageError, admit_plan_artifact_package_for_payload,
    package_event_payload, write_plan_artifact_package,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from tests.test_control_actions_recovery import _service
from tests.test_plan_artifact_package import _fixture_port, _package


def setup_replay(tmp_path):
    service = _service(tmp_path)
    port = _fixture_port(service.state_dir, "source_index", "old complete source")
    prior = _package(service.state_dir, revision="r0", generation="g0")
    prior["produced"].append(port)
    prior_descriptor = write_plan_artifact_package(service.state_dir, prior)
    package = _package(service.state_dir, inherited=[{
        **port, "source_package_ref": prior_descriptor["ref"],
        "source_package_digest": prior_descriptor["sha256"],
    }])
    descriptor = write_plan_artifact_package(service.state_dir, package)
    service.writer.append(ZfEvent(type="plan.artifact_package.admitted", origin="kernel",
        payload=package_event_payload(package, descriptor, status="admitted")))
    task_map = next(p for p in package["produced"] if p["logical_name"] == "task_map")
    payload = {"task_map_ref": task_map["ref"], "task_map_generation": "g1"}
    kwargs = dict(state_dir=service.state_dir, project_root=tmp_path,
        event_writer=service.writer, events=service.writer.event_log.read_all(),
        payload=payload, workflow_run_id="run-1", flow_kind="prd", producer_stage_id="plan",
        goal_id="G", metadata={"artifact_package": {"mode": "blocking"}})
    return service, descriptor, port, payload, kwargs


@pytest.mark.parametrize("form", ["inline", "descriptor", "top_level", "additional", "bad_digest"])
def test_changed_explicit_input_replay_rejected_without_admission(tmp_path, form):
    service, descriptor, port, payload, kwargs = setup_replay(tmp_path)
    before_events = service.writer.event_log.read_all()
    frozen = (service.state_dir / descriptor["ref"]).read_bytes()
    changed = _fixture_port(service.state_dir, "source_index", "new complete source")
    if form == "inline":
        payload["plan_ports"] = [{"logical_name": "source_index", "body": {"value": "changed"}}]
    elif form == "descriptor":
        payload["plan_ports"] = [changed]
    elif form == "top_level":
        payload["source_index_ref"] = changed["ref"]
    elif form == "additional":
        payload["plan_ports"] = [{"logical_name": "new_task_input", "body": {"required": True}}]
    else:
        payload["plan_ports"] = [{**port, "sha256": "f" * 64}]
    with pytest.raises(PlanArtifactPackageError, match="explicit.*port|input.*generation"):
        admit_plan_artifact_package_for_payload(**kwargs)
    assert (service.state_dir / descriptor["ref"]).read_bytes() == frozen
    assert service.writer.event_log.read_all() == before_events


@pytest.mark.parametrize("form", ["omitted", "same_descriptor", "equivalent_json", "inline"])
def test_identical_explicit_input_or_omitted_input_reuses_package(tmp_path, form):
    service, descriptor, port, payload, kwargs = setup_replay(tmp_path)
    body = hydrate_sidecar_ref(service.state_dir, port).payload
    if form == "same_descriptor":
        payload["plan_ports"] = [deepcopy(port)]
    elif form == "equivalent_json":
        path = service.state_dir / "artifacts" / "same-index.json"
        path.write_text(json.dumps(body, indent=4))
        payload["source_index_ref"] = "artifacts/same-index.json"
    elif form == "inline":
        payload["plan_ports"] = [{"logical_name": "source_index", "body": body}]
    before = service.writer.event_log.read_all()
    result = admit_plan_artifact_package_for_payload(**kwargs)
    assert result["plan_artifact_package_ref"] == descriptor["ref"]
    assert result["plan_artifact_package_digest"] == descriptor["sha256"]
    assert service.writer.event_log.read_all() == before


@pytest.mark.parametrize("changed", [False, True])
def test_product_acceptance_replay_binds_identity_but_preserves_content_fence(tmp_path, changed):
    from tests.test_product_acceptance import _spec
    service = _service(tmp_path)
    package = _package(service.state_dir)
    bound = {**_spec(), "workflow_run_id": "run-1", "plan_revision": "r1", "task_map_generation": "g1"}
    spec_ref = write_immutable_json_sidecar(service.state_dir, bound, root="fixtures/product-acceptance",
        kind="product_acceptance_spec", schema_version="product_acceptance_spec.v1", created_by="test")
    port = {"logical_name": "product_acceptance_spec", "artifact_kind": "product_acceptance_spec",
            "schema_version": "product_acceptance_spec.v1", "producer_stage_id": "prd-plan",
            "ref": spec_ref["ref"], "sha256": spec_ref["sha256"]}
    package["produced"].append(port)
    descriptor = write_plan_artifact_package(service.state_dir, package)
    service.writer.append(ZfEvent(type="plan.artifact_package.admitted", origin="kernel",
        payload=package_event_payload(package, descriptor, status="admitted")))
    # Real Plan writers submit semantic content; initial admission supplies these identities.
    semantic = {key: value for key, value in bound.items() if key not in {
        "schema_version", "workflow_run_id", "flow_kind", "plan_revision", "task_map_generation",
    }}
    if changed:
        semantic["user_journeys"] = []
    task_map = next(p for p in package["produced"] if p["logical_name"] == "task_map")
    before = service.writer.event_log.read_all()
    frozen = (service.state_dir / descriptor["ref"]).read_bytes()
    kwargs = dict(state_dir=service.state_dir, project_root=tmp_path, event_writer=service.writer,
        events=before, workflow_run_id="run-1", flow_kind="prd", producer_stage_id="plan",
        goal_id="G", metadata={"artifact_package": {"mode": "blocking"}}, payload={
            "task_map_ref": task_map["ref"], "task_map_generation": "g1",
            "plan_ports": [{"logical_name": "product_acceptance_spec", "body": semantic}],
        })
    if changed:
        with pytest.raises(PlanArtifactPackageError, match="differs from the admitted package"):
            admit_plan_artifact_package_for_payload(**kwargs)
    else:
        result = admit_plan_artifact_package_for_payload(**kwargs)
        assert result["plan_artifact_package_ref"] == descriptor["ref"]
    assert service.writer.event_log.read_all() == before
    assert (service.state_dir / descriptor["ref"]).read_bytes() == frozen
