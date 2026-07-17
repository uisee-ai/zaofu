"""Event writing boundary for enrichment before append."""

from __future__ import annotations

import uuid
from dataclasses import asdict, replace
from typing import TYPE_CHECKING

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.projectors import EventProjector, ProjectorResult, ProjectorRunner

if TYPE_CHECKING:
    from zf.core.verification.event_schema import (
        EventSchemaRegistry,
        SchemaViolation,
    )


# TR-EVENT-SCHEMA-LOCK-001 step 2/3 (doc 42 §11.3 A): meta events that
# describe schema-validation results must NEVER themselves be schema-
# validated. Recursion protection — without this, a discriminator.failed
# emitted because event X violated schema could itself violate schema
# and trigger another discriminator.failed indefinitely.
_SCHEMA_META_EVENT_TYPES = frozenset({
    "event.schema.violated",
    "discriminator.failed",
})

_BLOCKED_EVENT_IDENTITY_KEYS = (
    "workflow_run_id",
    "run_id",
    "trace_id",
    "fanout_id",
    "child_id",
    "lane_id",
    "stage_id",
    "stage_slot",
    "attempt_id",
    "task_map_generation",
    "contract_revision",
)


class EventWriter:
    """Thin append boundary over EventLog.

    It deliberately preserves the current event schema. The optional
    correlation context is the first enrichment hook; signing remains
    owned by the EventLog instance built by the factory.

    TR-EVENT-SCHEMA-LOCK-001 step 2/3:
    Optionally validates event payload against a schema registry. Mode
    is ``disabled`` by default — when caller passes a registry + non-
    disabled mode, ``warning`` mode appends an extra
    ``event.schema.violated`` event alongside the original; ``blocking``
    mode replaces the original with ``discriminator.failed``.
    """

    def __init__(
        self,
        event_log: EventLog,
        *,
        correlation_id: str | None = None,
        schema_registry: "EventSchemaRegistry | None" = None,
        schema_mode: str = "disabled",
        default_origin: str = "",
        projector_runner: ProjectorRunner | None = None,
    ) -> None:
        self.event_log = event_log
        self.correlation_id = correlation_id
        # 1405:按调用方标注事件 origin(kernel/worker/external);事件
        # 已自带 origin 时不覆盖(显式最高),不信任 payload 自报。
        self.default_origin = default_origin
        if schema_mode not in {"disabled", "warning", "blocking"}:
            raise ValueError(
                f"schema_mode must be disabled / warning / blocking; "
                f"got {schema_mode!r}"
            )
        self._schema_registry = schema_registry
        self._schema_mode = schema_mode
        self._projector_runner = projector_runner or default_projector_runner()
        self.projector_diagnostics: list[ProjectorResult] = []

    def append(self, event: ZfEvent) -> ZfEvent:
        self.projector_diagnostics = []
        if self.default_origin and not getattr(event, "origin", ""):
            event.origin = self.default_origin
        enriched = self._enrich(event)

        # TR-EVENT-SCHEMA-LOCK-001 step 2/3: schema gate
        if (
            self._schema_registry is not None
            and self._schema_mode != "disabled"
            and enriched.type not in _SCHEMA_META_EVENT_TYPES
        ):
            violations = self._schema_registry.validate(enriched)
            if violations:
                if self._schema_mode == "blocking":
                    return self._emit_discriminator_failed(enriched, violations)
                # warning mode: write original first, then write the warning
                self._append_with_projectors(enriched)
                self._emit_schema_violated_warning(enriched, violations)
                return enriched

        self._append_with_projectors(enriched)
        return enriched

    # --------------------------------------------------------------- helpers

    def _append_with_projectors(self, event: ZfEvent) -> None:
        self.event_log.append(event)
        self.projector_diagnostics.extend(
            self._projector_runner.run(self.event_log, event)
        )

    def _emit_schema_violated_warning(
        self,
        event: ZfEvent,
        violations: "list[SchemaViolation]",
    ) -> None:
        """Append a non-blocking ``event.schema.violated`` event.

        The original event is unchanged on disk; this warning sits next to
        it in the log so operators / dashboards can surface the failure
        without disturbing existing automation."""
        warning = ZfEvent(
            type="event.schema.violated",
            actor="zf-cli",
            task_id=event.task_id,
            causation_id=event.id,
            correlation_id=event.correlation_id,
            payload={
                "violated_event_id": event.id,
                "violated_event_type": event.type,
                "violations": [asdict(v) for v in violations],
                "mode": "warning",
            },
        )
        self._append_with_projectors(warning)

    def _emit_discriminator_failed(
        self,
        event: ZfEvent,
        violations: "list[SchemaViolation]",
    ) -> ZfEvent:
        """REPLACE the original event with ``discriminator.failed``.

        Used in blocking mode. The original event is not written to the log
        — its payload is preserved in the discriminator.failed payload so
        operators can reconstruct what the worker tried to emit."""
        blocked_payload = event.payload if isinstance(event.payload, dict) else {}
        identity = {
            key: blocked_payload[key]
            for key in _BLOCKED_EVENT_IDENTITY_KEYS
            if key in blocked_payload
        }
        failure = ZfEvent(
            type="discriminator.failed",
            actor="zf-cli",
            task_id=event.task_id,
            causation_id=event.causation_id,
            correlation_id=event.correlation_id,
            payload={
                **identity,
                "failed_d": ["EventSchemaD"],
                "blocked_event_id": event.id,
                "blocked_event_type": event.type,
                "blocked_event_payload": blocked_payload,
                "violations": [asdict(v) for v in violations],
                "mode": "blocking",
            },
        )
        self._append_with_projectors(failure)
        return failure

    def emit(
        self,
        event_type: str,
        *,
        actor: str | None = None,
        task_id: str | None = None,
        payload: dict | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ZfEvent:
        return self.append(ZfEvent(
            type=event_type,
            actor=actor,
            task_id=task_id,
            payload=payload or {},
            causation_id=causation_id,
            correlation_id=correlation_id,
        ))

    def _enrich(self, event: ZfEvent) -> ZfEvent:
        causation_id = event.causation_id
        correlation_id = event.correlation_id

        if correlation_id is None and self.correlation_id is not None:
            correlation_id = self.correlation_id

        if event.type == "user.message" and correlation_id is None:
            correlation_id = _new_trace_id()

        parent: ZfEvent | None = None
        if causation_id is not None:
            parent = self._event_by_id(causation_id)
        elif event.task_id is not None and event.type != "task.created":
            parent = self._latest_task_event(event.task_id)
            if parent is not None:
                causation_id = parent.id

        if correlation_id is None and parent is not None:
            correlation_id = parent.correlation_id

        if causation_id != event.causation_id or correlation_id != event.correlation_id:
            return replace(
                event,
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
        return event

    def _event_by_id(self, event_id: str) -> ZfEvent | None:
        # Cheap path: the EventLog's in-process index caches the full
        # event payload, so we don't need to touch the file at all.
        # Fall back to a full reverse scan only when the index has not
        # seen the id (cold start, eviction, external writer).
        index = getattr(self.event_log, "index", None)
        if index is not None:
            cached = index.lookup_event(event_id)
            if cached is not None:
                return cached
        for event in reversed(self.event_log.read_all()):
            if event.id == event_id:
                return event
        return None

    def _latest_task_event(self, task_id: str) -> ZfEvent | None:
        # Cheap path: index records the latest event per task, return it
        # directly without reading the log.
        index = getattr(self.event_log, "index", None)
        if index is not None:
            cached = index.latest_event_for_task(task_id)
            if cached is not None:
                return cached
        for event in reversed(self.event_log.read_all()):
            if event.task_id == task_id:
                return event
        return None


def _new_trace_id() -> str:
    return f"trace-{uuid.uuid4().hex[:12]}"


def default_projector_runner() -> ProjectorRunner:
    return ProjectorRunner((
        EventProjector(
            name="fanout_manifest",
            handler=_project_fanout_manifest,
            event_filter=lambda event: event.type.startswith("fanout."),
        ),
        EventProjector(
            name="stage_report",
            handler=_project_stage_report,
        ),
    ))


def _project_fanout_manifest(event_log: EventLog, event: ZfEvent) -> None:
    from zf.runtime.fanout import FanoutManifestProjector

    FanoutManifestProjector(
        event_log.path.parent,
    ).project_event(event_log, event)


def _project_stage_report(event_log: EventLog, event: ZfEvent) -> None:
    from zf.runtime.stage_reports import project_stage_report_for_event

    project_stage_report_for_event(
        event_log.path.parent,
        event,
    )
