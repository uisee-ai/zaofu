"""ZF-PWF-MEM-001 — 4-file working-memory projection tests (doc 41 §4.1)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from zf.core.state.state_packet import (
    StatePacket,
    StatePacketContract,
    StatePacketEvidence,
    StatePacketOwner,
)
from zf.runtime.working_memory_projection import (
    ProjectionInputs,
    list_projection_files,
    projection_dir,
    render_attempt_ledger,
    render_findings,
    render_plan,
    render_progress,
    write_projection_files,
)


@dataclass
class _Ev:
    type: str = ""
    id: str = ""
    ts: str = ""
    payload: dict | None = None


def _packet(**kw) -> StatePacket:
    defaults = {
        "task_id": "TASK-MEM",
        "current_stage": "implement",
        "next_event": "dev.build.done",
        "owner": StatePacketOwner(role="dev", instance_id="dev-1"),
        "contract": StatePacketContract(
            behavior="ship the thing",
            acceptance=("step1", "step2"),
        ),
    }
    defaults.update(kw)
    return StatePacket(**defaults)


# ---------------------------------------------------------------------------
# Header invariant — every file must declare "projection only"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("renderer", [
    render_plan, render_findings, render_progress, render_attempt_ledger,
])
def test_every_projection_has_projection_only_header(renderer) -> None:
    inputs = ProjectionInputs(
        packet=_packet(),
        source_event_ids=("evt-1",),
        state_packet_ref=".zf/state/state-packet.json",
    )
    text = renderer(inputs)
    assert "projection only, not runtime truth" in text
    assert "source_events:" in text
    assert "state_packet_ref:" in text
    assert "generated_at:" in text


# ---------------------------------------------------------------------------
# render_plan
# ---------------------------------------------------------------------------


def test_plan_renders_objective_and_stage() -> None:
    pkt = _packet(objective="deliver feature X")
    text = render_plan(ProjectionInputs(packet=pkt))
    assert "deliver feature X" in text
    assert "Current stage" in text
    assert "implement" in text


def test_plan_acceptance_renders_as_checklist() -> None:
    text = render_plan(ProjectionInputs(packet=_packet()))
    assert "- [ ] step1" in text
    assert "- [ ] step2" in text


def test_plan_completed_renders_checked() -> None:
    pkt = _packet(completed=("dev.build.done",))
    text = render_plan(ProjectionInputs(packet=pkt))
    assert "- [x] dev.build.done" in text


def test_plan_no_task_renders_safely() -> None:
    pkt = StatePacket(task_id="", current_stage="no_task")
    text = render_plan(ProjectionInputs(packet=pkt))
    assert "no task" in text


# ---------------------------------------------------------------------------
# render_findings
# ---------------------------------------------------------------------------


def test_findings_lists_research_paths() -> None:
    text = render_findings(ProjectionInputs(
        packet=_packet(),
        research_paths=("docs/research/x.md", "docs/research/y.md"),
    ))
    assert "docs/research/x.md" in text
    assert "docs/research/y.md" in text


def test_findings_renders_evidence_from_packet() -> None:
    pkt = _packet(evidence=(
        StatePacketEvidence(kind="test", path="/log", status="passed",
                             event_id="evt-1"),
    ))
    text = render_findings(ProjectionInputs(packet=pkt))
    assert "test" in text
    assert "passed" in text
    assert "evt-1" in text


def test_findings_empty_fallback() -> None:
    text = render_findings(ProjectionInputs(packet=_packet()))
    assert "No findings recorded" in text


# ---------------------------------------------------------------------------
# render_progress
# ---------------------------------------------------------------------------


def test_progress_lists_events_chronologically() -> None:
    inputs = ProjectionInputs(
        packet=_packet(),
        events=(
            _Ev(type="task.dispatched", id="evt-1", ts="2026-05-18T10:00:00Z"),
            _Ev(type="dev.build.done", id="evt-2", ts="2026-05-18T11:00:00Z"),
        ),
    )
    text = render_progress(inputs)
    assert "task.dispatched" in text
    assert "dev.build.done" in text
    assert text.index("task.dispatched") < text.index("dev.build.done")


def test_progress_renders_decisions() -> None:
    pkt = _packet(decisions=("chose approach A", "rejected approach B"))
    text = render_progress(ProjectionInputs(packet=pkt))
    assert "approach A" in text
    assert "approach B" in text


def test_progress_empty_fallback() -> None:
    text = render_progress(ProjectionInputs(packet=_packet()))
    assert "No progress recorded" in text


# ---------------------------------------------------------------------------
# render_attempt_ledger
# ---------------------------------------------------------------------------


def test_attempt_ledger_lists_rework_events() -> None:
    inputs = ProjectionInputs(
        packet=_packet(),
        rework_events=(
            _Ev(
                type="review.rejected",
                ts="2026-05-18T12:00:00Z",
                payload={"reason": "spec mismatch"},
            ),
            _Ev(
                type="test.failed",
                ts="2026-05-18T13:00:00Z",
                payload={"reason": "regression"},
            ),
        ),
    )
    text = render_attempt_ledger(inputs)
    assert "review.rejected" in text
    assert "spec mismatch" in text
    assert "test.failed" in text
    assert "regression" in text


def test_attempt_ledger_empty_fallback() -> None:
    text = render_attempt_ledger(ProjectionInputs(packet=_packet()))
    assert "No rework attempts" in text


# ---------------------------------------------------------------------------
# write_projection_files end-to-end
# ---------------------------------------------------------------------------


def test_write_projection_files_creates_four(tmp_path: Path) -> None:
    files = write_projection_files(
        tmp_path,
        ProjectionInputs(
            packet=_packet(),
            state_packet_ref=".zf/state/state-packet.json",
        ),
    )
    assert set(files) == {"plan", "findings", "progress", "attempt_ledger"}
    for p in files.values():
        assert p.exists()
    base = projection_dir(tmp_path, "TASK-MEM")
    assert (base / "plan.md").exists()
    assert (base / "findings.md").exists()
    assert (base / "progress.md").exists()
    assert (base / "attempt-ledger.md").exists()


def test_write_projection_skipped_when_task_id_empty(tmp_path: Path) -> None:
    """No task → no projection files (no-op)."""
    out = write_projection_files(
        tmp_path,
        ProjectionInputs(packet=StatePacket(task_id="", current_stage="no_task")),
    )
    assert out == {}
    assert not (tmp_path / "projections").exists()


def test_list_projection_files_returns_canonical_order(tmp_path: Path) -> None:
    write_projection_files(
        tmp_path, ProjectionInputs(packet=_packet()),
    )
    files = list_projection_files(tmp_path, "TASK-MEM")
    names = [p.name for p in files]
    assert names == [
        "plan.md", "findings.md", "progress.md", "attempt-ledger.md",
    ]


def test_list_projection_files_missing_task_returns_empty(tmp_path: Path) -> None:
    assert list_projection_files(tmp_path, "TASK-NEVER") == []
