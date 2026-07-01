"""Design contract tests — TDD verification of docs/design/14/15/16.

Each test encodes a specific claim from the design docs. If a test fails,
either the code drifted from the design or the design was aspirational.
Running this file is the fastest audit of whether the three-layer
architecture is actually implemented.

Organized by design doc section. Each test docstring cites the design doc
and quotes the claim being checked.
"""

from __future__ import annotations

import inspect
from dataclasses import fields
from pathlib import Path

import pytest


# =============================================================================
# 14-three-layer-architecture.md
# =============================================================================

class TestLayer1NoLLM:
    """Design 14: Layer 1 is 'deterministic, 零 LLM 调用'.

    Claim: Layer 1 modules (zf.runtime.orchestrator + helpers) do NOT import
    any LLM SDK or call any LLM API. Integration with Layer 2 goes through
    the TransportAdapter seam, not through a direct SDK import.
    """

    def test_orchestrator_core_has_no_claude_code_sdk_import(self):
        """zf.runtime.orchestrator must not import claude_code_sdk directly."""
        src = Path("src/zf/runtime/orchestrator.py").read_text()
        assert "import claude_code_sdk" not in src
        assert "from claude_code_sdk" not in src

    def test_zf_core_has_no_llm_imports(self):
        """zf.core.* is the deterministic kernel — NO LLM SDK imports at all."""
        for py in Path("src/zf/core").rglob("*.py"):
            text = py.read_text()
            assert "claude_code_sdk" not in text, f"{py} imports claude_code_sdk"
            assert "anthropic" not in text.lower() or "Anthropic" in text, \
                f"{py} references anthropic SDK"

    def test_orchestrator_housekeeping_handles_three_event_types(self):
        """Design 14: Layer 1 housekeeping handles agent.usage / memory.note /
        task.contract.update (and nothing else)."""
        from zf.runtime.housekeeping import (
            apply_agent_usage_event,
            apply_memory_note_event,
            apply_task_contract_event,
        )
        # All three exist as callable
        assert callable(apply_agent_usage_event)
        assert callable(apply_memory_note_event)
        assert callable(apply_task_contract_event)


class TestLayer2ClaudeCodeOrchestrator:
    """Design 14: Layer 2 is the Claude Code Orchestrator role."""

    def test_layer2_uses_stream_json_transport(self):
        """The orchestrator role in safe-team preset must have transport=stream-json."""
        from zf.core.config.presets import get_preset
        preset = get_preset("safe-team")
        orch = next(r for r in preset["roles"] if r["name"] == "orchestrator")
        assert orch["transport"] == "stream-json"

    def test_layer2_uses_allowlist_permission_mode(self):
        """Design 14 + 16: orchestrator role uses permission_mode=allowlist."""
        from zf.core.config.presets import get_preset
        preset = get_preset("safe-team")
        orch = next(r for r in preset["roles"] if r["name"] == "orchestrator")
        assert orch["permission_mode"] == "allowlist"

    def test_layer2_allowed_tools_match_design(self):
        """Design 16 §Allowed Tools: the canonical allowlist.

        Must include: zf feature, zf kanban, zf emit, zf events, Read.
        Must NOT include: Edit, Bash(*), WebSearch, broad Write.
        """
        from zf.core.config.presets import get_preset
        preset = get_preset("safe-team")
        orch = next(r for r in preset["roles"] if r["name"] == "orchestrator")
        tools = " ".join(orch["allowed_tools"])
        # required
        assert "Bash(zf feature" in tools
        assert "Bash(zf kanban" in tools
        assert "Bash(zf emit" in tools
        assert "Bash(zf events" in tools
        assert "Read" in orch["allowed_tools"]
        # forbidden
        assert "Edit" not in orch["allowed_tools"]
        assert "Bash(*)" not in orch["allowed_tools"]
        assert "WebSearch" not in orch["allowed_tools"]


class TestLayer3FiveWorkerRoles:
    """Design 14: Layer 3 has exactly 5 worker roles with specific trigger topology."""

    def test_five_worker_roles_present(self):
        """arch, dev, review, test, judge — all 5 must be in safe-team preset."""
        from zf.core.config.presets import get_preset
        preset = get_preset("safe-team")
        role_names = {r["name"] for r in preset["roles"]}
        for w in ("arch", "dev", "review", "test", "judge"):
            assert w in role_names, f"missing worker role: {w}"

    def test_review_modes_split_between_review_and_critic(self):
        """Design 14 + 16 originally gave review both modes; since 29f18e2
        the critic role takes design critique and review does code review
        behind the kernel static gate."""
        from zf.core.config.presets import get_preset
        preset = get_preset("safe-team")
        review = next(r for r in preset["roles"] if r["name"] == "review")
        assert review["triggers"] == ["static_gate.passed"]
        critic = next(r for r in preset["roles"] if r["name"] == "critic")
        assert "arch.proposal.done" in critic["triggers"]

    def test_workers_default_to_tmux(self):
        """Design 14: workers default to tmux, stream-json is opt-in."""
        from zf.core.config.presets import get_preset
        preset = get_preset("safe-team")
        for w_name in ("arch", "dev", "review", "test", "judge"):
            w = next(r for r in preset["roles"] if r["name"] == w_name)
            # if unspecified it's tmux (default); if specified it must be tmux
            assert w.get("transport", "tmux") == "tmux", \
                f"{w_name} should default to tmux"


class TestConditionalDispatch:
    """Design 14 §Conditional Dispatch (E4): Layer 2 takes over when orchestrator
    role is configured, otherwise legacy _on_* handlers fire."""

    def _fresh_state(self, tmp_path):
        sd = tmp_path / ".zf"
        sd.mkdir()
        (sd / "memory").mkdir()
        from zf.core.events.log import EventLog
        from zf.core.events.model import ZfEvent
        from zf.core.state.session import SessionStore
        EventLog(sd / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
        SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
        (sd / "kanban.json").write_text("[]\n")
        return sd

    def test_layer2_active_skips_legacy_handlers(self, tmp_path):
        """When orchestrator role exists, dev.build.done does NOT auto-move the task."""
        from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
        from zf.core.events.model import ZfEvent
        from zf.core.task.store import TaskStore
        from zf.core.task.schema import Task
        from zf.runtime.orchestrator import Orchestrator
        from zf.runtime.tmux import TmuxSession
        from zf.runtime.transport import TmuxTransport

        config = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(
                    name="orchestrator", backend="claude-code",
                    transport="stream-json",
                    triggers=["dev.build.done"],
                ),
                RoleConfig(name="dev", backend="mock"),
            ],
        )
        sd = self._fresh_state(tmp_path)
        TaskStore(sd / "kanban.json").add(
            Task(id="T1", title="x", status="in_progress", assigned_to="dev")
        )
        transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
        orch = Orchestrator(sd, config, transport)
        orch.run_once(events=[ZfEvent(type="dev.build.done", actor="dev", task_id="T1")])
        # Layer 2 mode: task NOT auto-moved
        assert TaskStore(sd / "kanban.json").get("T1").status == "in_progress"

    def test_layer1_only_fires_legacy_handler(self, tmp_path):
        """When no orchestrator role, dev.build.done DOES auto-move the task."""
        from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
        from zf.core.events.model import ZfEvent
        from zf.core.task.store import TaskStore
        from zf.core.task.schema import Task
        from zf.runtime.orchestrator import Orchestrator
        from zf.runtime.tmux import TmuxSession
        from zf.runtime.transport import TmuxTransport

        config = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", backend="mock")],
        )
        sd = self._fresh_state(tmp_path)
        TaskStore(sd / "kanban.json").add(
            Task(id="T1", title="x", status="in_progress", assigned_to="dev")
        )
        transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
        orch = Orchestrator(sd, config, transport)
        orch.run_once(events=[ZfEvent(type="dev.build.done", actor="dev", task_id="T1")])
        # Legacy mode: task auto-moved to review
        assert TaskStore(sd / "kanban.json").get("T1").status == "review"


# =============================================================================
# 15-state-model.md
# =============================================================================

class TestL1Feature:
    """Design 15 §L1 Feature: schema and semantics."""

    def test_feature_has_required_fields(self):
        """Feature dataclass must have all 8 fields from the schema."""
        from zf.core.feature.schema import Feature
        field_names = {f.name for f in fields(Feature)}
        expected = {
            "id", "title", "description", "status", "priority",
            "created_at", "completed_at", "user_message",
        }
        assert expected.issubset(field_names), \
            f"missing: {expected - field_names}"

    def test_feature_id_prefix(self):
        """Design 15: Feature.id starts with 'F-'."""
        from zf.core.feature.schema import Feature
        f = Feature(title="x")
        assert f.id.startswith("F-")

    def test_feature_status_constrained(self):
        """Design 15: status ∈ {planning, active, done, cancelled}."""
        from zf.core.feature.schema import Feature
        from zf.core.feature.store import FeatureStore
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            store = FeatureStore(Path(td) / "f.json")
            with pytest.raises(ValueError):
                store.add(Feature(title="x", status="bogus"))

    def test_feature_update_done_sets_completed_at(self, tmp_path):
        """Design 15: update(status=done) auto-populates completed_at."""
        from zf.core.feature.schema import Feature
        from zf.core.feature.store import FeatureStore
        store = FeatureStore(tmp_path / "f.json")
        f = store.add(Feature(title="x"))
        assert f.completed_at == ""
        updated = store.update(f.id, status="done")
        assert updated.completed_at


class TestL2TaskContract:
    """Design 15 §L2 Task: TaskContract schema."""

    def test_task_contract_has_required_fields(self):
        """TaskContract dataclass must have: behavior, verification, scope,
        exclusions, acceptance."""
        from zf.core.task.schema import TaskContract
        field_names = {f.name for f in fields(TaskContract)}
        for required in ("behavior", "verification", "scope", "exclusions", "acceptance"):
            assert required in field_names, f"TaskContract missing {required}"


class TestL3Events:
    """Design 15 §L3 Event: append-only + causal lineage."""

    def test_zf_event_has_causal_lineage_fields(self):
        """Design 15: ZfEvent has causation_id + correlation_id for causal chains."""
        from zf.core.events.model import ZfEvent
        field_names = {f.name for f in fields(ZfEvent)}
        assert "causation_id" in field_names
        assert "correlation_id" in field_names

    def test_event_log_is_append_only(self, tmp_path):
        """Design 15 §I-S1: events.jsonl is append-only; EventLog.append
        must not truncate or overwrite existing content."""
        from zf.core.events.log import EventLog
        from zf.core.events.model import ZfEvent
        log = EventLog(tmp_path / "events.jsonl")
        log.append(ZfEvent(type="a"))
        log.append(ZfEvent(type="b"))
        log.append(ZfEvent(type="c"))
        events = log.read_all()
        assert [e.type for e in events] == ["a", "b", "c"]
        # Underlying file should contain 3 lines, with originals intact
        lines = [l for l in (tmp_path / "events.jsonl").read_text().splitlines() if l.strip()]
        assert len(lines) == 3


class TestL4Progress:
    """Design 15 §L4 Narrative: progress.md hybrid generation."""

    def test_layer2_insight_marker_exists(self):
        """Design 15: LAYER2_INSIGHT_MARKER is the boundary between Layer 1
        auto-gen section and Layer 2 free-text section."""
        from zf.runtime.progress import LAYER2_INSIGHT_MARKER
        assert LAYER2_INSIGHT_MARKER

    def test_progress_has_four_sections(self, tmp_path):
        """Design 15: progress.md has 4 sections — Currently Active,
        Completed, Recent Events, Layer 2 Insights."""
        from zf.runtime.progress import regenerate_progress
        sd = tmp_path / ".zf"
        sd.mkdir()
        text = regenerate_progress(sd)
        for section in ("## Currently Active", "## Completed",
                        "## Recent Events", "## Layer 2 Insights"):
            assert section in text

    def test_regen_preserves_layer2_insights(self, tmp_path):
        """Design 15: 'Layer 2 的 insight 永远不会被 Layer 1 的 regen 覆盖'."""
        from zf.runtime.progress import regenerate_progress, LAYER2_INSIGHT_MARKER
        sd = tmp_path / ".zf"
        sd.mkdir()
        regenerate_progress(sd)
        # Append an insight as Layer 2 would
        path = sd / "progress.md"
        text = path.read_text()
        text = text.replace(
            LAYER2_INSIGHT_MARKER,
            LAYER2_INSIGHT_MARKER + "\n### insight-1\nmust survive regen\n"
        )
        path.write_text(text)
        # Regenerate (as Layer 1 would)
        new = regenerate_progress(sd)
        assert "must survive regen" in new


class TestL5Memory:
    """Design 15 §L5 Memory: decay-aware per-role markdown store."""

    def test_four_memory_types(self):
        """Design 15: memory types are decision / pattern / fix / context."""
        from zf.core.memory.store import _MEMORY_TYPES
        assert _MEMORY_TYPES == {"decision", "pattern", "fix", "context"}

    def test_decay_days_by_type(self):
        """Design 15 §Decay: decision=30, pattern=60, fix=7, context=14."""
        from zf.core.memory.store import _DECAY_DAYS
        assert _DECAY_DAYS["decision"] == 30
        assert _DECAY_DAYS["pattern"] == 60
        assert _DECAY_DAYS["fix"] == 7
        assert _DECAY_DAYS["context"] == 14


# =============================================================================
# 16-orchestrator-agent.md
# =============================================================================

class TestSessionIdDeterministic:
    """Design 16 §Session 模型: deterministic uuid5(NAMESPACE_DNS, ...)."""

    def test_uuid5_from_namespace_dns(self, tmp_path):
        """Design 16: session_id = uuid5(NAMESPACE_DNS, project_root + ':orchestrator')."""
        import uuid
        from zf.core.state.role_sessions import RoleSessionRegistry
        reg = RoleSessionRegistry(
            tmp_path / "role_sessions.yaml",
            project_root="/my/proj",
        )
        sid = reg.get_or_create("orchestrator")
        expected = uuid.uuid5(uuid.NAMESPACE_DNS, "/my/proj:orchestrator")
        assert sid == expected

    def test_same_project_same_role_same_uuid(self, tmp_path):
        """Design 16 §I-O1: same role + project always resolves to same UUID."""
        from zf.core.state.role_sessions import RoleSessionRegistry
        reg1 = RoleSessionRegistry(tmp_path / "a.yaml", project_root="/x")
        reg2 = RoleSessionRegistry(tmp_path / "b.yaml", project_root="/x")
        assert reg1.get_or_create("orchestrator") == reg2.get_or_create("orchestrator")


class TestSessionLock:
    """Design 16 §Concurrent Resume Safety: fcntl.flock non-blocking."""

    def test_session_lock_non_blocking(self, tmp_path):
        """Design 16: acquiring a held lock raises immediately, does NOT wait."""
        from zf.runtime.session_mutex import SessionLock, SessionLockBusy
        lock1 = SessionLock(tmp_path / "locks", "sid-1")
        lock1.__enter__()
        try:
            with pytest.raises(SessionLockBusy):
                SessionLock(tmp_path / "locks", "sid-1").__enter__()
        finally:
            lock1.__exit__(None, None, None)

    def test_session_lock_different_ids_dont_block(self, tmp_path):
        """Design 16 §I-O2: different session_ids lock independently."""
        from zf.runtime.session_mutex import SessionLock
        with SessionLock(tmp_path / "locks", "sid-a"):
            with SessionLock(tmp_path / "locks", "sid-b"):
                pass  # both held simultaneously, no deadlock


class TestBriefingSevenSections:
    """Design 16 §Briefing 模板: orchestrator briefing must have 7 sections."""

    def _state_dir(self, tmp_path):
        sd = tmp_path / ".zf"
        sd.mkdir()
        return sd

    def _config(self):
        from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
        return ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(
                name="orchestrator", backend="claude-code",
                allowed_tools=["Bash(zf kanban *)", "Read"],
                stages=["meta"],
            )],
        )

    def test_briefing_has_seven_sections(self, tmp_path):
        """Design 16: Trigger / Features / Kanban / Recent Events / Memory /
        Available Tools / What to do."""
        from zf.runtime.orchestrator_briefing import build_orchestrator_briefing
        from zf.core.events.model import ZfEvent
        sd = self._state_dir(tmp_path)
        briefing = build_orchestrator_briefing(
            state_dir=sd, config=self._config(),
            trigger_event=ZfEvent(type="user.message", actor="human"),
        )
        for heading in ("## Trigger", "## Features", "## Kanban",
                        "## Recent Events", "## Memory",
                        "## Available Tools", "## What to do"):
            assert heading in briefing, f"briefing missing section: {heading}"


class TestTriggerFilter:
    """Design 16 §I-O6: events not in orch.triggers don't wake Layer 2."""

    def test_untrigger_event_is_noop(self, tmp_path):
        """An event whose type is NOT in orchestrator.triggers must not dispatch."""
        from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
        from zf.core.events.model import ZfEvent
        from zf.runtime.orchestrator import Orchestrator
        from zf.runtime.tmux import TmuxSession
        from zf.runtime.transport import TmuxTransport

        # Test state setup
        sd = tmp_path / ".zf"
        sd.mkdir()
        (sd / "memory").mkdir()
        from zf.core.events.log import EventLog
        from zf.core.state.session import SessionStore
        EventLog(sd / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
        SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
        (sd / "kanban.json").write_text("[]\n")

        calls = []

        class RecordingTransport(TmuxTransport):
            def send_task(self, role_name, briefing_path, prompt):
                calls.append(role_name)

        config = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(
                name="orchestrator", backend="claude-code",
                transport="stream-json",
                triggers=["user.message"],  # only user.message triggers
            )],
        )
        transport = RecordingTransport(TmuxSession(session_name="t", dry_run=True))
        orch = Orchestrator(sd, config, transport)
        # Fire an event NOT in triggers
        orch._notify_orchestrator_agent(ZfEvent(type="gate.passed", actor="zf-cli"))
        assert calls == [], "Layer 2 was woken by a non-trigger event"

    def test_trigger_event_dispatches(self, tmp_path):
        """An event in orch.triggers DOES dispatch."""
        from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
        from zf.core.events.model import ZfEvent
        from zf.runtime.orchestrator import Orchestrator
        from zf.runtime.tmux import TmuxSession
        from zf.runtime.transport import TmuxTransport

        sd = tmp_path / ".zf"
        sd.mkdir()
        (sd / "memory").mkdir()
        from zf.core.events.log import EventLog
        from zf.core.state.session import SessionStore
        EventLog(sd / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
        SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
        (sd / "kanban.json").write_text("[]\n")

        calls = []

        class RecordingTransport(TmuxTransport):
            def send_task(self, role_name, briefing_path, prompt):
                calls.append(role_name)

        config = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(
                name="orchestrator", backend="claude-code",
                transport="stream-json",
                triggers=["user.message"],
            )],
        )
        transport = RecordingTransport(TmuxSession(session_name="t", dry_run=True))
        orch = Orchestrator(sd, config, transport)
        orch._notify_orchestrator_agent(ZfEvent(type="user.message", actor="human"))
        assert calls == ["orchestrator"]


class TestStreamJsonToolUseIngestion:
    """Design 16 §Tool Use Ingestion (B2): StreamJsonTransport maps SDK messages
    to agent.* events."""

    def test_text_block_becomes_agent_text(self, tmp_path):
        from dataclasses import dataclass, field as dfield
        from zf.core.state.role_sessions import RoleSessionRegistry
        from zf.runtime.transport_stream_json import StreamJsonTransport
        from zf.core.config.schema import RoleConfig

        @dataclass
        class FakeText:
            text: str

        @dataclass
        class FakeAssistant:
            content: list = dfield(default_factory=list)
            model: str = "fake"
            parent_tool_use_id: str | None = None

        async def fake_query(*, prompt, options=None, transport=None):
            yield FakeAssistant(content=[FakeText(text="hello world")])

        sd = tmp_path / ".zf"
        sd.mkdir()
        reg = RoleSessionRegistry(sd / "role_sessions.yaml", project_root=str(tmp_path))
        transport = StreamJsonTransport(sd, reg, query_fn=fake_query)
        transport.spawn(RoleConfig(name="dev"), argv=[])
        transport.send_task("dev", briefing_path=sd / "x.md", prompt="hi")
        events = transport.poll_events()
        text_events = [e for e in events if e.type == "agent.text"]
        assert len(text_events) == 1
        assert "hello world" in text_events[0].payload.get("text", "")

    def test_tool_use_block_becomes_agent_tool_use(self, tmp_path):
        from dataclasses import dataclass, field as dfield
        from zf.core.state.role_sessions import RoleSessionRegistry
        from zf.runtime.transport_stream_json import StreamJsonTransport
        from zf.core.config.schema import RoleConfig

        @dataclass
        class FakeToolUse:
            id: str
            name: str
            input: dict

        @dataclass
        class FakeAssistant:
            content: list = dfield(default_factory=list)
            model: str = "fake"
            parent_tool_use_id: str | None = None

        async def fake_query(*, prompt, options=None, transport=None):
            yield FakeAssistant(content=[FakeToolUse(id="tu1", name="Read", input={"p": "x"})])

        sd = tmp_path / ".zf"
        sd.mkdir()
        reg = RoleSessionRegistry(sd / "role_sessions.yaml", project_root=str(tmp_path))
        transport = StreamJsonTransport(sd, reg, query_fn=fake_query)
        transport.spawn(RoleConfig(name="dev"), argv=[])
        transport.send_task("dev", briefing_path=sd / "x.md", prompt="hi")
        events = transport.poll_events()
        tu = [e for e in events if e.type == "agent.tool.use"]
        assert len(tu) == 1
        assert tu[0].payload["tool"] == "Read"


# =============================================================================
# End of design contract tests.
# =============================================================================
