"""zf project — project-scoped review and insight commands."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from zf.core.config.loader import ConfigError
from zf.core.safety.path_guard import PathGuard, PathGuardError
from zf.core.workspace.project_initializer import ProjectInitializer
from zf.core.workflow.request_policy import default_lanes_for_kind
from zf.cli.flow import (
    _git_is_work_tree,
    apply_flow_submit,
    build_flow_intake,
    draft_flow_spec,
    draft_multi_kind_project_spec,
)
from zf.runtime.project_spine_review import (
    SpineReviewError,
    build_project_spine_review,
    create_spine_review_proposal,
    render_spine_review_markdown,
    resolve_spine_review_context,
    write_spine_review_artifact,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "project",
        help="Project-scoped review and insight commands",
    )
    sub = parser.add_subparsers(dest="project_cmd")

    init = sub.add_parser(
        "init",
        help="Initialize a project container for issue/prd/refactor workflow intake",
    )
    init.add_argument(
        "--kind",
        default="multi",
        choices=["multi", "issue", "prd", "refactor"],
        help=(
            "Project flow surface. Defaults to multi so requirements can be "
            "clarified before selecting and igniting issue/prd/refactor."
        ),
    )
    init.add_argument("--name", required=True)
    init.add_argument("--root", type=Path, default=Path("."))
    init.add_argument("--from", dest="source_ref", default="")
    init.add_argument("--objective", default="")
    init.add_argument(
        "--request-kind",
        default="auto",
        choices=["auto", "issue", "prd", "refactor", "feat"],
    )
    init.add_argument("--source-root", default="")
    init.add_argument("--target", "--target-root", dest="target_root", default="")
    init.add_argument("--backend", default="codex")
    init.add_argument("--lanes", type=int, default=0)
    init.add_argument("--state-dir", default="")
    init.add_argument("--strictness", default="standard")
    init.add_argument("--parity-scope", default="")
    init.add_argument("--acceptance", action="append", default=[])
    init.add_argument("--constraint", action="append", default=[])
    init.add_argument("--open-question", action="append", default=[])
    init.add_argument("--request-id", default="")
    init.add_argument(
        "--apply",
        action="store_true",
        help="Explicitly submit a ready init request after initialization.",
    )
    init.add_argument("--allow-missing-env", action="store_true")
    init.add_argument("--force", action="store_true")
    init.add_argument("--create", action="store_true")
    init.add_argument("--git-init", action="store_true")
    init.add_argument("--workspace", default="default")
    init.add_argument("--workspace-register", action="store_true")
    init.add_argument("--no-workspace-register", action="store_true")
    init.add_argument("--skip-instruction-docs", action="store_true")
    init.add_argument(
        "--notes", default="",
        help="Operator notes / conventions appended into project CLAUDE.md "
             "(same shared path as Web New Project 'description').",
    )
    init.add_argument("--json", action="store_true")
    init.set_defaults(func=_run_project_init)

    review = sub.add_parser(
        "review-spine",
        help="Review project design/delivery/runtime spine",
    )
    _add_review_args(review)
    review_nested = review.add_subparsers(dest="review_spine_cmd")

    propose = review_nested.add_parser(
        "propose",
        help="Create a pending proposal from a spine review corrective action",
    )
    propose.add_argument("--project-root", type=Path, default=None)
    propose.add_argument("--state-dir", default=None)
    propose.add_argument("--review-id", required=True)
    propose.add_argument(
        "--action",
        required=True,
        help="1-based action index or action_id from the persisted review",
    )
    propose.add_argument("--json", action="store_true", help="Emit JSON")
    propose.set_defaults(func=_run_review_spine_propose)

    review.set_defaults(func=_run_review_spine)
    parser.set_defaults(func=_run_help)


def _add_review_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--since", default=None)
    parser.add_argument("--format", choices=["md", "json"], default="md")
    parser.add_argument(
        "--write-artifact",
        action="store_true",
        help="Persist report/reflection artifacts and append an artifact event",
    )


def _run_help(args: argparse.Namespace) -> int:
    del args
    print(
        "Usage: zf project init [--kind multi|issue|prd|refactor] --name <name> "
        "| zf project review-spine [--format md|json]",
        file=sys.stderr,
    )
    return 2


def init_flow_project(
    *,
    kind: str,
    name: str,
    project_root: Path,
    source_ref: str = "",
    objective: str = "",
    request_kind: str = "auto",
    source_root: str = "",
    target_root: str = "",
    backend: str = "codex",
    lanes: int = 0,
    state_dir: str = "",
    strictness: str = "standard",
    parity_scope: tuple[str, ...] = (),
    acceptance: tuple[str, ...] = (),
    constraints: tuple[str, ...] = (),
    open_questions: tuple[str, ...] = (),
    request_id: str = "",
    apply_request: bool = False,
    allow_missing_env: bool = False,
    workspace: str = "default",
    force: bool = False,
    create_root: bool = False,
    git_init: bool = False,
    workspace_register: bool | None = None,
    with_instruction_docs: bool = True,
    notes: str = "",
) -> dict[str, Any]:
    """Single implementation of kind-based project init, shared by
    `zf project init` and the Web wizard (doc 125 §4/§8). Raises ValueError /
    PathGuardError / ConfigError / FileExistsError on invalid input.

    ``multi`` initializes a project container only.  It does not create a
    workflow request or emit an entry event; ignition remains a later explicit
    submit operation after requirement clarification.
    """
    kind = str(kind or "multi").strip().lower()
    if kind not in {"multi", "issue", "prd", "refactor"}:
        raise ValueError("kind must be one of multi, issue, prd, refactor")
    has_request_input = bool(
        str(objective or "").strip()
        or str(source_ref or "").strip()
        or open_questions
    )
    if apply_request and not has_request_input:
        raise ValueError("--apply requires --objective, --from, or --open-question")
    project_root = project_root.expanduser().resolve()
    if create_root:
        project_root.mkdir(parents=True, exist_ok=True)
    elif not project_root.exists():
        raise ValueError(f"project root does not exist: {project_root}")
    yaml_path = project_root / "zf.yaml"
    if yaml_path.exists() and not force:
        raise FileExistsError(f"{yaml_path} already exists. Use force to overwrite.")
    git_root = project_root
    created_git_repo = False
    if kind == "refactor":
        if not source_root:
            raise ValueError("refactor kind requires source_root")
        source = Path(source_root).expanduser()
        if not source.exists():
            raise ValueError(f"source_root does not exist: {source}")
        target = Path(target_root).expanduser() if target_root else project_root
        PathGuard.assert_disjoint(source, target)
        git_root = target if target.exists() else project_root
        if not _git_is_work_tree(target if target.exists() else project_root):
            if git_init:
                target.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["git", "-C", str(target), "init"],
                    capture_output=True, text=True, timeout=30, check=True,
                )
                created_git_repo = True
            else:
                raise ValueError(
                    f"refactor target is not a git repository: {target} "
                    "(pass git_init/--git-init, or run git init first)"
                )
    elif git_init and not _git_is_work_tree(git_root):
        git_root.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "-C", str(git_root), "init"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        created_git_repo = True
    if create_root or kind == "prd":
        _ensure_project_greenfield_seed(project_root, kind=kind)
    if kind == "multi":
        docs = draft_multi_kind_project_spec(
            backend=backend,
            lanes=lanes,
            project_name=name,
            state_dir=state_dir,
            project_root=project_root,
            strictness=strictness,
            parity_scope=parity_scope,
        )
    else:
        docs = draft_flow_spec(
            kind=kind,
            source_ref=source_ref,
            source_root=source_root,
            target_root=target_root,
            backend=backend,
            lanes=lanes or _default_project_lanes(kind),
            project_name=name,
            state_dir=state_dir,
            project_root=project_root,
            strictness=strictness,
            parity_scope=parity_scope,
        )
    yaml_path.write_text(
        yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    result = ProjectInitializer(workspace=workspace).initialize(
        cwd=project_root,
        force=force,
        with_instruction_docs=with_instruction_docs,
        workspace_register=workspace_register,
        notes=notes,
    )
    request_result: dict[str, Any] = {}
    submit_result: dict[str, Any] = {}
    if has_request_input:
        effective_request_kind = (
            kind if request_kind == "auto" and kind != "multi" else request_kind
        )
        request_result = build_flow_intake(
            kind=effective_request_kind,
            source_ref=source_ref,
            objective=objective,
            source_root=source_root,
            target_root=target_root,
            backend=backend,
            lanes=lanes,
            project_id=(
                result.registered_project.project_id
                if result.registered_project is not None
                else name
            ),
            project_name=name,
            strictness=strictness,
            parity_scope=parity_scope,
            acceptance=acceptance,
            constraints=constraints,
            open_questions=open_questions,
            request_id=request_id,
            source="project-init",
            created_by="zf-project-init",
            output=(
                project_root / "docs" / "intake" / f"{request_id}.md"
                if request_id
                else project_root / "docs" / "intake" / "project-init-request.md"
            ),
        )
    if created_git_repo:
        _create_initial_project_commit(project_root, state_dir=result.state_dir)
    if apply_request:
        submit_result = apply_flow_submit(
            config_path=yaml_path,
            intake_path=Path(str(request_result["intake_ref"])),
            flow_kind=str(request_result.get("effective_kind") or ""),
            requested_by="zf-project-init",
            allow_missing_env=allow_missing_env,
        )
    return {
        "ok": True,
        "kind": kind,
        "project_name": name,
        "project_root": str(project_root),
        "config_ref": str(yaml_path),
        "state_dir": str(result.state_dir),
        "workspace_project_id": (
            result.registered_project.project_id
            if result.registered_project is not None else ""
        ),
        "request": request_result,
        "submit": submit_result,
    }


def _ensure_project_greenfield_seed(project_root: Path, *, kind: str) -> None:
    """Create the minimum non-destructive scaffold required by cold start."""

    seeds = {
        "README.md": f"# {project_root.name}\n\nGenerated by `zf project init --kind {kind}`.\n",
        "src/.gitkeep": "",
        "tests/.gitkeep": "",
    }
    if kind == "prd":
        seeds.update({"app/.gitkeep": "", "docs/prd/.gitkeep": ""})
    for rel, content in seeds.items():
        path = project_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(content, encoding="utf-8")


def _create_initial_project_commit(project_root: Path, *, state_dir: Path) -> None:
    """Create the first snapshot required by worktree-mode workers."""

    try:
        relative_state = state_dir.resolve().relative_to(project_root.resolve())
    except ValueError:
        relative_state = None
    if relative_state is not None:
        ignore_path = project_root / ".gitignore"
        entry = f"/{relative_state.as_posix().rstrip('/')}/"
        existing = ignore_path.read_text(encoding="utf-8") if ignore_path.exists() else ""
        lines = existing.splitlines()
        if entry not in lines:
            prefix = "" if not existing or existing.endswith("\n") else "\n"
            ignore_path.write_text(
                f"{existing}{prefix}# ZaoFu runtime state\n{entry}\n",
                encoding="utf-8",
            )

    listed = subprocess.run(
        ["git", "-C", str(project_root), "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        timeout=30,
        check=True,
    ).stdout.decode("utf-8", errors="surrogateescape")
    paths = [item for item in listed.split("\0") if item]
    for offset in range(0, len(paths), 200):
        subprocess.run(
            ["git", "-C", str(project_root), "add", "--", *paths[offset:offset + 200]],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    subprocess.run(
        [
            "git", "-C", str(project_root),
            "-c", "user.email=zf-init@zaofu.local",
            "-c", "user.name=ZaoFu Project Init",
            "commit", "--no-verify", "--allow-empty", "-m", "chore: initialize ZaoFu project",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )


def _run_project_init(args: argparse.Namespace) -> int:
    workspace_register = None
    if args.no_workspace_register:
        workspace_register = False
    elif args.workspace_register:
        workspace_register = True
    try:
        payload = init_flow_project(
            kind=args.kind,
            name=args.name,
            project_root=args.root,
            source_ref=args.source_ref,
            objective=args.objective,
            request_kind=args.request_kind,
            source_root=args.source_root,
            target_root=args.target_root,
            backend=args.backend,
            lanes=args.lanes,
            state_dir=args.state_dir,
            strictness=args.strictness,
            parity_scope=_parse_csv(args.parity_scope),
            acceptance=tuple(args.acceptance),
            constraints=tuple(args.constraint),
            open_questions=tuple(args.open_question),
            request_id=args.request_id,
            apply_request=bool(args.apply),
            allow_missing_env=bool(args.allow_missing_env),
            workspace=args.workspace,
            force=bool(args.force),
            create_root=bool(args.create),
            git_init=bool(args.git_init),
            workspace_register=workspace_register,
            with_instruction_docs=not bool(args.skip_instruction_docs),
            notes=str(getattr(args, "notes", "") or ""),
        )
    except (ValueError, PathGuardError, ConfigError, FileExistsError,
            subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Initialized workflow project {args.name}: {payload['project_root']}")
        print(f"- kind: `{args.kind}`")
        print(f"- config: `{payload['config_ref']}`")
        print(f"- state_dir: `{payload['state_dir']}`")
        if payload.get("workspace_project_id"):
            print(f"- workspace_project_id: `{payload['workspace_project_id']}`")
        request = payload.get("request") or {}
        if request:
            print(f"- request_id: `{request.get('request_id', '')}`")
            print(f"- request_status: `{request.get('request_status', '')}`")
        submit = payload.get("submit") or {}
        if submit:
            print(f"- submit_status: `{submit.get('status', '')}`")
    submit = payload.get("submit") or {}
    return 1 if bool(args.apply) and submit.get("status") == "STOP" else 0


def _default_project_lanes(kind: str) -> int:
    return default_lanes_for_kind(kind)


def _parse_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def _run_review_spine(args: argparse.Namespace) -> int:
    try:
        context = resolve_spine_review_context(
            project_root=args.project_root,
            explicit_state_dir=args.state_dir,
        )
        review = build_project_spine_review(context, since=args.since)
        artifact = None
        if args.write_artifact:
            artifact = write_spine_review_artifact(context, review)
    except (ConfigError, SpineReviewError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        out = dict(review)
        if artifact is not None:
            out["artifact"] = artifact
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(render_spine_review_markdown(review), end="")
    if artifact is not None:
        print(f"\nArtifact: {artifact['artifact_dir']}")
        print(f"Artifact event: {artifact['event_id']}")
    return 0


def _run_review_spine_propose(args: argparse.Namespace) -> int:
    try:
        context = resolve_spine_review_context(
            project_root=args.project_root,
            explicit_state_dir=args.state_dir,
        )
        result = create_spine_review_proposal(
            context,
            review_id=args.review_id,
            action=args.action,
        )
    except (ConfigError, SpineReviewError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        proposal = result.get("proposal", {})
        print(f"Created spine review proposal: {result.get('event_id')}")
        print(f"- review_id: {proposal.get('review_id')}")
        print(f"- action_id: {proposal.get('action_id')}")
        print(f"- kind: {proposal.get('kind')}")
    return 0
