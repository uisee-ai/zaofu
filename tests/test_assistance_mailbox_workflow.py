"""The public CLI, role credentials, OA queue and periodic reconciliation share one question."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_task_pipeline_fault_matrix import _runtime
from zf.cli.main import build_parser
from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.artifact_read_capability import (
    bind_attempt_artifact_read_capability,
    provision_role_artifact_read_credential,
)
from zf.runtime.assistance_mailbox import NOTICE, notification_states, reserve_notice
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.worker_assistance import (
    dispatch_pending_assistance,
    reconcile_assistance,
)
from zf.runtime.workflow_operation import WorkflowOperationService


def _worker(runtime, number):
    state = runtime.state_dir
    actor, task, op = f"impl-{number}", f"TASK-{number}", f"op-{number}"
    now = datetime.now(timezone.utc)
    service = WorkflowOperationService(
        state_dir=state, event_log=runtime.event_log, event_writer=runtime.event_writer
    )
    request = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id=op,
        operation_type="task-stage",
        request={"task_pipeline_stage": "impl", "operation_generation": 1},
        task_id=task,
    )
    attempt = (
        TaskAttemptStore(state / "task_attempts.json")
        .ensure_for_dispatch(
            run_id="run-1",
            task_id=task,
            dispatch_id=f"dispatch-{number}",
            role="impl",
            instance_id=actor,
            operation_id=op,
            briefing_ref=f"briefings/{task}.md",
            created_at=now.isoformat(),
            lease_expires_at=(now + timedelta(hours=1)).isoformat(),
            max_attempts=3,
            identity_version="operation-v2",
            placement_epoch=1,
        )
        .attempt
    )
    aid = attempt["attempt_id"]
    service.mark_started(
        operation_id=op,
        request_hash=request.request_hash,
        workflow_run_id="run-1",
        task_id=task,
        dispatch_id=f"dispatch-{number}",
        role_instance=actor,
        active_attempt_id=aid,
        lease_id=attempt["lease_id"],
    )
    TaskStore(state / "kanban.json").add(
        Task(id=task, title="Sample", status="in_progress", assigned_to=actor)
    )
    token = provision_role_artifact_read_credential(
        state, actor, role_name="impl", provider="codex"
    )
    manifest = {
        "attempt_id": aid,
        "workflow_run_id": "run-1",
        "task_id": task,
        "read_purpose": "impl",
    }
    folder = state / "artifacts" / "attempts" / aid / "source-manifests"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "input.json").write_text(json.dumps(manifest))
    bind_attempt_artifact_read_capability(
        state, operation_id=op, attempt_id=aid, role_instance=actor, manifest=manifest
    )
    return actor, aid, token


@pytest.fixture
def project(tmp_path, monkeypatch, capsys):
    r = _runtime(tmp_path)
    r.config = load_config(Path(__file__).parents[1] / "zf-prd-v5.yaml")
    r.config.runtime.durable_mailbox.enabled = True
    workers = [_worker(r, n) for n in (1, 2)]
    oa = (
        "orchestrator",
        "",
        provision_role_artifact_read_credential(
            r.state_dir, "orchestrator", role_name="orchestrator", provider="codex"
        ),
    )
    monkeypatch.setattr(
        "zf.cli.artifact_mailbox.resolve_project_context", lambda **kw: r
    )
    monkeypatch.setattr(
        "zf.cli.artifact_mailbox.event_log_from_project", lambda *a, **kw: r.event_log
    )
    monkeypatch.setattr(
        "zf.runtime.orchestrator_agent_role.orchestrator_instance_id",
        lambda _: "orchestrator",
    )
    parser = build_parser()

    def call(worker, mode, *options, ok=True):
        actor, attempt, token = worker
        monkeypatch.setenv("ZF_ROLE_INSTANCE", actor)
        monkeypatch.setenv("ZF_ARTIFACT_READ_TOKEN", token.read_text().strip())
        monkeypatch.delenv("ZF_ROLE_NAME", raising=False)
        monkeypatch.delenv("ZF_ROLE_BACKEND", raising=False)
        args = parser.parse_args(
            [
                "artifact",
                "mailbox",
                mode,
                *(["--attempt", attempt] if attempt else []),
                *options,
            ]
        )
        status = args.func(args)
        streams = capsys.readouterr()
        if ok:
            assert status == 0, streams.err
            return json.loads(streams.out)
        assert status == 1, streams.out
        return streams.err

    def send(worker):
        return call(
            worker,
            "send",
            "--request-id",
            "help",
            "--kind",
            "question",
            "--recipient",
            '{"mode":"coordination","owner":"orchestrator"}',
            "--text",
            "Which approved sample applies?",
            "--evidence-ref",
            "requirement.md",
        )

    def fresh(transport):
        log = EventLog(r.state_dir / "events.jsonl")
        result = SimpleNamespace(
            state_dir=r.state_dir,
            config=r.config,
            event_log=log,
            event_writer=EventWriter(log),
        )
        result._add_layer2_pending = lambda _: None
        result._record_skill_provenance = lambda **kw: None
        result._dispatch_context = lambda **kw: {}
        result._send_transport_task = transport
        result._fire_orchestrator_turn = lambda role, events: (
            Orchestrator._fire_orchestrator_turn(result, role, events)
        )
        return result

    return r, workers, oa, call, send, fresh


def _dispatch(r):
    return [
        e
        for e in r.event_log.read_all()
        if e.type == "orchestrator.rework.triage.requested"
    ]


def _answer(call, oa, q):
    scope = ["--advisory", q["request_id"]]
    base = ["--message", q["message_key"]]
    batch = call(oa, "pull", *scope, *base, "--request-id", "pick")
    did = batch["delivery_id"]
    call(oa, "ack", *scope, "--delivery", did)
    claim = [*scope, *base, "--delivery", did]
    call(oa, "claim", *claim, "--request-id", "take")
    call(
        oa,
        "answer",
        *claim,
        "--request-id",
        "guidance",
        "--text",
        "Use the approved public sample.",
    )
    return did


def test_oa_busy_two_private_questions_crash_after_reservation_and_restart(project):
    r, workers, oa, call, send, fresh = project
    writes = []
    busy = {
        "operation_id": "existing-checkpoint",
        "request_hash": "h",
        "workflow_run_id": "run-1",
        "role_instance": oa[0],
        "operation_type": "orchestrator_agent_semantic",
    }
    for stage in ("requested", "started"):
        r.event_writer.emit("workflow.operation." + stage, payload=busy)
    one, two = (send(worker) for worker in workers)
    assert send(workers[0]) == one
    dispatch_pending_assistance(fresh(lambda *args: writes.append(args)))
    assert not writes and len(_dispatch(r)) == 2
    first = _dispatch(r)[0]
    r.event_writer.emit("workflow.operation.settled", payload=busy)
    assert reserve_notice(r, first)
    assert notification_states(r.event_log.read_all())[one["request_id"]] == "reserved"
    dispatch_pending_assistance(fresh(lambda *args: writes.append(args)))
    dispatch_pending_assistance(fresh(lambda *args: writes.append(args)))
    assert len(writes) == 1
    assert "artifact mailbox answer" in writes[0][1].read_text()
    assert call(workers[1], "read-answer", "--message", one["message_key"], ok=False)
    _answer(call, oa, one)
    dispatch_pending_assistance(fresh(lambda *args: writes.append(args)))
    assert len(writes) == 2
    _answer(call, oa, two)
    dispatch_pending_assistance(fresh(lambda *args: writes.append(args)))
    for worker, question in zip(workers, (one, two)):
        answer = call(worker, "read-answer", "--message", question["message_key"])
        assert "Use the approved public sample." in json.dumps(answer)
        assert answer["new_writer_authorized"] is False
        assert (
            TaskStore(r.state_dir / "kanban.json").get("TASK-" + worker[0][-1]).status
            == "in_progress"
        )
    assert (
        sum(e.type == "worker.assistance.closed" for e in r.event_log.read_all()) == 2
    )
    assert len(writes) == 2


@pytest.mark.parametrize(
    "lost", [RuntimeError, SystemExit], ids=["adapter-error", "process-died"]
)
def test_partial_transport_outcome_allows_controlled_pull_without_resending(
    project, lost
):
    r, workers, oa, call, send, fresh = project
    first, second = (send(worker) for worker in workers)
    writes = []

    def lost_receipt(*args):
        writes.append(args)
        raise lost("interrupted after possible provider input")

    if lost is SystemExit:
        with pytest.raises(SystemExit):
            dispatch_pending_assistance(fresh(lost_receipt))
        assert (
            notification_states(r.event_log.read_all())[first["request_id"]]
            == "attempted"
        )
    else:
        dispatch_pending_assistance(fresh(lost_receipt))
    dispatch_pending_assistance(fresh(lambda *args: writes.append(args)))
    assert notification_states(r.event_log.read_all())[first["request_id"]] == "unknown"
    assert len(writes) == 2
    assert call(
        (workers[0][0], "", workers[0][2]),
        "pull",
        "--advisory",
        first["request_id"],
        "--request-id",
        "forged",
        ok=False,
    )
    _answer(call, oa, first)
    _answer(call, oa, second)
    dispatch_pending_assistance(fresh(lambda *args: writes.append(args)))
    assert len(writes) == 2
    assert (
        sum(
            e.type == NOTICE and e.payload["status"] == "attempted"
            for e in r.event_log.read_all()
        )
        == 2
    )


def test_exact_taker_stop_and_authorized_wait_have_different_recovery(project):
    r, workers, oa, call, send, fresh = project
    one = send(workers[0])
    two = send(workers[1])

    def arrive(question, turn_id):
        dispatch_pending_assistance(fresh(lambda *args: None))
        r.event_writer.emit(
            "codex.hook.user_prompt_submit",
            actor=oa[0],
            payload={"session_id": "session", "turn_id": turn_id},
        )
        scope = ["--advisory", question["request_id"]]
        message = ["--message", question["message_key"]]
        delivery = call(oa, "pull", *scope, *message, "--request-id", "read")[
            "delivery_id"
        ]
        call(oa, "ack", *scope, "--delivery", delivery)
        options = [*scope, *message, "--delivery", delivery]
        call(oa, "claim", *options, "--request-id", "claim")
        return options

    arrive(one, "t1")
    r.event_writer.emit(
        "codex.hook.stop",
        actor=oa[0],
        payload={"session_id": "session", "turn_id": "unrelated"},
    )
    dispatch_pending_assistance(fresh(lambda *args: None))
    assert not any(e.type == "worker.assistance.closed" for e in r.event_log.read_all())
    r.event_writer.emit(
        "codex.hook.stop",
        actor=oa[0],
        payload={"session_id": "session", "turn_id": "t1"},
    )
    options = arrive(two, "t2")
    end = (datetime.now(timezone.utc) + timedelta(seconds=45)).isoformat()
    call(
        oa,
        "wait",
        *options,
        "--request-id",
        "approved-wait",
        "--deadline",
        end,
        "--text",
        "Waiting for the bound result",
    )
    r.event_writer.emit(
        "codex.hook.stop",
        actor=oa[0],
        payload={"session_id": "session", "turn_id": "t2"},
    )
    dispatch_pending_assistance(fresh(lambda *args: None))
    closed = [e for e in r.event_log.read_all() if e.type == "worker.assistance.closed"]
    assert [e.payload["request_id"] for e in closed] == [one["request_id"]]
    assert "without an answer" in closed[0].payload["reason"]
    assert (
        call(workers[0], "read-answer", "--message", one["message_key"])["status"]
        == "waiting"
    )
    pending = call(oa, "inbox", "--run", "run-1")
    assert [row["request_id"] for row in pending["items"]] == [two["request_id"]]
    assert "Waiting for the bound result" not in str(pending)


def test_confirmed_unsent_interactive_prompt_waits_for_same_run_resume(project):
    from zf.runtime.provider_interaction import ProviderInteractionRequired

    r, workers, oa, call, send, fresh = project
    q = send(workers[0])
    writes = []

    def prompt(*args):
        writes.append(args)
        raise ProviderInteractionRequired("model switch requires operator input")

    dispatch_pending_assistance(fresh(prompt))
    assert notification_states(r.event_log.read_all())[q["request_id"]] == "not_sent"
    r.event_writer.emit(
        "run.resumed", payload={"run_id": "another-run", "paused_event_id": "unrelated"}
    )
    dispatch_pending_assistance(fresh(prompt))
    assert len(writes) == 1
    from tests.test_run_admission import _control

    r.event_writer.emit(
        "run.admission.admitted",
        correlation_id="run-1",
        payload={"workflow_run_id": "run-1", "run_id": "run-1", "request_id": "run-1"},
    )
    assert _control(r, action="run-pause", run_id="run-1")["status"] == "paused"
    assert _control(r, action="run-resume", run_id="run-1")["status"] == "running"
    dispatch_pending_assistance(fresh(lambda *args: writes.append(args)))
    dispatch_pending_assistance(fresh(lambda *args: writes.append(args)))
    assert notification_states(r.event_log.read_all())[q["request_id"]] == "confirmed"
    assert len(writes) == 2


def test_current_compatible_task_attempt_can_retrieve_prior_question_answer(project):
    from zf.runtime.workflow_operation import load_workflow_operation

    r, workers, oa, call, send, fresh = project
    q = send(workers[0])
    dispatch_pending_assistance(fresh(lambda *args: None))
    _answer(call, oa, q)
    actor, original, token = workers[0]
    service = WorkflowOperationService(
        state_dir=r.state_dir, event_log=r.event_log, event_writer=r.event_writer
    )
    op = load_workflow_operation(r.event_log, "op-1")
    service.interrupt(
        operation_id="op-1",
        request_hash=op["request_hash"],
        workflow_run_id="run-1",
        reason="approved restart",
        source_attempt_id=original,
        task_id="TASK-1",
    )
    now = datetime.now(timezone.utc)
    store = TaskAttemptStore(r.state_dir / "task_attempts.json")
    store.update(original, status="expired", updated_at=now.isoformat())
    replacement = store.ensure_for_dispatch(
        run_id="run-1",
        task_id="TASK-1",
        dispatch_id="second-dispatch",
        role="impl",
        instance_id=actor,
        operation_id="op-1",
        briefing_ref="recovery.md",
        created_at=now.isoformat(),
        lease_expires_at=(now + timedelta(hours=1)).isoformat(),
        max_attempts=3,
        identity_version="operation-v2",
        placement_epoch=2,
    ).attempt
    assert replacement["attempt_id"] != original
    service.mark_retry_started(
        operation_id="op-1",
        request_hash=op["request_hash"],
        workflow_run_id="run-1",
        retry_attempt=1,
        reason="approved restart",
        dispatch_id="second-dispatch",
        role_instance=actor,
        active_attempt_id=replacement["attempt_id"],
        lease_id=replacement["lease_id"],
        task_id="TASK-1",
    )
    assert call(workers[0], "read-answer", "--message", q["message_key"], ok=False)
    assert call(workers[1], "read-answer", "--message", q["message_key"], ok=False)
    recovered = call(
        (actor, replacement["attempt_id"], token),
        "read-answer",
        "--message",
        q["message_key"],
    )
    assert (
        recovered["status"] == "answered"
        and recovered["text"] == "Use the approved public sample."
    )
    assert recovered["new_writer_authorized"] is False


def test_recipient_cli_inbox_is_pure_and_works_before_question(project, monkeypatch):
    r, workers, oa, call, send, fresh = project
    notice = call(
        workers[0],
        "send",
        "--request-id",
        "evidence-pointer",
        "--kind",
        "notice",
        "--recipient",
        '{"mode":"execution","operation_id":"op-2"}',
        "--text",
        "The agreed example is available.",
    )
    read_ledger = r.event_log.read_all
    before = read_ledger()

    def forbid_full_ledger():
        raise AssertionError("a recipient inbox must read the sealed scoped index")

    monkeypatch.setattr(r.event_log, "read_all", forbid_full_ledger)
    result = call(workers[1], "inbox")
    assert result["total"] == 1 and [i["message_key"] for i in result["items"]] == [
        notice["message_key"]
    ]
    assert call(workers[0], "inbox")["total"] == 0
    assert "agreed example" not in json.dumps(result)
    assert [e.id for e in read_ledger()] == [e.id for e in before]


def test_disabling_then_reenabling_pilot_preserves_each_existing_assistance_receipt(
    project,
):
    r, workers, oa, call, send, fresh = project
    first = send(workers[0])

    def uncertain(*args):
        raise RuntimeError("transport returned no receipt")

    dispatch_pending_assistance(fresh(uncertain))
    second = send(workers[1])
    confirmed = []
    dispatch_pending_assistance(fresh(lambda *args: confirmed.append(args)))
    _answer(call, oa, second)
    dispatch_pending_assistance(fresh(lambda *args: confirmed.append(args)))

    def ask(serial, ok=True):
        return call(
            workers[0],
            "send",
            "--request-id",
            serial,
            "--kind",
            "question",
            "--recipient",
            '{"mode":"coordination","owner":"orchestrator"}',
            "--text",
            "Which document applies?",
            "--evidence-ref",
            "requirement.md",
            ok=ok,
        )

    third = ask("waiting")
    states = notification_states(r.event_log.read_all())
    assert states[first["request_id"]] == "unknown"
    assert states[second["request_id"]] == "confirmed"
    assert third["request_id"] not in states
    r.config.runtime.durable_mailbox.enabled = False
    assert (
        call(workers[1], "read-answer", "--message", second["message_key"])["status"]
        == "answered"
    )
    assert "disabled" in ask("new-during-rollback", ok=False)
    assert ask("waiting") == third
    dispatch_pending_assistance(fresh(lambda *args: confirmed.append(args)))
    _answer(call, oa, first)
    dispatch_pending_assistance(fresh(lambda *args: confirmed.append(args)))
    assert (
        call(workers[0], "read-answer", "--message", first["message_key"])["status"]
        == "answered"
    )
    r.config.runtime.durable_mailbox.enabled = True
    assert ask("waiting") == third
    _answer(call, oa, third)
    dispatch_pending_assistance(fresh(lambda *args: confirmed.append(args)))
    events = r.event_log.read_all()
    attempts = [
        e.payload["request_id"]
        for e in events
        if e.type == NOTICE and e.payload["status"] == "attempted"
    ]
    assert attempts == [first["request_id"], second["request_id"], third["request_id"]]
    assert sum(e.type == "workflow.mailbox.accepted" for e in events) == 3
    assert (
        sum(
            e.type == "worker.assistance.closed" and e.payload["status"] == "answered"
            for e in events
        )
        == 3
    )


@pytest.mark.parametrize("backend", ["codex", "claude-code"])
@pytest.mark.parametrize(
    ("screen", "after_write", "status"),
    [
        ("ready", True, "confirmed"),
        ("modal", False, "not_sent"),
        ("unreadable", False, "not_sent"),
        ("modal", True, "unknown"),
        ("unreadable", True, "unknown"),
        ("blank", True, "unknown"),
    ],
)
def test_tmux_notice_checks_actual_screen_before_enter(
    project, monkeypatch, backend, screen, after_write, status
):
    from dataclasses import replace

    from zf.runtime.fanout_evidence_queries import FanoutEvidenceQueriesMixin
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.transport import TmuxTransport

    r, workers, _, _, send, fresh = project
    q = send(workers[0])
    tmux = TmuxSession("mailbox-test", dry_run=True)
    adapter = TmuxTransport(tmux)
    marker = "Read the bounded worker guidance briefing"

    def written():
        return any(
            " paste-buffer " in cmd or (" send-keys " in cmd and marker in cmd)
            for cmd in tmux.command_log
        )

    def capture(role, *, lines):
        if not after_write or written():
            if screen == "unreadable":
                raise OSError("screen unavailable")
            if screen == "blank":
                return ""
            if screen == "modal":
                return "Do you trust the files in this folder?"
        return "Ready for a briefing"

    monkeypatch.setattr(tmux, "capture_pane", capture)
    contexts = []

    def send_to_adapter(role, briefing, prompt, context):
        contexts.append(context)
        assert marker in prompt and not any(ord(c) < 32 for c in prompt)
        adapter.send_task(role, briefing, prompt, context=context)

    def reactor():
        rt = fresh(send_to_adapter)
        rt._current_run_id = lambda: "run-1"
        rt._trace_id_for_task = lambda _: "run-1"

        def bound_context(**options):
            role = replace(options["role"], backend=backend)
            return FanoutEvidenceQueriesMixin._dispatch_context(rt, **{**options, "role": role})

        rt._dispatch_context = bound_context
        return rt

    dispatch_pending_assistance(reactor())
    dispatch_pending_assistance(reactor())
    notices = [
        e.payload for e in r.event_log.read_all()
        if e.type == "workflow.mailbox.notification" and e.payload["request_id"] == q["request_id"]
    ]
    assert [e["status"] for e in notices] == ["reserved", "attempted", status]
    if status == "unknown":
        issue = "trust_prompt" if screen == "modal" else "screen_unreadable"
        assert notices[-1]["reason"] == f"submit_withheld:{issue}"
    assert len(contexts) == 1
    assert contexts[0].mailbox_notice and contexts[0].run_id == "run-1"
    assert written() is (after_write or status == "confirmed")
    assert any(cmd.endswith(" Enter") for cmd in tmux.command_log) is (status == "confirmed")
