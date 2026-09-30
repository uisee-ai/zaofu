"""Real CLI in-flight General Run, executed inside the offline K571 guest."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.e2e.generic_workflow_complex_support import _execute_action
from tests.e2e.scripts.prod_flow_terminal_audit import audit_terminal_delivery
from tests.testkit.evidence import sha256_file, write_json_atomic
from tests.testkit.general_route import GeneralRoute
from tests.testkit.isolated_vm_guest import _write_json
from tests.testkit.live_general_runtime import LiveGeneralRuntime
from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.workflow_anchor import mark_workflow_managed_task
from zf.runtime.workflow_start import WorkflowStartService


TASK = "TASK-K571-GUEST"
KINDS = {"workflow.invoke.requested", "run.admitted", "fanout.child.dispatched",
         "workflow.call.result.admitted", "run.goal.completed", "owner.visible_message.requested",
         "task.attempt.started", "task.attempt.superseded", "task.done"}


def _run_admission(command: list[str], *, project: Path, env: dict, output: Path):
    """Keep child diagnostics even when communicate raises before returning."""
    try:
        result = subprocess.run(command, cwd=project, env=env,
                                capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        def text(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
        diagnostic = text(exc.stdout) + text(exc.stderr)
        (output / "admission.log").write_text(diagnostic)
        print("ZF_K571_ADMISSION_TIMEOUT\n" + diagnostic, file=sys.stderr, flush=True)
        raise
    (output / "admission.log").write_text(result.stdout + result.stderr)
    return result


def restore_runtime(root: Path, checkpoint: dict, repo: Path) -> LiveGeneralRuntime:
    """Recreate process observers only; never call either bootstrap constructor."""
    project, output = root / "project", root / "runtime"
    config = load_config(project / "zf.yaml")
    state = project / config.project.state_dir
    assert str(state) == checkpoint["state_dir"]
    assert config.session.tmux_session == checkpoint["session"]
    assert sha256_file(project / "zf.yaml") == checkpoint["config_sha256"]
    assert sha256_file(output / "mock-bin/cat") == checkpoint["worker_sha256"]
    with (state / "events.jsonl").open("rb") as stream:
        prefix = stream.read(checkpoint["ledger_bytes"])
    assert hashlib.sha256(prefix).hexdigest() == checkpoint["ledger_sha256"], "observed prefix lost"
    assert prefix == base64.b64decode(checkpoint["ledger_base64"], validate=True)
    log = EventLog(state / "events.jsonl")
    route = SimpleNamespace(root=project, state=state, config=config, log=log,
                            events=lambda kind: [e for e in log.read_all() if e.type == kind])
    runtime = object.__new__(LiveGeneralRuntime)
    runtime.route, runtime.output, runtime.session = route, output, checkpoint["session"]
    runtime.process, runtime.starts, runtime.pids = None, 1, set()
    runtime.env = {
        **os.environ, "PATH": f"{output / 'mock-bin'}:{os.environ['PATH']}",
        "PYTHONPATH": f"{repo}:{repo / 'src'}", "PYTHONUNBUFFERED": "1",
        "ZF_CLI_CMD": f"{sys.executable} -m zf.cli.main",
    }
    return runtime


def firstboot(root: Path, repo: Path, patch: pytest.MonkeyPatch) -> dict:
    project, output = root / "project", root / "runtime"
    project.mkdir()
    output.mkdir()
    route = GeneralRoute(project, patch)
    runtime = LiveGeneralRuntime(route, output, repo)
    route.reopen()
    _, preview = route.prepare()
    proposal = preview["proposal"]
    applied = _execute_action(route.service, route.writer, "workflow-config-apply", {
        "proposal_id": proposal["proposal_id"], "proposal_ref": preview["proposal_ref"],
        "proposal_digest": proposal["proposal_digest"],
        "validation_result_ref": proposal["validation_result_ref"],
        "approval_ref": "owner:k571-guest", "idempotency_key": "k571:" + proposal["proposal_digest"],
    })
    assert applied["ok"], applied
    route.reopen()
    task = mark_workflow_managed_task(Task(
        id=TASK, title="K571 isolated guest in-flight recovery", contract=TaskContract(
            behavior="Deliver a report proving that fixture evidence is local.",
            verification="test -f README.md",
            acceptance_criteria=["the same Run settles once after guest interruption"],
        ),
    ))
    TaskStore(route.state / "kanban.json").add(task)
    (route.state / "testkit-worker-gate").touch()
    route.writer.append(ZfEvent(type="simulation.bootstrap", actor="testkit",
                               payload={"purpose": "k571-guest-recovery"}))
    ready = False
    try:
        runtime.start()
        proposed = WorkflowStartService(route.state, route.config, project_root=project).propose(
            route.writer, {"task_id": TASK, "route_id": "general:scope",
                           "objective": task.contract.behavior, "parameters": {"backend": "mock"}},
            actor="testkit-planner", origin="coding_agent",
        )
        assert proposed["ok"], proposed
        token = "k571-public-guest-test-token"
        result = _run_admission(
            [sys.executable, "-m", "tests.testkit.isolated_vm_cli", "workflow", "start", "--apply",
             "--proposal-event-id", proposed["proposal_event_id"],
             "--authorization-ref", "operator:k571", "--authorization-token", token,
             "--format", "json"], project=project,
            env={**runtime.env, "ZF_WORKFLOW_ACTION_TOKEN": token},
            output=output,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["ok"]
        receipts_dir = route.state / "testkit-transport"
        runtime.wait(lambda: list(receipts_dir.glob("*.json")), seconds=45)
        [receipt_path] = receipts_dir.glob("*.json")
        receipt = json.loads(receipt_path.read_text())
        assert receipt["stage_id"] == "scope" and "submission" not in receipt
        [invoke] = route.events("workflow.invoke.requested")
        [attempt] = [row for row in route.events("task.attempt.started")
                     if row.payload["dispatch_id"] == receipt["run_id"]]
        assert not route.events("workflow.call.result.admitted")
        assert not route.events("run.goal.completed")
        prefix = (route.state / "events.jsonl").read_bytes()
        checkpoint = {
            "state_dir": str(route.state), "session": runtime.session,
            "config_sha256": sha256_file(project / "zf.yaml"),
            "worker_sha256": sha256_file(output / "mock-bin/cat"),
            "workflow_run_id": invoke.payload["workflow_run_id"], "invoke_id": invoke.id,
            "first_receipt": receipt, "first_attempt": attempt.payload,
            "ledger_bytes": len(prefix), "ledger_sha256": hashlib.sha256(prefix).hexdigest(),
            "ledger_base64": base64.b64encode(prefix).decode(),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        }
        # Observation sidecar only: do not sync/fsync the business ledger or disk.
        # This marker must survive the deliberately unflushed business ledger
        # window; the guest launcher writer fsyncs the temporary sidecar before
        # replace.  It is observation only and never canonical state.
        _write_json(root / "checkpoint.json", checkpoint)
        ready = True
        return {"state": "inflight", "workflow_run_id": checkpoint["workflow_run_id"],
                "attempt": attempt.payload, "checkpoint_sha256": sha256_file(root / "checkpoint.json"),
                "ledger_sha256": checkpoint["ledger_sha256"]}
    finally:
        if not ready:
            runtime.stop()
        # On success leave the watcher and gated worker live for host QEMU kill.


def restore(root: Path, repo: Path) -> dict:
    checkpoint = json.loads((root / "checkpoint.json").read_text())
    assert checkpoint["boot_id"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    runtime = restore_runtime(root, checkpoint, repo)
    route = runtime.route
    report = {"state": "restore_failed", "workflow_run_id": checkpoint["workflow_run_id"]}
    try:
        runtime.start()  # Full CLI startup performs requeue/catchup; no parent tick.
        (route.state / "testkit-worker-release").touch()
        runtime.wait(lambda: route.events("run.goal.completed"), seconds=100)
        [terminal] = route.events("run.goal.completed")
        assert terminal.origin == "kernel"
        assert terminal.payload["workflow_run_id"] == checkpoint["workflow_run_id"]
        runtime.wait(lambda: TaskStore(route.state / "kanban.json").get(TASK).status == "done")
        runtime.wait(lambda: audit_terminal_delivery(route.state)["status"] == "passed")
        delivery = audit_terminal_delivery(route.state)
        receipts = [json.loads(path.read_text())
                    for path in (route.state / "testkit-transport").glob("*.json")]
        first = checkpoint["first_receipt"]
        assert len(receipts) == 6 and first in receipts
        submitted = [row for row in receipts if "submission" in row]
        assert len(submitted) == 5
        assert {row["stage_id"] for row in submitted} == {
            "scope", "collect-1", "collect-2", "synthesize", "verify",
        }
        [replacement] = [row for row in submitted if row["stage_id"] == "scope"]
        [new_dispatch] = [row for row in route.events("fanout.child.dispatched")
                          if row.id == replacement["dispatch_event_id"]]
        assert new_dispatch.payload["retry_of_run_id"] == first["run_id"]
        [new_attempt] = [row for row in route.events("task.attempt.started")
                         if row.payload["dispatch_id"] == replacement["run_id"]]
        old_attempt = checkpoint["first_attempt"]
        assert new_attempt.payload["operation_id"] == old_attempt["operation_id"]
        assert new_attempt.payload["attempt_id"] != old_attempt["attempt_id"]
        [superseded] = [row for row in route.events("task.attempt.superseded")
                        if row.payload["attempt_id"] == old_attempt["attempt_id"]]
        assert superseded.payload["superseded_by"] == new_attempt.payload["attempt_id"]
        admitted = route.events("workflow.call.result.admitted")
        assert len(admitted) == len({row.id for row in admitted}) == 6
        assert all(row.payload["workflow_run_id"] == checkpoint["workflow_run_id"] for row in admitted)
        assert {row["submission"]["admitted_event_id"] for row in submitted} < {row.id for row in admitted}
        [owner] = [row for row in route.events("owner.visible_message.requested")
                   if row.payload.get("terminal_event_id") == terminal.id]
        bodies = {ref: (route.state / ref).read_bytes() for ref in (
            owner.payload["dossier_ref"], owner.payload["completion_receipt_ref"],
        )}
        for receipt in submitted:
            assert sha256_file(Path(receipt["briefing_path"])) == receipt["briefing_sha256"]
        before = [row.to_json() for row in route.log.read_all() if row.type in KINDS]
        runtime.pids.update(row["pid"] for row in submitted)
        runtime.stop()
        runtime.start()
        guard = route.state / "processes/watcher.pid.json"
        for _ in range(2):
            heartbeat = json.loads(guard.read_text())["heartbeat_at"]
            runtime.wait(lambda: json.loads(guard.read_text())["heartbeat_at"] != heartbeat, seconds=20)
        assert [row.to_json() for row in route.log.read_all() if row.type in KINDS] == before
        assert all((route.state / ref).read_bytes() == body for ref, body in bodies.items())
        assert len(route.events("workflow.invoke.requested")) == 1
        assert not route.events("orchestrator.tick.failed")
        assert not route.events("kernel.housekeeping.failed")
        report.update(state="recovered", terminal_id=terminal.id, delivery=delivery,
                      first_attempt=old_attempt, new_attempt=new_attempt.payload, receipts=receipts,
                      ledger_prefix_preserved=True, terminal_restart_idempotent=True)
        return report
    finally:
        runtime.stop()
        EventWriter(route.log).append(ZfEvent(type="simulation.done", actor="testkit",
                                            payload={"purpose": "k571-guest-recovery"}))
        write_json_atomic(root / "restore-evidence.json", report)


def main() -> None:
    root, repo = Path.cwd(), Path(__file__).resolve().parents[2]
    mode = os.environ["ZF_K571_GUEST_MODE"]
    assert sorted(path.name for path in Path("/sys/class/net").iterdir()) == ["lo"]
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("ZF_DISABLE_LIVE_FEISHU", "1")
        patch.setenv("ZF_WORKSPACE_HOME", str(root / "workspace"))
        patch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        patch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        patch.setattr(Orchestrator, "run_once", lambda *_a, **_kw: pytest.fail("guest parent drove Kernel"))
        result = firstboot(root, repo, patch) if mode == "firstboot" else restore(root, repo)
    print("ZF_K571_SCENARIO " + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
