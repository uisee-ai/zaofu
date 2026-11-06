from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_task_pipeline_fault_matrix import _runtime, _running_operation_v2_attempt
from zf.cli.main import build_parser
from zf.core.config.loader import load_config
from zf.core.events import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator_briefing import _render_rework_triage_contract
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.worker_assistance import read_assistance, request_assistance, reconcile_assistance
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations


@pytest.fixture
def running(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.config = load_config(Path(__file__).parents[1] / "zf-prd-v5.yaml")
    attempt = _running_operation_v2_attempt(runtime)
    TaskAttemptStore(runtime.state_dir / "task_attempts.json").renew_lease(
        attempt["attempt_id"], updated_at=datetime.now(timezone.utc).isoformat(),
        lease_expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    TaskStore(runtime.state_dir / "kanban.json").add(Task(
        id="TASK-A", title="CSV", status="in_progress", assigned_to="impl-1"))
    runtime.attempt = attempt
    runtime.identity = {"workflow_run_id": "run-1", "task_id": "TASK-A",
                        "operation_id": attempt["operation_id"], "active_attempt_id": attempt["attempt_id"],
                        "lease_id": attempt["lease_id"]}
    return runtime


def _request(r, owner="orchestrator"):
    return request_assistance(state_dir=r.state_dir, writer=r.event_writer, config=r.config,
        identity=r.identity, actor="impl-1", owner=owner, question="当前合同是否允许本地 fixture？",
        evidence_refs=["requirement.md"], tried=["read the integration acceptance"])


def _answer(r, request_id, action="precise_rework", actor="orchestrator"):
    r.event_writer.append(ZfEvent(type="orchestrator.rework.triage.recorded", actor=actor, task_id="TASK-A",
        payload={"request_id": request_id, "recommended_action": action,
                 "guidance": "按当前合同执行真实集成验证；局部单测可使用 fixture。", "apply_policy": "proposal_only"}))


def test_rm_tick_routes_guidance_once_without_starting_second_writer(running):
    r = running
    request = _request(r)
    assert _request(r)["request_id"] == request["request_id"]
    before = reduce_workflow_operations(r.event_log.read_all())
    run_manager_tick(state_dir=r.state_dir, writer=r.event_writer, config=r.config,
                     event_log=r.event_log, spawn_repairs=False)
    dispatched = [e for e in r.event_log.read_all() if e.type == "orchestrator.rework.triage.requested"]
    assert len(dispatched) == 1
    briefing = _render_rework_triage_contract(dispatched[0])
    assert "只读求助" in briefing and request["request_id"] in briefing
    _answer(r, request["request_id"])
    result = read_assistance(state_dir=r.state_dir, writer=r.event_writer, request_id=request["request_id"])
    assert result["status"] == "guidance" and not result["new_writer_authorized"]
    reconcile_assistance(state_dir=r.state_dir, writer=r.event_writer, config=r.config)
    assert reduce_workflow_operations(r.event_log.read_all()) == before


def test_replan_answer_requires_controlled_handoff(running):
    r = running
    request = _request(r)
    _answer(r, request["request_id"], "replan")
    result = read_assistance(state_dir=r.state_dir, writer=r.event_writer, request_id=request["request_id"])
    assert result["status"] == "controlled_handoff_required"
    assert TaskStore(r.state_dir / "kanban.json").get("TASK-A").status == "in_progress"


def test_wrong_responder_and_old_attempt_cannot_supply_guidance(running):
    r = running
    request = _request(r)
    _answer(r, request["request_id"], actor="impl-1")
    assert read_assistance(state_dir=r.state_dir, writer=r.event_writer, request_id=request["request_id"])["status"] == "pending"
    operation = reduce_workflow_operations(r.event_log.read_all())[r.attempt["operation_id"]]
    service = WorkflowOperationService(state_dir=r.state_dir, event_log=r.event_log, event_writer=r.event_writer)
    service.cancel(operation_id=operation["operation_id"], request_hash=operation["request_hash"],
                   workflow_run_id="run-1", reason="cancel", task_id="TASK-A")
    _answer(r, request["request_id"])
    with pytest.raises(ValueError, match="running operation"):
        read_assistance(state_dir=r.state_dir, writer=r.event_writer, request_id=request["request_id"])


def test_cli_uses_current_identity_and_state_directory(running, monkeypatch, capsys):
    r = running
    import zf.cli.recover_assistance as cli
    monkeypatch.setattr(cli, "resolve_project_context", lambda **kw: SimpleNamespace(state_dir=r.state_dir, config=r.config))
    args = build_parser().parse_args(["recover", "assistance", "request", "--operation-id", r.attempt["operation_id"],
        "--attempt-id", r.attempt["attempt_id"], "--lease-id", r.attempt["lease_id"], "--actor", "impl-1",
        "--question", "Need guidance", "--evidence-ref", "requirement.md"])
    assert args.func(args) == 0
    assert '"status": "requested"' in capsys.readouterr().out
    args.lease_id = "stale"
    assert args.func(args) == 1


def test_late_answer_is_rejected_even_before_timeout_tick(running, monkeypatch):
    request = _request(running)
    _answer(running, request["request_id"])
    monkeypatch.setattr("zf.runtime.worker_assistance.active_request_age_seconds", lambda *a, **kw: 10**9)
    with pytest.raises(ValueError, match="deadline"):
        read_assistance(state_dir=running.state_dir, writer=running.event_writer, request_id=request["request_id"])
    reconcile_assistance(state_dir=running.state_dir, writer=running.event_writer, config=running.config)
    waits = [e for e in running.event_log.read_all() if e.type == "human.escalate"]
    assert waits[-1].payload["blocking_scope"] == "task"


def test_changed_question_evidence_creates_a_new_immutable_request(running):
    first = _request(running)
    second = request_assistance(state_dir=running.state_dir, writer=running.event_writer, config=running.config,
        identity=running.identity, actor="impl-1", owner="orchestrator", question="当前合同是否允许本地 fixture？",
        evidence_refs=["new-failure.log"], tried=["different probe"])
    assert first["request_id"] != second["request_id"]


def test_fresh_reactor_delivers_missed_guidance_once(running, monkeypatch):
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.worker_assistance import dispatch_pending_assistance
    from zf.runtime.orchestrator_agent_role import configured_orchestrator_role_from_config
    r = running
    _request(r)
    # Fresh requests must be routed by the reactor, without first waiting for
    # the periodic (300-second) supervisor / Run Manager sweep.
    sent = []
    monkeypatch.setattr("zf.runtime.orchestrator_agent_transport.orchestrator_checkpoint_busy", lambda _: False)
    monkeypatch.setattr("zf.runtime.orchestrator.build_orchestrator_briefing", lambda **kw: _render_rework_triage_contract(kw["trigger_event"]))
    r._record_skill_provenance = lambda **kw: None
    r._dispatch_context = lambda **kw: {}
    r._send_transport_task = lambda *args: sent.append(args)
    r._fire_orchestrator_turn = lambda role, events: Orchestrator._fire_orchestrator_turn(r, role, events)
    role = configured_orchestrator_role_from_config(r.config)
    monkeypatch.setattr("zf.runtime.orchestrator_agent_role.orchestrator_instance_id", lambda _: role.instance_id or role.name)
    dispatch_pending_assistance(r)
    dispatch_pending_assistance(r)
    assert len(sent) == 1
    assert "只读求助" in sent[0][1].read_text()


def test_reactive_and_periodic_reconciliation_publish_once(running):
    from concurrent.futures import ThreadPoolExecutor
    r = running
    request = _request(r)
    def reconcile(_):
        return reconcile_assistance(state_dir=r.state_dir, writer=r.event_writer, config=r.config)
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sum(workers.map(reconcile, range(4))) == 1
    _answer(r, request["request_id"])
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sum(workers.map(reconcile, range(4))) == 1
    events = r.event_log.read_all()
    assert sum(e.type == "orchestrator.rework.triage.requested" for e in events) == 1
    assert sum(e.type == "worker.assistance.closed" for e in events) == 1
