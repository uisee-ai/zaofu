"""Task stages bind to the compiled container through admitted generation facts."""
from copy import deepcopy

import pytest

from tests.test_sdlc_graph import sources
from zf.core.events.model import ZfEvent
from zf.runtime.sdlc_graph import build_sdlc_graph


def pipeline_source(*, origin="kernel", run="run-a", generation="G1", stage="build"):
    admission = ZfEvent(id="generation", type="task.pipeline.generation.admitted", origin=origin,
                       payload={"workflow_run_id": run, "task_map_generation": generation, "stage_id": stage})
    request = ZfEvent(id="request", type="workflow.operation.requested", origin="kernel", task_id="T1",
                     payload={"workflow_run_id": "run-a", "operation_id": "impl-op", "operation_type": "task-stage",
                              "parent_stage_id": "impl", "task_pipeline_stage": "impl", "pipeline_key": "P1",
                              "task_map_generation": "G1", "role_instance": "worker"})
    started = ZfEvent(id="started", type="workflow.operation.started", origin="kernel",
                     payload={"workflow_run_id": "run-a", "operation_id": "impl-op"})
    return sources(events=list(enumerate([admission, request, started], 1)))


@pytest.mark.parametrize("execution_only", [False, True])
def test_task_pipeline_execution_updates_its_compiled_container(execution_only):
    source = pipeline_source()
    before = deepcopy(source)
    graph = build_sdlc_graph(source, execution_only=execution_only)
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert nodes["workflow:stage:build"]["status"] == "running"
    operation = nodes["operation:impl-op"]["data"]
    assert operation["parent_stage_id"] == "impl"  # Canonical operation fact stays intact.
    assert operation["compiled_stage_id"] == "build"
    assert operation["compiled_stage_binding_event_id"] == "generation"
    assert any(e["source"] == "workflow:stage:build" and e["target"] == "operation:impl-op"
               for e in graph["edges"])
    assert source == before


@pytest.mark.parametrize("changes", [{"origin": "worker"}, {"run": "other-run"},
                                    {"generation": "G2"}, {"stage": "missing"}])
def test_unbound_or_untrusted_generation_cannot_claim_stage(changes):
    graph = build_sdlc_graph(pipeline_source(**changes))
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert nodes["workflow:stage:build"]["status"] == "pending"
    assert not nodes["operation:impl-op"]["data"].get("compiled_stage_id")


def test_completed_task_call_waits_for_exact_generation_freeze():
    source = pipeline_source()
    source["events"].append((4, ZfEvent(type="workflow.operation.settled", origin="kernel",
        payload={"workflow_run_id": "run-a", "operation_id": "impl-op"})))
    def stage():
        return next(n for n in build_sdlc_graph(source)["nodes"] if n["id"] == "workflow:stage:build")
    assert stage()["status"] == "running"
    for origin, generation in (("worker", "G1"), ("kernel", "G2")):
        source["events"].append((5, ZfEvent(type="candidate.ready", origin=origin,
            payload={"workflow_run_id": "run-a", "task_map_generation": generation})))
        assert stage()["status"] == "running"
    source["events"].append((6, ZfEvent(id="freeze", type="candidate.ready", origin="kernel",
        payload={"workflow_run_id": "run-a", "task_map_generation": "G1"})))
    assert stage()["status"] == "completed"
    assert stage()["data"]["pipeline_status_event_id"] == "freeze"
