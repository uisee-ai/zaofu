"""Bounded OTLP/HTTP export of redacted EventLog projections.

This module is deliberately outside the deterministic workflow path.  It reads
the canonical ledger, persists only exporter progress, and sends a sanitized
OTLP JSON batch in a daemon tick.  Export failure cannot modify TaskStore,
Gate, Judge, or provider dispatch behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import FileLock
from zf.runtime.operations_metrics import OperationsMetricsRegistry
from zf.runtime.runtime_logs import write_runtime_log


_SCHEMA_VERSION = "otlp-exporter.v1"
_MAX_ROOT_TRACES = 512
_MAX_OPERATION_TRACES = 1_024
_MAX_EVENT_IDS = 512
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_INTERNAL_PREFIXES = ("telemetry.exporter.", "runtime.attention.")
_CRITICAL_MARKERS = (
    "failed", "blocked", "timeout", "timed_out", "rejected", "replan",
    "autoresearch", "recovery", "security", "permission", "denied",
)
_NOISY_PREFIXES = (
    "agent.thinking", "agent.text", "agent.tool.", "agent.usage",
    "stream.", "worker.heartbeat", "progress.",
)
_SAFE_PAYLOAD_FIELDS = {
    "run_id": "zaofu.run_id",
    "workflow_run_id": "zaofu.workflow_run_id",
    "dispatch_id": "zaofu.dispatch_id",
    "attempt_id": "zaofu.attempt_id",
    "stage_id": "zaofu.stage_id",
    "task_pipeline_stage": "zaofu.stage",
    "provider": "zaofu.provider",
    "backend": "zaofu.backend",
    "role": "zaofu.role",
    "instance_id": "zaofu.role_instance_id",
    "failure_class": "zaofu.failure_class",
    "operation_kind": "zaofu.operation_kind",
    "route": "zaofu.route",
    "result": "zaofu.result",
    "status": "zaofu.status",
    "effective": "zaofu.telemetry_effective",
    "join_kind": "zaofu.join_kind",
}

HttpSender = Callable[[str, bytes, dict[str, str], float], int]


@dataclass(frozen=True)
class OtlpExportResult:
    status: str
    batch_id: str = ""
    exported_events: int = 0
    exported_spans: int = 0
    backlog_events: int = 0
    failure_class: str = ""


def otlp_exporter_state_path(state_dir: Path) -> Path:
    return Path(state_dir) / "projections" / "otlp_exporter.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _now_epoch() -> float:
    return time.time()


class OtlpExporter:
    """Read-only EventLog exporter with durable pending batch state."""

    def __init__(
        self,
        *,
        state_dir: Path,
        event_log: EventLog,
        config: Any | None = None,
        event_writer: EventWriter | None = None,
        metrics: OperationsMetricsRegistry | None = None,
        runtime_logs_enabled: bool = True,
        project_id: str = "",
        sender: HttpSender | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.event_log = event_log
        self.config = config
        self.event_writer = event_writer
        self.metrics = metrics
        self.runtime_logs_enabled = runtime_logs_enabled
        self.project_id = str(project_id or "")
        self.sender = sender or _send_otlp_http

    def run_once(self) -> OtlpExportResult:
        if not bool(getattr(self.config, "enabled", False)):
            return OtlpExportResult(status="disabled")
        endpoint = _endpoint_from_env(self.config)
        if not endpoint:
            self._record_configuration_failure("endpoint_env_missing")
            return OtlpExportResult(status="degraded", failure_class="endpoint_env_missing")
        headers, header_error = _headers_from_env(self.config)
        if header_error:
            self._record_configuration_failure(header_error)
            return OtlpExportResult(status="degraded", failure_class=header_error)

        prepared = self._prepare_pending_batch()
        if isinstance(prepared, OtlpExportResult):
            return prepared
        pending, state = prepared
        if not pending:
            return OtlpExportResult(
                status="idle",
                backlog_events=int(state.get("backlog_events") or 0),
            )

        batch_id = str(pending.get("batch_id") or "")
        body = json.dumps(
            pending.get("body") or {}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        outbound_headers = {
            "Content-Type": "application/json",
            "User-Agent": "zaofu-otlp-exporter/1",
            "Idempotency-Key": batch_id,
            **headers,
        }
        timeout_s = float(getattr(self.config, "request_timeout_seconds", 3.0) or 3.0)
        try:
            status_code = self.sender(endpoint, body, outbound_headers, timeout_s)
            if not 200 <= status_code < 300:
                raise OtlpExportError(f"otlp_http_{status_code}")
        except Exception as exc:
            failure_class = _failure_class(exc)
            self._mark_failure(batch_id, failure_class)
            return OtlpExportResult(
                status="degraded",
                batch_id=batch_id,
                exported_events=len(pending.get("event_ids") or []),
                exported_spans=int(pending.get("span_count") or 0),
                backlog_events=int(state.get("backlog_events") or 0),
                failure_class=failure_class,
            )

        self._mark_success(batch_id)
        return OtlpExportResult(
            status="exported",
            batch_id=batch_id,
            exported_events=len(pending.get("event_ids") or []),
            exported_spans=int(pending.get("span_count") or 0),
            backlog_events=int(state.get("backlog_events") or 0),
        )

    def _prepare_pending_batch(
        self,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]] | OtlpExportResult:
        now = _now_epoch()
        path = otlp_exporter_state_path(self.state_dir)
        lock = path.with_name(path.name + ".lock")
        with FileLock(lock):
            state = _load_state(path)
            pending = state.get("pending") if isinstance(state.get("pending"), dict) else {}
            inflight_until = float(state.get("inflight_until") or 0.0)
            if pending and inflight_until > now:
                return OtlpExportResult(
                    status="inflight",
                    batch_id=str(pending.get("batch_id") or ""),
                    backlog_events=int(state.get("backlog_events") or 0),
                )
            next_attempt_at = float(state.get("next_attempt_at") or 0.0)
            if pending and next_attempt_at > now:
                return OtlpExportResult(
                    status="backoff",
                    batch_id=str(pending.get("batch_id") or ""),
                    backlog_events=int(state.get("backlog_events") or 0),
                )
            next_export_at = float(state.get("next_export_at") or 0.0)
            if not pending and next_export_at > now:
                return OtlpExportResult(
                    status="idle",
                    backlog_events=int(state.get("backlog_events") or 0),
                )
            if not pending:
                events = self.event_log.read_all()
                source = _events_after_cursor(events, str(state.get("cursor_event_id") or ""))
                source_count = len(source)
                state["backlog_events"] = source_count
                if not source:
                    state["health"] = "healthy"
                    state["next_export_at"] = now + _interval_seconds(self.config)
                    _write_state(path, state)
                    return None, state
                source = source[:_batch_size(self.config)]
                pending = _build_pending_batch(
                    source,
                    emitted_root_traces=_string_list(state.get("emitted_root_traces")),
                    operation_trace_ids=_operation_trace_map(
                        state.get("operation_trace_ids")
                    ),
                    sample_rate=_controller_sample_rate(self.state_dir, self.config),
                    project_id=self.project_id,
                )
                policy = pending.get("policy") if isinstance(pending.get("policy"), dict) else {}
                _increment_counter(state, "dropped_by_policy", int(policy.get("dropped") or 0))
                _increment_counter(state, "sampled_out", int(policy.get("sampled_out") or 0))
                _increment_counter(state, "summary_events", int(policy.get("summary") or 0))
                _increment_counter(state, "redacted_fields", int(policy.get("redacted") or 0))
                if not pending.get("spans"):
                    state["cursor_event_id"] = str(pending.get("tail_event_id") or "")
                    state["backlog_events"] = max(0, source_count - len(source))
                    state["next_export_at"] = now + _interval_seconds(self.config)
                    state["health"] = "healthy"
                    _write_state(path, state)
                    return OtlpExportResult(
                        status="filtered",
                        backlog_events=int(state["backlog_events"]),
                    )
                pending["body"] = _otlp_request_body(pending.get("spans") or [])
                pending["attempt"] = 0
                pending["created_at"] = _now_iso()
                state["pending"] = pending
            pending = dict(pending)
            state["inflight_until"] = now + _request_timeout(self.config) + 5.0
            _write_state(path, state)
            return pending, state

    def _mark_success(self, batch_id: str) -> None:
        path = otlp_exporter_state_path(self.state_dir)
        lock = path.with_name(path.name + ".lock")
        recovered = False
        exported_events = 0
        exported_spans = 0
        with FileLock(lock):
            state = _load_state(path)
            pending = state.get("pending") if isinstance(state.get("pending"), dict) else {}
            if str(pending.get("batch_id") or "") != batch_id:
                return
            recovered = str(state.get("health") or "") == "degraded"
            exported_events = len(pending.get("event_ids") or [])
            exported_spans = int(pending.get("span_count") or 0)
            state["cursor_event_id"] = str(pending.get("tail_event_id") or "")
            state["backlog_events"] = len(_events_after_cursor(
                self.event_log.read_all(),
                state["cursor_event_id"],
            ))
            roots = [*_string_list(state.get("emitted_root_traces")), *_string_list(pending.get("root_trace_ids"))]
            state["emitted_root_traces"] = list(dict.fromkeys(roots))[-_MAX_ROOT_TRACES:]
            operation_traces = {
                **_operation_trace_map(state.get("operation_trace_ids")),
                **_operation_trace_map(pending.get("operation_trace_ids")),
            }
            state["operation_trace_ids"] = _bounded_operation_trace_map(
                operation_traces
            )
            state["last_success_at"] = _now_iso()
            state["last_batch_id"] = batch_id
            state["last_failure_class"] = ""
            state["health"] = "healthy"
            state["pending"] = {}
            state["inflight_until"] = 0.0
            state["next_attempt_at"] = 0.0
            state["next_export_at"] = _now_epoch() + _interval_seconds(self.config)
            _increment_counter(state, "exported_batches", 1)
            _increment_counter(state, "exported_events", exported_events)
            _increment_counter(state, "exported_spans", exported_spans)
            _write_state(path, state)
        self._record_runtime(
            level="INFO",
            message="OTLP export batch delivered",
            result="completed",
            failure_class="",
        )
        self._record_metrics(result="completed", span_count=exported_spans)
        if recovered:
            self._emit_health_event("telemetry.exporter.recovered", "")

    def _mark_failure(self, batch_id: str, failure_class: str) -> None:
        path = otlp_exporter_state_path(self.state_dir)
        lock = path.with_name(path.name + ".lock")
        changed_to_degraded = False
        span_count = 0
        with FileLock(lock):
            state = _load_state(path)
            pending = state.get("pending") if isinstance(state.get("pending"), dict) else {}
            if str(pending.get("batch_id") or "") != batch_id:
                return
            changed_to_degraded = str(state.get("health") or "") != "degraded"
            attempt = int(pending.get("attempt") or 0) + 1
            pending["attempt"] = attempt
            span_count = int(pending.get("span_count") or 0)
            state["pending"] = pending
            state["health"] = "degraded"
            state["last_failure_at"] = _now_iso()
            state["last_failure_class"] = failure_class
            state["inflight_until"] = 0.0
            state["next_attempt_at"] = _now_epoch() + _retry_delay(self.config, attempt)
            _increment_counter(state, "failed_attempts", 1)
            _write_state(path, state)
        self._record_runtime(
            level="ERROR",
            message="OTLP export batch failed",
            result="failed",
            failure_class=failure_class,
        )
        self._record_metrics(result="failed", span_count=span_count, failure_class=failure_class)
        if changed_to_degraded:
            self._emit_health_event("telemetry.exporter.degraded", failure_class)

    def _record_configuration_failure(self, failure_class: str) -> None:
        path = otlp_exporter_state_path(self.state_dir)
        lock = path.with_name(path.name + ".lock")
        changed_to_degraded = False
        with FileLock(lock):
            state = _load_state(path)
            changed_to_degraded = str(state.get("health") or "") != "degraded"
            state["health"] = "degraded"
            state["last_failure_at"] = _now_iso()
            state["last_failure_class"] = failure_class
            _write_state(path, state)
        self._record_runtime(
            level="WARN",
            message="OTLP exporter is configured but unavailable",
            result="degraded",
            failure_class=failure_class,
        )
        if changed_to_degraded:
            self._emit_health_event("telemetry.exporter.degraded", failure_class)

    def _record_runtime(
        self,
        *,
        level: str,
        message: str,
        result: str,
        failure_class: str,
    ) -> None:
        write_runtime_log(
            self.state_dir,
            level=level,
            component="otlp-exporter",
            message=message,
            failure_class=failure_class,
            fields={"status": result, "route": "otlp-http"},
            enabled=self.runtime_logs_enabled,
        )

    def _record_metrics(
        self,
        *,
        result: str,
        span_count: int,
        failure_class: str = "",
    ) -> None:
        if self.metrics is None:
            return
        labels = {"component": "otlp_exporter", "result": result}
        if failure_class:
            labels["failure_class"] = failure_class
        self.metrics.increment("zf_otlp_export_batches_total", labels=labels)
        if span_count:
            self.metrics.increment(
                "zf_otlp_export_spans_total",
                labels=labels,
                value=float(span_count),
            )

    def _emit_health_event(self, event_type: str, failure_class: str) -> None:
        if self.event_writer is None:
            return
        try:
            self.event_writer.append(ZfEvent(
                type=event_type,
                actor="zf-otlp-exporter",
                payload={
                    "schema_version": _SCHEMA_VERSION,
                    "component": "otlp_exporter",
                    "health": "degraded" if event_type.endswith("degraded") else "healthy",
                    "failure_class": failure_class,
                    "status": "failed" if failure_class else "completed",
                },
            ))
        except Exception:
            return


_THREADS: dict[str, threading.Thread] = {}
_THREADS_LOCK = threading.Lock()


def schedule_otlp_exporter(
    *,
    state_dir: Path,
    event_log: EventLog,
    config: Any | None,
    event_writer: EventWriter | None,
    metrics: OperationsMetricsRegistry | None,
    runtime_logs_enabled: bool,
    project_id: str = "",
) -> bool:
    """Schedule one daemon export round; never block the watcher tick."""

    if not bool(getattr(config, "enabled", False)):
        return False
    key = str(otlp_exporter_state_path(state_dir).resolve())
    with _THREADS_LOCK:
        existing = _THREADS.get(key)
        if existing is not None and existing.is_alive():
            return False

        def run() -> None:
            try:
                OtlpExporter(
                    state_dir=state_dir,
                    event_log=event_log,
                    config=config,
                    event_writer=event_writer,
                    metrics=metrics,
                    runtime_logs_enabled=runtime_logs_enabled,
                    project_id=project_id,
                ).run_once()
            finally:
                with _THREADS_LOCK:
                    _THREADS.pop(key, None)

        thread = threading.Thread(
            target=run,
            name="ZaoFuOtlpExporter",
            daemon=True,
        )
        _THREADS[key] = thread
        thread.start()
        return True


def read_otlp_exporter_status(state_dir: Path, *, config: Any | None = None) -> dict[str, Any]:
    """Return safe exporter health; pending request body and credentials stay private."""

    state = _load_state(otlp_exporter_state_path(state_dir))
    pending = state.get("pending") if isinstance(state.get("pending"), dict) else {}
    return redact_obj({
        "schema_version": _SCHEMA_VERSION,
        "enabled": bool(getattr(config, "enabled", False)),
        "health": str(state.get("health") or "idle"),
        "cursor_event_id": str(state.get("cursor_event_id") or ""),
        "backlog_events": int(state.get("backlog_events") or 0),
        "last_success_at": str(state.get("last_success_at") or ""),
        "last_failure_at": str(state.get("last_failure_at") or ""),
        "last_failure_class": str(state.get("last_failure_class") or ""),
        "last_batch_id": str(state.get("last_batch_id") or ""),
        "pending": {
            "batch_id": str(pending.get("batch_id") or ""),
            "event_count": len(pending.get("event_ids") or []),
            "span_count": int(pending.get("span_count") or 0),
            "attempt": int(pending.get("attempt") or 0),
            "created_at": str(pending.get("created_at") or ""),
        },
        "counters": dict(state.get("counters") or {}),
    })


class OtlpExportError(RuntimeError):
    pass


def _build_pending_batch(
    events: list[ZfEvent],
    *,
    emitted_root_traces: list[str],
    operation_trace_ids: dict[str, str],
    sample_rate: float,
    project_id: str,
) -> dict[str, Any]:
    spans: list[dict[str, Any]] = []
    event_ids: list[str] = []
    root_trace_ids: list[str] = []
    known_roots = set(emitted_root_traces)
    batch_operation_trace_ids = _bound_operation_trace_ids(events)
    resolved_operation_trace_ids = {
        **operation_trace_ids,
        **batch_operation_trace_ids,
    }
    policy = {"dropped": 0, "sampled_out": 0, "summary": 0, "redacted": 0}
    for event in events:
        decision = _policy_decision(event, sample_rate)
        if decision == "drop":
            policy["dropped"] += 1
            continue
        if decision == "sampled_out":
            policy["sampled_out"] += 1
            continue
        if decision == "summary":
            policy["summary"] += 1
        trace_id = _trace_id(event, operation_trace_ids=resolved_operation_trace_ids)
        root_span_id = _hash_hex(f"root:{trace_id}", 16)
        if trace_id not in known_roots:
            spans.append(_root_span(event, trace_id, root_span_id, project_id))
            known_roots.add(trace_id)
            root_trace_ids.append(trace_id)
        event_span, redacted = _event_span(
            event,
            trace_id=trace_id,
            root_span_id=root_span_id,
            project_id=project_id,
            mode=decision,
        )
        spans.append(event_span)
        stage_span = _stage_span(
            event,
            trace_id=trace_id,
            root_span_id=root_span_id,
            project_id=project_id,
        )
        if stage_span is not None:
            spans.append(stage_span)
        policy["redacted"] += redacted
        if event.id:
            event_ids.append(str(event.id))
    tail = events[-1] if events else None
    batch_seed = "|".join(event_ids or [str(getattr(tail, "id", ""))])
    return {
        "batch_id": _hash_hex(f"{_SCHEMA_VERSION}:{batch_seed}", 32),
        "event_ids": event_ids[-_MAX_EVENT_IDS:],
        "tail_event_id": str(getattr(tail, "id", "") or ""),
        "root_trace_ids": root_trace_ids[-_MAX_ROOT_TRACES:],
        "operation_trace_ids": _bounded_operation_trace_map(
            batch_operation_trace_ids
        ),
        "spans": spans,
        "span_count": len(spans),
        "policy": policy,
    }


def _policy_decision(event: ZfEvent, sample_rate: float) -> str:
    event_type = str(event.type or "").lower()
    if event_type.startswith(_INTERNAL_PREFIXES):
        return "drop"
    if event_type == "provider.telemetry.context.bound":
        return "retain"
    if any(marker in event_type for marker in _CRITICAL_MARKERS):
        return "retain"
    if event_type.startswith("provider."):
        return "summary"
    if event_type.startswith(_NOISY_PREFIXES):
        return "retain" if _sampled_in(event.id or event_type, sample_rate) else "sampled_out"
    return "retain"


def _sampled_in(seed: str, rate: float) -> bool:
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    value = int(_hash_hex(f"sample:{seed}", 16), 16) / float(16 ** 16 - 1)
    return value < rate


def _trace_id(
    event: ZfEvent,
    *,
    operation_trace_ids: dict[str, str] | None = None,
) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    explicit = str(payload.get("otel_trace_id") or "").strip().lower()
    if _is_hex(explicit, 32):
        return explicit
    for key in _operation_trace_keys(event):
        trace_id = str((operation_trace_ids or {}).get(key) or "").strip().lower()
        if _is_hex(trace_id, 32):
            return trace_id
    seed = "|".join([
        str(event.correlation_id or ""),
        str(payload.get("trace_id") or ""),
        str(payload.get("run_id") or payload.get("workflow_run_id") or ""),
        str(event.task_id or ""),
        str(event.id or ""),
    ])
    return _hash_hex(f"trace:{seed}", 32)


def _root_span(
    event: ZfEvent,
    trace_id: str,
    span_id: str,
    project_id: str,
) -> dict[str, Any]:
    timestamp = _unix_nano(event.ts)
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "name": "zaofu.workflow.root",
        "kind": 1,
        "startTimeUnixNano": str(timestamp),
        "endTimeUnixNano": str(timestamp),
        "attributes": _attributes({
            "service.name": "zaofu",
            "zaofu.project_id": project_id,
            "zaofu.trace_source": "event_ledger",
        }),
        "status": {"code": 1},
    }


def _event_span(
    event: ZfEvent,
    *,
    trace_id: str,
    root_span_id: str,
    project_id: str,
    mode: str,
) -> tuple[dict[str, Any], int]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    parent_span = root_span_id
    explicit_parent = str(payload.get("otel_parent_span_id") or "").strip().lower()
    if event.type == "provider.telemetry.context.bound" and _is_hex(explicit_parent, 16):
        span_id = explicit_parent
    else:
        span_id = _hash_hex(f"event:{event.id or event.type}", 16)
    attrs: dict[str, Any] = {
        "service.name": "zaofu",
        "zaofu.project_id": project_id,
        "zaofu.event.type": str(event.type or ""),
        "zaofu.event.id": str(event.id or ""),
        "zaofu.correlation_id": str(event.correlation_id or ""),
        "zaofu.task_id": str(event.task_id or ""),
        "zaofu.actor": str(event.actor or ""),
        "zaofu.export.mode": mode,
    }
    for source, destination in _SAFE_PAYLOAD_FIELDS.items():
        if payload.get(source) not in (None, ""):
            attrs[destination] = payload.get(source)
    redacted = sum(
        1 for key in payload
        if key not in _SAFE_PAYLOAD_FIELDS
        and key not in {"otel_trace_id", "otel_parent_span_id"}
    )
    status = "ERROR" if _is_failure(event) else "OK"
    timestamp = _unix_nano(event.ts)
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent_span,
        "name": _span_name(event, payload),
        "kind": 1,
        "startTimeUnixNano": str(timestamp),
        "endTimeUnixNano": str(timestamp),
        "attributes": _attributes(attrs),
        "status": {"code": 2 if status == "ERROR" else 1},
    }, redacted


def _span_name(event: ZfEvent, payload: dict[str, Any]) -> str:
    event_type = str(event.type or "event")
    if event_type == "provider.telemetry.context.bound":
        return "zaofu.provider.parent"
    if "dispatched" in event_type or "assigned" in event_type:
        return "zaofu.workflow.dispatch"
    if payload.get("stage_id") or payload.get("task_pipeline_stage"):
        return "zaofu.workflow.stage"
    return f"zaofu.event.{event_type}"


def _stage_span(
    event: ZfEvent,
    *,
    trace_id: str,
    root_span_id: str,
    project_id: str,
) -> dict[str, Any] | None:
    """Make stage ownership explicit even when the source event is dispatch."""

    payload = event.payload if isinstance(event.payload, dict) else {}
    stage_id = str(
        payload.get("stage_id") or payload.get("task_pipeline_stage") or ""
    ).strip()
    if not stage_id:
        return None
    timestamp = _unix_nano(event.ts)
    return {
        "traceId": trace_id,
        "spanId": _hash_hex(f"stage:{event.id or event.type}:{stage_id}", 16),
        "parentSpanId": root_span_id,
        "name": "zaofu.workflow.stage",
        "kind": 1,
        "startTimeUnixNano": str(timestamp),
        "endTimeUnixNano": str(timestamp),
        "attributes": _attributes({
            "service.name": "zaofu",
            "zaofu.project_id": project_id,
            "zaofu.stage": stage_id,
            "zaofu.event.id": str(event.id or ""),
            "zaofu.task_id": str(event.task_id or ""),
            "zaofu.correlation_id": str(event.correlation_id or ""),
        }),
        "status": {"code": 2 if _is_failure(event) else 1},
    }


def _is_failure(event: ZfEvent) -> bool:
    lowered = str(event.type or "").lower()
    return any(marker in lowered for marker in ("failed", "blocked", "timeout", "rejected", "denied"))


def _unix_nano(value: object) -> int:
    """Render an event timestamp as OTLP epoch nanoseconds without raising."""

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1_000_000_000)
    except (TypeError, ValueError, OverflowError):
        return int(_now_epoch() * 1_000_000_000)


def _attributes(values: dict[str, Any]) -> list[dict[str, Any]]:
    attributes = []
    for key, value in sorted(values.items()):
        if value in (None, ""):
            continue
        attributes.append({"key": key, "value": {"stringValue": str(value)[:256]}})
    return attributes


def _otlp_request_body(spans: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "resourceSpans": [{
            "resource": {"attributes": _attributes({"service.name": "zaofu"})},
            "scopeSpans": [{
                "scope": {"name": "zaofu.otlp_exporter", "version": "v1"},
                "spans": spans,
            }],
        }],
    }


def _events_after_cursor(events: list[ZfEvent], cursor_event_id: str) -> list[ZfEvent]:
    if not cursor_event_id:
        return list(events)
    for index, event in enumerate(events):
        if str(event.id or "") == cursor_event_id:
            return events[index + 1:]
    return list(events)


def _load_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    state = raw if isinstance(raw, dict) else {}
    state.setdefault("schema_version", _SCHEMA_VERSION)
    state.setdefault("health", "idle")
    state.setdefault("cursor_event_id", "")
    state.setdefault("backlog_events", 0)
    state.setdefault("pending", {})
    state.setdefault("emitted_root_traces", [])
    state.setdefault("operation_trace_ids", {})
    state.setdefault("counters", {})
    return state


def _write_state(path: Path, state: dict[str, Any]) -> None:
    state["schema_version"] = _SCHEMA_VERSION
    state["updated_at"] = _now_iso()
    atomic_write_text(
        path,
        json.dumps(redact_obj(state), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _increment_counter(state: dict[str, Any], name: str, amount: int) -> None:
    if amount <= 0:
        return
    counters = state.setdefault("counters", {})
    counters[name] = int(counters.get(name) or 0) + amount


def _endpoint_from_env(config: Any | None) -> str:
    env_name = str(getattr(config, "endpoint_env", "") or "").strip()
    endpoint = os.environ.get(env_name, "").strip() if env_name else ""
    if not endpoint.startswith(("http://", "https://")):
        return ""
    return endpoint.rstrip("/") + ("" if endpoint.rstrip("/").endswith("/v1/traces") else "/v1/traces")


def _headers_from_env(config: Any | None) -> tuple[dict[str, str], str]:
    env_name = str(getattr(config, "headers_env", "") or "").strip()
    if not env_name:
        return {}, ""
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return {}, ""
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {}, "headers_env_invalid_json"
    if not isinstance(decoded, dict):
        return {}, "headers_env_invalid_json"
    headers: dict[str, str] = {}
    for key, value in decoded.items():
        name = str(key or "").strip()
        if not _HEADER_NAME.fullmatch(name):
            return {}, "headers_env_invalid_name"
        headers[name] = str(value or "")
    return headers, ""


def _send_otlp_http(endpoint: str, body: bytes, headers: dict[str, str], timeout: float) -> int:
    request = Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            return int(getattr(response, "status", 200) or 200)
    except HTTPError as exc:
        raise OtlpExportError(f"otlp_http_{exc.code}") from exc
    except URLError as exc:
        raise OtlpExportError("otlp_transport_error") from exc
    except TimeoutError as exc:
        raise OtlpExportError("otlp_timeout") from exc


def _failure_class(exc: Exception) -> str:
    text = str(exc or "").strip().lower()
    if text.startswith("otlp_"):
        return text[:120]
    if "timed out" in text or isinstance(exc, TimeoutError):
        return "otlp_timeout"
    return "otlp_transport_error"


def _retry_delay(config: Any | None, attempt: int) -> float:
    initial = float(getattr(config, "retry_initial_seconds", 5.0) or 5.0)
    maximum = float(getattr(config, "retry_max_seconds", 300.0) or 300.0)
    return min(maximum, initial * (2 ** max(0, min(attempt - 1, 12))))


def _interval_seconds(config: Any | None) -> float:
    return float(getattr(config, "interval_seconds", 15.0) or 15.0)


def _request_timeout(config: Any | None) -> float:
    return float(getattr(config, "request_timeout_seconds", 3.0) or 3.0)


def _batch_size(config: Any | None) -> int:
    return max(1, min(512, int(getattr(config, "batch_size", 64) or 64)))


def _sample_rate(config: Any | None) -> float:
    return max(0.0, min(1.0, float(getattr(config, "healthy_sample_rate", 0.1) or 0.0)))


def _controller_sample_rate(state_dir: Path, config: Any | None) -> float:
    from zf.integrations.controller.runtime_settings import diagnostic_rate
    return diagnostic_rate(state_dir, _sample_rate(config))


def _hash_hex(seed: str, length: int) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:length]


def _is_hex(value: str, length: int) -> bool:
    return len(value) == length and all(char in "0123456789abcdef" for char in value)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _operation_trace_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(trace_id).lower()
        for key, trace_id in value.items()
        if _is_hex(str(trace_id).lower(), 32)
    }


def _bound_operation_trace_ids(events: list[ZfEvent]) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for event in events:
        if event.type != "provider.telemetry.context.bound":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        trace_id = str(payload.get("otel_trace_id") or "").strip().lower()
        if not _is_hex(trace_id, 32):
            continue
        for key in _operation_trace_keys(event):
            bindings[key] = trace_id
    return bindings


def _operation_trace_keys(event: ZfEvent) -> list[str]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    values = [
        ("correlation", str(event.correlation_id or "")),
        ("dispatch", str(payload.get("dispatch_id") or "")),
    ]
    return [
        f"{kind}:{_hash_hex(value, 24)}"
        for kind, value in values
        if value
    ]


def _bounded_operation_trace_map(value: dict[str, str]) -> dict[str, str]:
    rows = list(value.items())[-_MAX_OPERATION_TRACES:]
    return {str(key): str(trace_id) for key, trace_id in rows}


__all__ = [
    "OtlpExporter",
    "OtlpExportResult",
    "otlp_exporter_state_path",
    "read_otlp_exporter_status",
    "schedule_otlp_exporter",
]
