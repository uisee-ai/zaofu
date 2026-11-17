"""Removal effects and refusal paths with isolated processes and real data paths."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml

from zf.core.config.presets import generate_preset_yaml


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def removal(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("remove_install", ROOT / "tools/remove_install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root, home = tmp_path / "source", tmp_path / "workspace"
    for folder in (root / ".venv", root / "web/node_modules", root / "web/dist", home):
        folder.mkdir(parents=True)
        (folder / "keep-or-remove").write_text("original")
    (root / "install.sh").write_text("source")
    (root / "pyproject.toml").write_text('[project]\nname="zaofu"\n')
    state = {"owned": True, "busy": False, "foreign": False, "killed": []}

    def command(argv):
        out, code, err = "", 0, ""
        if argv[0] == "ps":
            out = f"{os.getpid()} 0 pytest\n"
            if state["owned"]:
                out += f"900001 1 bash\n900002 900001 {root}/.venv/bin/python -m zf web\n"
            if state["busy"]:
                out += f"900003 1 {root}/.venv/bin/zf start\n"
        elif argv[1] == "list-sessions":
            out = f"$2\tother-service\t/other/checkout\t/other/workspace\n"
            if state["owned"]:
                out += f"$1\tzf-web-8003\t{root}\t{home}\n"
        elif argv[1] == "list-panes":
            if state["owned"]:
                out += f"$1\t900001\t{root}\n"
            if state["foreign"]:
                out += f"$3\t900004\t{root}\n"
        elif argv[1] == "kill-session":
            state["killed"].append(argv[-1])
            state["owned"] = False
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(argv, code, out, err)

    monkeypatch.setattr(module, "command", command)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/fake/tmux")
    return module, root, home, state


def apply(removal, *, yes=True):
    module, root, home, _ = removal
    return module.remove(root, home, home / "controller/zf.yaml", yes=yes)


def assert_intact(removal):
    _, root, home, state = removal
    assert all((root / path / "keep-or-remove").read_text() == "original"
               for path in (".venv", "web/node_modules", "web/dist"))
    assert (home / "keep-or-remove").read_text() == "original"
    assert state["killed"] == []


def test_removal_stops_only_owned_services_preserves_data_and_is_repeatable(removal, tmp_path):
    _, root, home, state = removal
    shared = tmp_path / "shared-herdr"
    shared.write_text("shared dependency")
    (root / ".venv/shared-link").symlink_to(shared)
    (home / "web-action-token").write_text("test-only-credential")
    apply(removal)
    assert state["killed"] == ["$1"]
    assert all(not (root / path).exists() for path in (".venv", "web/node_modules", "web/dist"))
    assert (root / "install.sh").read_text() == "source"
    assert (home / "web-action-token").read_text() == "test-only-credential"
    assert (home / "keep-or-remove").read_text() == "original"
    assert shared.read_text() == "shared dependency"
    apply(removal)
    assert state["killed"] == ["$1"]


def test_preview_and_cancel_do_not_stop_or_delete(removal, monkeypatch):
    module, _, _, _ = removal
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)
    with pytest.raises(ValueError, match="--yes"):
        apply(removal, yes=False)
    assert_intact(removal)
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    apply(removal, yes=False)
    assert_intact(removal)


@pytest.mark.parametrize("cause", ["busy", "foreign", "managed"])
def test_active_or_unowned_installation_blocks_all_mutations(removal, cause):
    _, _, home, state = removal
    if cause == "managed":
        (home / "controller-binding.json").symlink_to(home / "missing-pin")
    else:
        state[cause] = True
    with pytest.raises(ValueError):
        apply(removal)
    assert_intact(removal)


@pytest.mark.parametrize("folder", [".venv", "web", "web/dist"])
def test_linked_removal_targets_are_rejected(removal, tmp_path, folder):
    module, root, _, state = removal
    original = root / folder
    moved = tmp_path / "outside"
    original.rename(moved)
    original.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError, match="linked"):
        apply(removal)
    assert moved.is_dir() and original.is_symlink()
    assert state["killed"] == []


def register_project(home, project, state_dir):
    project.mkdir()
    config = yaml.safe_load(generate_preset_yaml("minimal", "removal-test"))
    config["project"]["state_dir"] = str(state_dir)
    (project / "zf.yaml").write_text(yaml.safe_dump(config))
    registry = home / "workspaces/default/projects.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"projects": [{"root": str(project),
        "config_path": str(project / "zf.yaml"), "state_dir_hint": str(project / "stale-hint")}]}))


def test_configured_project_state_in_generated_directory_is_preserved(removal, tmp_path):
    _, root, home, _ = removal
    register_project(home, tmp_path / "project", root / "web/dist/data")
    with pytest.raises(ValueError, match="project data"):
        apply(removal)
    assert_intact(removal)


def test_workspace_inside_runtime_directory_is_preserved(removal):
    module, root, _, state = removal
    workspace = root / ".venv/user-data"
    workspace.mkdir()
    (workspace / "keep").write_text("private workspace")
    with pytest.raises(ValueError, match="workspace or project data"):
        module.remove(root, workspace, workspace / "controller/zf.yaml", yes=True)
    assert (workspace / "keep").read_text() == "private workspace"
    assert not state["killed"]


def test_active_terminal_in_custom_state_directory_blocks_removal(removal, tmp_path):
    _, _, home, _ = removal
    state_dir = tmp_path / "custom-state"
    state_dir.mkdir()
    register_project(home, tmp_path / "project", state_dir)
    (state_dir / "terminal-resource-registry.v1.json").write_text(json.dumps({"sessions": [{"state":"active"}]}))
    with pytest.raises(ValueError, match="Stop CLI"):
        apply(removal)
    assert_intact(removal)
