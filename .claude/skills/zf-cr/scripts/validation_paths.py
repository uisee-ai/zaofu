"""Print paths for an explicitly selected validation project; never create state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zf.core.config.loader import load_config


def validation_paths(project: Path | str) -> dict[str, str]:
    if not str(project).strip():
        raise ValueError("An explicit non-empty project path is required")
    root = Path(project).expanduser().resolve()
    config_path = root / "zf.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"Missing explicit project config: {config_path}")
    # Deliberately use the explicit config, not another session's ZF_* overrides.
    config = load_config(config_path)
    state = Path(config.project.state_dir)
    if not state.is_absolute():
        state = root / state
    return {
        "project_root": str(root),
        "config_path": str(config_path),
        "state_dir": str(state.resolve()),
        "events": str(state.resolve() / "events.jsonl"),
        "tasks": str(root / "tasks"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    args = parser.parse_args()
    print(json.dumps(validation_paths(args.project), ensure_ascii=False, indent=2))
