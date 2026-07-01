from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from zf.cli.main import main


class _Runner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(
        self,
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if args[:2] == ["tmux", "list-panes"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=(
                    "%11\t\tproject\t/tmp/p/.zf/workdirs/dev/project\n"
                    "%12\t\tproject\t/tmp/p/.zf/workdirs/review/project\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


def _write_project(path: Path) -> None:
    (path / ".zf").mkdir()
    (path / "zf.yaml").write_text(
        yaml.safe_dump({
            "version": "1.0",
            "project": {"name": "t", "state_dir": ".zf"},
            "session": {"tmux_session": "zf-t", "tmux_layout": "pane_grid"},
            "roles": [
                {"name": "dev", "backend": "mock"},
                {"name": "review", "backend": "mock"},
            ],
        }),
        encoding="utf-8",
    )


def test_panes_repair_cli_writes_binding(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = _Runner()
    monkeypatch.setattr("zf.runtime.pane_bindings.subprocess.run", runner)

    result = main(["panes", "repair"])

    assert result == 0
    out = capsys.readouterr().out
    assert "Pane repair:" in out
    assert (tmp_path / ".zf" / "pane_bindings.json").exists()
    assert any(
        call == ["tmux", "set-option", "-p", "-t", "%11", "@zf_instance_id", "dev"]
        for call in runner.calls
    )


def test_doctor_panes_cli_reports_issues(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    _write_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("zf.runtime.pane_bindings.subprocess.run", _Runner())

    result = main(["doctor", "panes"])

    assert result == 1
    out = capsys.readouterr().out
    assert "Pane issues:" in out
    assert "pane binding missing" in out
