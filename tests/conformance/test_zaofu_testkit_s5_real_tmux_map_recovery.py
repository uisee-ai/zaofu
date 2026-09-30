"""Approved recovery of an expired old-map verify over a real tmux worker."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import (
    _blocking_runtime_at,
    _submit_generation_stage,
)
from tests.conformance.test_zaofu_testkit_s3_old_verify_replacement import (
    _expired_old_verify_handover,
)
from tests.conformance.test_zaofu_testkit_s5_real_tmux_recovery import (
    _assert_gone,
    _live_pane,
    _process_ids,
)
from tests.test_task_pipeline_generation import _git
from zf.core.events import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import ResultSubmitError
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport
from zf.runtime.workflow_operation import reduce_workflow_operations


@pytest.mark.host
@pytest.mark.serial
def test_approved_old_verify_recovery_releases_new_map_slot(monkeypatch, record_property):
    if shutil.which("tmux") is None:
        pytest.skip("host tier requires tmux")
    activation = Orchestrator._ensure_role_active
    with TemporaryDirectory(prefix="zf-k483-map-recovery-20260923-") as directory:
        project = Path(directory)
        project.rmdir()
        runtime, _inert, admission = _blocking_runtime_at(project, monkeypatch)
        monkeypatch.setattr(Orchestrator, "_ensure_role_active", activation)
        state, config = runtime.state_dir, runtime.config
        config.session.tmux_session = f"zf-k483-{uuid.uuid4().hex}"
        tmux = TmuxSession(config.session.tmux_session)
        transport = TmuxTransport(tmux)
        runtime.transport = transport
        contexts = []
        transport.contexts = contexts
        owned_pids: set[str] = set()
        original_send = transport.send_task

        def observed_send(role, briefing, prompt, *, context=None):
            _live_pane(tmux, role)
            owned_pids.update(_process_ids(tmux, role))
            original_send(role, briefing, prompt, context=context)
            if context and context.task_pipeline_stage:
                contexts.append(context)

        monkeypatch.setattr(transport, "send_task", observed_send)
        try:
            tmux.create_session()
            runtime, old_verify, tokens = _expired_old_verify_handover(
                (runtime, transport, admission),
            )
            assert len(contexts) == 3
            old_pid = _live_pane(tmux, "verify-1")
            old_pids = _process_ids(tmux, "verify-1")
            owned_pids.update(old_pids)
            tick = run_manager_tick(
                state_dir=state, writer=runtime.event_writer, config=config,
                project_root=project, event_log=runtime.event_log,
                action_filter={"worker-lifecycle-recover"}, spawn_repairs=False,
            )
            assert tick.actions_applied == 1
            [request] = [e for e in runtime.event_log.read_all()
                         if e.type == "worker.respawn.requested"]
            assert request.payload["operation_id"] == old_verify.operation_id
            runtime = Orchestrator(state, config, transport, project_root=project)
            runtime.run_once()
            new_pid = _live_pane(tmux, "verify-1")
            owned_pids.update(_process_ids(tmux, "verify-1"))
            assert old_pid != new_pid
            _assert_gone(old_pids)
            for _ in range(2):
                runtime = Orchestrator(state, config, transport, project_root=project)
                runtime.run_once()
            events = runtime.event_log.read_all()
            operations = reduce_workflow_operations(events)
            redrives = [e for e in events if e.type == "workflow.operation.redrive_admitted"
                        and e.payload.get("operation_id") == old_verify.operation_id]
            waits = [e for e in events if e.type == "task.pipeline.stage.waiting"
                     and e.payload.get("task_map_generation") == "map-live-verify-new"]
            record_property("cross_map_recovery", json.dumps({
                "old_pid": old_pid, "new_pid": new_pid,
                "old_operation": operations[old_verify.operation_id],
                "dispatch_count": len(contexts),
                "redrive_ids": [e.id for e in redrives],
                "waits": [e.payload for e in waits],
            }))
            assert len(contexts) == 4
            new_verify = contexts[-1]
            assert new_verify.operation_id != old_verify.operation_id
            assert new_verify.task_pipeline_stage == "verify"
            assert operations[new_verify.operation_id]["task_map_generation"] == "map-live-verify-new"
            before = reduce_workflow_operations(runtime.event_log.read_all())
            with pytest.raises(ResultSubmitError) as late:
                _submit_generation_stage(runtime, old_verify, tokens["verify-1"])
            assert late.value.code == "operation_not_running"
            assert reduce_workflow_operations(runtime.event_log.read_all()) == before
            _submit_generation_stage(runtime, new_verify, tokens["verify-1"])
            runtime.run_once()
            [candidate] = [e for e in runtime.event_log.read_all() if e.type == "candidate.ready"]
            assert candidate.payload["task_map_generation"] == "map-live-verify-new"
            assert _git(project, "show", f"{candidate.payload['commit']}:README.md") == "K462 repaired"
            assert runtime.task_store.get("TASK-BLOCKING").status == "done"
            for _ in range(2):
                runtime = Orchestrator(state, config, transport, project_root=project)
                runtime.run_once()
            assert len(contexts) == 4
            for kind in ("worker.respawn.requested", "worker.respawn.completed", "candidate.ready"):
                assert sum(e.type == kind for e in runtime.event_log.read_all()) == 1
            assert not any(e.type in {"kernel.housekeeping.failed", "worker.respawn.failed"}
                           for e in runtime.event_log.read_all())
            record_property("candidate", json.dumps({
                "event_id": candidate.id, "commit": candidate.payload["commit"],
                "task_map_generation": candidate.payload["task_map_generation"],
                "pipeline_dispatch_count": len(contexts),
            }))
        finally:
            try:
                runtime.event_writer.append(ZfEvent(
                    type="simulation.done", actor="testkit",
                    payload={"reason": "cross-map-recovery-cleanup", "session": tmux.session_name},
                ))
            finally:
                tmux.kill_session()
                assert not tmux.has_session()
                _assert_gone(owned_pids)
                record_property("cleanup", "owned session and observed process trees removed")
