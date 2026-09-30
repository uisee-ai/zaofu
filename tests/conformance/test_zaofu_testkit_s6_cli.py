"""S6 CLI surface cases for project state-directory isolation."""

from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main
from zf.core.events.log import EventLog


def _init_project(tmp_path: Path, monkeypatch) -> Path:  # noqa: ANN001
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: s6-cli\n"
        "  state_dir: runtime-state\n",
        encoding="utf-8",
    )
    assert main(["init"]) == 0
    return tmp_path / "runtime-state"


def test_emit_cli_uses_configured_state_dir_and_not_default_zf(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = _init_project(tmp_path, monkeypatch)

    assert main(["emit", "s6.cli.probe", "--payload", '{"ok":true}']) == 0

    events = EventLog(state_dir / "events.jsonl").read_all()
    probe = [event for event in events if event.type == "s6.cli.probe"]
    assert len(probe) == 1
    assert probe[0].payload == {"ok": True}
    assert (state_dir / "event_index.json").is_file()
    assert not (tmp_path / ".zf").exists()


def test_events_cli_queries_same_configured_state_dir(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    state_dir = _init_project(tmp_path, monkeypatch)
    assert main(["emit", "s6.cli.query"]) == 0
    capsys.readouterr()

    assert main(["events", "--type", "s6.cli.query", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert any(
        event["type"] == "s6.cli.query"
        for event in result["data"]["events"]
    )
    assert (state_dir / "events.jsonl").is_file()
    assert not (tmp_path / ".zf").exists()
