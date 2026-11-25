"""A positive verdict publishes the Planner bytes observed by the Critic."""

import json

import pytest

from tests.test_reader_fanout_runtime import _task_map_synth_orchestrator
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import (
    build_plan_artifact_package,
    hydrate_plan_artifact_package,
    write_plan_artifact_package,
)
from zf.runtime.reviewed_plan_handoff import reviewed_plan_children
from zf.runtime.run_contract import stable_json_sha256, write_run_contract_snapshot


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


def test_prd_plan_aggregate_packages_exact_critic_bound_prepared_planner_report(tmp_path):
    state, _log, orch, fanout = _task_map_synth_orchestrator(tmp_path)
    project_docs = tmp_path / "docs" / "plans"
    project_docs.mkdir(parents=True)

    def put(name, body):
        ref = write_immutable_json_sidecar(
            state, body, root="handoff/" + name, kind=name,
            schema_version=name + ".v1", created_by="test",
        )
        return {**ref, "source_id": name, "artifact_id": name + ".json"}

    names = ["requirement_spec", "goal_claim_set", "task_map", "planning_result",
             "source_index", "source_inventory"]
    project_refs = [f"docs/plans/{name}.json" for name in names]
    project_refs.insert(3, "docs/plans/plan.md")
    for project_ref in project_refs:
        path = tmp_path / project_ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"unreviewed":"project copy"}')
    port_paths = {
        name: f"docs/plans/{name}.json" for name in names
    }
    artifact_by_path = {}
    source_rows = []
    for position, project_ref in enumerate(dict.fromkeys([
        "docs/plans/plan.md", port_paths["task_map"], port_paths["source_index"],
        port_paths["source_inventory"], port_paths["requirement_spec"],
        port_paths["goal_claim_set"], port_paths["planning_result"],
    ]), start=1):
        name = project_ref.rsplit("/", 1)[-1].removesuffix(".json").removesuffix(".md")
        artifact = put(f"child-artifact-source-reader-{position}", {
            "schema_version": name + ".v1", "status": "ready", "value": name,
        })
        artifact_by_path[project_ref] = artifact
        source_rows.append(artifact)
    ports = [{
        "logical_name": name, "artifact_kind": name,
        "schema_version": name + ".v1", "producer_stage_id": "prd-plan",
        "ref": port_paths[name], "sha256": artifact_by_path[port_paths[name]]["sha256"],
    } for name in names]
    producer = put("child-result-source-reader", {
        "child_id": "source-reader", "status": "passed",
        "task_map_ref": "docs/plans/task_map.json",
        "source_index_ref": "docs/plans/source_index.json",
        "source_inventory_ref": "docs/plans/source_inventory.json",
        "plan_artifact_ref": "docs/plans/plan.md",
        "plan_ports": ports,
    })
    # The Planner's prepared report and each port are the sources frozen into
    # the Critic request; verdict-only output contributes no structure.
    source_rows.insert(0, producer)
    identity = dict(workflow_run_id="trace-product", fanout_id=fanout,
                    stage_id="prd-plan", plan_revision="revision")
    producer_binding = {key: producer[key] for key in ("source_id", "artifact_id", "sha256")}
    contract = put("contract", {
        "schema_version": "plan-synth-contract.v1", **identity,
        "child_bindings": [{"child_id": "source-reader", **producer_binding}],
        "source_bindings": [
            {key: row[key] for key in ("source_id", "artifact_id", "ref", "sha256")}
            for row in source_rows
        ],
    })
    manifest_source = put("attempt-source-manifest", {
        "schema_version": "attempt-source-manifest.v1", "attempt_id": "attempt-1",
        "dispatch_id": "run-prd-plan-synth", "workflow_run_id": "trace-product",
        "sources": source_rows,
    })
    run = "run-prd-plan-synth"
    review = {
        **identity, "operation_id": "operation-1", "attempt_id": "attempt-1",
        "run_id": run, "plan_synth_contract_ref": contract["ref"],
        "plan_synth_contract_digest": contract["sha256"],
        "reviewed_plan_candidate_digest": contract["sha256"],
        "plan_synth_verdict_only": True,
    }
    orch.event_writer.append(ZfEvent(type="fanout.synth.dispatched", payload={
        **review, "request_hash": "request-hash", "attempt_source_manifest": manifest_source,
    }))
    verdict = orch.event_writer.append(ZfEvent(
        type="fanout.synth.completed", actor="plan-critic", correlation_id="trace-product",
        payload={**review, "role_instance": "task-map-synth", "report": {
            "status": "passed", "recommendation": "approve", "summary": "Reviewed exact Plan",
        }},
    ))

    children = reviewed_plan_children(orch, {
        "fanout_id": fanout,
        "children": [{"child_id": "source-reader"}],
    }, review)
    aggregate = orch._generic_fanout_success_payload(
        manifest={"fanout_id": fanout, "children": [{"child_id": "source-reader"}]},
        success_event="task_map.ready", extra_payloads=[verdict.payload],
        reviewed_children=children,
    )
    assert aggregate["source_index_ref"] != aggregate["source_inventory_ref"]
    assert all(port["ref"] != port_paths[port["logical_name"]]
               for port in aggregate["plan_ports"])
    assert not (state / "workdirs" / "source-reader" / "project").exists()

    contract_body = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}}
    run_contract = write_run_contract_snapshot(state, {
        "schema_version": "run-contract.v1", "workflow": {"kind": "prd"},
        "contract_digest": stable_json_sha256(contract_body),
    })
    package = build_plan_artifact_package(
        workflow_run_id="trace-product", flow_kind="prd", producer_stage_id="prd-plan",
        run_contract=run_contract, plan_revision="revision", task_map_generation="generation",
        produced=aggregate["plan_ports"], required_ports=names[:4],
    )
    descriptor = write_plan_artifact_package(state, package)
    hydrated = hydrate_plan_artifact_package(state, descriptor)
    assert {port["logical_name"] for port in hydrated["produced"]} == set(names)
    assert aggregate["source_index_ref"] == next(
        port["ref"] for port in hydrated["produced"] if port["logical_name"] == "source_index"
    )
