"""Dispatch with a scheduler lease and separately bound fanout artifact capability."""

import json
import shlex

from tests.test_scheduler_task_attempt import _runtime


def test_fanout_mailbox_uses_the_issued_operation_attempt(
    tmp_path, monkeypatch, capsys
):
    from zf.cli.main import build_parser
    from zf.runtime.artifact_read_capability import (
        bind_attempt_artifact_read_capability,
        dispatch_mailbox_attempt,
        provision_role_artifact_read_credential,
    )
    from zf.runtime.workflow_operation import WorkflowOperationService

    runtime, transport, state = _runtime(tmp_path)
    runtime.config.runtime.durable_mailbox.enabled = True
    dispatch = "run-plan-synthesis"
    op = "wop-plan-synthesis"
    role = runtime.config.roles[0]
    token = provision_role_artifact_read_credential(
        state, role.instance_id, role_name=role.name, provider="mock"
    )
    args = {"workflow_run_id": "RUN-1", "operation_id": op, "task_id": "TASK-1"}
    service = WorkflowOperationService(
        state_dir=state, event_log=runtime.event_log, event_writer=runtime.event_writer
    )
    requested = service.ensure_operation(
        **args, operation_type="workflow_call", request={"stage": "plan-synth"}
    )
    manifest = {
        "workflow_run_id": "RUN-1",
        "task_id": "TASK-1",
        "attempt_id": dispatch,
        "dispatch_id": dispatch,
    }
    folder = state / "artifacts" / "attempts" / dispatch / "source-manifests"
    folder.mkdir(parents=True)
    (folder / "input.json").write_text(json.dumps(manifest))
    bind_attempt_artifact_read_capability(
        state,
        operation_id=op,
        attempt_id=dispatch,
        role_instance=role.instance_id,
        manifest=manifest,
    )
    briefing = state / "synth.md"
    briefing.write_text("# Fanout synthesis\n")
    context = runtime._send_scoped_transport_task(
        role,
        briefing,
        "read",
        trace_id="RUN-1",
        dispatch_id=dispatch,
        identity_sources=(
            {"task_id": "TASK-1", "workflow_run_id": "RUN-1", "operation_id": op},
        ),
    )
    service.mark_started(
        **args,
        request_hash=requested.request_hash,
        dispatch_id=dispatch,
        role_instance=role.instance_id,
        active_attempt_id=dispatch,
        lease_id=dispatch,
    )
    assert context.attempt_id != dispatch
    assert (
        context.attempt_id in transport.observed_briefing
    )  # valid scheduler heartbeat identity
    words = transport.observed_briefing.split("mailbox inbox --attempt ", 1)[1].split()
    assert words[0] == dispatch
    assert "recover assistance read" not in transport.observed_briefing
    monkeypatch.setenv("ZF_ROLE_INSTANCE", role.instance_id)
    monkeypatch.setenv("ZF_ARTIFACT_READ_TOKEN", token.read_text().strip())
    monkeypatch.setattr(
        "zf.cli.artifact_mailbox.resolve_project_context", lambda **_: runtime
    )
    monkeypatch.setattr(
        "zf.cli.artifact_mailbox.event_log_from_project",
        lambda *a, **kw: runtime.event_log,
    )
    inbox = transport.observed_briefing.split("交接收件箱 `", 1)[1].split("`", 1)[0]
    args = build_parser().parse_args(shlex.split(inbox)[1:])
    assert args.func(args) == 0, capsys.readouterr().err
    assert json.loads(capsys.readouterr().out)["items"] == []
    row = dict(
        task_id="TASK-1",
        run_id="RUN-1",
        instance_id=role.instance_id,
        operation_id=op,
        dispatch_id=dispatch,
        attempt_id=context.attempt_id,
    )
    for field in ("run_id", "instance_id", "operation_id", "task_id"):
        assert (
            dispatch_mailbox_attempt(state, {**row, field: "different"})
            == context.attempt_id
        )
