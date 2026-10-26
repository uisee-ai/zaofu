from dataclasses import replace

from tests.test_task_pipeline_result import _operation_events, _result_event
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_agent_aggregation import _current_result_descriptors


def _refs(events, source=None, generation="map-g1"):
    return _current_result_descriptors(
        events,
        source_event=source or _result_event(),
        workflow_run_id="run-1",
        task_map_generation=generation,
    )


def test_current_pipeline_impl_and_verify_results_reach_oa():
    events = _operation_events()
    assert {r["sha256"] for r in _refs(events)} == {"control-sha"}
    verify = []
    for event in events[1:]:
        p = {
            **event.payload,
            "task_pipeline_stage": "verify",
            "operation_id": "op-verify",
        }
        if event.type == "workflow.call.result.admitted":
            p["control_result_ref"] = {
                "ref": "results/verify.json",
                "sha256": "verify-sha",
            }
        verify.append(replace(event, id=event.id + "-verify", payload=p))
    assert {r["sha256"] for r in _refs([*events, *verify])} == {
        "control-sha",
        "verify-sha",
    }


def test_other_run_unscoped_and_obsolete_results_do_not_enter_pack():
    source = ZfEvent(
        type="judge.passed",
        correlation_id="run-1",
        payload={
            "task_map_generation": "map-g1",
            "result_refs": [{"ref": "judge.json", "sha256": "judge-sha"}],
        },
    )
    rows = [source]
    for run, gen in [("run-2", "map-g1"), (None, "map-g1"), ("run-1", "map-old")]:
        rows.append(
            ZfEvent(
                type="judge.passed",
                correlation_id=run,
                payload={
                    "task_map_generation": gen,
                    "result_refs": [{"ref": "wrong.json", "sha256": "wrong"}],
                },
            )
        )
    assert _refs(rows, source) == source.payload["result_refs"]


def test_new_running_attempt_supersedes_old_passed_result():
    rows = _operation_events()
    new = replace(
        rows[1],
        id="retry",
        payload={
            **rows[1].payload,
            "operation_id": "op-2",
            "operation_generation": 2,
        },
    )
    assert _refs([*rows, new]) == []


def test_partial_replan_retains_unreplaced_task_evidence():
    rows = _operation_events()
    # TASK-1 remains bound to g1 while another task is admitted in g2.
    rows.append(
        ZfEvent(
            type="task.pipeline.generation.admitted",
            payload={
                "schema_version": "task-pipeline-generation.v1",
                "workflow_run_id": "run-1",
                "task_map_generation": "map-g2",
                "task_ids": ["T2"],
            },
        )
    )
    assert {r["sha256"] for r in _refs(rows, generation="map-g2")} == {"control-sha"}
    rows.append(
        ZfEvent(
            type="task.pipeline.generation.admitted",
            payload={
                "schema_version": "task-pipeline-generation.v1",
                "workflow_run_id": "run-1",
                "task_map_generation": "map-g2",
                "task_ids": ["T1"],
            },
        )
    )
    assert _refs(rows, generation="map-g2") == []


def test_checkpoint_required_reads_include_impl_verify_and_judge(tmp_path):
    from tests.test_orchestrator_agent_aggregation import (
        _runtime,
        _seed_run,
        _result,
        _prepared,
    )
    from zf.runtime.orchestrator_agent_aggregation import pre_closeout_checkpoint_state

    runtime, _ = _runtime(tmp_path, checkpoint="pre_closeout")
    package = _seed_run(runtime)
    descriptors = []
    for stage in ("impl", "verify"):
        # Separate descriptors with real immutable sidecars for each stage.
        from zf.runtime.call_result_envelope import write_immutable_json_sidecar

        descriptor = write_immutable_json_sidecar(
            runtime.state_dir,
            {"stage": stage, "verdict": "passed"},
            root="fixtures/pipeline-results",
            kind="stage_result",
            schema_version="test-stage-result.v1",
            created_by="test",
        )
        descriptors.append(descriptor)
        for event in _operation_events():
            payload = {
                **event.payload,
                "workflow_run_id": package["workflow_run_id"],
                "task_map_generation": package["task_map_generation"],
            }
            if event.type != "task.pipeline.generation.admitted":
                payload.update(task_pipeline_stage=stage, operation_id=f"op-{stage}")
            if event.type == "workflow.call.result.admitted":
                payload["control_result_ref"] = descriptor
            runtime.event_writer.append(replace(event, payload=payload))
    judge = _result(runtime)
    source = runtime.event_writer.append(
        ZfEvent(
            type="judge.passed",
            correlation_id=package["workflow_run_id"],
            payload={**package, "result_refs": [judge]},
        )
    )
    state = pre_closeout_checkpoint_state(runtime, source)
    assert state.enabled and state.blocking
    prepared = _prepared(runtime)
    manifest = prepared.context.source_manifest
    required = {
        (row["source_id"], row["artifact_id"])
        for row in prepared.context.read_policy["required_reads"]
    }
    refs = {
        row["sha256"]
        for row in manifest["sources"]
        if (row["source_id"], row["artifact_id"]) in required
    }
    assert {row["sha256"] for row in [*descriptors, judge]} <= refs
    count = len(
        [
            e
            for e in runtime.event_log.read_all()
            if e.type == "orchestrator.semantic.checkpoint.requested"
        ]
    )
    pre_closeout_checkpoint_state(runtime, source)
    assert (
        len(
            [
                e
                for e in runtime.event_log.read_all()
                if e.type == "orchestrator.semantic.checkpoint.requested"
            ]
        )
        == count
    )
