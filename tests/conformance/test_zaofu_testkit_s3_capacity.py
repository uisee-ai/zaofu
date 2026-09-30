"""PERF-04/06 finite fanout samples, not native concurrency or long-run proof."""

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import time

import pytest

from zf.core.config.schema import RoleConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import Orchestrator

from tests.test_reader_fanout_runtime import _RecordingTransport, _config
from tests.testkit.oracles import assert_no_duplicate_event_ids
from tests.testkit.runtime_case_evidence import attach_runtime_case_evidence


def _start(root: Path, request, count: int, *, timeout: str = ""):
    config = _config(synth=not timeout)
    config.project.state_dir = "runtime-state"
    readers = [f"review-{index:02d}" for index in range(count)]
    config.roles = [RoleConfig(
        name=name, backend="mock", role_kind="reader",
        publishes=["workflow.child.completed", "workflow.child.failed"],
    ) for name in readers] + ([] if timeout else [config.roles[-1]])
    stage = config.workflow.stages[0]
    stage.roles = readers
    stage.timeout_seconds = 3 if timeout == "natural" else 3600
    stage.aggregate.max_retries = 1 if timeout else 0
    state = root / config.project.state_dir
    state.mkdir()
    if request.config.getoption("--testkit-report", default=None) and count == 32:
        # Measured 305 files / 1.2 MiB for this finite sample. Keep all raw state,
        # with explicit per-case headroom, not a global budget relaxation.
        evidence = request.getfixturevalue("testkit_case_evidence")
        evidence.max_files = 512
    attach_runtime_case_evidence(
        request, state,
        given="finite mock-reader batch through real Orchestrator fanout dispatch",
        when="timeout/retry exhaustion" if timeout else "reverse results, duplicate delivery, mid-run rebuild",
        then="bounded dispatches and one aggregate with correct child identities",
        forbidden=["early synth", "duplicate dispatch", "terminal state regression"],
        inputs={"children": readers, "max_retries": stage.aggregate.max_retries,
                "clock": timeout or "wall clock", "transport": "recording only",
                "max_evidence_files": 512 if count == 32 else 128},
    )
    transport = _RecordingTransport()
    runtime = Orchestrator(state, config, transport, project_root=root)
    trigger = runtime.event_writer.append(ZfEvent(
        type="candidate.ready", actor="zf-cli", correlation_id="trace-capacity",
        payload={"pdd_id": "F-CAPACITY"},
    ))
    runtime.run_once(events=[trigger])
    started = [e for e in runtime.event_log.read_all() if e.type == "fanout.started"]
    assert len(started) == 1
    fanout = started[0].payload["fanout_id"]
    dispatches = _events(runtime.event_log, fanout, "fanout.child.dispatched")
    assert Counter(e.payload["child_id"] for e in dispatches) == Counter(readers)
    assert Counter(row[0] for row in transport.sent) == Counter(readers)
    return state, runtime, transport, fanout, readers, dispatches


def _events(log: EventLog, fanout: str, kind: str) -> list[ZfEvent]:
    return [e for e in log.read_all() if e.type == kind and e.payload.get("fanout_id") == fanout]


def _manifest(state: Path, fanout: str) -> dict:
    return json.loads((state / "fanouts" / fanout / "manifest.json").read_text())


@pytest.mark.parametrize("count", [2, 32])
def test_reader_capacity_waits_for_every_child_across_rebuild(
    tmp_path: Path, request, count: int,
) -> None:
    state, runtime, transport, fanout, readers, dispatches = _start(tmp_path, request, count)
    log = runtime.event_log
    completed: set[str] = set()
    for index, dispatch in enumerate(reversed(dispatches)):
        child_id = dispatch.payload["child_id"]
        result = runtime.event_writer.append(ZfEvent(
            type="workflow.child.completed", actor=child_id, origin="worker",
            correlation_id="trace-capacity",
            payload={"fanout_id": fanout, "child_id": child_id,
                     "run_id": dispatch.payload["run_id"], "status": "completed",
                     "summary": f"mock observation for {child_id}"},
        ))
        runtime.run_once(events=[result])
        runtime.run_once(events=[result])
        completed.add(child_id)
        if index + 1 == count // 2:
            runtime = Orchestrator(state, runtime.config, transport, project_root=tmp_path)
            runtime.run_once(events=[])
        manifest = _manifest(state, fanout)
        assert {c["child_id"] for c in manifest["children"] if c["status"] == "completed"} == completed
        assert not _events(log, fanout, "fanout.aggregate.completed")
        assert len(_events(log, fanout, "fanout.synth.dispatched")) == int(len(completed) == count)
        assert [e.id for e in _events(log, fanout, "fanout.child.dispatched")] == [e.id for e in dispatches]

    assert Counter(e.payload["child_id"] for e in _events(log, fanout, "fanout.child.completed")) == Counter(readers)
    synth = runtime.event_writer.append(ZfEvent(
        type="fanout.synth.completed", actor="review-synth", origin="worker",
        correlation_id="trace-capacity",
        payload={"fanout_id": fanout, "recommendation": "approve", "summary": "mock aggregate"},
    ))
    runtime.run_once(events=[synth])
    runtime.run_once(events=[synth])
    terminal = _manifest(state, fanout)
    aggregates = _events(log, fanout, "fanout.aggregate.completed")
    assert len(aggregates) == 1 and aggregates[0].payload["status"] == "completed"
    assert len(_events(log, fanout, "review.approved")) == 1
    for _ in range(2):
        runtime = Orchestrator(state, runtime.config, transport, project_root=tmp_path)
        runtime.run_once(events=[])
        assert _manifest(state, fanout) == terminal
        assert _events(log, fanout, "fanout.aggregate.completed") == aggregates
        assert not runtime._has_nonterminal_reader_fanouts()
    assert Counter(row[0] for row in transport.sent) == Counter([*readers, "review-synth"])
    assert_no_duplicate_event_ids(log)


@pytest.mark.parametrize("clock", ["injected", "natural"])
def test_timeout_batch_exhaustion_does_not_regrow_recovery_work(
    tmp_path: Path, request, clock: str,
) -> None:
    state, runtime, transport, fanout, readers, first = _start(tmp_path, request, 8, timeout=clock)
    log = runtime.event_log
    # Recording providers do not emit idle observations. Use the real state
    # setter for this Given; never replace retry/dispatch/terminal logic.
    for role in readers:
        runtime._set_worker_state(role, "idle", force=True)
    if clock == "injected":
        runtime._now = lambda: time.time() + 4000
    else:
        deadline = max(datetime.fromisoformat(e.ts).timestamp() for e in first) + 3
        time.sleep(max(0, deadline - time.time()) + 0.02)
    runtime.run_once(events=[])
    dispatches = _events(log, fanout, "fanout.child.dispatched")
    assert Counter(e.payload["child_id"] for e in dispatches) == Counter({r: 2 for r in readers})
    for initial in first:
        history = [e for e in dispatches if e.payload["child_id"] == initial.payload["child_id"]]
        assert history[1].payload["retry_of_run_id"] == initial.payload["run_id"]
        assert history[1].payload["run_id"] != initial.payload["run_id"]
    assert not _events(log, fanout, "fanout.timed_out")
    if clock == "natural":
        deadline = max(datetime.fromisoformat(e.ts).timestamp() for e in dispatches) + 3
        time.sleep(max(0, deadline - time.time()) + 0.02)
    runtime.run_once(events=[])
    failures = _events(log, fanout, "fanout.child.failed")
    assert Counter(e.payload["child_id"] for e in failures) == Counter(readers)
    timeouts = _events(log, fanout, "fanout.timed_out")
    assert len(timeouts) == 1 and set(timeouts[0].payload["pending_children"]) == set(readers)
    aggregates = _events(log, fanout, "fanout.aggregate.completed")
    assert len(aggregates) == 1 and aggregates[0].payload["status"] == "failed"
    assert not _events(log, fanout, "review.approved")
    terminal = _manifest(state, fanout)
    for _ in range(3):
        runtime = Orchestrator(state, runtime.config, transport, project_root=tmp_path)
        if clock == "injected":
            runtime._now = lambda: time.time() + 8000
        runtime.run_once(events=[])
        runtime.run_once(events=[])
        assert _manifest(state, fanout) == terminal
        assert _events(log, fanout, "fanout.child.dispatched") == dispatches
        assert _events(log, fanout, "fanout.child.failed") == failures
        assert _events(log, fanout, "fanout.timed_out") == timeouts
        assert _events(log, fanout, "fanout.aggregate.completed") == aggregates
        assert not runtime._has_nonterminal_reader_fanouts()
    assert Counter(row[0] for row in transport.sent) == Counter({r: 2 for r in readers})
    assert_no_duplicate_event_ids(log)
