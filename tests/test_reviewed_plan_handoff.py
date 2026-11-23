"""A positive verdict publishes the Planner bytes observed by the Critic."""

import json

import pytest

from tests.test_reader_fanout_runtime import _task_map_synth_orchestrator
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar


@pytest.mark.parametrize("fault", ["", "artifact", "author", "dispatch", "fanout_children", "unbound"])
def test_verdict_only_synth_hands_off_reviewed_sources_without_a_workdir(tmp_path, fault):
    state, log, orch, fanout = _task_map_synth_orchestrator(tmp_path)

    def put(name, body):
        ref = write_immutable_json_sidecar(state, body, root="handoff/" + name,
            kind=name, schema_version=name + ".v1", created_by="test")
        return {**ref, "source_id": name, "artifact_id": name + ".json"}

    mapping = put("child-artifact-source-reader-1", {"tasks": []})
    index = put("child-artifact-source-reader-2", {"sources": []})
    port = {"ref": "docs/producer-index.json", "sha256": index["sha256"]}
    shadow = tmp_path / port["ref"]
    shadow.parent.mkdir(exist_ok=True)
    shadow.write_text('{"unreviewed":"later project file"}')
    producer = put("child-result-source-reader", {
        "child_id": "source-reader", "task_map_ref": mapping["ref"],
        "source_index_ref": "docs/producer-index.json", "plan_ports": [{"logical_name": "source_index", **port}]})
    sources = [producer, mapping] if fault == "unbound" else [producer, mapping, index]
    identity = dict(workflow_run_id="trace-product", fanout_id=fanout,
                    stage_id="product-task-map", plan_revision="revision")
    children = [{"child_id": "source-reader", **{key: producer[key] for key in ("source_id","artifact_id","sha256")}}]
    contract = put("contract", {"schema_version": "plan-synth-contract.v1", **identity,
                  "child_bindings": children, "source_bindings": sources})
    run_id = f"run-{fanout}-synth"
    manifest = put("manifest", {"schema_version": "attempt-source-manifest.v1",
        "attempt_id": "a", "dispatch_id": run_id, "workflow_run_id": "trace-product", "sources": sources})
    review = {**identity, "operation_id": "op", "attempt_id": "a", "run_id": run_id,
              "plan_synth_contract_ref": contract["ref"], "plan_synth_contract_digest": contract["sha256"],
              "reviewed_plan_candidate_digest": contract["sha256"], "plan_synth_verdict_only": True}
    orch.event_writer.append(ZfEvent(type="fanout.synth.dispatched", payload={
        **review, "attempt_id": "other" if fault == "dispatch" else "a", "attempt_source_manifest": manifest}))
    if fault in {"artifact", "author"}:
        (state / (index if fault == "artifact" else producer)["ref"]).write_text("{}\n")
    result = orch.event_writer.append(ZfEvent(type="fanout.synth.completed", actor="task-map-synth",
        correlation_id="trace-product", payload={**review, "role_instance": "task-map-synth",
            "report": {"status":"passed", "recommendation":"approve", "summary":"Reviewed immutable sources"}}))
    if fault == "fanout_children":
        path = state / "fanouts" / fanout / "manifest.json"
        body = json.loads(path.read_text())
        body["children"].append({"child_id": "second"})
        path.write_text(json.dumps(body))
    orch._finalize_fanout_synth(result)
    published = [event for event in log.read_all() if event.type in {"task_map.ready","task_map.blocked"}
                 and event.causation_id == result.id]
    assert len(published) == 1
    event = published[0]
    if fault:
        assert event.type == "task_map.blocked"
        assert event.payload["failure_class"] == "plan_artifact_package_protocol"
        assert event.payload["semantic_rework_cost"] == 0
    else:
        assert event.type == "task_map.ready", event.payload.get("reason")
        assert event.payload["task_map_ref"] == mapping["ref"]
        assert event.payload["source_index_ref"] == index["ref"]
        assert producer["ref"] in event.payload["artifact_refs"]
        assert event.payload["plan_ports"][0]["sha256"] == index["sha256"]
