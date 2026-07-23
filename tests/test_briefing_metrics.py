from __future__ import annotations

import json
from pathlib import Path

from zf.runtime.briefing_metrics import write_briefing_with_metrics


def test_briefing_metrics_are_read_only_soft_observations(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    path = state_dir / "briefings" / "judge.md"
    text = "# Judge\n\n## Required Inputs\n\n- one\n\n## Output Contract\n\n- result\n"
    metrics = write_briefing_with_metrics(
        path,
        text,
        state_dir=state_dir,
        stage="thin-judge",
        role="judge-1",
        payload={
            "output_profile_id": "thin-judge-goal-closure",
            "output_profile_revision": "1",
            "required_reads": [{"source_id": "closure"}],
        },
        indexed_skills=["judge-method"],
        auto_injected_skills=["wrapper"],
    )

    saved = json.loads(path.with_suffix(".md.metrics.json").read_text())
    assert path.read_text() == text
    assert saved == metrics
    assert saved["stage_profile"] == "judge"
    assert saved["required_read_count"] == 1
    assert saved["actually_invoked_skills"] == "unknown"
    assert saved["section_bytes"]["Required Inputs"] > 0


def test_briefing_budget_excess_warns_without_rejecting_write(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    path = state_dir / "briefings" / "large.md"
    metrics = write_briefing_with_metrics(
        path,
        "# Impl\n" + ("x" * (13 * 1024)),
        state_dir=state_dir,
        stage="impl",
        role="dev-1",
    )
    assert metrics["soft_budget_exceeded"] is True
    assert path.exists()
