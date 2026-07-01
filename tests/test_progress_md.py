"""Tests for progress.md hybrid generation (E1.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.progress import (
    LAYER2_INSIGHT_MARKER,
    regenerate_progress,
)


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    return sd


def test_progress_md_has_required_sections(state_dir: Path):
    text = regenerate_progress(state_dir)
    assert "## Currently Active" in text
    assert "## Completed" in text
    assert "## Recent Events" in text
    assert "## Layer 2 Insights" in text


def test_progress_md_lists_active_features_and_tasks(state_dir: Path):
    fs = FeatureStore(state_dir / "feature_list.json")
    fs.add(Feature(id="F-001", title="OAuth login", status="active"))
    fs.add(Feature(id="F-002", title="Profile page", status="planning"))
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="Design oauth", status="in_progress"))
    text = regenerate_progress(state_dir)
    assert "OAuth login" in text
    assert "F-001" in text
    assert "Design oauth" in text
    assert "T1" in text


def test_progress_md_lists_completed(state_dir: Path):
    fs = FeatureStore(state_dir / "feature_list.json")
    fs.add(Feature(id="F-001", title="Done feature", status="done"))
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="Done task", status="done"))
    text = regenerate_progress(state_dir)
    assert "Done feature" in text
    assert "Done task" in text


def test_progress_md_includes_recent_events(state_dir: Path):
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="user.message", actor="human"))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator", task_id="T1"))
    text = regenerate_progress(state_dir)
    assert "user.message" in text
    assert "task.dispatched" in text


def test_progress_md_hybrid_preserves_layer2_insights(state_dir: Path):
    """If a previous progress.md has a Layer 2 Insights section with content,
    regeneration must preserve that content."""
    progress_path = state_dir / "progress.md"
    initial = regenerate_progress(state_dir)
    progress_path.write_text(initial)
    # Append a Layer 2 insight by hand (simulating what Layer 2 would do)
    insight_block = "\n### 2026-04-14 my-decision\nWe should prioritize OAuth before profile.\n"
    text = progress_path.read_text()
    text = text.replace(LAYER2_INSIGHT_MARKER, LAYER2_INSIGHT_MARKER + insight_block)
    progress_path.write_text(text)
    # Now regenerate
    new_text = regenerate_progress(state_dir)
    assert "my-decision" in new_text
    assert "prioritize OAuth before profile" in new_text


def test_progress_md_deterministic_given_same_inputs(state_dir: Path):
    fs = FeatureStore(state_dir / "feature_list.json")
    fs.add(Feature(id="F-001", title="OAuth", status="active", created_at="2026-04-14T00:00:00Z"))
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="design", status="in_progress"))
    text1 = regenerate_progress(state_dir)
    text2 = regenerate_progress(state_dir)
    # Both runs should produce identical content (modulo any timestamp in the header)
    # We check the body sections are identical
    body1 = text1.split("## Currently Active")[1]
    body2 = text2.split("## Currently Active")[1]
    assert body1 == body2


def test_progress_md_handles_empty_state(state_dir: Path):
    text = regenerate_progress(state_dir)
    assert "## Currently Active" in text
    assert "_(none)_" in text or "_(no" in text


def test_layer2_insight_marker_present(state_dir: Path):
    text = regenerate_progress(state_dir)
    assert LAYER2_INSIGHT_MARKER in text
