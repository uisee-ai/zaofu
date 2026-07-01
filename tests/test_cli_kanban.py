"""Tests for zf kanban CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from zf.cli.main import main


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "version": "1.0",
        "project": {"name": "test"},
        "roles": [{"name": "dev", "backend": "mock"}],
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))
    main(["init"])
    return tmp_path


class TestKanbanAdd:
    def test_add_task(self, project_dir: Path, capsys):
        result = main(["kanban", "add", "Implement auth"])
        assert result == 0
        captured = capsys.readouterr()
        assert "TASK-" in captured.out

    def test_add_id_only_for_scripts(self, project_dir: Path, capsys):
        result = main(["kanban", "add", "Implement auth", "--id-only"])

        assert result == 0
        captured = capsys.readouterr()
        assert captured.out.startswith("TASK-")
        assert captured.out.strip().count("\n") == 0
        assert "Added:" not in captured.out

    def test_add_json_for_scripts(self, project_dir: Path, capsys):
        result = main(["kanban", "add", "Implement auth", "--json"])

        assert result == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["task_id"].startswith("TASK-")
        assert payload["title"] == "Implement auth"

    def test_add_with_key(self, project_dir: Path, capsys):
        result = main(["kanban", "add", "Auth", "--key", "auth:jwt"])
        assert result == 0
        captured = capsys.readouterr()
        assert "auth:jwt" in captured.out or "TASK-" in captured.out

    def test_add_accepts_positional_feature_id(self, project_dir: Path):
        result = main(["kanban", "add", "F-1234abcd", "Implement", "greet"])

        assert result == 0
        kanban = json.loads((project_dir / ".zf" / "kanban.json").read_text())
        assert kanban[0]["title"] == "Implement greet"
        assert kanban[0]["key"].startswith("F-1234abcd:")
        events = [
            json.loads(line)
            for line in (project_dir / ".zf" / "events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        created = next(event for event in events if event["type"] == "task.created")
        assert created["payload"]["feature_id"] == "F-1234abcd"

    def test_add_feature_option_prefixes_custom_key(self, project_dir: Path):
        result = main([
            "kanban", "add", "Implement greet",
            "--feature", "F-1234abcd",
            "--key", "greet",
        ])

        assert result == 0
        kanban = json.loads((project_dir / ".zf" / "kanban.json").read_text())
        assert kanban[0]["key"] == "F-1234abcd:greet"

    def test_add_persists(self, project_dir: Path):
        main(["kanban", "add", "Test task"])
        kanban = json.loads((project_dir / ".zf" / "kanban.json").read_text())
        assert len(kanban) == 1
        assert kanban[0]["title"] == "Test task"

    def test_add_uses_project_state_dir(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = {
            "version": "1.0",
            "project": {"name": "test", "state_dir": "runtime-state"},
            "roles": [{"name": "dev", "backend": "mock"}],
        }
        (tmp_path / "zf.yaml").write_text(yaml.dump(config))
        main(["init"])

        result = main(["kanban", "add", "Runtime task"])

        assert result == 0
        kanban = json.loads((tmp_path / "runtime-state" / "kanban.json").read_text())
        assert kanban[0]["title"] == "Runtime task"
        assert not (tmp_path / ".zf").exists()


class TestKanbanMove:
    def test_move_task(self, project_dir: Path, capsys):
        main(["kanban", "add", "Task A"])
        captured = capsys.readouterr()
        task_id = captured.out.strip().split()[-1]  # last word is ID
        # Get actual task id from kanban
        kanban = json.loads((project_dir / ".zf" / "kanban.json").read_text())
        task_id = kanban[0]["id"]

        result = main(["kanban", "move", task_id, "in_progress"])
        assert result == 0

        kanban = json.loads((project_dir / ".zf" / "kanban.json").read_text())
        assert kanban[0]["status"] == "in_progress"

    def test_move_invalid_transition(self, project_dir: Path):
        main(["kanban", "add", "Task A"])
        kanban = json.loads((project_dir / ".zf" / "kanban.json").read_text())
        task_id = kanban[0]["id"]
        # backlog -> done is invalid
        result = main(["kanban", "move", task_id, "done"])
        assert result != 0

    def test_move_nonexistent_task(self, project_dir: Path):
        result = main(["kanban", "move", "TASK-FAKE", "in_progress"])
        assert result != 0


class TestKanbanAssign:
    def test_assign_task(self, project_dir: Path):
        main(["kanban", "add", "Task A"])
        kanban = json.loads((project_dir / ".zf" / "kanban.json").read_text())
        task_id = kanban[0]["id"]

        result = main(["kanban", "assign", task_id, "dev"])
        assert result == 0

        kanban = json.loads((project_dir / ".zf" / "kanban.json").read_text())
        assert kanban[0]["assigned_to"] == "dev"

    def test_handoff_updates_contract_assignment_and_events_idempotently(
        self,
        project_dir: Path,
        capsys,
    ):
        main(["kanban", "add", "Task A"])
        capsys.readouterr()
        kanban = json.loads((project_dir / ".zf" / "kanban.json").read_text())
        task_id = kanban[0]["id"]
        contract_path = project_dir / "contract.json"
        contract_path.write_text(json.dumps({
            "contract": {
                "phase": "implementation",
                "owner_role": "dev",
                "behavior": "finish task A",
                "acceptance": ["exit_code=0"],
            },
        }), encoding="utf-8")

        args = [
            "kanban", "handoff", task_id,
            "--contract-file", str(contract_path),
            "--assign", "dev",
            "--trigger-event", "evt-critic-approved",
            "--json",
        ]
        result = main(args)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["task_id"] == task_id
        assert payload["assigned_to"] == "dev"
        assert payload["idempotent"] is False
        kanban = json.loads((project_dir / ".zf" / "kanban.json").read_text())
        assert kanban[0]["assigned_to"] == "dev"
        assert kanban[0]["contract"]["phase"] == "implementation"
        assert kanban[0]["contract"]["owner_role"] == "dev"
        assert kanban[0]["contract"]["acceptance"] == "exit_code=0"

        result = main(args)

        assert result == 0
        replay = json.loads(capsys.readouterr().out)
        assert replay["idempotent"] is True
        events = [
            json.loads(line)
            for line in (project_dir / ".zf" / "events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        handoff_contracts = [
            event for event in events
            if event["type"] == "task.contract.update"
            and event["task_id"] == task_id
            and event["payload"].get("source") == "kanban_handoff"
        ]
        handoff_assigns = [
            event for event in events
            if event["type"] == "task.assigned"
            and event["task_id"] == task_id
            and event["payload"].get("source") == "kanban_handoff"
        ]
        assert len(handoff_contracts) == 1
        assert len(handoff_assigns) == 1
        assert handoff_assigns[0]["payload"]["assignee"] == "dev"
        assert handoff_assigns[0]["payload"]["trigger_event_id"] == "evt-critic-approved"


class TestKanbanShow:
    def test_show_task(self, project_dir: Path, capsys):
        main(["kanban", "add", "Task A"])
        kanban = json.loads((project_dir / ".zf" / "kanban.json").read_text())
        task_id = kanban[0]["id"]

        result = main(["kanban", "show", task_id])
        assert result == 0
        captured = capsys.readouterr()
        assert "Task A" in captured.out
        assert task_id in captured.out

    def test_show_nonexistent(self, project_dir: Path):
        result = main(["kanban", "show", "TASK-FAKE"])
        assert result != 0


class TestKanbanList:
    def test_list_board(self, project_dir: Path, capsys):
        main(["kanban", "add", "Task A"])
        main(["kanban", "add", "Task B"])
        result = main(["kanban"])
        assert result == 0
        captured = capsys.readouterr()
        assert "Task A" in captured.out
        assert "Task B" in captured.out

    def test_ready_shows_unblocked(self, project_dir: Path, capsys):
        main(["kanban", "add", "Task A"])
        result = main(["kanban", "ready"])
        assert result == 0
        captured = capsys.readouterr()
        assert "Task A" in captured.out

    def test_open_shows_non_terminal(self, project_dir: Path, capsys):
        main(["kanban", "add", "Task A"])
        result = main(["kanban", "open"])
        assert result == 0
        captured = capsys.readouterr()
        assert "Task A" in captured.out

    def test_pending_shows_backlog(self, project_dir: Path, capsys):
        main(["kanban", "add", "Task A"])
        result = main(["kanban", "pending"])
        assert result == 0
        captured = capsys.readouterr()
        assert "Task A" in captured.out


class TestKanbanExport:
    @pytest.mark.parametrize(
        "args_template",
        [
            ["kanban", "--state-dir", "{state_dir}", "export", "--format", "json"],
            ["kanban", "export", "--state-dir", "{state_dir}", "--format", "json"],
        ],
    )
    def test_export_accepts_state_dir_before_or_after_subcommand(
        self,
        project_dir: Path,
        capsys,
        args_template: list[str],
    ):
        main(["kanban", "add", "Exported task"])
        capsys.readouterr()
        state_dir = str(project_dir / ".zf")
        args = [
            part.format(state_dir=state_dir)
            for part in args_template
        ]

        result = main(args)

        assert result == 0
        captured = capsys.readouterr()
        tasks = json.loads(captured.out)
        assert tasks[0]["title"] == "Exported task"
