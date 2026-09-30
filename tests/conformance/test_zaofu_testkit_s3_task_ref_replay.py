"""Same-trigger TaskRef replay and downstream idempotence evidence."""

import json
from collections import Counter

import pytest

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import (
    _submit_generation_stage,
    blocking_runtime,  # noqa: F401 -- imported fixture
)
from tests.test_task_pipeline_generation import _git
from zf.core.config.schema import RoleConfig, RoleLifecycleConfig
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import provision_role_submit_credential
from zf.runtime.workflow_operation import reduce_workflow_operations


class SameTriggerTaskRefReplay(AssertionError):
    """The same durable dev.build.done trigger created another TaskRef event."""


@pytest.mark.parametrize(
    "delivery",
    [
        "same-instance",
        pytest.param(
            "reopened-push",
            marks=pytest.mark.xfail(
                strict=True,
                raises=SameTriggerTaskRefReplay,
                reason="ZF-TEST-064: same dev.build.done replay appends task.ref.updated again",
            ),
        ),
        "reopened-poll",
    ],
)
def test_dev_build_replay_preserves_downstream_effects(
    blocking_runtime, delivery: str, record_property,
) -> None:
    runtime, transport, _admission = blocking_runtime
    state, config, project = runtime.state_dir, runtime.config, runtime.project_root
    config.roles.append(RoleConfig(
        name="verify", instance_id="verify-1", backend="mock", role_kind="reader",
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
    ))
    tokens = {role: provision_role_submit_credential(state, role)
              for role in ("impl-1", "verify-1")}
    task = runtime.task_store.get("TASK-BLOCKING")
    assert task is not None
    task.contract.scope = ["README.md"]
    task.contract.acceptance_criteria = ["README exists at the pinned commit"]
    task.contract.verification = "git cat-file -e HEAD:README.md"
    task.contract.validation = {"commands": [{
        "id": "readme", "command": task.contract.verification,
        "rolling_smoke": True, "tier": "runtime", "deterministic": True, "reusable": True,
    }]}
    runtime.task_store.update(task.id, contract=task.contract)
    assert [decision.action for decision in runtime._dispatch_ready()] == ["task_pipeline_dispatch"]
    impl = transport.contexts[-1]
    trigger, _ = _submit_generation_stage(runtime, impl, tokens["impl-1"], repair=True)
    assert trigger.type == "dev.build.done"
    runtime._react_to_events()
    assert [decision.action for decision in runtime._dispatch_ready()] == ["task_pipeline_dispatch"]
    verify = transport.contexts[-1]
    assert verify.task_pipeline_stage == "verify"

    before_events = runtime.event_log.read_all()
    before_counts = Counter(event.type for event in before_events)
    before_index = json.loads((state / "refs" / "task-index.json").read_text())
    before_task = runtime.task_store.get(task.id)
    before_operations = reduce_workflow_operations(before_events)
    before_attempts = TaskAttemptStore(state / "task_attempts.json").current_rows()

    reopened = runtime if delivery == "same-instance" else Orchestrator(
        state, config, transport, project_root=project,
    )
    reopened._react_to_events(pushed=None if delivery == "reopened-poll" else [trigger])
    # Consume any newly emitted TaskRef event, then exercise its scheduling caller.
    reopened._react_to_events()
    assert reopened._dispatch_ready() == []
    after_events = reopened.event_log.read_all()
    after_counts = Counter(event.type for event in after_events)

    # Derived IDs are distinct: this is not an EventWriter duplicate-ID issue.
    # Check all downstream effects before the narrow repeated-projection detector.
    updated = [event for event in after_events if event.type == "task.ref.updated"]
    assert len({event.id for event in updated}) == len(updated)
    assert after_counts["dev.build.done"] == before_counts["dev.build.done"] == 1
    for event in updated:
        assert event.causation_id == trigger.id
        assert event.payload["trigger_event_id"] == trigger.id
        assert {k: v for k, v in event.payload.items() if k != "created_at"} == {
            k: v for k, v in updated[0].payload.items() if k != "created_at"
        }
    after_index = json.loads((state / "refs" / "task-index.json").read_text())
    record_property("task_ref_replay_observation", json.dumps({
        "delivery": delivery, "trigger_event_id": trigger.id,
        "updates_before": before_counts["task.ref.updated"],
        "updates_after": after_counts["task.ref.updated"],
        "index_created_at_before": before_index[task.id]["created_at"],
        "index_created_at_after": after_index[task.id]["created_at"],
    }, sort_keys=True))
    assert before_index.keys() == after_index.keys() == {task.id}
    assert before_counts["task.ref.updated"] == 1
    assert {k: v for k, v in before_index[task.id].items() if k != "created_at"} == {
        k: v for k, v in after_index[task.id].items() if k != "created_at"
    }
    assert _git(project, "rev-parse", f"refs/heads/{updated[0].payload['task_ref']}") == updated[0].payload["source_commit"]
    assert after_counts["task.pipeline.stage.dispatched"] == before_counts["task.pipeline.stage.dispatched"]
    assert after_counts["workflow.operation.requested"] == before_counts["workflow.operation.requested"]
    assert after_counts["workflow.operation.started"] == before_counts["workflow.operation.started"]
    assert after_counts["workflow.call.result.admitted"] == before_counts["workflow.call.result.admitted"]
    assert after_counts["workflow.operation.settled"] == before_counts["workflow.operation.settled"]
    assert after_counts["task.ref.rejected"] == before_counts["task.ref.rejected"]
    assert reopened.task_store.get(task.id) == before_task
    assert reduce_workflow_operations(after_events) == before_operations
    assert TaskAttemptStore(state / "task_attempts.json").current_rows() == before_attempts
    assert len(transport.contexts) == 2

    verified, body = _submit_generation_stage(reopened, verify, tokens["verify-1"])
    assert body["verdict"] == "passed"
    reopened._react_to_events()
    assert "task_pipeline_candidate_frozen" in [d.action for d in reopened._dispatch_ready()]
    final = reopened.event_log.read_all()
    for event_type in ("integration.queue.integrated", "task.done", "candidate.ready"):
        assert sum(event.type == event_type for event in final) == 1
    [ready] = [event for event in final if event.type == "candidate.ready"]
    assert _git(project, "show", f"{ready.payload['commit']}:README.md") == "K462 repaired"
    assert reopened.task_store.get(task.id).status == "done"

    # Terminal replay must not re-integrate, remint a candidate or resend work.
    terminal_operations = reduce_workflow_operations(final)
    terminal_attempts = TaskAttemptStore(state / "task_attempts.json").current_rows()
    terminal_task = reopened.task_store.get(task.id)
    reopened = Orchestrator(state, config, transport, project_root=project)
    reopened._react_to_events(pushed=[trigger, verified])
    reopened._react_to_events()
    assert reopened._dispatch_ready() == []
    replayed = reopened.event_log.read_all()
    for event_type in (
        "dev.build.done", "task.pipeline.verify.completed", "task.pipeline.stage.dispatched",
        "workflow.operation.requested", "workflow.operation.started",
        "workflow.call.result.admitted", "workflow.operation.settled",
        "integration.queue.integrated", "task.done", "candidate.ready",
    ):
        assert sum(e.type == event_type for e in replayed) == sum(e.type == event_type for e in final)
    assert reduce_workflow_operations(replayed) == terminal_operations
    assert TaskAttemptStore(state / "task_attempts.json").current_rows() == terminal_attempts
    assert reopened.task_store.get(task.id) == terminal_task
    assert len(transport.contexts) == 2
    if after_counts["task.ref.updated"] != before_counts["task.ref.updated"]:
        duplicate = updated[-1].payload
        raise SameTriggerTaskRefReplay(
            "same trigger_event_id produced another task.ref.updated: "
            f"trigger={duplicate.get('trigger_event_id')!r}, "
            f"occurrences={after_counts['task.ref.updated']}, "
            f"index_before={before_index!r}, index_after={after_index!r}"
        )
    assert after_index == before_index
