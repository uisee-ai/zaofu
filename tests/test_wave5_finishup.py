"""Wave 5 finish-up — SKILL-PROVENANCE-001 / CONTEXT-REC-001 / PREREQ-C."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from zf.core.skills.provenance import SkillLockEntry
from zf.core.state.state_packet import (
    StatePacket,
    StatePacketContract,
    StatePacketEvidence,
    StatePacketOwner,
)
from zf.runtime.backend_session_reader import TranscriptCatchup
from zf.runtime.fanout_run_id import (
    fanout_payload_run_ids,
    is_canonical_payload,
    read_child_run_id,
)
from zf.runtime.recovery_briefing import render_recovery_briefing


# ---------------------------------------------------------------------------
# SKILL-PROVENANCE-001
# ---------------------------------------------------------------------------


class TestSkillProvenance:
    def test_default_lock_entry_has_provenance_fields(self) -> None:
        entry = SkillLockEntry(
            role="dev", instance_id="dev-1", backend="claude",
            task_id=None, run_id=None,
            name="zf-harness-x", source=None, sha256=None,
        )
        # New fields default to safe values
        assert entry.override is False
        assert entry.last_synced_at is None

    def test_override_marks_local_divergence(self) -> None:
        entry = SkillLockEntry(
            role="dev", instance_id="dev-1", backend="claude",
            task_id=None, run_id=None,
            name="zf-harness-x", source="yoke", sha256="abc",
            override=True, last_synced_at="2026-05-18T10:00:00Z",
        )
        assert entry.override is True
        assert entry.last_synced_at == "2026-05-18T10:00:00Z"

    def test_serializable_with_new_fields(self) -> None:
        """asdict round-trip must include the new fields so
        skills.lock readers see them."""
        entry = SkillLockEntry(
            role="dev", instance_id="dev-1", backend="claude",
            task_id="T", run_id="r",
            name="x", source="yoke", sha256="hash",
            override=True, last_synced_at="2026-05-18T00:00:00Z",
        )
        d = asdict(entry)
        assert d["override"] is True
        assert d["last_synced_at"] == "2026-05-18T00:00:00Z"


# ---------------------------------------------------------------------------
# CONTEXT-REC-001
# ---------------------------------------------------------------------------


def _packet() -> StatePacket:
    return StatePacket(
        task_id="TASK-REC",
        current_stage="implement",
        next_event="dev.build.done",
        owner=StatePacketOwner(role="dev", instance_id="dev-1"),
        objective="ship feature X",
        contract=StatePacketContract(
            behavior="implement feature X", acceptance=("step1",),
        ),
        evidence=(StatePacketEvidence(
            kind="test", path="/log", status="passed", event_id="evt-1",
        ),),
    )


class TestRecoveryBriefing:
    def test_briefing_starts_with_active_task_marker(self) -> None:
        out = render_recovery_briefing(_packet())
        assert out.startswith("Active task: TASK-REC")

    def test_briefing_includes_recovery_banner(self) -> None:
        out = render_recovery_briefing(_packet(), recovery_reason="precompact")
        assert "Recovery briefing" in out
        assert "precompact" in out

    def test_briefing_includes_workflow_state_breadcrumb(self) -> None:
        out = render_recovery_briefing(_packet())
        assert "<zf-workflow-state>" in out
        assert "</zf-workflow-state>" in out

    def test_briefing_includes_state_packet_summary(self) -> None:
        out = render_recovery_briefing(_packet())
        assert "State Packet" in out
        assert "TASK-REC" in out
        assert "ship feature X" in out

    def test_briefing_lists_projection_refs(self) -> None:
        out = render_recovery_briefing(
            _packet(),
            projection_refs=[
                ".zf/projections/tasks/TASK-REC/plan.md",
                ".zf/projections/tasks/TASK-REC/progress.md",
            ],
        )
        assert "plan.md" in out
        assert "progress.md" in out

    def test_catchup_is_marked_as_evidence_not_truth(self) -> None:
        catchup = TranscriptCatchup(
            instance_id="dev-1",
            since_timestamp="2026-05-18T08:00:00Z",
            new_user_messages=("please review this",),
            new_tool_uses=("Bash(...)", "Edit(...)"),
            new_edits=("src/foo.py",),
            new_errors=(),
            backend="claude-code",
        )
        out = render_recovery_briefing(_packet(), catchup=catchup)
        # Evidence-not-truth invariant
        assert "evidence" in out.lower()
        assert "not truth" in out.lower()
        # Surfaces the actual catchup data
        assert "please review this" in out
        assert "src/foo.py" in out

    def test_briefing_ends_with_resume_instructions(self) -> None:
        out = render_recovery_briefing(_packet())
        assert "Resume instructions" in out
        assert "Don't rely on conversation memory" in out

    def test_no_active_task_packet_renders_safely(self) -> None:
        empty = StatePacket(task_id="", current_stage="no_task")
        out = render_recovery_briefing(empty, recovery_reason="cold_boot")
        assert out.startswith("Active task: (none")
        assert "no_active_task" in out  # from breadcrumb


# ---------------------------------------------------------------------------
# PREREQ-C — fanout run_id dual-write
# ---------------------------------------------------------------------------


class TestFanoutRunId:
    def test_dual_write_keys_present(self) -> None:
        payload = fanout_payload_run_ids("run-abc-1")
        assert payload == {"run_id": "run-abc-1", "child_run_id": "run-abc-1"}

    def test_read_prefers_child_run_id(self) -> None:
        payload = {"run_id": "old", "child_run_id": "new"}
        assert read_child_run_id(payload) == "new"

    def test_read_falls_back_to_run_id(self) -> None:
        legacy_payload = {"run_id": "only-legacy"}
        assert read_child_run_id(legacy_payload) == "only-legacy"

    def test_read_empty_when_missing(self) -> None:
        assert read_child_run_id({}) == ""
        assert read_child_run_id(None) == ""

    def test_read_ignores_non_string_values(self) -> None:
        assert read_child_run_id({"run_id": 123}) == ""

    def test_is_canonical_payload_detects_new_format(self) -> None:
        assert is_canonical_payload({"child_run_id": "x"}) is True
        assert is_canonical_payload({"run_id": "x"}) is False
        assert is_canonical_payload(None) is False
