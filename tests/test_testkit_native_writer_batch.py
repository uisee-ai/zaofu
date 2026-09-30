"""Offline caller closure before the opt-in real Claude multi-task batch."""

import json

import pytest
import yaml

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import _submit_generation_stage
from tests.conformance.test_zaofu_testkit_s3_plan_approval import planning_runtime_at
from tests.test_task_pipeline_generation import _git
from tests.testkit.native_writer_batch import batch_continuity_evidence, completed_task_evidence, writer_batch_items
from zf.cli.main import main
from zf.core.config.loader import load_config
from zf.core.config.render import renderable_config_to_primitive
from zf.core.events import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.workflow.inspection import build_workflow_inspection_report, inspection_failed
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import provision_role_submit_credential
from zf.runtime.run_contract import build_run_contract, write_run_contract
from zf.runtime.workflow_operation import interrupt_active_workflow_operations


def _continuity_events():
    def dispatch(task, session):
        return ZfEvent(type="task.pipeline.stage.dispatched", task_id=task, payload={
            "task_id": task, "role_instance": "impl-1", "provider_session_id": session,
            "task_stage_session_binding": f"binding-{session}",
        })

    return [
        dispatch("TASK-1", "first"),
        ZfEvent(type="task.done", task_id="TASK-1"),
        ZfEvent(type="role.lifecycle.suspended", task_id="TASK-1", payload={
            "instance_id": "impl-1", "reason": "task_stage_session_archived",
        }),
        ZfEvent(type="role.lifecycle.continuity.downgraded", task_id="TASK-2", payload={
            "instance_id": "impl-1", "reason": "provider_session_resume_not_proven",
            "previous_state": "suspended",
        }),
        dispatch("TASK-2", "second"),
        ZfEvent(type="workflow.operation.interrupted", task_id="TASK-2"),
        dispatch("TASK-2", "second"),
    ]


def test_batch_continuity_distinguishes_task_switch_from_recovery():
    events = _continuity_events()
    assert batch_continuity_evidence(events, events[4].id) == [events[3].id]
    # A runtime that distinguishes fresh task activation need not emit a downgrade.
    assert batch_continuity_evidence([e for i, e in enumerate(events) if i != 3], events[4].id) == []


@pytest.mark.parametrize("corruption", [
    "after-stop", "terminal-replay", "same-task", "same-session", "same-binding",
    "no-done", "no-archive", "wrong-role", "wrong-reason", "wrong-state", "wrong-task",
])
def test_batch_continuity_rejects_unproven_boundary_or_recovery_loss(corruption):
    events = _continuity_events()
    cutoff = events[4].id
    if corruption in {"after-stop", "terminal-replay"}:
        downgrade = events.pop(3)
        events.insert(5 if corruption == "after-stop" else len(events), downgrade)
    elif corruption == "same-task":
        events[0].payload["task_id"] = "TASK-2"
    elif corruption in {"same-session", "same-binding"}:
        key = "provider_session_id" if corruption == "same-session" else "task_stage_session_binding"
        events[4].payload[key] = events[0].payload[key]
    elif corruption in {"no-done", "no-archive"}:
        events.pop(1 if corruption == "no-done" else 2)
    elif corruption == "wrong-task":
        events[3].task_id = "TASK-1"
    else:
        key = {"wrong-role": "instance_id", "wrong-reason": "reason", "wrong-state": "previous_state"}[corruption]
        events[3].payload[key] = "unexpected"
    with pytest.raises(AssertionError):
        batch_continuity_evidence(events, cutoff)


@pytest.mark.parametrize("reopen", [False, True])
def test_blocking_writer_batch_resume_keeps_completed_task(tmp_path, monkeypatch, capsys, reopen):
    runtime, transport, trigger = planning_runtime_at(
        tmp_path / "project", monkeypatch, task_items=writer_batch_items("K546"),
    )
    project, state = runtime.project_root, runtime.state_dir
    (project / "zf.yaml").write_text(yaml.safe_dump(renderable_config_to_primitive(runtime.config)))
    # The old in-memory fixture had a minimal contract. Bind the actual loaded
    # config before admission, as full CLI startup does in the native test.
    write_run_contract(state, build_run_contract(
        load_config(project / "zf.yaml"), config_path=project / "zf.yaml",
        project_root=project, state_dir=state,
    ))
    inspection = build_workflow_inspection_report(
        load_config(project / "zf.yaml"), project_root=project, state_dir=state,
    )
    assert not inspection_failed(inspection), inspection["diagnostics"]
    monkeypatch.chdir(project)
    runtime.run_once()
    assert not transport.contexts
    assert runtime.task_store.get("TASK-1") is None
    assert main(["plan", "approve", trigger.id]) == 0
    tokens = {role: provision_role_submit_credential(state, role) for role in ("impl-1", "verify-1")}
    runtime.run_once()
    first = transport.contexts[-1]
    assert (first.task_id, first.task_pipeline_stage) == ("TASK-1", "impl")
    _submit_generation_stage(runtime, first, tokens["impl-1"], writes={"a.txt": "K546-1\n"})
    runtime.run_once()
    first_verify = transport.contexts[-1]
    assert (first_verify.task_id, first_verify.task_pipeline_stage) == ("TASK-1", "verify")
    _submit_generation_stage(runtime, first_verify, tokens["verify-1"])
    for _ in range(3):
        runtime.run_once()
    old = transport.contexts[-1]
    assert (old.task_id, old.task_pipeline_stage) == ("TASK-2", "impl")
    [second_dispatch] = [e for e in runtime.event_log.read_all()
                         if e.type == "task.pipeline.stage.dispatched"
                         and e.payload["attempt_id"] == old.attempt_id]
    assert _git(project, "show", f"{second_dispatch.payload['target_ref']}:a.txt") == "K546-1"
    preserved = completed_task_evidence(runtime, "TASK-1")
    assert not any(e.type == "candidate.ready" for e in runtime.event_log.read_all())
    interrupt_active_workflow_operations(
        state_dir=state, event_log=runtime.event_log,
        event_writer=runtime.event_writer, reason="fast_stop",
    )
    capsys.readouterr()
    assert main(["recover", "workflow", "--json"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    [checkpoint] = [c for c in inspected["projection"]["operation_checkpoints"]
                    if c["operation_id"] == old.operation_id]
    args = ["recover", "workflow", "--resume-pending", "--json", "--checkpoint-id", checkpoint["checkpoint_id"]]
    for extra, expected in ((["--dry-run"], 0), ([], 1), ([], 0)):
        before = (state / "events.jsonl").read_bytes()
        assert main([*args, *extra]) == 0
        assert json.loads(capsys.readouterr().out)["applied"] == expected
        if not expected:
            assert (state / "events.jsonl").read_bytes() == before
    if reopen:
        runtime = Orchestrator(state, runtime.config, transport, project_root=project)
    runtime.run_once()
    replacement = transport.contexts[-1]
    assert replacement.operation_id == old.operation_id
    assert replacement.attempt_id != old.attempt_id
    _submit_generation_stage(runtime, replacement, tokens["impl-1"], writes={"b.txt": "K546-2\n"})
    runtime.run_once()
    _submit_generation_stage(runtime, transport.contexts[-1], tokens["verify-1"])
    for _ in range(3):
        runtime.run_once()
    assert completed_task_evidence(runtime, "TASK-1") == preserved
    assert runtime.task_store.get("TASK-2").status == "done"
    assert TaskAttemptStore(state / "task_attempts.json").get(old.attempt_id)["status"] == "superseded"
    [ready] = [e for e in runtime.event_log.read_all() if e.type == "candidate.ready"]
    assert _git(project, "show", f"{ready.payload['commit']}:a.txt") == "K546-1"
    assert _git(project, "show", f"{ready.payload['commit']}:b.txt") == "K546-2"
    assert [(c.task_id, c.task_pipeline_stage) for c in transport.contexts] == [
        ("TASK-1", "impl"), ("TASK-1", "verify"), ("TASK-2", "impl"),
        ("TASK-2", "impl"), ("TASK-2", "verify"),
    ]
    before = [e.id for e in runtime.event_log.read_all() if e.type in {
        "candidate.ready", "workflow.operation.settled", "task.pipeline.stage.dispatched", "task.done",
    }]
    for _ in range(2):
        runtime = Orchestrator(state, runtime.config, transport, project_root=project)
        runtime.run_once()
    assert [e.id for e in runtime.event_log.read_all() if e.type in {
        "candidate.ready", "workflow.operation.settled", "task.pipeline.stage.dispatched", "task.done",
    }] == before
