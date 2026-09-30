"""Generated candidate-sweep replay identity model.

The model covers the deterministic replay selector.  The real
``Orchestrator._run_candidate_rework_sweep`` caller remains covered by the
focused writer-fanout integration tests; this model must stay cheap enough to
explore many old-generation/reopen permutations.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given, settings, strategies as st
import pytest

from tests.testkit.model_evidence import ModelEvidence
from zf.core.events.model import ZfEvent
from zf.runtime.candidate_rework_generation import (
    current_rework_task_map_replay_candidates,
)


_MODEL_SETTINGS = settings(
    max_examples=24,
    deadline=None,
    database=None,
    derandomize=True,
)


@st.composite
def _generated_ready_history(draw: st.DrawFn) -> list[tuple[str, str, str, bool]]:
    return draw(st.lists(
        st.tuples(
            st.sampled_from(["run-a", "run-b"]),
            st.sampled_from(["PDD-A", "PDD-B"]),
            st.sampled_from(["generation-a", "generation-b", "generation-c"]),
            st.booleans(),
        ),
        min_size=1,
        max_size=8,
    ))


def _ready(index: int, run_id: str, pdd_id: str, generation: str) -> ZfEvent:
    return ZfEvent(
        id=f"ready-{index}",
        type="task_map.ready",
        actor="zf-cli",
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "pdd_id": pdd_id,
            "task_map_generation": generation,
            "rework_of": f"reject-{index}",
        },
    )


@_MODEL_SETTINGS
@given(_generated_ready_history())
def test_generated_rework_replay_selects_one_latest_current_scope(
    generated: list[tuple[str, str, str, bool]],
) -> None:
    events: list[ZfEvent] = []
    superseded: set[str] = set()
    latest: dict[tuple[str, str], tuple[int, str, str]] = {}
    for index, (run_id, pdd_id, generation, amend_previous) in enumerate(generated):
        scope = (run_id, pdd_id)
        if amend_previous and scope in latest:
            superseded.add(latest[scope][1])
            events.append(ZfEvent(
                id=f"amended-{index}",
                type="task_map.amended",
                actor="zf-cli",
                payload={"supersedes_task_map_generation": latest[scope][1]},
            ))
        event = _ready(index, run_id, pdd_id, generation)
        events.append(event)
        latest[scope] = (index, generation, event.id)

    # The selector first excludes every superseded generation, then keeps the
    # latest remaining marker in each run/PDD scope.  A later marker reusing an
    # old generation is therefore also excluded (the generation identity is
    # the replay authority, not the event id).
    expected = set()
    for scope in latest:
        candidates = [
            (index, event_id)
            for (run_id, pdd_id, generation, _duplicate), index, event_id in [
                (row, idx, f"ready-{idx}")
                for idx, row in enumerate(generated)
            ]
            if (run_id, pdd_id) == scope and generation not in superseded
        ]
        if candidates:
            expected.add(max(candidates)[1])
    with TemporaryDirectory(prefix="zf-candidate-sweep-model-") as root:
        evidence = ModelEvidence(
            root=Path(root),
            model_name="CandidateSweepReplayMachine",
            source=Path(__file__),
            settings=str(_MODEL_SETTINGS),
            reference=lambda: {"generated": generated},
        )
        with evidence.guard("candidate_rework_replay", {"generated": generated}):
            selected = current_rework_task_map_replay_candidates(events)
            assert {event.id for event in selected} == expected
            assert len(selected) == len({
                (
                    str(event.payload.get("workflow_run_id") or event.correlation_id),
                    str(event.payload.get("pdd_id") or event.task_id),
                )
                for event in selected
            })


@pytest.mark.parametrize("actions", [
    ("sweep", "reopen", "sweep"),
    ("reopen", "replay", "sweep"),
    ("replay", "reopen", "replay"),
])
def test_real_candidate_sweep_reopen_keeps_one_recovery_dispatch(
    tmp_path: Path, actions: tuple[str, ...],
) -> None:
    """Persist one actual recovery, then redeliver without resetting its ledger."""
    from tests.test_writer_fanout_runtime import _state
    from zf.core.events.log import EventLog
    from zf.runtime.orchestrator import Orchestrator

    state_dir, log, transport, runtime = _state(tmp_path, synthesize_canonical=True)
    config = runtime.config
    log.append(ZfEvent(
        id="sweep-input-map",
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
            "source_commit": "abc123",
            "candidate_base_commit": "abc123",
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
        },
    ))
    failure = ZfEvent(
        id="sweep-rejected-map",
        type="fanout.child.failed",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={
            "fanout_id": "fanout-dev-fanout-old",
            "stage_id": "dev-fanout",
            "child_id": "dev-1-TASK-1",
            "task_id": "TASK-1",
            "pdd_id": "F-11111111",
            "trace_id": "trace-1",
            "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
            "reason": "stale_task_map",
        },
    )
    log.append(failure)
    runtime._run_candidate_rework_sweep()

    def receipts() -> tuple[str, str, str]:
        rows = EventLog(state_dir / "events.jsonl").read_all()
        ready = [e for e in rows if e.type == "task_map.ready"
                 and e.payload.get("rework_of") == failure.id]
        assert len(ready) == 1
        started = [e for e in rows if e.type == "fanout.started"
                   and e.payload.get("trigger_event_id") == ready[0].id]
        assert len(started) == 1
        dispatched = [e for e in rows if e.type == "fanout.child.dispatched"
                      and e.payload.get("fanout_id") == started[0].payload["fanout_id"]]
        assert len(dispatched) == 1
        assert dispatched[0].payload["task_id"] == "TASK-1"
        assert [entry[0] for entry in transport.sent] == ["dev-1"]
        applied = [e for e in rows if e.type == "run.manager.action.applied"
                   and e.payload.get("source_event_id") == failure.id]
        assert len(applied) == 1
        assert not [e for e in rows if e.type == "orchestrator.housekeeping.failed"]
        return ready[0].id, started[0].id, dispatched[0].id

    first = receipts()
    for action in actions:
        if action == "reopen":
            runtime = Orchestrator(state_dir, config, transport)
        elif action == "replay":
            # Redelivery is not a second occurrence appended to the ledger.
            runtime.run_once(events=[failure])
        else:
            runtime._run_candidate_rework_sweep()
        assert receipts() == first
