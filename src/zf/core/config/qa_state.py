"""State locations a development check can reach before or after changing cwd."""

from __future__ import annotations

import json
import os
from pathlib import Path

from zf.core.config.project_context import _find_project_root, _resolve_state_path
from zf.core.config.loader import load_config


def configured_state(directory: Path, *, exact_project: bool = False) -> Path:
    root = directory if exact_project else _find_project_root(directory)
    path = root / "zf.yaml"
    name = load_config(path).project.state_dir if path.is_file() else ".zf"
    return _resolve_state_path(root, name)


def assigned_states(cwd: Path) -> tuple[Path | None, set[Path]]:
    project = os.environ.get("ZF_PROJECT_ROOT", "").strip()
    state = os.environ.get("ZF_STATE_DIR", "").strip()
    root = (cwd / Path(project).expanduser()).resolve() if project else None
    states = {configured_state(root, exact_project=True)} if root else set()
    if state:
        states.add(_resolve_state_path(root or _find_project_root(cwd), state))
    return root, states


def has_ledger(state: Path) -> bool:
    return (state / "events.jsonl").is_file() or any(file.is_file() for file in (state / "events").glob("*.jsonl"))


def certified_checkout_state(checkout: Path) -> Path | None:
    """A clean target may gain fixture events between tiers of the same QA invocation."""
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", ""))
    home = Path(os.environ.get("HOME", ""))
    private = runtime.parent
    if (not runtime.is_absolute() or private.parent != Path("/tmp") or not private.name.startswith("zf-qa-")
            or runtime != private / "run" or home != private / "home"):
        return None
    try:
        receipt = json.loads((runtime / "clean-checkout.json").read_text())
        if isinstance(receipt, dict) and receipt.get("checkout") == str(checkout):
            return Path(receipt["state_dir"])
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None
