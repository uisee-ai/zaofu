from __future__ import annotations

import json
import subprocess
from pathlib import Path

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.runtime.pane_bindings import PaneBindingManager


class _Runner:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
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
                self.returncode,
                stdout=self.stdout,
                stderr="missing session" if self.returncode else "",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t", state_dir=".zf"),
        session=SessionConfig(tmux_session="zf-t", tmux_layout="pane_grid"),
        roles=[
            RoleConfig(name="dev", instance_id="dev"),
            RoleConfig(name="review", instance_id="review"),
            RoleConfig(name="api", instance_id="api", transport="stream-json"),
        ],
    )


def test_pane_binding_repair_infers_roles_from_workdirs(tmp_path: Path):
    runner = _Runner(
        "%11\t\tproject\t/tmp/p/.zf/workdirs/dev/project\n"
        "%12\t\tproject\t/tmp/p/.zf/workdirs/review/project\n"
    )
    manager = PaneBindingManager(
        project_root=tmp_path,
        state_dir=tmp_path / ".zf",
        config=_config(),
        runner=runner,
    )

    actions = manager.repair()

    assert "dev: bound %11" in actions
    assert "review: bound %12" in actions
    assert any(
        call == ["tmux", "set-option", "-p", "-t", "%11", "@zf_instance_id", "dev"]
        for call in runner.calls
    )
    assert any(
        call == ["tmux", "select-pane", "-t", "%12", "-T", "review"]
        for call in runner.calls
    )
    data = json.loads((tmp_path / ".zf" / "pane_bindings.json").read_text())
    assert data["roles"]["dev"]["pane"] == "%11"
    assert data["roles"]["review"]["cwd"].endswith("/.zf/workdirs/review/project")
    assert "api" not in data["roles"]


def test_pane_binding_doctor_reports_unrepaired_identity_and_binding(
    tmp_path: Path,
):
    runner = _Runner(
        "%11\t\tproject\t/tmp/p/.zf/workdirs/dev/project\n"
        "%12\treview\treview\t/tmp/p/.zf/workdirs/review/project\n"
    )
    manager = PaneBindingManager(
        project_root=tmp_path,
        state_dir=tmp_path / ".zf",
        config=_config(),
        runner=runner,
    )

    issues = manager.doctor()

    assert "dev: pane %11 @zf_instance_id=''" in issues
    assert "dev: pane binding missing" in issues
    assert "review: pane binding missing" in issues


def test_pane_binding_doctor_accepts_matching_binding(tmp_path: Path):
    binding = tmp_path / ".zf" / "pane_bindings.json"
    binding.parent.mkdir()
    binding.write_text(
        json.dumps({
            "session": "zf-t",
            "window": "roles",
            "roles": {
                "dev": {
                    "pane": "%11",
                    "cwd": "/tmp/p/.zf/workdirs/dev/project",
                    "session": "zf-t",
                    "window": "roles",
                },
                "review": {
                    "pane": "%12",
                    "cwd": "/tmp/p/.zf/workdirs/review/project",
                    "session": "zf-t",
                    "window": "roles",
                },
            },
        })
        + "\n"
    )
    runner = _Runner(
        "%11\tdev\tdev\t/tmp/p/.zf/workdirs/dev/project\n"
        "%12\treview\treview\t/tmp/p/.zf/workdirs/review/project\n"
    )
    manager = PaneBindingManager(
        project_root=tmp_path,
        state_dir=tmp_path / ".zf",
        config=_config(),
        runner=runner,
    )

    assert manager.doctor() == []


def test_pane_binding_doctor_skips_window_per_role(tmp_path: Path):
    config = ZfConfig(
        project=ProjectConfig(name="t", state_dir=".zf"),
        session=SessionConfig(tmux_session="zf-t", tmux_layout="window_per_role"),
        roles=[RoleConfig(name="dev", instance_id="dev")],
    )
    runner = _Runner("", returncode=1)
    manager = PaneBindingManager(
        project_root=tmp_path,
        state_dir=tmp_path / ".zf",
        config=config,
        runner=runner,
    )

    assert manager.doctor() == []
    assert runner.calls == []
