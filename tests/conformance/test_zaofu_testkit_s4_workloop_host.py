"""Opt-in sustained Task Pipeline work with real tmux and scripted worker semantics."""

import gc
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import time
import tracemalloc
import weakref
from uuid import uuid4

import pytest

from tests.conformance.test_zaofu_testkit_s3_plan_approval import planning_runtime_at
from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import _submit_generation_stage
from tests.conformance.test_zaofu_testkit_s5_real_tmux_recovery import (
    _assert_gone, _live_pane, _process_ids,
)
from tests.test_task_pipeline_generation import _git
from tests.testkit.evidence import write_json_atomic
from tests.testkit.workloop_resources import (
    assert_locks_released, assert_workers_exited, observe_process, observe_python_allocations,
    observe_tree_bytes,
)
from tests.testkit.memray_observer import MemrayWorkloopObserver
from tests.testkit.allocation_segments import build_plan, validate_observations
from zf.core.events import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import provision_role_submit_credential
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport
from zf.runtime.workflow_operation import reduce_workflow_operations


def _runtime_weakrefs(runtime):
    return {name: weakref.ref(obj) for name, obj in (
        ("runtime", runtime), ("event_log", runtime.event_log),
        ("event_index", runtime.event_log.index),
    )}


@pytest.mark.host
@pytest.mark.serial
@pytest.mark.perf
def test_sustained_task_pipeline_releases_owned_resources(monkeypatch, record_property, request):
    if os.environ.get("ZF_RUN_MOCK_WORKLOOP") != "1":
        pytest.skip("set ZF_RUN_MOCK_WORKLOOP=1 for the owned tmux workload")
    if not shutil.which("tmux") or not Path("/proc/self/status").is_file():
        pytest.skip("requires tmux and Linux /proc sensors")
    count = int(os.environ.get("ZF_MOCK_WORKLOOP_TASKS", "32"))
    assert 2 <= count <= 64
    allocations_enabled = os.environ.get("ZF_RUN_MOCK_WORKLOOP_ALLOC") == "1"
    memray_enabled = os.environ.get("ZF_RUN_MOCK_WORKLOOP_MEMRAY") == "1"
    if memray_enabled and not allocations_enabled:
        raise AssertionError("Memray work-loop mode requires ZF_RUN_MOCK_WORKLOOP_ALLOC=1")
    if memray_enabled:
        assert os.environ.get("ZF_MOCK_WORKLOOP_MEMRAY_OUTPUT"), "explicit Memray output required"
        memray_root = Path(os.environ["ZF_MOCK_WORKLOOP_MEMRAY_OUTPUT"]).resolve()
        assert not memray_root.exists(), "Memray output must be a fresh evidence directory"
        junit_property = record_property
        lifecycle_properties = {}

        def record_property(name, value):
            junit_property(name, value)
            # KeyboardInterrupt can skip pytest's call report and lose user_properties.
            lifecycle_properties[name] = value
            write_json_atomic(memray_root / "lifecycle.json", {
                "schema_version": "zf-testkit-workloop-lifecycle.v1",
                "properties": lifecycle_properties,
            })
    allocation_sample_every = (
        int(os.environ.get("ZF_MOCK_WORKLOOP_ALLOC_SAMPLE_EVERY", "1"))
        if allocations_enabled else 1
    )
    assert 1 <= allocation_sample_every <= count
    allocation_plan = build_plan(count, segment_size=allocation_sample_every)
    task_ids = [f"TASK-LOOP-{i:03d}" for i in range(count)]
    files = {tid: f"loop-{i:03d}.txt" for i, tid in enumerate(task_ids)}
    items = [{
        "task_id": tid, "title": f"Write {files[tid]}",
        "scope": files[tid], "allowed_paths": [files[tid]],
        "blocked_by": [task_ids[i - 1]] if i else [],
        "acceptance_criteria": [f"{files[tid]} contains {tid}"],
        "verification": f"test \"$(git show HEAD:{files[tid]})\" = {tid}",
        "validation": {"commands": [{
            "id": "content", "command": f"test \"$(git show HEAD:{files[tid]})\" = {tid}",
            "tier": "runtime", "rolling_smoke": True, "deterministic": True, "reusable": True,
        }]},
    } for i, tid in enumerate(task_ids)]
    started = time.monotonic()
    deadline = started + 1200
    with TemporaryDirectory(prefix="zf-k552-workloop-20260925-") as directory, ExitStack() as allocation_cleanup:
        monkeypatch.setenv("TMUX_TMPDIR", directory)
        project = Path(directory) / "project"
        # Undo the fixture's no-activation monkeypatch; host dispatch must use production activation.
        with monkeypatch.context() as given:
            runtime, _, trigger = planning_runtime_at(project, given, task_items=items)
        state, config = runtime.state_dir, runtime.config
        config.session.tmux_session = f"zf-k552-{uuid4().hex}"
        tmux = TmuxSession(config.session.tmux_session)
        transport = TmuxTransport(tmux)
        runtime.transport = transport
        pending = []
        original_send = transport.send_task

        def observe_send(role, briefing, prompt, *, context=None):
            original_send(role, briefing, prompt, context=context)
            if context and context.task_pipeline_stage:
                pending.append(context)

        monkeypatch.setattr(transport, "send_task", observe_send)
        tokens = {role: provision_role_submit_credential(state, role)
                  for role in ("impl-1", "verify-1")}
        observed_pids = set()
        task_pids = {tid: set() for tid in task_ids}
        worker_samples = []
        samples = []
        allocation_segments = []
        runtime_refs = [_runtime_weakrefs(runtime)]
        baseline = observe_process(os.getpid())
        timing = {"loop_runtime_seconds": 0.0, "loop_runtime_calls": 0,
                  "stage_submit_seconds": 0.0, "resource_observation_seconds": 0.0}
        memray_observer = MemrayWorkloopObserver(memray_root) if memray_enabled else None
        observe_allocations = memray_observer.observe if memray_observer else observe_python_allocations
        if allocations_enabled:
            assert not tracemalloc.is_tracing(), "do not overwrite an external allocation tracer"
            if memray_observer:
                def check_closed_resources():
                    closed = observe_process(os.getpid())
                    record_property("workloop_after_tracker_close", json.dumps(closed))
                    assert closed["fds"] <= baseline["fds"] + 2
                    assert closed["socket_inodes"] == baseline["socket_inodes"]

                memray_observer.start()
                allocation_cleanup.callback(check_closed_resources)
                allocation_cleanup.callback(memray_observer.close)
                record_property("workloop_memray_directory", str(memray_root))
            else:
                tracemalloc.start(1)
                allocation_cleanup.callback(tracemalloc.stop)
            record_property("workloop_allocation_sample_every", str(allocation_sample_every))
            record_property("workloop_allocations_baseline", json.dumps(
                observe_allocations(state),
            ))
        tmux.create_session()
        try:
            observed_pids.update(_process_ids(tmux, "0"))
            runtime.run_once()
            assert not pending
            requested = runtime.event_writer.append(ZfEvent(
                type="control.action.requested", actor="operator", correlation_id="run-1",
                payload={"action": "plan-approve", "plan_id": trigger.id},
            ))
            result = ControlledActionService(
                state, runtime.event_writer, config=config, project_root=project,
                actor="operator", source="test", surface="test",
            ).execute(action="plan-approve", requested_action="plan-approve",
                      payload={"plan_id": trigger.id}, requested=requested)
            assert result["ok"]
            completed = set()
            handled = []
            while len(completed) < count:
                assert time.monotonic() < deadline, "work-loop exceeded its 1200s budget"
                tick_started = time.monotonic()
                try:
                    runtime.run_once()
                finally:
                    timing["loop_runtime_seconds"] += time.monotonic() - tick_started
                    timing["loop_runtime_calls"] += 1
                while pending:
                    context = pending.pop(0)
                    _live_pane(tmux, context.instance_id)
                    pids = _process_ids(tmux, context.instance_id)
                    observed_pids.update(pids)
                    task_pids[context.task_id].update(pids)
                    worker_samples.append({
                        "task_id": context.task_id, "stage": context.task_pipeline_stage,
                        "processes": [observe_process(int(pid)) for pid in sorted(pids)],
                    })
                    assert context.task_id in task_ids
                    submit_started = time.monotonic()
                    try:
                        _submit_generation_stage(
                            runtime, context, tokens[context.instance_id],
                            writes={files[context.task_id]: context.task_id + "\n"}
                            if context.task_pipeline_stage == "impl" else {},
                        )
                    finally:
                        timing["stage_submit_seconds"] += time.monotonic() - submit_started
                    handled.append((context.task_id, context.task_pipeline_stage))
                # Terminal Tasks may already be archived out of list_all's active view.
                done = {tid for tid in task_ids
                        if (task := runtime.task_store.get(tid)) is not None and task.status == "done"}
                if done != completed:
                    assert done > completed
                    # Previous Task exits must be reaped by the next Task completion;
                    # zombies cannot accumulate across the sustained workload.
                    _assert_gone({pid for tid in completed for pid in task_pids[tid]})
                    for tid in done - completed:
                        zombies = assert_workers_exited(task_pids[tid])
                        if zombies:
                            record_property("workloop_worker_zombies", json.dumps({
                                "task_id": tid, "processes": zombies,
                            }))
                    completed = done
                    observation_started = time.monotonic()
                    sample = {**observe_process(os.getpid()), "completed": len(done),
                              "locks_checked": assert_locks_released(state),
                              "state_usage": observe_tree_bytes(state),
                              "allocation_sampled": False}
                    if allocations_enabled and (
                        len(done) % allocation_sample_every == 0 or len(done) == count
                    ):
                        sample["python_allocations"] = observe_allocations(state)
                        sample["allocation_sampled"] = True
                        if memray_observer is not None:
                            segment = allocation_plan.segments[len(allocation_segments)]
                            allocation_segments.append({
                                "segment_index": segment.index,
                                "first_task": segment.first_task,
                                "last_task": segment.last_task,
                                "completed_tasks": len(done),
                                **{
                                    key: sample["python_allocations"][key]
                                    for key in ("tracker_pid", "tracker_started_ns", "capture_path", "window")
                                },
                            })
                    timing["resource_observation_seconds"] += time.monotonic() - observation_started
                    sample["cumulative_timing"] = dict(timing)
                    samples.append(sample)
                    write_json_atomic(Path(directory) / "resources.json", {"samples": samples})
                    assert sample["fds"] <= baseline["fds"] + 8
                    assert sample["socket_inodes"] == baseline["socket_inodes"]
                    assert not list(state.rglob("*.tmp"))
            assert handled == [(tid, stage) for tid in task_ids for stage in ("impl", "verify")]
            events = runtime.event_log.read_all()
            [ready] = [e for e in events if e.type == "candidate.ready"]
            for tid in task_ids:
                assert _git(project, "show", f"{ready.payload['commit']}:{files[tid]}") == tid
            attempts = TaskAttemptStore(state / "task_attempts.json").rows()
            assert len(attempts) == 2 * count
            assert all(a["status"] == "succeeded" for a in attempts)
            assert all(o["status"] == "settled" for o in reduce_workflow_operations(events).values())
            bindings = RoleSessionRegistry(state / "role_sessions.yaml", str(project)).task_stage_bindings()
            assert len(bindings) == 2 * count
            assert all(b["status"] == "archived" for b in bindings.values())
            assert sum(e.type == "task.done" for e in events) == count
            truth_types = {"task.done", "candidate.ready", "task.pipeline.stage.dispatched", "workflow.operation.settled"}
            truth = [e.id for e in events if e.type in truth_types]
            for _ in range(2):
                runtime = Orchestrator(state, config, transport, project_root=project)
                runtime.run_once()
                runtime_refs.append(_runtime_weakrefs(runtime))
            assert not pending
            assert [e.id for e in runtime.event_log.read_all() if e.type in truth_types] == truth
            assert not any(e.type in {"kernel.housekeeping.failed", "worker.respawn.failed"} for e in events)
            # Includes legitimate cache/ledger growth: safety ceiling, not a no-linear-leak proof.
            assert max(s["rss_kib"] for s in samples) <= baseline["rss_kib"] + 64 * 1024 + count * 4096
            if memray_observer is not None:
                record_property("workloop_allocation_segments", json.dumps(
                    validate_observations(allocation_plan, allocation_segments),
                ))
        finally:
            try:
                record_property("workloop_truth", json.dumps({
                    "events": [{"id": e.id, "type": e.type, "task_id": e.task_id,
                                "payload": e.payload} for e in runtime.event_log.read_all()
                               if e.type in {"task.done", "candidate.ready", "task.pipeline.stage.dispatched",
                                             "workflow.operation.settled", "kernel.housekeeping.failed"}],
                    "attempts": TaskAttemptStore(state / "task_attempts.json").rows(),
                    "tasks": [{"id": tid, "status": task.status if
                               (task := runtime.task_store.get(tid)) is not None else "missing"}
                              for tid in task_ids],
                }))
                runtime.event_writer.append(ZfEvent(type="simulation.done", actor="testkit"))
            finally:
                tmux.kill_session()
                assert not tmux.has_session()
                _assert_gone(observed_pids)
                final = observe_process(os.getpid())
                record_property("workloop_resources", json.dumps({
                    "task_count": count, "elapsed_seconds": time.monotonic() - started,
                    "allocation_trace_mode": "continuous" if allocations_enabled else "disabled",
                    "timing": timing,
                    "timing_scope": "loop run_once, signed stage submission, resource observations; excludes setup, PID waits, evidence serialization, cleanup and terminal rebuilds",
                    "baseline": baseline, "samples": samples, "final": final,
                    "worker_samples": worker_samples,
                    "observed_pids": sorted(observed_pids), "owned_pids_gone": True,
                    "tmux_stopped": True, "provider": "mock-cat; scripted signed results",
                    "ports": "owned Python socket FD set; no Web/API listener was exercised",
                    "state_usage": observe_tree_bytes(state),
                }))
                # The continuous Tracker still owns its capture/status FDs here.
                # The original final +2 bound runs after close, including failure paths.
                assert final["fds"] <= baseline["fds"] + (8 if memray_observer else 2)
                assert final["socket_inodes"] == baseline["socket_inodes"]
                if allocations_enabled:
                    record_property("workloop_allocations_final", json.dumps(
                        observe_allocations(state),
                    ))
        if allocations_enabled:
            # Only after truth evidence and physical cleanup: do not break failure finally.
            phases = []

            def checkpoint(phase, collected=0):
                phases.append({
                    "phase": phase, "gc_collected": collected,
                    "process": observe_process(os.getpid()),
                    "allocations": observe_allocations(state),
                    "objects_alive": [{key: ref() is not None for key, ref in row.items()}
                                      for row in runtime_refs],
                })

            persisted = {name: hashlib.sha256((state / name).read_bytes()).hexdigest()
                         for name in ("events.jsonl", "event_index.json", "kanban.json",
                                      "task_attempts.json", "role_sessions.yaml")}
            checkpoint("terminal_held")
            checkpoint("held_after_gc", gc.collect())
            assert runtime_refs[-1]["runtime"]() is runtime
            # Serialized JUnit properties remain available; discard only redundant local copies.
            del samples, sample, worker_samples, events, attempts, bindings, ready
            checkpoint("test_buffers_released", gc.collect())
            del runtime
            checkpoint("runtime_released", gc.collect())
            record_property("workloop_runtime_release", json.dumps({
                "phases": phases, "persisted_before": persisted,
                "persisted_after": {name: hashlib.sha256((state / name).read_bytes()).hexdigest()
                                    for name in persisted},
            }))
            assert not any(alive for row in phases[-1]["objects_alive"] for alive in row.values())
            cache_keys = ("event_log_read_cache_entries", "event_log_fold_entries",
                          "event_index_file_cache_entries", "cached_event_references",
                          "cached_index_event_dicts")
            assert {key: phases[0]["allocations"][key] for key in cache_keys} == {
                key: phases[-1]["allocations"][key] for key in cache_keys
            }
            assert persisted == {name: hashlib.sha256((state / name).read_bytes()).hexdigest()
                                 for name in persisted}
            if memray_observer is None:
                tracemalloc.stop()
        if memray_observer is not None:
            record_property("workloop_memray", json.dumps(memray_observer.finish()))
