"""Causal scope for readers recovering an admitted Task Pipeline failure."""
from __future__ import annotations

from zf.core.events.model import ZfEvent


TaskRecoveryScope = tuple[str, str, str]  # Run, Task, failed operation


class ReaderTaskRecoveryScopes:
    """Resolve immutable failure ancestry; report claims never create scope."""

    def __init__(self, events: list[ZfEvent]) -> None:
        self._by_id = {event.id: event for event in events}
        self._roots: dict[str, TaskRecoveryScope] = {}
        self._cache: dict[str, TaskRecoveryScope | None] = {}
        self._fanouts: dict[str, set[str]] = {}
        for event in events:
            p = event.payload
            if event.type == "fanout.started":
                self._fanouts.setdefault(str(p.get("fanout_id") or ""), set()).add(
                    str(p.get("trigger_event_id") or ""))
            if (event.type != "workflow.call.result.admitted"
                    or p.get("admission_status") != "admitted"
                    or p.get("semantic_verdict") not in {"failed", "blocked", "rejected"}):
                continue
            source = self._by_id.get(str(p.get("source_event_id") or ""))
            if source is None:
                continue
            sp = source.payload
            run = str(sp.get("workflow_run_id") or "")
            task = str(source.task_id or sp.get("task_id") or "")
            operation = str(sp.get("operation_id") or "")
            if (run and task and operation
                    and sp.get("task_pipeline_stage") in {"impl", "verify", "acceptance_review"}
                    and p.get("workflow_run_id") == run
                    and p.get("operation_id") == operation):
                self._roots[source.id] = (run, task, operation)

    def get(self, event: ZfEvent) -> TaskRecoveryScope | None:
        path: list[ZfEvent] = []
        seen: set[str] = set()
        scope = None
        while event.id not in seen:
            if event.id in self._cache:
                scope = self._cache[event.id]
                break
            seen.add(event.id)
            path.append(event)
            if event.id in self._roots:
                scope = self._roots[event.id]
                break
            p = event.payload
            source_id = str(p.get("rework_of") or "")
            if not source_id:
                origins = self._fanouts.get(str(p.get("fanout_id") or ""), set())
                if len(origins) == 1:
                    source_id = next(iter(origins))
            if not source_id:
                source_id = str(p.get("source_event_id") or event.causation_id or "")
            source = self._by_id.get(source_id)
            if source is None:
                break
            event = source
        # A request correlation can be a trace; only explicit Run fields bind it.
        for item in reversed(path):
            explicit_run = item.payload.get("workflow_run_id") or item.payload.get("run_id")
            if scope and explicit_run and explicit_run != scope[0]:
                scope = None
            self._cache[item.id] = scope
        return scope
