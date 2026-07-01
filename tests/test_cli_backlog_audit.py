from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from zf.cli.backlog import audit_backlog_status
from zf.cli.main import main


def test_backlog_audit_reports_stale_active_status(tmp_path: Path) -> None:
    backlogs = tmp_path / "backlogs"
    backlogs.mkdir()
    (backlogs / "2026-05-01-P0-old.md").write_text(
        "# Old\n\n> 状态: planning\n",
        encoding="utf-8",
    )
    (backlogs / "2026-05-18-P0-new.md").write_text(
        "# New\n\n> 状态: proposed\n",
        encoding="utf-8",
    )

    findings = audit_backlog_status(
        tmp_path,
        max_age_days=7,
        today=date(2026, 5, 19),
    )

    assert [finding.path for finding in findings] == ["backlogs/2026-05-01-P0-old.md"]
    assert findings[0].status == "planning"


def test_backlog_audit_json_cli_defaults_to_report_only(
    tmp_path: Path,
    capsys,
) -> None:
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "2026-05-01-old-task.md").write_text(
        "# Task\n\nStatus: pending\n",
        encoding="utf-8",
    )

    rc = main([
        "backlog",
        "audit",
        "--root",
        str(tmp_path),
        "--max-age-days",
        "0",
        "--json",
    ])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["path"] == "tasks/2026-05-01-old-task.md"


def test_backlog_audit_uses_latest_status_update(tmp_path: Path) -> None:
    backlogs = tmp_path / "backlogs"
    backlogs.mkdir()
    (backlogs / "2026-05-01-P0-implemented.md").write_text(
        "# Old\n\n> 状态: pending\n\n## Implementation Status Update\n\nStatus: implemented\n",
        encoding="utf-8",
    )

    findings = audit_backlog_status(
        tmp_path,
        max_age_days=7,
        today=date(2026, 5, 19),
    )

    assert findings == []
