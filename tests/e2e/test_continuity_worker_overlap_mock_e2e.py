"""Scheduler-to-held-process overlap; no claim of real-provider throughput."""
import json
from pathlib import Path
import selectors
import subprocess
import sys

import pytest

from tests.e2e.test_verification_eight_slots_mock_e2e import _runtime
from zf.core.events import ZfEvent
from zf.runtime.task_pipeline_runtime import reconcile_task_pipeline_runtime
from zf.runtime.task_workspaces import TaskWorkspaceManager

pytestmark = pytest.mark.mock_e2e

WORKER = """
import json, os, pathlib, sys, time
root = pathlib.Path.cwd()
assert root.joinpath('README.md').read_text() == 'fixture\\n'
start = time.monotonic_ns()
root.joinpath('worker-progress.txt').write_text(sys.argv[1])
print(json.dumps({'pid': os.getpid(), 'start': start, 'workdir': str(root)}), flush=True)
assert sys.stdin.readline().strip() == 'release'
print(json.dumps({'end': time.monotonic_ns()}), flush=True)
"""


@pytest.mark.parametrize("stage,count,expected", [
    ("impl", 9, 8), ("verify", 9, 8), ("mixed", 17, 16),
    ("impl", 2, 2), ("verify", 2, 2),
])
def test_eligible_frontier_runs_in_overlapping_isolated_processes(
    tmp_path, monkeypatch, record_property, stage, count, expected,
):
    runtime, delivered, head = _runtime(tmp_path, monkeypatch, stage, task_count=count)
    manager = TaskWorkspaceManager(state_dir=runtime.state_dir, project_root=tmp_path, config=runtime.config)
    children, intervals = [], []

    def send(role, briefing, prompt, context):
        plan = manager.plan(workflow_run_id=context.run_id, task_id=context.task_id,
            task_map_generation=context.task_map_generation, workspace_generation=1, base_ref=head)
        child = subprocess.Popen([sys.executable, "-u", "-c", WORKER, context.task_id],
            cwd=plan.project_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        children.append(child)
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), "mock worker failed to reach the held execution step"
        interval = json.loads(child.stdout.readline())
        intervals.append({**interval, "stage": context.task_pipeline_stage, "task": context.task_id})
        delivered.append((role, briefing, context))
        return context

    monkeypatch.setattr(runtime, "_send_transport_task", send)
    try:
        decisions = reconcile_task_pipeline_runtime(runtime)
        assert len(children) == expected, decisions
        assert all(child.poll() is None for child in children)
        assert len({row["workdir"] for row in intervals}) == expected
        assert len({row["pid"] for row in intervals}) == expected
        if stage == "mixed":
            assert sum(row["stage"] == "impl" for row in intervals) == 8
            assert sum(row["stage"] == "verify" for row in intervals) == 8
        reconcile_task_pipeline_runtime(runtime)
        assert len(children) == expected  # Replay cannot spawn extra workers.
        for child, interval in zip(children, intervals):
            stdout, stderr = child.communicate("release\n", timeout=10)
            assert child.returncode == 0, stderr
            interval.update(json.loads(stdout))
            assert Path(interval["workdir"], "worker-progress.txt").read_text() == interval["task"]
        assert max(row["start"] for row in intervals) < min(row["end"] for row in intervals)
        record_property("execution_intervals", json.dumps(intervals))
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)
        runtime.event_writer.append(ZfEvent(type="simulation.done", origin="kernel", payload={
            "scope": "mock-worker-overlap", "real_provider": False,
        }))
