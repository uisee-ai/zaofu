"""Graceful shutdown — 10-step sequence."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.workflow.topology import WorkflowEventSets
from zf.runtime.transport import TransportAdapter


_STAGE_PROGRESS_EVENTS = WorkflowEventSets.baseline().stage_progress_events


def requeue_stale_inflight_tasks(
    state_dir: Path,
    event_log,
    *,
    source: str,
    reason: str,
) -> bool:
    """Requeue `in_progress` tasks whose current dispatch made no progress.

    For each such task, emit `task.requeued` and reset `status=backlog,
    assigned_to=None, active_dispatch_id=""`. Tasks whose current dispatch
    already produced a stage-progress event (`dev.build.done`,
    `static_gate.passed`, ...) are skipped with `task.requeue.skipped`:
    pending-handoff reconciliation owns advancing those, and requeueing
    would strand completed work back in backlog.

    Two callers, one semantic (worker sessions do not survive either):
    - graceful stop (#R cangjie 2026-05-21 stale-WIP fix)
    - zf start boot reconcile (ZF-E2E-RACING-P1 2026-07-11: a restart reset
      the review worker's pane mid-dispatch; the in-flight claim survived in
      kanban.json and no sweep shape covered it — the pipeline froze until an
      operator re-assign).

    Errors are swallowed per task: cleanup must not break the caller's
    sequence; manual intervention remains the fallback.
    """
    from zf.core.task.store import TaskStore

    kanban_path = state_dir / "kanban.json"
    if not kanban_path.exists():
        return False
    try:
        task_store = TaskStore(kanban_path)
        in_flight = task_store.filter(status="in_progress")
    except Exception:
        return False

    requeued_any = False
    for task in in_flight:
        from_assignee = task.assigned_to or ""
        from_dispatch_id = task.active_dispatch_id or ""
        try:
            progress_event = _latest_current_dispatch_progress(event_log, task)
            if progress_event is not None:
                event_log.append(ZfEvent(
                    type="task.requeue.skipped",
                    actor="zf-cli",
                    task_id=task.id,
                    payload={
                        "task_id": task.id,
                        "source": source,
                        "from_status": "in_progress",
                        "from_assignee": from_assignee,
                        "from_dispatch_id": from_dispatch_id,
                        "reason": (
                            "current dispatch already has stage-progress "
                            "evidence; preserve for restart handoff "
                            "reconciliation"
                        ),
                        "progress_event_id": progress_event.id,
                        "progress_event_type": progress_event.type,
                    },
                ))
                continue
            task_store.update(
                task.id,
                status="backlog",
                assigned_to=None,
                active_dispatch_id="",
            )
            requeued_any = True
            event_log.append(ZfEvent(
                type="task.requeued",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "task_id": task.id,
                    "source": source,
                    "from_status": "in_progress",
                    "from_assignee": from_assignee,
                    "from_dispatch_id": from_dispatch_id,
                    "to_status": "backlog",
                    "reason": reason,
                },
            ))
        except Exception:
            # Individual task failure shouldn't abort whole cleanup;
            # log nothing (no logger in this module) and move on.
            continue
    return requeued_any


def _latest_current_dispatch_progress(event_log, task: Task) -> ZfEvent | None:
    """Return the latest stage-progress event for the active dispatch.

    The scan is intentionally local to this task and ordered backward.
    A later `task.requeued` invalidates earlier progress; a matching
    `task.dispatched` without later progress means the worker was truly
    mid-turn and should be requeued.
    """
    try:
        events = event_log.events_for_task(task.id)
    except Exception:
        try:
            events = [
                event for event in event_log.read_all()
                if event.task_id == task.id
            ]
        except Exception:
            return None
    if not events:
        return None
    active_dispatch_id = task.active_dispatch_id or ""
    for event in reversed(events):
        if event.type == "task.requeued":
            return None
        if event.type == "task.dispatched":
            payload = event.payload if isinstance(event.payload, dict) else {}
            dispatch_id = str(payload.get("dispatch_id") or "")
            if not active_dispatch_id or dispatch_id == active_dispatch_id:
                return None
            continue
        if event.type not in _STAGE_PROGRESS_EVENTS:
            continue
        if _progress_matches_dispatch(event, active_dispatch_id):
            return event
    return None


def _progress_matches_dispatch(
    event: ZfEvent,
    active_dispatch_id: str,
) -> bool:
    if not active_dispatch_id:
        return True
    payload = event.payload if isinstance(event.payload, dict) else {}
    dispatch_id = str(
        payload.get("dispatch_id")
        or payload.get("actual_dispatch_id")
        or ""
    )
    return not dispatch_id or dispatch_id == active_dispatch_id


class GracefulShutdown:
    """Execute the 10-step graceful shutdown sequence."""

    def __init__(
        self,
        state_dir: Path,
        transport: TransportAdapter,
        config: ZfConfig | None = None,
        preserve_run_manager: bool = False,
    ) -> None:
        self.state_dir = state_dir
        self.transport = transport
        self.config = config
        self.preserve_run_manager = preserve_run_manager
        self.event_log = event_log_from_project(state_dir, config=config)
        self.session_store = SessionStore(state_dir / "session.yaml")
        self.steps_completed: list[str] = []

    def execute(self) -> list[str]:
        """Run the full shutdown sequence. Returns completed steps."""
        (self.state_dir / "shutdown-requested").write_text("")
        self.steps_completed.append("shutdown_marker")

        self.event_log.append(ZfEvent(type="loop.shutdown_requested", actor="zf-cli"))
        self.steps_completed.append("emit_shutdown_event")

        self.session_store.update(runtime_state="shutdown_requested")
        self.steps_completed.append("stop_dispatch")

        # Step 4: in-flight task wait — best-effort, no blocking poll in the
        # deterministic kernel. The orchestrator's _processed_event_ids set
        # plus the persisted offset (A4) means resume from this point is safe
        # even if a turn was mid-flight.
        self.steps_completed.append("wait_active_turns")

        # Step 4.5 #R fix (TR-ZF-STOP-GRACEFUL-CLEANUP-001, cangjie
        # 2026-05-21 observation-R): emit task.requeued for any
        # in_progress task before tmux kill. Without this, post-restart
        # kanban thinks worker WIP busy on stale task, but LLM session
        # is fresh (no memory of pending task) → forever stuck loop
        # of dispatch_skipped wip_busy_reassign_branch.
        # Refs: tasks/2026-05-22-0142-zf-stop-graceful-cleanup-stale-wip.md
        requeued_inflight = self._emit_stale_inflight_cleanup()
        self.steps_completed.append("stale_inflight_cleanup")
        if requeued_inflight:
            try:
                from zf.runtime.progress import regenerate_progress

                regenerate_progress(self.state_dir)
            except Exception:
                pass

        # Step 5: capture each role's recent output to .zf/logs/<role>.log
        self._save_transcripts()
        self.steps_completed.append("save_transcripts")

        # Step 6: snapshot the memory dir for cold-start recovery
        self._snapshot_memory()
        self.steps_completed.append("save_memory")

        # Step 7: snapshot current state to .zf/last-shutdown/ for
        # post-mortem inspection. (Prior version used CheckpointManager
        # but restore() was never called from production — removed
        # 2026-04-20 per backlog P1-2.)
        self._save_last_shutdown_snapshot()
        self.steps_completed.append("save_shutdown_snapshot")

        self.event_log.append(ZfEvent(type="loop.stopped", actor="zf-cli"))
        self.steps_completed.append("emit_completion")

        preserved_roles = self._apply_resident_run_manager_preserve(
            reason="graceful_stop",
        )
        self.steps_completed.append("preserve_run_manager")

        self._stop_autoresearch_sidecar()
        self.steps_completed.append("stop_autoresearch_sidecar")

        # Final flush of the in-process event index so latest_event_by_*
        # mappings observed in the dying watcher survive into the next
        # cold start.
        try:
            self.event_log.close()
        except Exception:
            pass
        self.steps_completed.append("flush_event_index")

        self.transport.shutdown(exclude_roles=preserved_roles)
        self.steps_completed.append("kill_session")

        # Phase 2.5 Bug 1: the foreground watcher process won't exit just
        # because loop.shutdown_requested was emitted — watcher.run()'s
        # loop only checks self.stopped. Kill the pid recorded in the
        # lock file so stale watchers don't accumulate across sessions.
        self._kill_watcher()
        self.steps_completed.append("kill_watcher")

        self.session_store.update(runtime_state="stopped")
        lock_path = self.state_dir / "loop.lock"
        lock_path.unlink(missing_ok=True)
        (self.state_dir / "shutdown-requested").unlink(missing_ok=True)
        self.steps_completed.append("release_lock")

        return self.steps_completed

    def execute_fast(self) -> list[str]:
        """Run a fast scoped teardown with deterministic requeue + event trail.

        This intentionally skips transcript, memory, and last-shutdown
        snapshots. It keeps the safety-critical parts of graceful shutdown:
        stop dispatch, requeue stale in-flight work, append events through the
        EventLog/EventWriter path, kill only the configured transport session,
        and release the loop lock.
        """
        (self.state_dir / "shutdown-requested").write_text("")
        self.steps_completed.append("shutdown_marker")

        self.event_log.append(ZfEvent(
            type="loop.shutdown_requested",
            actor="zf-cli",
            payload={"mode": "fast"},
        ))
        self.steps_completed.append("emit_shutdown_event")

        self.session_store.update(runtime_state="shutdown_requested")
        self.steps_completed.append("stop_dispatch")

        requeued_inflight = self._emit_stale_inflight_cleanup()
        self.steps_completed.append("stale_inflight_cleanup")
        if requeued_inflight:
            try:
                from zf.runtime.progress import regenerate_progress

                regenerate_progress(self.state_dir)
            except Exception:
                pass

        self.event_log.append(ZfEvent(
            type="run.teardown",
            actor="zf-cli",
            payload={
                "mode": "fast",
                "scope": "configured_transport_session",
                "state_dir": str(self.state_dir),
                "requeued_inflight": requeued_inflight,
            },
        ))
        self.steps_completed.append("emit_teardown_event")

        self.event_log.append(ZfEvent(
            type="loop.stopped",
            actor="zf-cli",
            payload={"mode": "fast"},
        ))
        self.steps_completed.append("emit_completion")

        preserved_roles = self._apply_resident_run_manager_preserve(
            reason="fast_stop",
        )
        self.steps_completed.append("preserve_run_manager")

        self._stop_autoresearch_sidecar()
        self.steps_completed.append("stop_autoresearch_sidecar")

        try:
            self.event_log.close()
        except Exception:
            pass
        self.steps_completed.append("flush_event_index")

        self.transport.shutdown(exclude_roles=preserved_roles)
        self.steps_completed.append("kill_session")

        self._kill_watcher()
        self.steps_completed.append("kill_watcher")

        self.session_store.update(runtime_state="stopped")
        lock_path = self.state_dir / "loop.lock"
        lock_path.unlink(missing_ok=True)
        (self.state_dir / "shutdown-requested").unlink(missing_ok=True)
        self.steps_completed.append("release_lock")

        return self.steps_completed

    def _stop_autoresearch_sidecar(self) -> None:
        """Cross-process teardown of the autoresearch resident's process
        group via its pid file (R3/R4/R5: zf stop left the resident, its loop
        subprocess and a 2h inner runner orphaned every round)."""
        try:
            from zf.runtime.autoresearch_resident_sidecar import (
                stop_autoresearch_resident_sidecar_by_pidfile,
            )

            stop_autoresearch_resident_sidecar_by_pidfile(
                self.state_dir, event_log=self.event_log,
            )
        except Exception:
            pass

    def _apply_resident_run_manager_preserve(self, *, reason: str) -> set[str]:
        if self.config is None:
            return set()
        try:
            from zf.runtime.run_manager_resident import (
                RUN_MANAGER_RESIDENT_PRESERVED,
                build_resident_preserve_payload,
                clear_resident_preserve_marker,
                write_resident_preserve_marker,
            )

            payload = None
            if self.preserve_run_manager:
                payload = build_resident_preserve_payload(
                    config=self.config,
                    state_dir=self.state_dir,
                    reason=reason,
                )
            if payload is None:
                clear_resident_preserve_marker(self.state_dir)
                return set()
            write_resident_preserve_marker(state_dir=self.state_dir, payload=payload)
            self.event_log.append(ZfEvent(
                type=RUN_MANAGER_RESIDENT_PRESERVED,
                actor="zf-cli",
                payload=payload,
            ))
            instance_id = str(payload.get("instance_id") or "").strip()
            return {instance_id} if instance_id else set()
        except Exception:
            return set()

    def _kill_watcher(self) -> None:
        """Read the lock file pid and escalate SIGTERM → SIGKILL.

        Skips if lock is missing (already stopped), pid is own process,
        or OS signalling fails (process already dead).
        """
        import os
        import signal
        import time as _time
        lock_path = self.state_dir / "loop.lock"
        if not lock_path.exists():
            return
        try:
            pid = int(lock_path.read_text().strip())
        except (OSError, ValueError):
            return
        if pid == os.getpid():
            return  # calling ourselves; caller will release lock
        # Gentle: SIGTERM + 2s grace
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        for _ in range(20):
            _time.sleep(0.1)
            try:
                os.kill(pid, 0)  # existence probe
            except ProcessLookupError:
                return
        # Still alive after 2s — forceful kill
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _save_transcripts(self) -> None:
        if self.config is None:
            return
        logs_dir = self.state_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        for role in self.config.roles:
            if role.name == "orchestrator":
                continue
            try:
                output = self.transport.capture_log(role.name, lines=2000)
            except Exception:
                continue
            if output:
                (logs_dir / f"{role.name}.log").write_text(output, encoding="utf-8")

    def _snapshot_memory(self) -> None:
        # A staleness sweep would require StalenessChecker + per-entry rewrite,
        # which is out of scope. For now we just confirm the memory dir exists
        # and leave a marker so step 7 can pick it up.
        memory_dir = self.state_dir / "memory"
        if memory_dir.exists():
            (memory_dir / ".snapshotted").touch()

    def _emit_stale_inflight_cleanup(self) -> bool:
        """#R fix: requeue stale in_progress tasks before tmux kill.

        Cangjie evidence (2026-05-21): 4 P*V* tasks stuck
        `assigned_to=review status=in_progress` for 1h+ post zf
        stop+start cycle. Logic shared with the zf start boot reconcile —
        see requeue_stale_inflight_tasks.
        """
        return requeue_stale_inflight_tasks(
            self.state_dir,
            self.event_log,
            source="graceful_stop_inflight_cleanup",
            reason=(
                "zf stop graceful — release WIP before kill tmux "
                "(#R cangjie 2026-05-21 stale-WIP fix)"
            ),
        )

    def _save_last_shutdown_snapshot(self) -> None:
        """Copy key state files + memory to .zf/last-shutdown/ for
        post-mortem. No production reader — this is a debugging aid.

        Overwrites any prior snapshot; we only keep the most recent.
        """
        snapshot_dir = self.state_dir / "last-shutdown"
        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir)
        snapshot_dir.mkdir(parents=True)

        # Core state files
        for name in ("events.jsonl", "kanban.json", "session.yaml", "cost.jsonl"):
            src = self.state_dir / name
            if src.exists():
                shutil.copy2(src, snapshot_dir / name)

        # Memory dir
        memory_src = self.state_dir / "memory"
        if memory_src.exists():
            memory_dest = snapshot_dir / "memory"
            memory_dest.mkdir(exist_ok=True)
            for item in memory_src.iterdir():
                if item.is_file():
                    shutil.copy2(item, memory_dest / item.name)
        else:
            (snapshot_dir / "memory_snapshot.txt").write_text("no memory dir\n")

        # Timestamp marker
        (snapshot_dir / "snapshot_at").write_text(
            datetime.now(timezone.utc).isoformat() + "\n"
        )
