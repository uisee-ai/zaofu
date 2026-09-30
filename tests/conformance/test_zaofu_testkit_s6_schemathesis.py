"""Bounded Schemathesis contract coverage for the read-only API surface."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

schemathesis = pytest.importorskip("schemathesis")
from hypothesis import settings
from schemathesis.config import SchemathesisConfig
from schemathesis.config._phases import CoveragePhaseConfig, PhasesConfig
from schemathesis.config._projects import ProjectConfig, ProjectsConfig

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.web.server import create_app
_CONFIG = SchemathesisConfig(
    projects=ProjectsConfig(
        default=ProjectConfig(
            phases=PhasesConfig(coverage=CoveragePhaseConfig(enabled=False)),
        )
    )
)
@pytest.fixture(scope="module")
def api_schema():  # noqa: ANN201
    """Build the ASGI schema inside fixture scope so failed collection cleans up."""
    from zf.web.server import create_app

    with tempfile.TemporaryDirectory(prefix="zf-testkit-schemathesis-") as root_text:
        root = Path(root_text)
        state = root / "state"
        state.mkdir()
        EventLog(state / "events.jsonl").append(
            ZfEvent(type="session.started", actor="testkit")
        )
        app = create_app(state, project_root=root)
        app.state.testkit_state_dir = str(state)
        app.state.testkit_ledger_before = (state / "events.jsonl").read_bytes()
        yield schemathesis.openapi.from_asgi("/openapi.json", app, config=_CONFIG)


_SCHEMA = schemathesis.pytest.from_fixture("api_schema").include(
    path=["/health", "/api/provider-health", "/api/state"],
    method="GET",
)
_EVENTS_SCHEMA = schemathesis.pytest.from_fixture("api_schema").include(
    path="/api/events",
    method="GET",
)

@_SCHEMA.parametrize()
@settings(max_examples=3, deadline=None, derandomize=True)
def test_read_only_api_schema_cases_are_valid(case) -> None:  # noqa: ANN001
    response = case.call_and_validate()
    assert response.status_code == 200
    body = response.json()
    if case.path == "/health":
        assert body["ok"] is True
    elif case.path == "/api/provider-health":
        assert body["schema_version"] == "provider-health.v1"
        assert body["providers"] == []
    else:
        assert body["tasks"] == []
        assert body["state_dir"] == case.operation.app.state.testkit_state_dir


@_EVENTS_SCHEMA.parametrize()
@settings(max_examples=3, deadline=None, derandomize=True)
def test_events_api_schema_inputs_are_read_only(case) -> None:  # noqa: ANN001
    response = case.call_and_validate()
    assert response.status_code in {200, 422}
    if response.status_code == 200:
        body = response.json()
        assert isinstance(body["items"], list)
        assert all(item["type"] == "session.started" for item in body["items"])
    else:
        assert response.json()["detail"]
    state = Path(case.operation.app.state.testkit_state_dir)
    assert (state / "events.jsonl").read_bytes() == case.operation.app.state.testkit_ledger_before
