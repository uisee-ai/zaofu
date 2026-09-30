"""Generated candidate-rework planning model.

This model exercises the deterministic candidate planner only.  It does not
dispatch a provider or claim Task Pipeline execution; the independent oracle
counts unique handled rejection identities by failure fingerprint.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from hypothesis import given, settings, strategies as st

from tests.testkit.model_evidence import ModelEvidence
from zf.core.events import ZfEvent
from zf.runtime.candidate_rework import plan_candidate_rework


_MODEL_SETTINGS = settings(
    max_examples=20,
    deadline=None,
    database=None,
    derandomize=True,
)


def _config() -> SimpleNamespace:
    return SimpleNamespace(goal=SimpleNamespace(rework_fingerprint=True))


def _failure(index: int, fingerprint: str) -> ZfEvent:
    return ZfEvent(
        type="review.rejected",
        id=f"reject-{index}",
        actor="review",
        correlation_id="trace-candidate-model",
        payload={
            "pdd_id": "PDD-CANDIDATE-MODEL",
            "trace_id": "trace-candidate-model",
            "target_ref": "candidate/PDD-CANDIDATE-MODEL",
            "reason": fingerprint,
            "findings": [{"severity": "high", "message": fingerprint}],
            "failed_task_ids": ["TASK-CANDIDATE-MODEL"],
        },
    )


def _marker(index: int, *, duplicate: bool = False) -> ZfEvent:
    suffix = "-duplicate" if duplicate else ""
    return ZfEvent(
        type="task_map.ready",
        id=f"ready-{index}{suffix}",
        actor="zf-cli",
        correlation_id="trace-candidate-model",
        payload={
            "pdd_id": "PDD-CANDIDATE-MODEL",
            "trace_id": "trace-candidate-model",
            "rework_of": f"reject-{index}",
            "rework_source": "review.rejected",
            "task_map_generation": f"generation-{index}",
        },
    )


@st.composite
def _generated_failures(draw: st.DrawFn) -> list[tuple[str, bool]]:
    return draw(
        st.lists(
            st.tuples(
                st.sampled_from(["same-defect", "new-defect"]),
                st.booleans(),
            ),
            min_size=1,
            max_size=5,
        )
    )


@_MODEL_SETTINGS
@given(_generated_failures())
def test_generated_candidate_fingerprint_budget_has_independent_oracle(
    generated: list[tuple[str, bool]],
) -> None:
    """Duplicate ready markers do not spend candidate fingerprint budget twice."""

    events: list[ZfEvent] = []
    handled: set[str] = set()
    fingerprints: dict[str, str] = {}
    for index, (fingerprint, duplicate_marker) in enumerate(generated):
        failure = _failure(index, fingerprint)
        events.append(failure)
        fingerprints[failure.id] = fingerprint
        # Every non-final rejection is handled, so the final one is the
        # planner's current source.  A duplicate marker has the same
        # rework_of identity and must not increment the independent count.
        if index < len(generated) - 1:
            events.append(_marker(index))
            handled.add(failure.id)
            if duplicate_marker:
                events.append(_marker(index, duplicate=True))

    with TemporaryDirectory(prefix="zf-candidate-rework-model-") as temporary:
        evidence = ModelEvidence(
            root=Path(temporary),
            model_name="CandidateReworkFingerprintMachine",
            source=Path(__file__),
            settings=str(_MODEL_SETTINGS),
            reference=lambda: {
                "generated": generated,
                "handled": sorted(handled),
                "fingerprints": fingerprints,
            },
        )
        with evidence.guard(
            "candidate_fingerprint_budget",
            {"generated": generated},
        ):
            plans = plan_candidate_rework(
                events,
                max_attempts=2,
                config=_config(),
            )

            current = events[-1]
            assert current.type == "review.rejected"
            current_fingerprint = fingerprints[current.id]
            independent_attempt = sum(
                fingerprints[event_id] == current_fingerprint
                for event_id in handled
            )
            assert len(plans) == 1
            plan = plans[0]
            assert plan.attempt == independent_attempt + 1
            assert plan.action == (
                "escalate" if independent_attempt >= 2 else "retrigger"
            )


def test_candidate_infrastructure_failure_has_no_quality_budget() -> None:
    """Provider/host failure is not converted into candidate rework budget."""

    events = [
        ZfEvent(
            type="fanout.started",
            id="fanout-infra",
            actor="zf-cli",
            payload={"fanout_id": "fanout-infra", "pdd_id": "PDD-INFRA"},
        ),
        ZfEvent(
            type="fanout.child.failed",
            id="child-infra",
            actor="zf-cli",
            correlation_id="trace-infra",
            payload={
                "fanout_id": "fanout-infra",
                "child_id": "verify-lane",
                "reason": "pane is not running an agent process",
                "trace_id": "trace-infra",
            },
        ),
        ZfEvent(
            type="verify.failed",
            id="verify-infra",
            actor="zf-cli",
            correlation_id="trace-infra",
            payload={
                "fanout_id": "fanout-infra",
                "pdd_id": "PDD-INFRA",
                "target_ref": "candidate/PDD-INFRA",
                "trace_id": "trace-infra",
            },
        ),
    ]
    assert plan_candidate_rework(events, max_attempts=2, config=_config()) == []
