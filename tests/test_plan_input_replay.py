"""Explicit input changes must not disappear when an admitted generation replays."""
import json
from copy import deepcopy

import pytest

from zf.core.events.model import ZfEvent
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
