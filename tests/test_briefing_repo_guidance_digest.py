"""Regression test for B-SKILL-10: generate_task_briefing must inject a per-task
repo-guidance + enabled-skills digest.

Before the fix the briefing contained no AGENTS.md/CLAUDE.md existence reminder
and no enabled-skills summary (smoke: 0 grep hits for AGENTS.md / CLAUDE.md /
skill name). The fix adds a "## Repo Guidance (read-only)" projection.

Evidence: docs/records/2026-06-16-axisB-code-debt-smoke-REPORT.md
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.task.schema import Task, TaskContract
from zf.runtime.injection import generate_task_briefing


def _role_with_skill() -> RoleConfig:
    role = RoleConfig(name="dev", instance_id="dev-1", backend="claude-code")
    role.skills = ["my-enabled-skill"]
    return role


def _task() -> Task:
    return Task(
        id="T-bskill10",
        title="implement widget",
        status="in_progress",
        contract=TaskContract(behavior="do the thing", verification="pytest"),
    )


def test_briefing_includes_guidance_files_and_enabled_skills(tmp_path: Path) -> None:
    proj = tmp_path
    (proj / "AGENTS.md").write_text("# AGENTS\n", encoding="utf-8")
    (proj / "CLAUDE.md").write_text("# CLAUDE\n", encoding="utf-8")

    briefing = generate_task_briefing(
        ZfConfig(), _role_with_skill(), _task(), project_root=proj
    )

    assert "AGENTS.md ✓" in briefing
    assert "CLAUDE.md ✓" in briefing
    assert "my-enabled-skill" in briefing
    assert "Repo Guidance" in briefing


def test_briefing_marks_absent_guidance_files(tmp_path: Path) -> None:
    # empty project: no AGENTS.md / CLAUDE.md
    briefing = generate_task_briefing(
        ZfConfig(), _role_with_skill(), _task(), project_root=tmp_path
    )
    assert "AGENTS.md ✗" in briefing
    assert "CLAUDE.md ✗" in briefing


def test_briefing_skills_summary_without_project_root() -> None:
    # project_root omitted (back-compat): still surfaces the enabled-skills line
    briefing = generate_task_briefing(ZfConfig(), _role_with_skill(), _task())
    assert "my-enabled-skill" in briefing
    assert "Guidance files:" not in briefing  # no path -> no existence digest


def test_briefing_skills_none_when_role_has_no_skills() -> None:
    role = RoleConfig(name="dev", instance_id="dev-1", backend="claude-code")
    briefing = generate_task_briefing(ZfConfig(), role, _task())
    assert "Enabled skills (dev): none" in briefing


def test_briefing_smoke_repro_now_passes(tmp_path: Path) -> None:
    """The exact pre-fix smoke assertion (omits digest) must now be False."""
    proj = tmp_path
    (proj / "AGENTS.md").write_text("# AGENTS\n", encoding="utf-8")
    (proj / "CLAUDE.md").write_text("# CLAUDE\n", encoding="utf-8")
    briefing = generate_task_briefing(
        ZfConfig(), _role_with_skill(), _task(), project_root=proj
    )

    def count(s: str) -> int:
        return sum(1 for ln in briefing.splitlines() if s in ln)

    omits = (
        count("AGENTS.md") == 0
        and count("CLAUDE.md") == 0
        and count("my-enabled-skill") == 0
    )
    assert omits is False
