"""TR-EVENT-SCHEMA-LOCK-001 step 2/3 — tests for EventWriter schema gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.verification.event_schema import EventSchemaRegistry


# Minimal fixture: one rule that requires payload.feature_id
FIXTURE = {
    "arch.proposal.done": {
        "required": ["feature_id", "proposal_ref"],
    },
    "review.approved": {
        "required": ["verdict", "diff_ref"],
        "enum": {"verdict": ["approved", "rejected"]},
    },
}


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    return sd


@pytest.fixture
def event_log(state_dir: Path) -> EventLog:
    return EventLog(state_dir / "events.jsonl")


def _read_events(state_dir: Path) -> list[dict]:
    path = state_dir / "events.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ---------------------------------------------------------------------------
# disabled mode — backward compat
# ---------------------------------------------------------------------------


class TestDisabledMode:
    def test_no_registry_passes_event_through(self, state_dir, event_log):
        writer = EventWriter(event_log)  # default disabled
        event = ZfEvent(type="arch.proposal.done", payload={})  # would fail schema
        result = writer.append(event)
        assert result.type == "arch.proposal.done"
        events = _read_events(state_dir)
        assert len(events) == 1
        assert events[0]["type"] == "arch.proposal.done"

    def test_disabled_mode_with_registry_no_op(self, state_dir, event_log):
        """schema_mode=disabled never triggers validation."""
        registry = EventSchemaRegistry.from_dict(FIXTURE)
        writer = EventWriter(
            event_log,
            schema_registry=registry,
            schema_mode="disabled",
        )
        # Bad payload would fail under warning/blocking, but disabled passes through
        writer.append(ZfEvent(type="arch.proposal.done", payload={}))
        events = _read_events(state_dir)
        assert len(events) == 1
        assert events[0]["type"] == "arch.proposal.done"
        # No warning event
        assert not any(e["type"] == "event.schema.violated" for e in events)

    def test_invalid_mode_raises(self, event_log):
        with pytest.raises(ValueError, match="schema_mode"):
            EventWriter(event_log, schema_mode="strict-but-typo")


# ---------------------------------------------------------------------------
# warning mode
# ---------------------------------------------------------------------------


class TestWarningMode:
    def test_violation_writes_both_original_and_warning(self, state_dir, event_log):
        registry = EventSchemaRegistry.from_dict(FIXTURE)
        writer = EventWriter(
            event_log,
            schema_registry=registry,
            schema_mode="warning",
        )
        writer.append(ZfEvent(type="arch.proposal.done", payload={}))
        events = _read_events(state_dir)
        types = [e["type"] for e in events]
        assert types.count("arch.proposal.done") == 1
        assert types.count("event.schema.violated") == 1

    def test_warning_event_payload_includes_violations(
        self, state_dir, event_log
    ):
        registry = EventSchemaRegistry.from_dict(FIXTURE)
        writer = EventWriter(
            event_log,
            schema_registry=registry,
            schema_mode="warning",
        )
        writer.append(ZfEvent(type="arch.proposal.done", payload={}))
        events = _read_events(state_dir)
        warning = [e for e in events if e["type"] == "event.schema.violated"][0]
        assert warning["actor"] == "zf-cli"
        payload = warning["payload"]
        assert payload["mode"] == "warning"
        assert payload["violated_event_type"] == "arch.proposal.done"
        violations = payload["violations"]
        assert len(violations) >= 2
        field_paths = {v["field_path"] for v in violations}
        assert "payload.feature_id" in field_paths
        assert "payload.proposal_ref" in field_paths

    def test_valid_event_no_warning(self, state_dir, event_log):
        registry = EventSchemaRegistry.from_dict(FIXTURE)
        writer = EventWriter(
            event_log,
            schema_registry=registry,
            schema_mode="warning",
        )
        writer.append(ZfEvent(
            type="arch.proposal.done",
            payload={"feature_id": "F-1", "proposal_ref": "ref"},
        ))
        events = _read_events(state_dir)
        types = [e["type"] for e in events]
        assert types == ["arch.proposal.done"]
        assert "event.schema.violated" not in types

    def test_loose_event_no_warning(self, state_dir, event_log):
        """Events without registered schema (e.g. worker.heartbeat) pass through."""
        registry = EventSchemaRegistry.from_dict(FIXTURE)
        writer = EventWriter(
            event_log,
            schema_registry=registry,
            schema_mode="warning",
        )
        writer.append(ZfEvent(type="worker.heartbeat", actor="dev-1", payload={}))
        events = _read_events(state_dir)
        assert [e["type"] for e in events] == ["worker.heartbeat"]


# ---------------------------------------------------------------------------
# blocking mode
# ---------------------------------------------------------------------------


class TestBlockingMode:
    def test_violation_replaced_by_discriminator_failed(
        self, state_dir, event_log
    ):
        registry = EventSchemaRegistry.from_dict(FIXTURE)
        writer = EventWriter(
            event_log,
            schema_registry=registry,
            schema_mode="blocking",
        )
        result = writer.append(
            ZfEvent(type="arch.proposal.done", payload={}, task_id="T-1")
        )
        # Returned event is discriminator.failed, not arch.proposal.done
        assert result.type == "discriminator.failed"
        events = _read_events(state_dir)
        types = [e["type"] for e in events]
        # original NOT written
        assert "arch.proposal.done" not in types
        assert types == ["discriminator.failed"]

    def test_blocked_event_payload_preserves_original(self, state_dir, event_log):
        registry = EventSchemaRegistry.from_dict(FIXTURE)
        writer = EventWriter(
            event_log,
            schema_registry=registry,
            schema_mode="blocking",
        )
        writer.append(ZfEvent(
            type="arch.proposal.done",
            task_id="T-aud-design-001",
            payload={"feature_id": "F-1"},  # missing proposal_ref
        ))
        events = _read_events(state_dir)
        assert len(events) == 1
        failure = events[0]
        assert failure["type"] == "discriminator.failed"
        assert failure["task_id"] == "T-aud-design-001"
        payload = failure["payload"]
        assert payload["mode"] == "blocking"
        assert payload["blocked_event_type"] == "arch.proposal.done"
        assert payload["blocked_event_payload"] == {"feature_id": "F-1"}
        assert "EventSchemaD" in payload["failed_d"]

    def test_blocking_replacement_preserves_dispatch_identity(
        self, state_dir, event_log
    ):
        registry = EventSchemaRegistry.from_dict(FIXTURE)
        writer = EventWriter(
            event_log,
            schema_registry=registry,
            schema_mode="blocking",
        )
        original = ZfEvent(
            type="arch.proposal.done",
            task_id="T-1",
            payload={
                "fanout_id": "fanout-1",
                "child_id": "child-1",
                "run_id": "run-1",
                "workflow_run_id": "workflow-1",
                "task_map_generation": "generation-2",
            },
        )

        failure = writer.append(original)

        assert failure.type == "discriminator.failed"
        assert failure.task_id == "T-1"
        assert failure.payload["blocked_event_id"] == original.id
        assert failure.payload["fanout_id"] == "fanout-1"
        assert failure.payload["child_id"] == "child-1"
        assert failure.payload["run_id"] == "run-1"
        assert failure.payload["workflow_run_id"] == "workflow-1"
        assert failure.payload["task_map_generation"] == "generation-2"

    def test_valid_event_passes_in_blocking_mode(self, state_dir, event_log):
        registry = EventSchemaRegistry.from_dict(FIXTURE)
        writer = EventWriter(
            event_log,
            schema_registry=registry,
            schema_mode="blocking",
        )
        result = writer.append(ZfEvent(
            type="arch.proposal.done",
            payload={"feature_id": "F-1", "proposal_ref": "ref"},
        ))
        assert result.type == "arch.proposal.done"
        events = _read_events(state_dir)
        assert [e["type"] for e in events] == ["arch.proposal.done"]


# ---------------------------------------------------------------------------
# Recursion protection
# ---------------------------------------------------------------------------


class TestSchemaMetaEventsBypass:
    def test_discriminator_failed_not_re_validated(
        self, state_dir, event_log
    ):
        """discriminator.failed itself is in _SCHEMA_META_EVENT_TYPES,
        so it skips validation (otherwise infinite loop)."""
        # Hostile fixture: discriminator.failed has fake "schema" — should be IGNORED
        registry = EventSchemaRegistry.from_dict({
            "discriminator.failed": {
                "required": ["this_should_never_check"],
            },
        })
        writer = EventWriter(
            event_log,
            schema_registry=registry,
            schema_mode="blocking",
        )
        writer.append(ZfEvent(type="discriminator.failed", payload={}))
        events = _read_events(state_dir)
        # discriminator.failed passes through unchanged
        assert [e["type"] for e in events] == ["discriminator.failed"]

    def test_event_schema_violated_not_re_validated(
        self, state_dir, event_log
    ):
        """event.schema.violated is also in the meta bypass set."""
        registry = EventSchemaRegistry.from_dict({
            "event.schema.violated": {
                "required": ["impossible"],
            },
        })
        writer = EventWriter(
            event_log,
            schema_registry=registry,
            schema_mode="warning",
        )
        writer.append(ZfEvent(type="event.schema.violated", payload={}))
        events = _read_events(state_dir)
        # Only the meta event itself (no validation, no recursive warning)
        types = [e["type"] for e in events]
        assert types == ["event.schema.violated"]


# ---------------------------------------------------------------------------
# Enum violations also caught
# ---------------------------------------------------------------------------


class TestEnumViolations:
    def test_warning_on_bad_enum(self, state_dir, event_log):
        registry = EventSchemaRegistry.from_dict(FIXTURE)
        writer = EventWriter(
            event_log,
            schema_registry=registry,
            schema_mode="warning",
        )
        writer.append(ZfEvent(
            type="review.approved",
            payload={"verdict": "maybe", "diff_ref": "x"},  # 'maybe' not in enum
        ))
        events = _read_events(state_dir)
        warning = [e for e in events if e["type"] == "event.schema.violated"][0]
        codes = {v["code"] for v in warning["payload"]["violations"]}
        assert "enum_mismatch" in codes


# ---------------------------------------------------------------------------
# Orchestrator integration: end-to-end through Orchestrator
# ---------------------------------------------------------------------------


class TestOrchestratorIntegration:
    """Verify Orchestrator wires registry + mode from config correctly."""

    def test_orchestrator_default_disabled(self, tmp_path):
        from zf.core.config.schema import (
            ProjectConfig, RoleConfig, SessionConfig, ZfConfig,
        )
        from zf.core.state.session import SessionStore
        from zf.runtime.orchestrator import Orchestrator
        from zf.runtime.tmux import TmuxSession
        from zf.runtime.transport import TmuxTransport

        sd = tmp_path / ".zf"
        sd.mkdir()
        (sd / "memory").mkdir()
        EventLog(sd / "events.jsonl").append(
            ZfEvent(type="loop.started", actor="zf-cli")
        )
        SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
        (sd / "kanban.json").write_text("[]\n")

        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", backend="mock")],
        )
        orch = Orchestrator(
            sd, cfg, TmuxTransport(TmuxSession(session_name="t", dry_run=True)),
        )
        # Default config → schema disabled, no registry installed
        assert orch.event_writer._schema_mode == "disabled"

    def test_orchestrator_warning_mode_from_config(self, tmp_path):
        """Set verification.event_schema.mode=warning + populate event_schemas
        — orchestrator's EventWriter is in warning mode with rules."""
        from zf.core.config.schema import (
            EventSchemaValidationConfig,
            ProjectConfig,
            RoleConfig,
            SessionConfig,
            VerificationConfig,
            WorkflowConfig,
            WorkflowDagConfig,
            ZfConfig,
        )
        from zf.core.state.session import SessionStore
        from zf.runtime.orchestrator import Orchestrator
        from zf.runtime.tmux import TmuxSession
        from zf.runtime.transport import TmuxTransport

        sd = tmp_path / ".zf"
        sd.mkdir()
        (sd / "memory").mkdir()
        EventLog(sd / "events.jsonl").append(
            ZfEvent(type="loop.started", actor="zf-cli")
        )
        SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
        (sd / "kanban.json").write_text("[]\n")

        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", backend="mock")],
            verification=VerificationConfig(
                event_schema=EventSchemaValidationConfig(mode="warning"),
            ),
            workflow=WorkflowConfig(
                dag=WorkflowDagConfig(
                    event_schemas={
                        "arch.proposal.done": {
                            "required": ["feature_id"],
                        },
                    },
                ),
            ),
        )

        orch = Orchestrator(
            sd, cfg, TmuxTransport(TmuxSession(session_name="t", dry_run=True)),
        )

        assert orch.event_writer._schema_mode == "warning"

        # Emit a violating event — warning event should also fire
        orch.event_writer.append(ZfEvent(
            type="arch.proposal.done", payload={}, task_id="T-X",
        ))
        events = _read_events(sd)
        types = [e["type"] for e in events]
        assert "arch.proposal.done" in types
        assert "event.schema.violated" in types


# ---------------------------------------------------------------------------
# Loader smoke
# ---------------------------------------------------------------------------


class TestLoaderParsesMode:
    def test_loader_accepts_warning_mode(self, tmp_path):
        import textwrap
        yaml_path = tmp_path / "zf.yaml"
        yaml_path.write_text(textwrap.dedent("""
            version: '1.0'
            project: {name: t}
            session: {tmux_session: t}
            verification:
              event_schema:
                mode: warning
            workflow:
              dag:
                event_schemas:
                  arch.proposal.done: {required: [feature_id]}
            roles:
              - {name: dev, backend: mock}
        """).strip(), encoding="utf-8")
        from zf.core.config.loader import load_config
        cfg = load_config(yaml_path)
        assert cfg.verification.event_schema.mode == "warning"

    def test_loader_rejects_unknown_mode(self, tmp_path):
        import textwrap
        yaml_path = tmp_path / "zf.yaml"
        yaml_path.write_text(textwrap.dedent("""
            version: '1.0'
            project: {name: t}
            session: {tmux_session: t}
            verification:
              event_schema:
                mode: nuke-everything
            roles:
              - {name: dev, backend: mock}
        """).strip(), encoding="utf-8")
        from zf.core.config.loader import ConfigError, load_config
        with pytest.raises(ConfigError, match="event_schema.mode"):
            load_config(yaml_path)
