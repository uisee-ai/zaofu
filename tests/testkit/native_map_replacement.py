"""Owned Provider pause and Given map replacement; production owns recovery.

The scheduler observer delegates unchanged. No result, expiry, respawn, redrive
or terminal event is authored here. Only Plan input and a selected lease clock
are injected; the old-operation probe tests its status gate, not semantic validity.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from tests.test_plan_artifact_package import _package
from tests.testkit.native_pipeline_recovery import (
    _owned_claude_pid, _stop_owned_provider, transcript_rows,
)
from zf.core.events import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.plan_artifact_package import package_event_payload, write_plan_artifact_package
from zf.runtime.result_submit import ResultSubmitError, SemanticResultSubmitService
from zf.runtime.session_tailer import claude_session_path
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler
from zf.runtime.task_pipeline_runtime import admit_task_pipeline_generation
from zf.runtime.workflow_operation import reduce_workflow_operations


NEW_MAP = "map-native-replacement"
EXPIRED_CLOCK = "2000-01-01T00:00:00+00:00"


def install_scheduler_observer(monkeypatch, observer):
    """Capture the actual scheduler result, including old-map slot occupancy."""
    original = TaskPipelineReconciler.reconcile

    def observed(reconciler, **inputs):
        projection = original(reconciler, **inputs)
        observer.observe_schedule(projection)
        return projection

    monkeypatch.setattr(TaskPipelineReconciler, "reconcile", observed)


class NativeMapReplacement:
    def __init__(self, runtime, tmux, record_property, *, old_stage="impl"):
        assert old_stage in {"impl", "verify"}
        self.runtime, self.tmux, self.record = runtime, tmux, record_property
        self.old_stage = old_stage
        self.expected_contexts = 3 if old_stage == "impl" else 4
        self.old = None
        self.old_pids: set[str] = set()
        self.admission = None
        self.schedule = None
        self.clock_injected = False
        self.handover = None
        self.fault: dict = {}
        self.prior_impl = None

    def observe_schedule(self, projection):
        if self.admission is None or self.clock_injected:
            return
        for task in projection.get("tasks", []):
            if (task["task_id"] == self.old.task_id
                    and task["stage"] == f"{self.old_stage}_ready"
                    and "no_compatible_idle_role" in task.get("blockers", [])):
                assert projection["dispatchable"][self.old_stage] == []
                self.schedule = {
                    "input_digest": projection["input_digest"],
                    "task": dict(task), "occupancy": dict(projection["occupancy"]),
                }

    def poll(self, contexts, events):
        store = TaskAttemptStore(self.runtime.state_dir / "task_attempts.json")
        if self.old is None:
            self._pause_and_admit(contexts, events, store)
            return
        if not self.clock_injected:
            assert not any(c.task_map_generation == NEW_MAP
                           and c.task_pipeline_stage == self.old_stage for c in contexts), \
                "new map stole a live old stage slot"
            active_old = [
                context for context in contexts
                if context.task_map_generation == self.old.task_map_generation
                and store.get(context.attempt_id)["status"] in {"prepared", "delivering", "sent"}
            ]
            assert active_old == [self.old], "new map stole a live old stage slot"
            assert store.get(self.old.attempt_id)["status"] == "sent"
            assert reduce_workflow_operations(events)[self.old.operation_id]["status"] == "running"
            if self.schedule is None:
                return
            if self.old_stage == "verify":
                [new_impl] = [c for c in contexts if c.task_map_generation == NEW_MAP
                              and c.task_pipeline_stage == "impl"]
                assert store.get(new_impl.attempt_id)["status"] == "succeeded"
                self._check_prior_impl(events, store)
            self.record("native_map_live_wait", json.dumps(self.schedule))
            self.clock_injected = True
        attempt = store.get(self.old.attempt_id)
        # Buffered usage from before SIGSTOP must not restore a long lease.
        if attempt["status"] == "sent" and attempt["lease_expires_at"] != EXPIRED_CLOCK:
            store.renew_lease(self.old.attempt_id, updated_at=EXPIRED_CLOCK,
                              lease_expires_at=EXPIRED_CLOCK)
        replacements = [c for c in contexts if c.task_map_generation == NEW_MAP
                        and c.task_pipeline_stage == self.old_stage]
        if replacements and self.handover is None:
            [new] = replacements
            if any(e.type == "task.pipeline.stage.dispatched"
                   and e.payload.get("attempt_id") == new.attempt_id for e in events):
                self._check_handover(new, events, store)

    def _pause_and_admit(self, contexts, events, store):
        if not contexts:
            return
        candidates = [context for context in contexts
                      if context.task_pipeline_stage == self.old_stage
                      and context.task_map_generation == "map-current"]
        if not candidates:
            return
        [old] = candidates
        assert len(contexts) == (1 if self.old_stage == "impl" else 2)
        dispatches = [e for e in events if e.type == "task.pipeline.stage.dispatched"
                      and e.payload.get("attempt_id") == old.attempt_id]
        if not dispatches:
            return
        [dispatch] = dispatches
        assert store.get(old.attempt_id)["status"] == "sent", "missed in-flight map switch"
        registry = RoleSessionRegistry(self.runtime.state_dir / "role_sessions.yaml",
                                       str(self.runtime.project_root))
        binding = registry.task_stage_bindings()[old.task_stage_session_binding]
        workdir = Path(dispatch.payload["workdir"])
        transcript = claude_session_path(str(workdir), binding["session_id"])
        assistants = [r for r in transcript_rows(transcript)
                      if r.get("type") == "assistant" and r.get("sessionId") == binding["session_id"]]
        if not assistants:
            return
        assert reduce_workflow_operations(events)[old.operation_id]["status"] == "running"
        pid = _owned_claude_pid(self.tmux, old.instance_id, workdir)
        probe = self.tmux.pane_process_probe(old.instance_id, max_entries=64)
        self.old_pids = {str(probe["pane_pid"]), *(str(p["pid"]) for p in probe["processes"])}
        _stop_owned_provider(pid)
        self.old = old
        # Detect a submit racing the signal; never call that an in-flight pause.
        assert store.get(old.attempt_id)["status"] == "sent"
        assert reduce_workflow_operations(self.runtime.event_log.read_all())[old.operation_id]["status"] == "running"
        self.fault = {
            "fault": "owned-provider-SIGSTOP", "provider_pid": pid,
            "owned_pids": sorted(self.old_pids), "assistant_count": len(assistants),
            "transcript": str(transcript), "old_session_id": binding["session_id"],
            "old_map": old.task_map_generation, "old_operation_id": old.operation_id,
            "old_attempt_id": old.attempt_id, "old_workdir": str(workdir),
            "old_lease_before": store.get(old.attempt_id)["lease_expires_at"],
        }
        if self.old_stage == "verify":
            [impl] = [c for c in contexts if c.task_pipeline_stage == "impl"
                      and c.task_map_generation == old.task_map_generation]
            row = store.get(impl.attempt_id)
            assert row["status"] == "succeeded"
            settled = [e.id for e in events if e.type == "workflow.operation.settled"
                       and e.payload.get("operation_id") == impl.operation_id]
            assert len(settled) == 1
            self.prior_impl = {"attempt": row, "operation_id": impl.operation_id,
                               "settlement_ids": settled,
                               "target_commit": dispatch.payload["target_commit"]}
            self._check_prior_impl(events, store)
        self.record("native_map_fault", json.dumps(self.fault))
        self.admission = self._admit_given_map(events)

    def _check_prior_impl(self, events, store):
        prior = self.prior_impl
        assert store.get(prior["attempt"]["attempt_id"]) == prior["attempt"]
        assert [e.id for e in events if e.type == "workflow.operation.settled"
                and e.payload.get("operation_id") == prior["operation_id"]] == prior["settlement_ids"]
        head = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=self.fault["old_workdir"], timeout=10).decode().strip()
        assert head == prior["target_commit"], "new map changed old verify's pinned workspace"

    def _admit_given_map(self, events):
        runtime, old = self.runtime, self.old
        [prior] = [e for e in events if e.type == "task.pipeline.generation.admitted"
                   and e.payload.get("task_map_generation") == old.task_map_generation]
        package = _package(runtime.state_dir, revision="native-replacement-r2", generation=NEW_MAP)
        descriptor = write_plan_artifact_package(runtime.state_dir, package)
        identity = {
            "plan_artifact_package_id": descriptor["package_id"],
            "plan_artifact_package_ref": descriptor["ref"],
            "plan_artifact_package_digest": descriptor["sha256"],
        }
        runtime.event_writer.append(ZfEvent(
            type="plan.artifact_package.admitted", correlation_id=old.run_id,
            payload=package_event_payload(package, descriptor, status="admitted"),
        ))
        task = runtime.task_store.get(old.task_id)
        task.contract.evidence_contract.update(task_map_generation=NEW_MAP, source_refs=identity)
        runtime.task_store.update(task.id, contract=task.contract)
        trigger = runtime.event_writer.append(ZfEvent(
            type="task_map.ready", correlation_id=old.run_id,
            payload={"task_map_generation": NEW_MAP},
        ))
        admitted = runtime.event_writer.append(ZfEvent(
            type="task_map.admitted", correlation_id=old.run_id, causation_id=trigger.id,
            payload={"task_map_generation": NEW_MAP, "task_ids": [task.id]},
        ))
        return admit_task_pipeline_generation(
            runtime, trigger_event=trigger, task_map_admitted_event=admitted,
            stage_id="impl", trace_id=old.run_id, task_items=[{"task_id": task.id}],
            loaded=SimpleNamespace(workflow_run_id=old.run_id, task_map_generation=NEW_MAP,
                                   dispatch_base_commit=prior.payload["dispatch_base_commit"], **identity),
        )

    def _check_handover(self, new, events, store):
        old = self.old
        assert new.operation_id != old.operation_id and new.attempt_id != old.attempt_id
        assert new.dispatch_id != old.dispatch_id and new.lease_id != old.lease_id
        [dispatch] = [e for e in events if e.type == "task.pipeline.stage.dispatched"
                      and e.payload.get("attempt_id") == new.attempt_id]
        ids = [e.id for e in events]
        requests = [e for e in events if e.type == "worker.respawn.requested"
                    and e.payload.get("attempt_id") == old.attempt_id]
        assert len(requests) == 1, "missing exact Run Manager recovery request"
        request = requests[0]
        assert request.actor == "run-manager"
        completed_rows = [e for e in events if e.type == "worker.respawn.completed"
                          and e.causation_id == request.id]
        redrive_rows = [e for e in events if e.type == "workflow.operation.redrive_admitted"
                       and e.payload.get("source_attempt_id") == old.attempt_id]
        assert len(completed_rows) == len(redrive_rows) == 1, "incomplete recovery authorization"
        completed, redrive = completed_rows[0], redrive_rows[0]
        assert redrive.payload["recovery_decision_event_id"] == request.id
        ordered = [self.admission.id, request.id, completed.id, redrive.id, dispatch.id]
        assert [ids.index(identity) for identity in ordered] == sorted(ids.index(identity) for identity in ordered)
        assert dispatch.payload["workdir"] != self.fault["old_workdir"]
        # Checked during watcher execution, BEFORE simulation shutdown/finally.
        remaining = [pid for pid in self.old_pids if Path(f"/proc/{pid}").exists()]
        assert not remaining, f"old owned processes survived production handover: {remaining}"
        assert store.get(old.attempt_id)["status"] == "expired"
        before = self.runtime.event_log.read_all()
        operations = reduce_workflow_operations(before)
        assert operations[old.operation_id]["status"] != "running"
        assert operations[new.operation_id]["task_map_generation"] == NEW_MAP
        task_before = self.runtime.task_store.get(old.task_id)
        attempts_before = store.current_rows()
        try:
            SemanticResultSubmitService(
                state_dir=self.runtime.state_dir, event_log=self.runtime.event_log,
                event_writer=self.runtime.event_writer,
            ).submit(operation_id=old.operation_id, role_instance=old.instance_id,
                     credential="unused-operation-status-probe", semantic_result={})
        except ResultSubmitError as exc:
            assert exc.code == "operation_not_running", exc.code
        else:
            raise AssertionError("old operation accepted a late result")
        assert self.runtime.event_log.read_all() == before
        assert self.runtime.task_store.get(old.task_id) == task_before
        assert store.current_rows() == attempts_before
        self.handover = {
            "new_map": NEW_MAP, "new_operation_id": new.operation_id,
            "new_attempt_id": new.attempt_id, "new_workdir": dispatch.payload["workdir"],
            "request_id": request.id, "completed_id": completed.id, "redrive_id": redrive.id,
            "dispatch_id": dispatch.id, "old_attempt_status": "expired",
            "old_operation_status": operations[old.operation_id]["status"],
            "old_pids_gone_before_shutdown": True,
            "late_result_code": "operation_not_running", "late_probe_no_state_change": True,
        }
        self.record("native_map_handover", json.dumps(self.handover))

    def verify(self, contexts, events):
        assert self.old is not None and self.schedule is not None and self.handover is not None
        assert len(contexts) == self.expected_contexts
        completed = [c for c in contexts if c.task_map_generation == NEW_MAP]
        assert [c.task_pipeline_stage for c in completed] == ["impl", "verify"]
        assert not any(e.type in {"workflow.operation.settled", "dev.build.done"}
                       and e.payload.get("operation_id") == self.old.operation_id for e in events)
        [candidate] = [e for e in events if e.type == "candidate.ready"]
        assert candidate.payload["task_map_generation"] == NEW_MAP
        store = TaskAttemptStore(self.runtime.state_dir / "task_attempts.json")
        assert all(store.get(c.attempt_id)["status"] == "succeeded" for c in completed)
        if self.prior_impl is not None:
            self._check_prior_impl(events, store)
            self.record("native_map_prior_impl", json.dumps(self.prior_impl))
        self.record("native_map_replacement", json.dumps({**self.fault, **self.handover,
                                                         "candidate_id": candidate.id}))
        return completed
