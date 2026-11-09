"""Read-only Web adapters for ZaoFu's optional observability plane.

This module owns the Web-facing wiring only.  EventLog and the canonical
stores stay authoritative; telemetry, runtime logs, metrics, and OTLP health
are derived state or controlled read-only exports.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from zf.core.config.schema import ZfConfig
from zf.core.events.writer import EventWriter
from zf.runtime.observability_alerts import read_observability_alert_status
from zf.runtime.operations_metrics import OperationsMetricsRegistry
from zf.runtime.otlp_exporter import read_otlp_exporter_status
from zf.runtime.provider_telemetry import (
    ProviderTelemetryRuntime,
    read_provider_telemetry_snapshot,
)
from zf.runtime.runtime_logs import read_runtime_logs, summarize_runtime_logs
from zf.web.headless_agent import HeadlessTurnResult, KanbanHeadlessAgent


def register_observability_routes(
    app: FastAPI,
    *,
    state_dir: Path,
    config: ZfConfig | None,
    default_project_id: str,
    default_project_root: Path,
    resolve_api_project: Callable[..., Any],
) -> None:
    """Register optional operations endpoints without enlarging ``server.py``."""

    @app.get("/metrics", include_in_schema=False)
    def operations_metrics(x_zf_metrics_token: str | None = Header(default=None)) -> Response:
        """Token-gated Prometheus exposition for the default runtime state."""

        observability = getattr(config, "observability", None)
        metrics_config = getattr(observability, "metrics", None)
        if not bool(getattr(metrics_config, "enabled", False)):
            raise HTTPException(404, "operations metrics are disabled")
        token_env = str(getattr(metrics_config, "access_token_env", "") or "")
        expected = os.environ.get(token_env, "") if token_env else ""
        if not expected or not x_zf_metrics_token or not secrets.compare_digest(
            expected,
            x_zf_metrics_token,
        ):
            raise HTTPException(403, "operations metrics token is required")
        body = OperationsMetricsRegistry(state_dir, enabled=True).prometheus_text()
        return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

    @app.get("/api/projects/{project_id}/observability/runtime-logs")
    def project_runtime_logs(
        project_id: str,
        limit: int = 200,
        level: str = "DEBUG",
        provider: str | None = None,
        task_id: str | None = None,
    ) -> JSONResponse:
        """Redacted process diagnostics, separate from Event Logs."""

        context = resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=default_project_root,
        )
        rows = read_runtime_logs(
            context.state_dir,
            limit=max(1, min(limit, 500)),
            level=level,
            provider=provider or "",
            task_id=task_id or "",
        )
        return JSONResponse({
            "schema_version": "runtime-logs.v1",
            "project_id": project_id,
            "scope": "project-state",
            "summary": summarize_runtime_logs(context.state_dir),
            "rows": rows,
            "count": len(rows),
        })

    @app.get("/api/projects/{project_id}/observability/operations")
    def project_operations_observability(project_id: str) -> JSONResponse:
        """Lazy operations health slice; it never duplicates a Delivery graph."""

        context = resolve_api_project(
            project_id,
            default_project_id=default_project_id,
            default_state_dir=state_dir,
            default_config=config,
            default_project_root=default_project_root,
        )
        observability = getattr(context.config, "observability", None)
        metrics_config = getattr(observability, "metrics", None)
        exporter_config = getattr(observability, "otlp_exporter", None)
        alerts_config = getattr(observability, "alerts", None)
        try:
            from zf.core.events.factory import event_log_from_project
            from zf.runtime.owner_delivery_health import owner_delivery_health

            delivery = {"available": True, "routes": owner_delivery_health(
                event_log_from_project(context.state_dir, config=context.config).read_all()
            )}
        except Exception:
            delivery = {"available": False, "routes": []}
        return JSONResponse({
            "schema_version": "operations-observability.v1",
            "project_id": project_id,
            "scope": {
                "kind": "project-state",
                "note": (
                    "Provider capability and runtime logs belong to this project state; "
                    "Delivery Graph, Run Graph, and Fanout DAG remain in Delivery."
                ),
            },
            "provider_telemetry": read_provider_telemetry_snapshot(
                context.state_dir,
                config=getattr(observability, "provider_telemetry", None),
            ),
            "runtime_logs": summarize_runtime_logs(context.state_dir),
            "owner_delivery": delivery,
            "otlp_exporter": read_otlp_exporter_status(
                context.state_dir,
                config=exporter_config,
            ),
            "alerts": read_observability_alert_status(
                context.state_dir,
                config=alerts_config,
            ),
            "metrics": OperationsMetricsRegistry(
                context.state_dir,
                enabled=bool(getattr(metrics_config, "enabled", False)),
            ).snapshot(),
        })


def create_observed_headless_agent(
    *,
    state_dir: Path,
    project_root: Path,
    config: ZfConfig | None,
) -> KanbanHeadlessAgent:
    """Construct a Kanban Agent with fail-open observability dependencies."""

    observability = getattr(config, "observability", None)
    return KanbanHeadlessAgent(
        state_dir=state_dir,
        project_root=project_root,
        telemetry=ProviderTelemetryRuntime(
            state_dir,
            getattr(observability, "provider_telemetry", None),
        ),
        operations_metrics=OperationsMetricsRegistry(
            state_dir,
            enabled=bool(
                getattr(getattr(observability, "metrics", None), "enabled", False)
            ),
        ),
        runtime_logs_enabled=bool(
            getattr(getattr(observability, "runtime_logs", None), "enabled", True)
        ),
    )


def emit_headless_telemetry_events(
    *,
    writer: EventWriter,
    result: HeadlessTurnResult,
    task_id: str | None,
    causation_id: str | None,
    correlation_id: str | None,
) -> None:
    """Project a headless turn's capability without changing task truth."""

    telemetry = result.telemetry if isinstance(result.telemetry, dict) else {}
    capability = telemetry.get("capability")
    if not isinstance(capability, dict):
        return
    context = telemetry.get("context")
    telemetry_context = context if isinstance(context, dict) else {}
    telemetry_payload = {
        "provider": str(capability.get("provider") or result.backend),
        "route": str(capability.get("route") or "headless"),
        "requested": str(capability.get("requested") or "off"),
        "detected": str(capability.get("detected") or "absent"),
        "effective": str(capability.get("effective") or "disabled"),
        "join_kind": str(capability.get("join_kind") or "derived_only"),
        "w3c_inbound": bool(capability.get("w3c_inbound")),
        "signals": capability.get("signals") or {},
        "failure_class": str(capability.get("failure_class") or ""),
        "evidence_ref": "projection:provider_telemetry.json",
    }
    writer.emit(
        "provider.telemetry.capability.observed",
        actor="web",
        task_id=task_id,
        causation_id=causation_id,
        correlation_id=correlation_id,
        payload=telemetry_payload,
    )
    if telemetry_payload["effective"] != "active":
        return
    writer.emit(
        "provider.telemetry.context.bound",
        actor="web",
        task_id=task_id,
        causation_id=causation_id,
        correlation_id=correlation_id,
        payload={
            **telemetry_payload,
            "otel_trace_id": str(telemetry_context.get("otel_trace_id") or ""),
            "otel_parent_span_id": str(
                telemetry_context.get("otel_parent_span_id") or ""
            ),
        },
    )
