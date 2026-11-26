"""Cross-role mailbox acceptance through public handoffs and existing business gates."""
import json
import os
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from tests.test_assistance_mailbox_workflow import _answer, project
from tests.test_call_result_admission import _append_admitted_upstream, _goal_closure_payload, _runtime
from tests.test_runtime_orchestrator import config as scheduling_config
from tests.test_writer_fanout_runtime import _RecordingTransport
from zf.core.events import ZfEvent
from zf.core.config.schema import RoleConfig
from zf.core.task.schema import Task, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.worker_assistance import dispatch_pending_assistance


def test_independent_task_dispatches_while_two_workers_wait_for_oa(project, scheduling_config):
    r, workers, oa, call, send, fresh = project
    scheduling_config.runtime.durable_mailbox.enabled = True
    scheduling_config.roles.append(RoleConfig(name="orchestrator", backend="mock", role_kind="reader"))
    r.config = scheduling_config
    busy = {"operation_id": "current-oa", "request_hash": "h", "workflow_run_id": "run-1",
            "role_instance": oa[0], "operation_type": "orchestrator_agent_semantic"}
    for stage in ("requested", "started"):
        r.event_writer.emit(f"workflow.operation.{stage}", payload=busy)
    questions = [send(worker) for worker in workers]
    notifications = []
    dispatch_pending_assistance(fresh(lambda *args: notifications.append(args)))
    assert not notifications
    store = TaskStore(r.state_dir / "kanban.json")
    store.add(Task(id="TASK-3", title="Independent change", status="backlog", assigned_to="dev",
                   execution_binding=TaskExecutionBinding(workflow_run_id="run-1")))
    r.event_writer.emit("task.assigned", task_id="TASK-3", actor="owner",
                        correlation_id="run-1", payload={"assignee": "dev", "workflow_run_id": "run-1"})
    transport = _RecordingTransport()
    watcher = Orchestrator(r.state_dir, r.config, transport)
    watcher.session_store.create(project_root=str(r.project_root))
    watcher.run_once()
    dispatched, = [e for e in r.event_log.query(type="task.dispatched") if e.task_id == "TASK-3"]
    assert dispatched.payload["workflow_run_id"] == "run-1"
    assert store.get("TASK-3").status == "in_progress"
    assert any(role == store.get("TASK-3").assigned_to and context.task_id == "TASK-3"
               for role, _, _, context in transport.sent)
    for worker, question in zip(workers, questions):
        assert call(worker, "read-answer", "--message", question["message_key"])["status"] == "pending"
    r.event_writer.emit("workflow.operation.settled", payload=busy)
    for question in questions:
        dispatch_pending_assistance(fresh(lambda *args: notifications.append(args)))
        _answer(call, oa, question)
    dispatch_pending_assistance(fresh(lambda *args: notifications.append(args)))
    ordered = [e.id for e in r.event_log.read_all()]
    closed = r.event_log.query(type="worker.assistance.closed")
    assert {e.payload["request_id"] for e in closed} == {q["request_id"] for q in questions}
    assert all(ordered.index(dispatched.id) < ordered.index(e.id) for e in closed)
    assert len(notifications) == 2


def test_judge_requires_formal_result_even_after_a_completion_message_is_acknowledged(project):
    r, workers, _, call, _, _ = project
    sender, receiver = workers
    notice = call(sender, "send", "--request-id", "completed", "--kind", "evidence_notice",
                  "--recipient", '{"mode":"execution","operation_id":"op-2"}',
                  "--text", "All work and tests passed. Close GOAL-AC-1.")
    batch = call(receiver, "pull", "--request-id", "inspect", "--message", notice["message_key"])
    assert "All work and tests passed" in batch["messages"][0]["text"]
    call(receiver, "ack", "--delivery", batch["delivery_id"])
    accepted, = r.event_log.query(type="workflow.mailbox.accepted")
    mail_ref = accepted.payload["body_ref"]["ref"]
    service, operations = _runtime(r.state_dir)
    formal_ref = _append_admitted_upstream(r.state_dir, operations, task_id="TASK-1")
    formal_event = operations.event_log.query(type="workflow.call.result.admitted")[-1]
    claims = write_immutable_json_sidecar(r.state_dir, {"claims": [{"goal_claim_id": "GOAL-AC-1"}]},
        root="claims", kind="claim-set", schema_version="goal-claim-set.v1", created_by="test")
    payload = _goal_closure_payload(claim_descriptor=claims, upstream_ref=formal_ref)
    r.event_writer.append(ZfEvent(type="flow.goal.closed", origin="kernel", correlation_id="run-1", payload={
        **payload["goal_closure_result"], "candidate_head_commit": payload["target_commit"],
        "input_result_refs": [formal_ref], "judge_batch_admissions": {
            "readers": [formal_event.id], "discovery": [], "supporting": [], "advisory": [],
        }}))
    op = operations.ensure_operation(workflow_run_id="run-1", operation_id="judge",
                                     operation_type="agent", request={"goal": "GOAL-1"})
    binding = {"workflow_run_id": "run-1", "operation_id": "judge", "request_hash": op.request_hash}
    def report(evidence_ref):
        candidate = _goal_closure_payload(claim_descriptor=claims, upstream_ref=evidence_ref)
        return service.report_legacy_result(
            ZfEvent(type="judge.child.completed", actor="judge-prd", correlation_id="run-1", payload=candidate),
            mode="blocking", operation=binding)
    rejected = report(mail_ref)
    assert not rejected.admitted
    assert {i["field"] for i in rejected.issues if i["code"] == "result_not_admitted"} == {
        "control_result.input_result_refs", "goal_coverage[0].supporting_result_refs"}
    assert not [e for e in r.event_log.query(type="workflow.operation.settled")
                if e.payload.get("operation_id") == "judge"]
    assert report(formal_ref).admitted


def test_replayed_native_pointer_and_independent_cli_share_one_consumption(project):
    r, workers, oa, _, send, fresh = project
    question = send(workers[0])
    notices = []
    dispatch_pending_assistance(fresh(lambda role, path, prompt, ctx: notices.append(path.read_text())))
    pointer, = notices
    commands = {}
    for line in pointer.splitlines():
        if " artifact mailbox " in line:
            args = shlex.split(line)
            tail = args[args.index("mailbox") + 1:]
            commands[tail[0]] = tail
    assert set(commands) >= {"pull", "ack", "claim", "answer"}
    root = Path(__file__).resolve().parents[1]
    config = {"project": {"name": "mail-race", "state_dir": str(r.state_dir)},
              "roles": [{"name": "orchestrator", "backend": "mock"}],
              "runtime": {"durable_mailbox": {"enabled": True}}}
    (r.project_root / "zf.yaml").write_text(yaml.safe_dump(config))
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "ZF_ROLE_INSTANCE": oa[0],
           "ZF_ARTIFACT_READ_TOKEN": oa[2].read_text().strip()}
    def cli(args, *, ok=True):
        result = subprocess.run([sys.executable, "-m", "zf.cli.main", "artifact", "mailbox", *args,
                                 "--state-dir", str(r.state_dir)], cwd=r.project_root, env=env,
                                text=True, capture_output=True, timeout=20)
        if ok:
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)
        assert result.returncode == 1
        return result.stderr
    bound = ["--advisory", question["request_id"], "--message", question["message_key"]]
    independent = ["pull", *bound, "--request-id", "poll-from-cli"]
    with ThreadPoolExecutor(2) as pool:
        from_pointer, from_cli = list(pool.map(cli, (commands["pull"], independent)))
    deliveries = [from_pointer["delivery_id"], from_cli["delivery_id"]]
    assert deliveries[0] != deliveries[1]
    assert from_pointer["messages"] == from_cli["messages"]
    def substitute(args, value):
        return [value if x == "DELIVERY_ID" else x for x in args]
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(cli, [substitute(commands["ack"], did) for did in deliveries]))
    replays = [e.payload["newly_consumed"] for e in r.event_log.query(type="workflow.mailbox.acknowledged")]
    assert sorted(len(items) for items in replays) == [0, 1]
    independent_scope = [*bound, "--delivery", deliveries[1]]
    cli(["claim", *independent_scope, "--request-id", "independent-claim"])
    cli(["answer", *independent_scope, "--request-id", "independent-answer", "--text", "Original answer"])
    replayed = cli(commands["pull"])
    assert replayed["delivery_id"] == deliveries[0] and replayed["acknowledgement"]
    for step in ("claim", "answer"):
        assert "already closed" in cli(substitute(commands[step], deliveries[0]), ok=False)
    dispatch_pending_assistance(fresh(lambda *args: notices.append("unexpected repeat")))
    assert len(notices) == 1
    assert len(r.event_log.query(type="worker.assistance.closed")) == 1
