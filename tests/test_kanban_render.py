"""Tests for terminal kanban renderer."""

from __future__ import annotations

from zf.core.task.schema import Task
from zf.cli.kanban_render import render_board


class TestRenderBoard:
    def test_empty_board(self):
        result = render_board([])
        assert "empty" in result.lower()

    def test_single_task(self):
        tasks = [Task(title="Build auth", id="TASK-001", status="backlog")]
        result = render_board(tasks, use_color=False)
        assert "Active Board" in result
        assert "delivery history lives in `zf trace delivery`" in result
        assert "Build auth" in result
        assert "backlog" in result.lower()

    def test_multiple_columns(self):
        tasks = [
            Task(title="A", id="T1", status="backlog"),
            Task(title="B", id="T2", status="in_progress"),
            Task(title="C", id="T3", status="done"),
        ]
        result = render_board(tasks, use_color=False)
        assert "backlog" in result.lower()
        assert "in_progress" in result.lower()
        assert "done" in result.lower()

    def test_wip_summary(self):
        tasks = [
            Task(title="A", status="backlog"),
            Task(title="B", status="in_progress"),
        ]
        result = render_board(tasks, use_color=False)
        assert "WIP:" in result

    def test_assigned_to_shown(self):
        tasks = [Task(title="A", id="T1", status="in_progress", assigned_to="dev")]
        result = render_board(tasks, use_color=False)
        assert "@dev" in result

    def test_fanout_queued_blocked_task_projects_to_backlog_with_badge(self):
        tasks = [
            Task(
                title="Queued child",
                id="TASK-Q",
                status="blocked",
                blocked_reason="fanout_queue:F-1:child-6",
            ),
        ]

        result = render_board(tasks, use_color=False)

        assert "TASK-Q" in result
        assert "[queued]" in result
        assert "blocked" in result.lower()

    def test_ordinary_blocked_task_uses_blocked_column(self):
        tasks = [
            Task(
                title="Needs operator",
                id="TASK-B",
                status="blocked",
                blocked_reason="waiting for decision",
            ),
        ]

        result = render_board(tasks, use_color=False)

        assert "blocked (1)" in result.lower()
        assert "Needs operat" in result

    def test_with_color(self):
        tasks = [Task(title="A", status="done")]
        result = render_board(tasks, use_color=True)
        assert "\033[" in result  # ANSI escape present

    def test_board_flag_via_cli(self, tmp_path, monkeypatch, capsys):
        import yaml
        from zf.cli.main import main
        monkeypatch.chdir(tmp_path)
        (tmp_path / "zf.yaml").write_text(yaml.dump({"version": "1.0", "project": {"name": "t"}}))
        main(["init"])
        main(["kanban", "add", "Test task"])
        result = main(["kanban", "--board"])
        assert result == 0
        captured = capsys.readouterr()
        assert "WIP:" in captured.out
