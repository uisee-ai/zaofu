"""Generated reader-retry interleaving model.

Two reconstructed Orchestrator instances interleave timeout sweeps, replay
delivery and service reopen actions against one ledger.  The oracle derives
the retry budget solely from the generated action sequence; it does not use
the production dispatch count to decide the expected result.
"""

from __future__ import annotations

import time
from pathlib import Path

from hypothesis import given, settings, strategies as st

from tests.conformance.test_zaofu_testkit_s11_fanout_model import FanoutMachine
from tests.test_reader_fanout_runtime import _RecordingTransport
from tests.testkit.model_evidence import ModelEvidence
from zf.runtime.orchestrator import Orchestrator


_MODEL_SETTINGS = settings(
    max_examples=8,
    deadline=None,
    database=None,
    derandomize=True,
)


@st.composite
def _interleaved_retry_sequences(draw: st.DrawFn) -> tuple[int, list[tuple[str, int]]]:
    return (
        draw(st.integers(min_value=0, max_value=2)),
        draw(st.lists(
            st.tuples(
                st.sampled_from(["sweep", "reopen", "replay"]),
                st.integers(min_value=0, max_value=1),
            ),
            min_size=1,
            max_size=7,
        )),
    )


def _sweep(runtime: Orchestrator) -> None:
    for role in runtime.config.workflow.stages[0].roles:
        runtime._set_worker_state(role, "idle", force=True)
    future = time.time() + 10
    runtime._now = lambda future=future: future  # type: ignore[method-assign]
    runtime.run_once(events=[])


@_MODEL_SETTINGS
@given(_interleaved_retry_sequences())
def test_generated_reader_retry_interleavings_keep_one_budget_and_lineage(
    generated: tuple[int, list[tuple[str, int]]],
) -> None:
    max_retries, actions = generated
    model = FanoutMachine(max_retries=max_retries)
    try:
        runtimes = [
            model.runtime,
            Orchestrator(
                model.state,
                model.config,
                _RecordingTransport(),
                project_root=model.root,
            ),
        ]
        row = model.rounds[0]
        sweep_count = 0
        evidence = ModelEvidence(
            root=model.root,
            model_name="ReaderRetryInterleavingMachine",
            source=Path(__file__),
            settings=str(_MODEL_SETTINGS),
            reference=lambda: {
                "max_retries": max_retries,
                "actions": actions,
                "sweeps": sweep_count,
            },
        )
        for action, actor_index in actions:
            actor_index %= len(runtimes)
            with evidence.guard(
                "reader_retry_interleaving",
                {
                    "max_retries": max_retries,
                    "action": action,
                    "actor_index": actor_index,
                    "sweeps": sweep_count,
                },
            ):
                runtime = runtimes[actor_index]
                if action == "reopen":
                    runtimes[actor_index] = Orchestrator(
                        model.state,
                        model.config,
                        _RecordingTransport(),
                        project_root=model.root,
                    )
                elif action == "replay":
                    events = runtime.event_log.read_all()
                    assert events
                    runtime.run_once(events=[events[-1]])
                else:
                    sweep_count += 1
                    _sweep(runtime)

                expected_dispatches = 1 + min(sweep_count, max_retries)
                events = model.runtime.event_log.read_all()
                for role in model.roles:
                    dispatches = [
                        event for event in events
                        if event.type == "fanout.child.dispatched"
                        and event.payload.get("fanout_id") == row["fanout_id"]
                        and event.payload.get("child_id") == role
                    ]
                    assert len(dispatches) == expected_dispatches
                    assert len({event.payload.get("run_id") for event in dispatches}) == len(dispatches)
                    for previous, current in zip(dispatches, dispatches[1:]):
                        assert current.payload.get("retry_of_run_id") == previous.payload.get("run_id")

                timed_out = [
                    event for event in events
                    if event.type == "fanout.timed_out"
                    and event.payload.get("fanout_id") == row["fanout_id"]
                ]
                assert len(timed_out) == int(sweep_count > max_retries)
                failed = [
                    event for event in events
                    if event.type == "fanout.child.failed"
                    and event.payload.get("fanout_id") == row["fanout_id"]
                ]
                assert len(failed) == len(model.roles) * int(sweep_count > max_retries)
    finally:
        model.teardown()
