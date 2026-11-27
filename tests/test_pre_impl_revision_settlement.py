"""A settled old worker cannot revive authorization after a new package or Owner verdict."""
import pytest

from tests.test_orchestrator_agent_pre_impl_revision import _event, pre_impl
from tests.test_pre_impl_revision_owner_gate import pending_planner, submit_critic


def change_parent(runtime, ready, previous, package, change):
    if change == "R5":
        pre_impl._revised_candidate(runtime, prior_package=package, prior_ref=previous,
                                    revision="5", generation=change)
    else:
        _event(runtime, change, plan_id=ready.id, task_map_generation="R4",
               plan_artifact_package_digest=previous["sha256"], plan_artifact_package_ref=previous["ref"])


@pytest.mark.parametrize("phase", ["planner", "critic"])
@pytest.mark.parametrize("change", ["R5", "plan.approved", "plan.rejected"])
def test_inflight_result_cannot_publish_for_a_retired_parent(tmp_path, monkeypatch, phase, change):
    runtime, result, ready, previous, package, _ = pending_planner(tmp_path, monkeypatch)
    if phase == "critic":
        runtime.run_once(events=[result])
        result = submit_critic(runtime)
    change_parent(runtime, ready, previous, package, change)
    before = len(runtime.event_log.read_all())
    for _ in range(2):
        runtime.run_once(events=[result])
    fresh = runtime.event_log.read_all()[before:]
    assert not {e.type for e in fresh} & {
        "fanout.synth.dispatched", "plan.artifact_package.admitted", "task_map.ready", "plan.approval.requested"}
    cancellations = [e for e in fresh if e.type == "fanout.cancelled"]
    assert len(cancellations) == 1 and cancellations[0].payload["reason"].startswith("pre_impl_revision_")


@pytest.mark.parametrize("boundary", ["plan.artifact_package.admitted", "task_map.ready"])
def test_admission_and_ready_recheck_an_intervening_successor(tmp_path, monkeypatch, boundary):
    runtime, planner, ready, previous, package, _ = pending_planner(tmp_path, monkeypatch)
    runtime.run_once(events=[planner])
    critic = submit_critic(runtime)
    append = runtime.event_writer.append
    injected = []

    def interleave(event, **kwargs):
        if event.type == boundary and not injected:
            injected.append(event.id)
            change_parent(runtime, ready, previous, package, "R5")
        return append(event, **kwargs)

    monkeypatch.setattr(runtime.event_writer, "append", interleave)
    runtime.run_once(events=[critic])
    assert injected
    packages = runtime.event_log.query(type="plan.artifact_package.admitted")
    assert packages[-1].payload["task_map_generation"] == "R5"
    assert runtime.event_log.query(type="task_map.ready")[-1].id == ready.id
    assert not runtime.event_log.query(type="plan.approval.requested")


def test_pause_can_settle_a_running_critic_without_approving_or_dispatching(tmp_path, monkeypatch):
    runtime, planner, ready, *_ = pending_planner(tmp_path, monkeypatch)
    runtime.run_once(events=[planner])
    critic = submit_critic(runtime)
    _event(runtime, "run.admission.admitted")
    _event(runtime, "run.paused")
    runtime.run_once(events=[critic])
    revised = runtime.event_log.query(type="task_map.ready")[-1]
    assert revised.id != ready.id
    runtime.run_once(events=[revised])
    assert not runtime.event_log.query(type="plan.approved")
    assert not runtime.event_log.query(type="task.pipeline.stage.dispatched")
    assert not runtime.event_log.query(type="plan.approval.requested")


def test_normal_idle_wake_resumes_pending_planner_aggregate_once(tmp_path, monkeypatch):
    runtime, planner, _, _, _, transport = pending_planner(tmp_path, monkeypatch)
    _event(runtime, "run.admission.admitted")
    paused = _event(runtime, "run.paused")
    runtime.run_once(events=[paused, planner])
    assert not runtime.event_log.query(type="fanout.synth.dispatched")
    resumed = _event(runtime, "run.resumed")
    runtime.run_once(events=[resumed])
    for _ in range(2):
        runtime.run_once(events=[])
    dispatches = runtime.event_log.query(type="fanout.synth.dispatched")
    assert len(dispatches) == 1
    assert [r for r, *_ in transport.sent].count("plan-critic") == 1


def test_busy_ready_publication_resumes_on_idle_without_a_second_critic(tmp_path, monkeypatch):
    runtime, planner, old, *_ = pending_planner(tmp_path, monkeypatch)
    runtime.run_once(events=[planner])
    critic = submit_critic(runtime)
    append = runtime.event_writer.append
    attempts = []

    def contend(event, **kwargs):
        if event.type == "task_map.ready":
            attempts.append(event.id)
            if len(attempts) <= 3:
                _event(runtime, "worker.idle")
        return append(event, **kwargs)

    monkeypatch.setattr(runtime.event_writer, "append", contend)
    runtime.run_once(events=[critic])
    assert len(attempts) == 3 and len(set(attempts)) == 1
    assert [e.id for e in runtime.event_log.query(type="task_map.ready")] == [old.id]
    runtime.run_once(events=[])
    runtime.run_once(events=[])
    ready = runtime.event_log.query(type="task_map.ready")
    assert [e.id for e in ready] == [old.id, attempts[0]]
    assert len(runtime.event_log.query(type="fanout.synth.dispatched")) == 1


def test_pause_does_not_override_expired_parent(tmp_path, monkeypatch):
    runtime, planner, ready, previous, package, _ = pending_planner(tmp_path, monkeypatch)
    runtime.run_once(events=[planner])
    critic = submit_critic(runtime)
    _event(runtime, "run.admission.admitted")
    _event(runtime, "run.paused")
    change_parent(runtime, ready, previous, package, "R5")
    runtime.run_once(events=[critic])
    assert runtime.event_log.query(type="task_map.ready")[-1].id == ready.id
    assert runtime.event_log.query(type="plan.artifact_package.admitted")[-1].payload["task_map_generation"] == "R5"
