"""Host tier for real tmux/mock-cat replacement and recovery wiring.

No Provider CLI runs. Activation, respawn, transport delivery and OS PIDs are
real; plan admission and semantic result receipts remain deterministic fixtures.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import (
    _blocking_runtime_at,
    _submit_generation_stage,
)
from tests.test_task_pipeline_generation import _git
from zf.core.config.schema import RoleConfig, RoleLifecycleConfig
from zf.core.events import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import provision_role_submit_credential
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def _live_pane(tmux: TmuxSession, instance: str) -> str:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if tmux.pane_current_command(instance) == "cat":
            pid = tmux.pane_pid(instance)
            assert pid.isdigit() and int(pid) > 1
            return pid
        time.sleep(0.05)
    raise AssertionError(f"mock-cat did not start in owned pane {instance}")


def _process_ids(tmux: TmuxSession, instance: str) -> set[str]:
    probe = tmux.pane_process_probe(instance)
    assert probe["available"]
    return {probe["pane_pid"], *(row["pid"] for row in probe["processes"])}


def _assert_gone(pids: set[str]) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        remaining = {pid for pid in pids if Path(f"/proc/{pid}").exists()}
        if not remaining:
            return
        time.sleep(0.05)
    assert not remaining, f"owned mock worker processes remain: {sorted(remaining)}"


@pytest.mark.host
@pytest.mark.serial
@pytest.mark.parametrize("interrupted_stage", ["impl", "verify"])
def test_real_tmux_run_manager_replacement_reaches_candidate(
    monkeypatch, record_property, interrupted_stage,
):
    if shutil.which("tmux") is None:
        pytest.skip("host tier requires tmux")
    original_activation = Orchestrator._ensure_role_active
    with TemporaryDirectory(prefix="zf-k478-tmux-20260923-") as directory:
        project = Path(directory)
        project.rmdir()
        runtime, _inert_transport, _admission = _blocking_runtime_at(project, monkeypatch)
        monkeypatch.setattr(Orchestrator, "_ensure_role_active", original_activation)
        state, config = runtime.state_dir, runtime.config
        config.session.tmux_session = f"zf-k478-{uuid.uuid4().hex}"
        config.roles.append(RoleConfig(
            name="verify", instance_id="verify-1", backend="mock", role_kind="reader",
            lifecycle=RoleLifecycleConfig(mode="on_demand"),
        ))
        task = runtime.task_store.get("TASK-BLOCKING")
        task.contract.scope = ["README.md"]
        task.contract.acceptance_criteria = ["README exists at pinned commit"]
        task.contract.verification = "git cat-file -e HEAD:README.md"
        task.contract.validation = {"commands": [{
            "id": "readme", "command": task.contract.verification, "rolling_smoke": True,
            "tier": "runtime", "deterministic": True, "reusable": True,
        }]}
        runtime.task_store.update(task.id, contract=task.contract)
        tokens = {role: provision_role_submit_credential(state, role)
                  for role in ("impl-1", "verify-1")}
        tmux = TmuxSession(config.session.tmux_session)
        transport = TmuxTransport(tmux)
        runtime.transport = transport
        contexts = []
        original_send = transport.send_task

        def observed_send(role, briefing, prompt, *, context=None):
            original_send(role, briefing, prompt, context=context)
            if context and context.task_pipeline_stage:
                contexts.append(context)

        monkeypatch.setattr(transport, "send_task", observed_send)
        store = TaskAttemptStore(state / "task_attempts.json")
        owned_pids: set[str] = set()
        tmux.create_session()
        try:
            runtime.run_once()
            assert len(contexts) == 1
            if interrupted_stage == "verify":
                _live_pane(tmux, "impl-1")
                owned_pids.update(_process_ids(tmux, "impl-1"))
                _submit_generation_stage(runtime, contexts[-1], tokens["impl-1"], repair=True)
                runtime.run_once()
                assert len(contexts) == 2
            first = contexts[-1]
            assert first.task_pipeline_stage == interrupted_stage
            instance = first.instance_id
            old_pid = _live_pane(tmux, instance)
            old_pids = _process_ids(tmux, instance)
            owned_pids.update(old_pids)
            # Expire only the selected lease through its canonical store;
            # retain the production clock predicate and recovery sweep.
            store.renew_lease(
                first.attempt_id, updated_at="2000-01-01T00:00:00+00:00",
                lease_expires_at="2000-01-01T00:00:00+00:00",
            )
            runtime.run_once()
            assert store.get(first.attempt_id)["status"] == "expired"
            tick = run_manager_tick(
                state_dir=state, writer=runtime.event_writer, config=config,
                project_root=project, event_log=runtime.event_log,
                action_filter={"worker-lifecycle-recover"}, spawn_repairs=False,
            )
            assert tick.actions_applied == 1
            runtime = Orchestrator(state, config, transport, project_root=project)
            runtime.run_once()
            new_pid = _live_pane(tmux, instance)
            owned_pids.update(_process_ids(tmux, instance))
            assert old_pid != new_pid
            _assert_gone(old_pids)
            assert tmux.has_session()
            assert len(contexts) == (2 if interrupted_stage == "impl" else 3)
            second = contexts[-1]
            assert second.operation_id == first.operation_id
            assert second.attempt_id != first.attempt_id
            assert second.dispatch_id != first.dispatch_id
            assert second.task_pipeline_stage == interrupted_stage
            assert store.get(first.attempt_id)["superseded_by"] == second.attempt_id
            if interrupted_stage == "impl":
                _submit_generation_stage(runtime, second, tokens["impl-1"], repair=True)
                runtime.run_once()
            assert len(contexts) == 3
            verify = contexts[-1]
            assert verify.task_pipeline_stage == "verify"
            verify_pid = _live_pane(tmux, "verify-1")
            owned_pids.update(_process_ids(tmux, "verify-1"))
            _submit_generation_stage(runtime, verify, tokens["verify-1"])
            runtime.run_once()
            assert runtime.task_store.get(task.id).status == "done"
            [ready] = [e for e in runtime.event_log.read_all() if e.type == "candidate.ready"]
            assert _git(project, "show", f"{ready.payload['commit']}:README.md") == "K462 repaired"
            for _ in range(2):
                runtime = Orchestrator(state, config, transport, project_root=project)
                runtime.run_once()
            assert len(contexts) == 3
            events = runtime.event_log.read_all()
            for kind in ("worker.respawn.requested", "worker.respawn.completed",
                         "workflow.operation.redrive_admitted", "candidate.ready"):
                assert sum(e.type == kind for e in events) == 1
            assert not any(e.type in {"kernel.housekeeping.failed", "worker.respawn.failed"}
                           for e in events)
            record_property("physical_recovery", json.dumps({
                "interrupted_stage": interrupted_stage,
                "session": tmux.session_name, "old_pid": old_pid, "new_pid": new_pid,
                "verify_pid": verify_pid, "candidate": ready.payload["commit"],
                "dispatches": len(contexts), "provider": "mock-cat",
                "owned_processes": sorted(owned_pids),
            }))
        finally:
            try:
                runtime.event_writer.append(ZfEvent(
                    type="simulation.done", actor="testkit",
                    payload={"reason": "host-test-cleanup", "session": tmux.session_name},
                ))
            finally:
                tmux.kill_session()
                assert not tmux.has_session()
                _assert_gone(owned_pids)
                record_property("cleanup", "owned session and observed process trees removed")
