"""Read-only baseline preview and explicit controlled apply for existing projects."""
from __future__ import annotations

import json
from pathlib import Path


def register(subparsers) -> None:
    parser = subparsers.add_parser("git-baseline", help="Preview or apply an exact initial Git file manifest")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--paths-file", type=Path, help="JSON array of explicit project-relative source files")
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--branch", default="main")
    parser.add_argument("--manifest", type=Path, help="Previously reviewed manifest to apply")
    parser.add_argument("--apply", action="store_true")
    parser.set_defaults(func=run)


def run(args) -> int:
    from zf.core.config.loader import load_config
    from zf.core.events.factory import event_log_from_project
    from zf.core.events.writer import EventWriter
    from zf.runtime.control_actions import ControlledActionService
    from zf.runtime.project_git_baseline import preview_git_baseline

    root = args.root.expanduser().resolve()
    try:
        config = load_config(root / "zf.yaml")
        state = Path(config.project.state_dir)
        state = state if state.is_absolute() else root / state
        if args.apply:
            if not args.manifest:
                raise ValueError("--apply requires a reviewed --manifest")
            payload = {"manifest": json.loads(args.manifest.read_text())}
            if not isinstance(payload["manifest"], dict):
                raise ValueError("--manifest must contain an object")
            writer = EventWriter(event_log_from_project(state, config=config))
            requested = writer.emit("control.action.requested", actor="zf-cli", payload={
                "action": "project-git-baseline", "manifest_digest": payload["manifest"].get("manifest_digest"),
            })
            result = ControlledActionService(state, writer, config=config, project_root=root,
                                             actor="zf-cli", source="project-git-baseline", surface="cli").execute(
                action="project-git-baseline", requested_action="project-git-baseline",
                payload=payload, requested=requested)
        else:
            if not args.paths_file:
                raise ValueError("preview requires --paths-file; files are never selected implicitly")
            paths = json.loads(args.paths_file.read_text())
            if not isinstance(paths, list) or any(not isinstance(x, str) for x in paths):
                raise ValueError("--paths-file must contain a JSON array of relative file paths")
            result = preview_git_baseline(root, state, paths=paths, exclude_paths=args.exclude, branch=args.branch)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False))
        return 1
