"""Research failures must traverse real replan admission before reaching its cap."""

from copy import deepcopy
from pathlib import Path

import pytest

from tests.conformance.test_zaofu_testkit_s8_research import (
    isolated_research as isolated_research,
    ResearchScenario,
)
from tests.testkit.model_evidence import ModelEvidence
from zf.core.events.model import ZfEvent
from zf.runtime.research_generation import research_generation_binding_error


class ResearchReplanIdentityDrift(AssertionError):
    """Kernel rewrote digest-bound input without renewing Research generation."""


def fail_current_stage(scenario: ResearchScenario) -> ZfEvent:
    children = [event for event in scenario.events("fanout.child.dispatched")
                if event.payload["fanout_id"] == scenario.fanout_id]
    assert len(children) == (1 if scenario.adaptive else 4)
    for dispatch in children:
        scenario.orch.run_once(events=[scenario.report(dispatch, failed=True)])
    if not scenario.adaptive:
        [synth] = [event for event in scenario.events("fanout.synth.dispatched")
                   if event.payload["fanout_id"] == scenario.fanout_id]
        scenario.orch.run_once(events=[scenario.report(synth, synth=True, failed=True)])
    [failure] = [event for event in scenario.events(f"{scenario.stage.replace('-', '.')}.failed")
                 if event.payload["fanout_id"] == scenario.fanout_id]
    assert failure.origin == "kernel"
    return failure


@pytest.mark.parametrize("adaptive", [False, True], ids=["fixed", "adaptive"])
@pytest.mark.parametrize("reopen", [False, True], ids=["same-process", "reconstructed"])
@pytest.mark.xfail(
    strict=True, raises=ResearchReplanIdentityDrift,
    reason="ZF-TEST-044: Kernel Research replan changes digest-bound reason without new generation",
)
def test_research_failures_retry_with_valid_identity_then_stop_at_cap(
    isolated_research: Path, adaptive: bool, reopen: bool,
) -> None:
    observation = {}
    evidence = ModelEvidence(
        root=isolated_research, model_name="research-replan-cap", source=Path(__file__),
        reference=lambda: {"retry_limit": 2, "observed": observation},
        settings="three failed stages; real controlled admission; no Provider turns",
        max_files=512,
    )
    with evidence.guard("fail-replan-cap", {"adaptive": adaptive, "reopen": reopen}):
        scenario = ResearchScenario(isolated_research, adaptive)
        scenario.start()
        for attempt in range(3):
            failure = fail_current_stage(scenario)
            scenario.assert_not_delivered()
            if reopen and attempt == 0:
                scenario.reopen()
            scenario.orch.run_once(events=[failure])
            replans = [event for event in scenario.events("workflow.invoke.requested")
                       if event.causation_id == failure.id]
            if attempt == 2:
                assert not replans
                assert scenario.events("human.escalate")
                assert scenario.events("run.goal.blocked")
                assert scenario.store.get("TASK-RESEARCH").status == "blocked"
                break
            [replan] = replans
            assert replan.origin == "kernel"
            assert replan.payload["rework_attempt"] == attempt + 1
            assert replan.payload["request_kind"] == "research"
            before = len(scenario.events("fanout.started"))
            scenario.orch.run_once(events=[replan])
            rejected = [event for event in scenario.events("workflow.invoke.rejected")
                        if event.payload.get("source_event_id") == replan.id]
            observation.update(
                attempt=attempt, old_reason=scenario.invoke.payload["reason"],
                replan_payload=replan.payload, failure_id=failure.id,
                rejection=[event.payload for event in rejected],
            )
            if (len(rejected) == 1
                    and rejected[0].payload.get("reason") == "research_generation_workflow_generation_stale"
                    and len(scenario.events("fanout.started")) == before
                    and scenario.store.get("TASK-RESEARCH").status == "in_progress"):
                # Diagnostic only: never execute this edited request or mutate its ledger entry.
                restored = {**replan.payload, "reason": scenario.invoke.payload["reason"]}
                diagnostic = research_generation_binding_error(
                    scenario.state, config=scenario.config,
                    task=scenario.store.get("TASK-RESEARCH"), payload=restored,
                )
                observation["restored_reason_binding_error"] = diagnostic
                assert diagnostic == ""
                for _ in range(3):
                    scenario.orch.run_once(events=[])
                assert len(scenario.events("fanout.started")) == before
                assert not scenario.events("run.goal.blocked")
                assert not scenario.events("human.escalate")
                scenario.assert_not_delivered()
                raise ResearchReplanIdentityDrift(
                    "real Kernel replan rejected by Research generation gate; failed Task remains active"
                )
            assert not rejected
            assert len(scenario.events("fanout.started")) == before + 1
            scenario.fanout_id = scenario.events("fanout.started")[-1].payload["fanout_id"]
        assert len(scenario.events("fanout.started")) == 3
        assert not scenario.events("workflow.result.available")
        assert not scenario.events("task.done.evidence")


@pytest.mark.parametrize("adaptive", [False, True], ids=["fixed", "adaptive"])
def test_research_reason_tampering_remains_fail_closed(
    isolated_research: Path, adaptive: bool,
) -> None:
    evidence = ModelEvidence(
        root=isolated_research, model_name="research-replan-cap", source=Path(__file__),
        reference=lambda: {"expected": "tampered invoke rejected, original fanout unchanged"},
        max_files=256,
    )
    with evidence.guard("tampered-reason", {"adaptive": adaptive}):
        scenario = ResearchScenario(isolated_research, adaptive)
        scenario.start()
        before = scenario.business_snapshot()
        dispatch_count = len(scenario.transport.sent)
        payload = deepcopy(scenario.invoke.payload)
        payload["reason"] = "untrusted replacement reason"
        event = scenario.writer.append(ZfEvent(
            type="workflow.invoke.requested", actor="web", origin="external",
            task_id="TASK-RESEARCH", payload=payload,
            correlation_id=scenario.invoke.correlation_id,
        ))
        scenario.orch.run_once(events=[event])
        [rejected] = scenario.events("workflow.invoke.rejected")
        assert rejected.payload["source_event_id"] == event.id
        assert rejected.payload["reason"] == "research_generation_workflow_generation_stale"
        assert scenario.business_snapshot() == before
        assert len(scenario.transport.sent) == dispatch_count
        assert len(scenario.events("fanout.started")) == 1


@pytest.mark.parametrize("adaptive", [False, True], ids=["fixed", "adaptive"])
def test_research_failure_replay_after_reconstruction_emits_one_replan(
    isolated_research: Path, adaptive: bool,
) -> None:
    evidence = ModelEvidence(
        root=isolated_research, model_name="research-replan-cap", source=Path(__file__),
        reference=lambda: {"expected_replans_for_original_failure": 1}, max_files=256,
    )
    with evidence.guard("duplicate-failure-wakeup", {"adaptive": adaptive}):
        scenario = ResearchScenario(isolated_research, adaptive)
        scenario.start()
        failure = fail_current_stage(scenario)
        scenario.orch.run_once(events=[failure])
        [replan] = [event for event in scenario.events("workflow.invoke.requested")
                   if event.causation_id == failure.id]
        scenario.orch.run_once(events=[failure])
        scenario.reopen()
        scenario.orch.run_once(events=[failure])
        replans = [event for event in scenario.events("workflow.invoke.requested")
                   if event.causation_id == failure.id]
        assert [event.id for event in replans] == [replan.id]
        assert replans[0].payload["rework_attempt"] == 1
        assert len(scenario.events("fanout.started")) == 1
        scenario.assert_not_delivered()
