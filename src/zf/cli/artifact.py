"""Artifact helper commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import EventSigningConfigError, event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_manifest import (
    normalize_artifact_kind,
    validate_artifact_manifest,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("artifact", help="Artifact manifest helpers")
    sub = parser.add_subparsers(dest="artifact_cmd")

    manifest = sub.add_parser("manifest", help="Artifact manifest commands")
    manifest_sub = manifest.add_subparsers(dest="artifact_manifest_cmd")

    create = manifest_sub.add_parser(
        "create",
        help="Create a deterministic artifact manifest JSON",
    )
    create.add_argument("--task", required=True, help="Task id for the manifest")
    create.add_argument("--role", required=True, help="Role producing the manifest")
    create.add_argument(
        "--status",
        default="proposed",
        choices=["draft", "proposed", "accepted", "superseded", "rejected"],
        help="Artifact status applied to all --kind refs",
    )
    create.add_argument(
        "--kind",
        action="append",
        default=[],
        metavar="KIND=PATH",
        help="Artifact kind/path pair; repeat for multiple refs",
    )
    create.add_argument("--feature-id", default="", help="Optional feature id")
    create.add_argument(
        "--skill",
        action="append",
        default=[],
        help="Skill used to produce artifacts; repeat as needed",
    )
    create.add_argument(
        "--workdir",
        default="",
        help="Optional runtime workdir root to read artifact files from",
    )
    create.add_argument(
        "--output",
        default="-",
        help="Output path, or '-' for stdout (default)",
    )
    create.add_argument(
        "--emit",
        action="store_true",
        help="Append artifact.manifest.published to events.jsonl",
    )
    create.add_argument(
        "--state-dir",
        default=None,
        help="Runtime state dir override",
    )
    create.set_defaults(func=_run_manifest_create)

    list_cmd = sub.add_parser(
        "list",
        help="List immutable inputs available to one provider attempt",
    )
    list_cmd.add_argument("--attempt", required=True, help="Attempt or dispatch id")
    list_cmd.add_argument("--state-dir", default=None, help="Runtime state dir override")
    list_cmd.set_defaults(func=_run_attempt_list)

    read_cmd = sub.add_parser(
        "read",
        help="Read one attempt input and append read evidence",
    )
    read_cmd.add_argument("--attempt", required=True, help="Attempt or dispatch id")
    read_cmd.add_argument("--source", required=True, help="Source id from artifact list")
    read_cmd.add_argument("--artifact", required=True, help="Artifact id from artifact list")
    read_cmd.add_argument("--json-path", default="$", help="Supported $.field[index] selector")
    read_cmd.add_argument("--max-items", type=int, default=0)
    read_cmd.add_argument("--max-chars", type=int, default=0)
    read_cmd.add_argument("--state-dir", default=None, help="Runtime state dir override")
    read_cmd.set_defaults(func=_run_attempt_read)

    parser.set_defaults(func=_run_help)
    manifest.set_defaults(func=_run_help)


def _run_help(args: argparse.Namespace) -> int:
    print("usage: zf artifact {manifest create|list|read} ...")
    return 0


def _run_attempt_list(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
        manifest = _load_attempt_source_manifest(context.state_dir, args.attempt)
    except (ConfigError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _run_attempt_read(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
        manifest = _load_attempt_source_manifest(context.state_dir, args.attempt)
        from zf.runtime.artifact_read_ledger import read_attempt_artifact

        result = read_attempt_artifact(
            context.state_dir,
            manifest=manifest,
            source_id=args.source,
            artifact_id=args.artifact,
            json_path=args.json_path,
            max_items=max(0, int(args.max_items or 0)),
            max_chars=max(0, int(args.max_chars or 0)),
        )
    except (ConfigError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(result["content"])
    return 0


def _load_attempt_source_manifest(state_dir: Path, attempt_id: str) -> dict[str, Any]:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(attempt_id)).strip("-._") or "attempt"
    root = state_dir / "artifacts" / "attempts" / safe / "source-manifests"
    manifests: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and str(value.get("attempt_id") or "") == str(attempt_id):
            manifests.append(value)
    if not manifests:
        raise ValueError(f"attempt source manifest not found: {attempt_id}")
    canonical = {
        json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for item in manifests
    }
    if len(canonical) != 1:
        raise ValueError(f"attempt has divergent source manifests: {attempt_id}")
    return manifests[0]


def _run_manifest_create(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    source_root = _source_root(args.workdir, context.project_root)
    try:
        refs = [
            _ref_from_kind_pair(
                pair,
                status=args.status,
                project_root=context.project_root,
                state_dir=context.state_dir,
                source_root=source_root,
            )
            for pair in args.kind
        ]
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if not refs:
        print("Error: at least one --kind KIND=PATH is required", file=sys.stderr)
        return 2

    manifest: dict[str, Any] = {
        "task_id": args.task,
        "role": args.role,
        "skills_used": list(args.skill or []),
        "artifact_refs": refs,
    }
    if args.feature_id:
        manifest["feature_id"] = args.feature_id

    validation = validate_artifact_manifest(
        manifest,
        project_root=context.project_root,
        state_dir=context.state_dir,
    )
    if not validation.ok:
        print("Error: invalid manifest: " + "; ".join(validation.errors), file=sys.stderr)
        return 2

    output: dict[str, Any] = {"manifest": manifest}
    if args.emit:
        if not context.state_dir.exists():
            print("Error: not initialized. Run 'zf init' first.", file=sys.stderr)
            return 1
        try:
            event_log = event_log_from_project(context.state_dir, config=context.config)
        except EventSigningConfigError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        written = EventWriter(event_log).append(ZfEvent(
            type="artifact.manifest.published",
            actor=args.role,
            task_id=args.task,
            payload=manifest,
        ))
        event_log.close()
        output["event_id"] = written.id

    text = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output == "-":
        print(text, end="")
    else:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
    return 0


def _ref_from_kind_pair(
    pair: str,
    *,
    status: str,
    project_root: Path,
    state_dir: Path,
    source_root: Path | None,
) -> dict[str, Any]:
    if "=" not in pair:
        raise ValueError(f"--kind value must be KIND=PATH, got {pair!r}")
    raw_kind, raw_path = pair.split("=", 1)
    kind = normalize_artifact_kind(raw_kind)
    rel_path = raw_path.strip()
    if not kind or not rel_path:
        raise ValueError(f"--kind value must include non-empty kind and path: {pair!r}")
    source = _resolve_source_path(
        rel_path,
        project_root=project_root,
        state_dir=state_dir,
        source_root=source_root,
    )
    if not source.exists() or not source.is_file():
        raise ValueError(f"artifact file not found: {rel_path}")
    ref: dict[str, Any] = {
        "kind": kind,
        "path": Path(rel_path).as_posix(),
        "sha256": _sha256(source),
        "summary": Path(rel_path).name,
        "status": status,
    }
    if source_root is not None:
        ref["workdir_path"] = str(source_root)
    return ref


def _resolve_source_path(
    raw_path: str,
    *,
    project_root: Path,
    state_dir: Path,
    source_root: Path | None,
) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    candidates = []
    if source_root is not None:
        candidates.append(source_root / path)
    candidates.extend([project_root / path, state_dir / path])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _source_root(raw: str, project_root: Path) -> Path | None:
    if not raw.strip():
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
