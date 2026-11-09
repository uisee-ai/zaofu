from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.core.config.schema import (
    ObservabilityAlertConfig,
    ObservabilityConfig,
    OperationsMetricsConfig,
    OtlpExporterConfig,
    ProjectConfig,
    ProviderTelemetryConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.operations_metrics import OperationsMetricsRegistry
from zf.runtime.provider_telemetry import (
    ProviderTelemetryRuntime,
    TelemetryOperationContextV1,
)
from zf.runtime.runtime_logs import write_runtime_log
from zf.web.server import create_app


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "kanban.json").write_text("[]", encoding="utf-8")
    (state / "feature_list.json").write_text("[]", encoding="utf-8")
    EventLog(state / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
    EventLog(state / "events.jsonl").append(ZfEvent(type="owner.visible_message.failed", actor="zf-cli", payload={
        "target": "feishu", "route": "approval", "reason": "route missing",
    }))
    return state


def test_operations_and_runtime_log_routes_are_read_only(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_TEST_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    config = ZfConfig(
        project=ProjectConfig(name="demo"),
        observability=ObservabilityConfig(
            provider_telemetry=ProviderTelemetryConfig(
                mode="managed",
                endpoint_env="ZF_TEST_OTLP_ENDPOINT",
            ),
        ),
    )
    runtime = ProviderTelemetryRuntime(state_dir, config.observability.provider_telemetry)
    runtime.launch(
        TelemetryOperationContextV1.interaction(
            operation_kind="kanban_turn",
            project_id="demo",
            provider="claude-headless",
        ),
        route="headless",
    )
    write_runtime_log(
        state_dir,
        level="INFO",
        component="test",
        message="operation completed",
        fields={"provider": "claude"},
    )
    client = TestClient(create_app(state_dir, config=config, project_root=tmp_path))

    operations = client.get("/api/projects/default/observability/operations")
    logs = client.get("/api/projects/default/observability/runtime-logs")

    assert operations.status_code == 200
    assert operations.json()["provider_telemetry"]["capabilities"][0]["effective"] == "active"
    assert operations.json()["otlp_exporter"]["enabled"] is False
    assert operations.json()["owner_delivery"]["routes"][0]["reason"] == "route missing"
    assert operations.json()["owner_delivery"]["routes"][0]["failure_count"] == 1
    assert logs.status_code == 200
    assert logs.json()["rows"][0]["component"] == "test"
    assert not (state_dir / "kanban.json").read_text(encoding="utf-8").strip("[]\n")


def test_metrics_endpoint_is_disabled_or_token_gated(
    state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disabled = TestClient(create_app(state_dir, project_root=tmp_path))
    assert disabled.get("/metrics").status_code == 404

    monkeypatch.setenv("ZF_TEST_METRICS_TOKEN", "metrics-secret")
    config = ZfConfig(
        project=ProjectConfig(name="demo"),
        observability=ObservabilityConfig(
            metrics=OperationsMetricsConfig(
                enabled=True,
                access_token_env="ZF_TEST_METRICS_TOKEN",
            ),
        ),
    )
    OperationsMetricsRegistry(state_dir, enabled=True).increment(
        "zf_provider_operations_total",
        labels={"provider": "claude", "result": "completed"},
    )
    client = TestClient(create_app(state_dir, config=config, project_root=tmp_path))
    assert client.get("/metrics").status_code == 403
    response = client.get("/metrics", headers={"x-zf-metrics-token": "metrics-secret"})
    assert response.status_code == 200
    assert "zf_provider_operations_total" in response.text


def test_operations_exposes_safe_exporter_and_alert_health(
    state_dir: Path,
    tmp_path: Path,
) -> None:
    exporter_state = state_dir / "projections" / "otlp_exporter.json"
    exporter_state.parent.mkdir(parents=True, exist_ok=True)
    exporter_state.write_text(
        '{"health":"degraded","backlog_events":3,'
        '"last_failure_class":"otlp_http_503",'
        '"pending":{"body":{"secret":"must-not-leak"}}}',
        encoding="utf-8",
    )
    config = ZfConfig(
        project=ProjectConfig(name="demo"),
        observability=ObservabilityConfig(
            otlp_exporter=OtlpExporterConfig(
                enabled=True,
                endpoint_env="ZF_TEST_OTLP_ENDPOINT",
            ),
            alerts=ObservabilityAlertConfig(enabled=True),
        ),
    )
    client = TestClient(create_app(state_dir, config=config, project_root=tmp_path))

    response = client.get("/api/projects/default/observability/operations")
    assert response.status_code == 200
    payload = response.json()
    assert payload["otlp_exporter"]["health"] == "degraded"
    assert payload["otlp_exporter"]["backlog_events"] == 3
    assert "body" not in payload["otlp_exporter"]["pending"]
    assert "must-not-leak" not in response.text
    assert payload["alerts"]["enabled"] is True
    assert client.post("/api/projects/default/observability/operations").status_code == 405
