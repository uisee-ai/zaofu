"""Real watcher SIGKILL and CLI takeover; scripted workers, no host reboot."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from tests.e2e.generic_workflow_complex_support import _execute_action
from tests.e2e.scripts.prod_flow_terminal_audit import audit_terminal_delivery
from tests.testkit.evidence import write_json_atomic
from tests.testkit.general_route import GeneralRoute
from tests.testkit.live_general_runtime import LiveGeneralRuntime
from zf.core.events import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.tmux_process_cleanup import _Pidfd, _identity
from zf.runtime.workflow_anchor import mark_workflow_managed_task
from zf.runtime.workflow_start import WorkflowStartService


pytestmark = [pytest.mark.host, pytest.mark.serial]


@pytest.mark.parametrize("restart_mode", ["preserve-workers", "full-start"])
def test_watcher_hard_exit_then_cli_takeover_finishes_same_run(
    tmp_path: Path, monkeypatch, record_property, restart_mode: str,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    project, output = tmp_path / "project", tmp_path / "takeover"
    project.mkdir()
    output.mkdir()
    for key in tuple(os.environ):
        if key.startswith(("ZF_OWNER_VISIBLE_", "FEISHU_", "LARK_", "ZF_PROJECT_ROOT",
                           "ZF_STATE_DIR", "ZF_WORKFLOW_ACTION_TOKEN", "ZF_WEB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ZF_DISABLE_LIVE_FEISHU", "1")
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    route = GeneralRoute(project, monkeypatch)
    runtime = LiveGeneralRuntime(route, output, repo)
    route.reopen()
    _, preview = route.prepare()
    proposal = preview["proposal"]
    applied = _execute_action(route.service, route.writer, "workflow-config-apply", {
        "proposal_id": proposal["proposal_id"], "proposal_ref": preview["proposal_ref"],
        "proposal_digest": proposal["proposal_digest"],
        "validation_result_ref": proposal["validation_result_ref"],
        "approval_ref": "owner:k541-watcher-takeover",
        "idempotency_key": f"k541:{proposal['proposal_digest']}",
    })
    assert applied["ok"], applied
    route.reopen()
    task_id = "TASK-K541-WATCHER-TAKEOVER"
    task = mark_workflow_managed_task(Task(
        id=task_id,
        title="K541 watcher takeover",
        contract=TaskContract(
            behavior="Deliver a report proving that fixture evidence is local.",
            verification="test -f README.md",
            acceptance_criteria=["the live scripted worker returns every stage result"],
        ),
    ))
    TaskStore(route.state / "kanban.json").add(task)
    (route.state / "testkit-worker-gate").touch()
    route.writer.append(ZfEvent(type="simulation.bootstrap", actor="testkit",
                               payload={"purpose": "watcher-hard-exit"}))
    monkeypatch.setattr(Orchestrator, "run_once",
                        lambda *_args, **_kwargs: pytest.fail("parent drove the Kernel"))
    report = {"verdict": "fail", "restart_mode": restart_mode,
              "state_dir": str(route.state), "session": runtime.session}
    try:
        runtime.start()
        proposed = WorkflowStartService(route.state, route.config, project_root=project).propose(
            route.writer,
            {"task_id": task_id, "route_id": "general:scope",
             "objective": task.contract.behavior, "parameters": {"backend": "mock"}},
            actor="testkit-planner", origin="coding_agent",
        )
        assert proposed["ok"], proposed
        token = "k541-public-test-token"
        started = subprocess.run(
            [sys.executable, "-m", "zf.cli.main", "workflow", "start", "--apply",
             "--proposal-event-id", proposed["proposal_event_id"],
             "--authorization-ref", "operator:k541-test", "--authorization-token", token,
             "--format", "json"],
            cwd=project, env={**runtime.env, "ZF_WORKFLOW_ACTION_TOKEN": token},
            capture_output=True, text=True, timeout=30,
        )
        (output / "workflow-start.log").write_text(started.stdout + started.stderr)
        assert started.returncode == 0, started.stdout + started.stderr
        assert json.loads(started.stdout)["ok"]
        [invoke] = route.events("workflow.invoke.requested")
        assert invoke.task_id == task_id
        report.update(invoke_event_id=invoke.id, proposal_event_id=proposed["proposal_event_id"])
        receipts_dir = route.state / "testkit-transport"
        runtime.wait(lambda: list(receipts_dir.glob("*.json")))
        [first_path] = receipts_dir.glob("*.json")
        first = json.loads(first_path.read_text())
        assert first["stage_id"] == "scope" and "submission" not in first
        worker_pid = first["pid"]
        runtime.pids.add(worker_pid)
        assert Path(f"/proc/{worker_pid}").exists()
        assert not route.events("workflow.call.result.admitted")
        assert not route.events("run.goal.completed")
        guard_path = route.state / "processes/watcher.pid.json"
        old_guard = json.loads(guard_path.read_text())
        old_process = runtime.process
        assert old_guard["owner_pid"] == old_process.pid != os.getpid()
        # Kill only the Popen-owned watcher, retaining stale lock/guard bytes.
        old_process.kill()
        assert old_process.wait(timeout=10) == -signal.SIGKILL
        assert not Path(f"/proc/{old_process.pid}").exists()
        assert json.loads(guard_path.read_text()) == old_guard
        assert (route.state / "loop.lock").exists()
        assert Path(f"/proc/{worker_pid}").exists()
        assert TmuxSession(runtime.session).has_session()
        report.update(old_watcher_pid=old_process.pid, old_guard=old_guard,
                      old_worker_pid=worker_pid, first_handoff=first)
        write_json_atomic(output / "takeover.json", report)

        runtime.start(control_plane_only=restart_mode == "preserve-workers")
        new_guard = json.loads(guard_path.read_text())
        assert new_guard["owner_pid"] == runtime.process.pid != old_process.pid
        assert new_guard["epoch"] != old_guard["epoch"]
        if restart_mode == "preserve-workers":
            assert Path(f"/proc/{worker_pid}").exists()
            assert [row.id for row in route.events("fanout.child.dispatched")] == [
                first["dispatch_event_id"],
            ]
        else:
            runtime.wait(lambda: not Path(f"/proc/{worker_pid}").exists(), seconds=5)
        (route.state / "testkit-worker-release").touch()
        runtime.wait(lambda: route.events("run.goal.completed"), seconds=90)
        [terminal] = route.events("run.goal.completed")
        assert terminal.origin == "kernel"
        assert terminal.payload["workflow_run_id"] == invoke.payload["workflow_run_id"]
        runtime.wait(lambda: TaskStore(route.state / "kanban.json").get(invoke.task_id).status == "done")
        runtime.wait(lambda: any(row.payload.get("terminal_event_id") == terminal.id
                                for row in route.events("owner.visible_message.requested")))
        [owner] = [row for row in route.events("owner.visible_message.requested")
                   if row.payload.get("terminal_event_id") == terminal.id]
        delivery = audit_terminal_delivery(route.state)
        assert delivery["status"] == "passed", delivery
        assert delivery["terminal"]["event_id"] == terminal.id
        receipts = [json.loads(path.read_text()) for path in receipts_dir.glob("*.json")]
        submitted = [row for row in receipts if "submission" in row]
        assert len(submitted) == 5
        assert {row["stage_id"] for row in submitted} == {
            "scope", "collect-1", "collect-2", "synthesize", "verify",
        }
        if restart_mode == "preserve-workers":
            assert len(receipts) == 5
            assert json.loads(first_path.read_text())["pid"] == worker_pid
        else:
            assert len(receipts) == 6
            assert json.loads(first_path.read_text()) == first
            [replacement] = [row for row in submitted if row["stage_id"] == "scope"]
            assert replacement["pid"] != worker_pid
            [new_dispatch] = [row for row in route.events("fanout.child.dispatched")
                              if row.id == replacement["dispatch_event_id"]]
            assert new_dispatch.payload["retry_of_run_id"] == first["run_id"]
            assert new_dispatch.payload["run_id"] == replacement["run_id"]
            [old_attempt] = [row for row in route.events("task.attempt.started")
                             if row.payload["dispatch_id"] == first["run_id"]]
            [new_attempt] = [row for row in route.events("task.attempt.started")
                             if row.payload["dispatch_id"] == replacement["run_id"]]
            [superseded] = [row for row in route.events("task.attempt.superseded")
                            if row.payload["attempt_id"] == old_attempt.payload["attempt_id"]]
            assert superseded.payload["superseded_by"] == new_attempt.payload["attempt_id"]
            assert old_attempt.payload["operation_id"] == new_attempt.payload["operation_id"]
        dispatched = {row.id: row for row in route.events("fanout.child.dispatched")}
        assert set(dispatched) == {row["dispatch_event_id"] for row in receipts}
        assert all(dispatched[row["dispatch_event_id"]].payload["run_id"] == row["run_id"]
                   for row in receipts)
        admission_rows = route.events("workflow.call.result.admitted")
        admissions = {row.id for row in admission_rows}
        # scope has one child admission plus the fanout aggregate admission;
        # the five physical worker receipts still each map to one admission.
        assert len(admissions) == 6
        worker_admissions = {row["submission"]["admitted_event_id"] for row in submitted}
        assert len(worker_admissions) == 5 and worker_admissions < admissions
        [aggregate] = [row for row in admission_rows if row.id not in worker_admissions]
        assert aggregate.origin == "kernel"
        assert aggregate.payload["control_result_schema"] == "fanout-aggregate-result.v1"
        [scope_receipt] = [row for row in submitted if row["stage_id"] == "scope"]
        [scope_admission] = [row for row in admission_rows
                             if row.id == scope_receipt["submission"]["admitted_event_id"]]
        assert aggregate.payload["operation_id"] == scope_admission.payload["parent_operation_id"]
        assert all(row.payload["workflow_run_id"] == invoke.payload["workflow_run_id"]
                   for row in admission_rows)
        runtime.pids.update(row["pid"] for row in receipts)
        for row in submitted:
            assert hashlib.sha256(Path(row["briefing_path"]).read_bytes()).hexdigest() == row["briefing_sha256"]
        bodies = {route.state / owner.payload[key]: (route.state / owner.payload[key]).read_bytes()
                  for key in ("dossier_ref", "completion_receipt_ref")}
        kinds = {"workflow.invoke.requested", "run.admitted", "fanout.child.dispatched",
                 "workflow.call.result.admitted", "run.goal.completed", "owner.visible_message.requested"}
        before = [row.to_json() for row in route.log.read_all() if row.type in kinds]
        for _ in range(2):
            heartbeat = json.loads(guard_path.read_text())["heartbeat_at"]
            runtime.wait(lambda: json.loads(guard_path.read_text())["heartbeat_at"] != heartbeat, seconds=20)
        assert [row.to_json() for row in route.log.read_all() if row.type in kinds] == before
        assert all(path.read_bytes() == body for path, body in bodies.items())
        assert len(route.events("workflow.invoke.requested")) == 1
        assert not route.events("orchestrator.tick.failed")
        assert not route.events("kernel.housekeeping.failed")
        report.update(verdict="pass", new_watcher_pid=runtime.process.pid, new_guard=new_guard,
                      terminal_event_id=terminal.id, delivery=delivery, receipts=receipts)
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        cleanup_error = None
        try:
            runtime.stop()
        except Exception as exc:
            cleanup_error = repr(exc)
        route.writer.append(ZfEvent(type="simulation.done", actor="testkit",
                                   payload={"purpose": "watcher-hard-exit"}))
        report.update(cleanup_ok=cleanup_error is None, cleanup_error=cleanup_error,
                      owned_pids=sorted(runtime.pids))
        write_json_atomic(output / "takeover.json", report)
        record_property("watcher_takeover", json.dumps(report, ensure_ascii=False))
        assert cleanup_error is None, cleanup_error


def test_worker_sigkill_respawn_preserves_same_run_terminal_evidence(
    tmp_path: Path, monkeypatch, record_property,
) -> None:
    """Bind an owned worker SIGKILL to watchdog recovery and same-Run delivery."""
    repo = Path(__file__).resolve().parents[2]
    project, output = tmp_path / "project", tmp_path / "worker-kill"
    project.mkdir()
    output.mkdir()
    for key in tuple(os.environ):
        if key.startswith(("ZF_OWNER_VISIBLE_", "FEISHU_", "LARK_", "ZF_PROJECT_ROOT",
                           "ZF_STATE_DIR", "ZF_WORKFLOW_ACTION_TOKEN", "ZF_WEB_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ZF_DISABLE_LIVE_FEISHU", "1")
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    route = GeneralRoute(project, monkeypatch)
    _, preview = route.prepare()
    proposal = preview["proposal"]
    applied = _execute_action(route.service, route.writer, "workflow-config-apply", {
        "proposal_id": proposal["proposal_id"], "proposal_ref": preview["proposal_ref"],
        "proposal_digest": proposal["proposal_digest"],
        "validation_result_ref": proposal["validation_result_ref"],
        "approval_ref": "owner:k565-worker-kill",
        "idempotency_key": f"k565:{proposal['proposal_digest']}",
    })
    assert applied["ok"], applied
    route.reopen()
    task_id = "TASK-K565-WORKER-KILL"
    task = mark_workflow_managed_task(Task(
        id=task_id,
        title="K565 worker kill recovery",
        contract=TaskContract(
            behavior="Deliver a report proving that fixture evidence is local.",
            verification="test -f README.md",
            acceptance_criteria=["the same workflow run settles after worker recovery"],
        ),
    ))
    TaskStore(route.state / "kanban.json").add(task)
    (route.state / "testkit-worker-gate").touch()
    route.writer.append(ZfEvent(type="simulation.bootstrap", actor="testkit",
                               payload={"purpose": "worker-sigkill-recovery"}))
    runtime = LiveGeneralRuntime(route, output, repo)
    report = {"verdict": "fail", "state_dir": str(route.state), "session": runtime.session}
    monkeypatch.setattr(Orchestrator, "run_once",
                        lambda *_args, **_kwargs: pytest.fail("parent drove the Kernel"))
    try:
        runtime.start()
        proposed = WorkflowStartService(route.state, route.config, project_root=project).propose(
            route.writer,
            {"task_id": task_id, "route_id": "general:scope",
             "objective": task.contract.behavior, "parameters": {"backend": "mock"}},
            actor="testkit-planner", origin="coding_agent",
        )
        assert proposed["ok"], proposed
        token = "k565-worker-kill-token"
        started = subprocess.run(
            [sys.executable, "-m", "zf.cli.main", "workflow", "start", "--apply",
             "--proposal-event-id", proposed["proposal_event_id"],
             "--authorization-ref", "operator:k565-test", "--authorization-token", token,
             "--format", "json"],
            cwd=project, env={**runtime.env, "ZF_WORKFLOW_ACTION_TOKEN": token},
            capture_output=True, text=True, timeout=30,
        )
        assert started.returncode == 0, started.stdout + started.stderr
        [invoke] = route.events("workflow.invoke.requested")
        receipts_dir = route.state / "testkit-transport"
        runtime.wait(lambda: list(receipts_dir.glob("*.json")), seconds=30)
        [first_path] = receipts_dir.glob("*.json")
        first_receipt = json.loads(first_path.read_text())
        assert first_receipt["stage_id"] == "scope" and "submission" not in first_receipt
        first_pid = int(first_receipt["pid"])
        runtime.pids.add(first_pid)
        process_dir = route.state / "testkit-worker-processes"
        runtime.wait(lambda: any(json.loads(path.read_text()).get("pid") == first_pid
                                 for path in process_dir.glob("*.json")), seconds=15)
        first_process_path = next(path for path in process_dir.glob("*.json")
                                  if json.loads(path.read_text()).get("pid") == first_pid)
        first_process_before = first_process_path.read_bytes()
        # Pin the exact process and check its private state/role identity before
        # signalling. A receipt's numeric PID alone does not authorize a kill.
        identity = _identity(first_pid)
        assert identity is not None
        api = _Pidfd()
        descriptor = api.open(first_pid)
        try:
            process_env = Path(f"/proc/{first_pid}/environ").read_bytes().split(b"\0")
            assert f"ZF_STATE_DIR={route.state}".encode() in process_env
            assert f"ZF_ROLE_INSTANCE={first_receipt['role']}".encode() in process_env
            assert _identity(first_pid) == identity
            report.update(invoke_event_id=invoke.id, first_receipt=first_receipt,
                          first_process=json.loads(first_process_before),
                          first_process_sha256=hashlib.sha256(first_process_before).hexdigest(),
                          fault={"kind": "SIGKILL", "pid": first_pid, "birth_ticks": identity[1]})
            write_json_atomic(output / "worker-kill.json", report)
            api.send(descriptor, signal.SIGKILL)
        finally:
            os.close(descriptor)
        runtime.wait(lambda: not Path(f"/proc/{first_pid}").exists(), seconds=10)
        killed_record = json.loads(first_process_path.read_text())
        assert killed_record["status"] == "running"
        assert "finished_at_ns" not in killed_record
        assert first_process_path.read_bytes() == first_process_before

        runtime.wait(lambda: route.events("worker.runner.failed"), seconds=90)
        runtime.wait(lambda: route.events("worker.respawned"), seconds=90)
        [failed] = [event for event in route.events("worker.runner.failed")
                    if event.actor == first_receipt["role"]]
        [respawned] = [event for event in route.events("worker.respawned")
                       if event.actor == first_receipt["role"]]
        assert route.log.read_all().index(failed) < route.log.read_all().index(respawned)
        assert failed.task_id == task_id
        assert failed.payload["instance_id"] == first_receipt["role"]
        assert "exit_reason" not in failed.payload["lifecycle"]

        def replacements():
            return [row for path in process_dir.glob("*.json")
                    if (row := json.loads(path.read_text()))["role"] == first_receipt["role"]
                    and row["pid"] != first_pid
                    and row["started_at_ns"] > killed_record["started_at_ns"]]

        runtime.wait(lambda: replacements(), seconds=30)
        [replacement] = replacements()
        assert replacement["pid"] != first_pid
        runtime.pids.add(int(replacement["pid"]))
        runtime.wait(lambda: any(
            json.loads(path.read_text()).get("pid") == replacement["pid"]
            for path in receipts_dir.glob("*.json")), seconds=30)
        [recovered_receipt] = [row for path in receipts_dir.glob("*.json")
                               if (row := json.loads(path.read_text()))["pid"] == replacement["pid"]]
        assert "submission" not in recovered_receipt
        assert recovered_receipt["run_id"] == first_receipt["run_id"]
        assert recovered_receipt["dispatch_event_id"] == first_receipt["dispatch_event_id"]
        [injected] = [event for event in route.events("worker.recovery.injected")
                      if event.actor == first_receipt["role"]]
        assert injected.payload["run_id"] == recovered_receipt["run_id"]
        assert injected.payload["briefing_path"] == recovered_receipt["briefing_path"]
        assert injected.correlation_id == invoke.payload["workflow_run_id"]
        report.update(failed_event=json.loads(failed.to_json()),
                      respawned_event=json.loads(respawned.to_json()),
                      injected_event=json.loads(injected.to_json()),
                      recovered_receipt=recovered_receipt, replacement=replacement)
        write_json_atomic(output / "worker-kill.json", report)
        (route.state / "testkit-worker-release").touch()
        runtime.wait(lambda: route.events("run.goal.completed"), seconds=120)
        [terminal] = route.events("run.goal.completed")
        assert terminal.origin == "kernel"
        assert terminal.payload["workflow_run_id"] == invoke.payload["workflow_run_id"]
        runtime.wait(lambda: TaskStore(route.state / "kanban.json").get(task_id).status == "done")
        # Delivery is projected by a later watcher cycle.  Bind the audit to
        # this run's terminal request instead of treating a still-pending
        # projection as a recovery failure.
        runtime.wait(
            lambda: any(
                row.payload.get("terminal_event_id") == terminal.id
                for row in route.events("owner.visible_message.requested")
            ),
            seconds=30,
        )
        delivery = audit_terminal_delivery(route.state)
        assert delivery["status"] == "passed", delivery
        assert delivery["terminal"]["event_id"] == terminal.id
        assert first_process_path.read_bytes() == first_process_before
        receipts = [json.loads(path.read_text()) for path in receipts_dir.glob("*.json")]
        assert len(receipts) == 5 and all("submission" in row for row in receipts)
        dispatched = {row.id: row for row in route.events("fanout.child.dispatched")}
        assert set(dispatched) == {row["dispatch_event_id"] for row in receipts}
        admissions = {row.id: row for row in route.events("workflow.call.result.admitted")}
        assert len(admissions) == 6  # Five workers plus the kernel scope aggregate.
        for row in receipts:
            dispatch = dispatched[row["dispatch_event_id"]]
            assert dispatch.payload["run_id"] == row["run_id"]
            assert dispatch.payload["workflow_run_id"] == invoke.payload["workflow_run_id"]
            admission = admissions[row["submission"]["admitted_event_id"]]
            assert admission.payload["workflow_run_id"] == invoke.payload["workflow_run_id"]
            assert admission.payload["operation_id"] == dispatch.payload["operation_id"]
            runtime.pids.add(row["pid"])
        assert not route.events("fanout.child.dispatch_lost")
        assert [row.id for row in route.events("worker.runner.failed")] == [failed.id]
        assert [row.id for row in route.events("worker.respawned")] == [respawned.id]
        assert not [row for path in process_dir.glob("*.json")
                    if (row := json.loads(path.read_text()))["status"] == "failed"]
        assert not route.events("orchestrator.tick.failed")
        assert not route.events("kernel.housekeeping.failed")
        assert not route.events("worker.respawn.failed")
        report.update(verdict="pass", invoke_event_id=invoke.id,
                      failed_event_id=failed.id, respawned_event_id=respawned.id,
                      terminal_event_id=terminal.id, first_receipt=first_receipt,
                      first_process=killed_record, replacement=replacement,
                      delivery=delivery, receipts=receipts)
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        cleanup_error = None
        try:
            runtime.stop()
        except Exception as exc:
            cleanup_error = repr(exc)
        route.writer.append(ZfEvent(type="simulation.done", actor="testkit",
                                   payload={"purpose": "worker-sigkill-recovery"}))
        report.update(cleanup_ok=cleanup_error is None, cleanup_error=cleanup_error,
                      owned_pids=sorted(runtime.pids))
        write_json_atomic(output / "worker-kill.json", report)
        record_property("worker_sigkill_recovery", json.dumps(report, ensure_ascii=False))
        assert cleanup_error is None, cleanup_error
