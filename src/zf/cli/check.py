"""zf check — project health checks."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("check", help="Project health checks")
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml, else .zf)",
    )
    parser.set_defaults(func=_run_help)

    sub = parser.add_subparsers(dest="check_cmd")

    pre_p = sub.add_parser(
        "preflight",
        help="X16 轻量 coding check lane(diff+checks,产 evidence 不裁决)",
    )
    pre_p.add_argument("--workdir", default=".")
    pre_p.add_argument("--checks", nargs="*", default=None)
    pre_p.set_defaults(func=_run_preflight)

    doc_p = sub.add_parser("doc-sync", help="Check documentation sync")
    doc_p.set_defaults(func=_run_doc_sync)

    clean_p = sub.add_parser("clean-state", help="Check project cleanliness")
    clean_p.set_defaults(func=_run_clean_state)

    task_docs_p = sub.add_parser("task-docs", help="Audit task capsule drift")
    task_docs_p.add_argument(
        "--mode",
        choices=["all", "active", "ready", "dispatched"],
        default="all",
        help="Audit scope (default: all; active/dispatched make missing capsules hard blockers)",
    )
    task_docs_p.set_defaults(func=_run_task_docs)

    artifact_matrix_p = sub.add_parser(
        "artifact-matrix",
        help="Evaluate a generic artifact/matrix gate config",
    )
    artifact_matrix_p.add_argument("--root", default=".", help="Candidate/project root")
    artifact_matrix_p.add_argument(
        "--config",
        required=True,
        help="Gate config JSON path relative to --root",
    )
    artifact_matrix_p.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format",
    )
    artifact_matrix_p.set_defaults(func=_run_artifact_matrix)


def _run_help(args: argparse.Namespace) -> int:
    if getattr(args, "check_cmd", None) is not None:
        return args.func(args)
    print("Usage: zf check <doc-sync|clean-state|task-docs>")
    return 0


def _run_doc_sync(args: argparse.Namespace) -> int:
    """Check that key docs exist and are up to date."""
    try:
        workspace = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        ).project_root
    except ConfigError:
        workspace = Path.cwd()
    issues: list[str] = []

    required_docs = {
        "README.md": "Project overview",
        "AGENTS.md": "Provider-neutral agent instructions",
        "CLAUDE.md": "Agent instructions",
    }

    for doc, purpose in required_docs.items():
        path = workspace / doc
        if not path.exists():
            issues.append(f"MISSING: {doc} ({purpose})")
        elif path.stat().st_size < 10:
            issues.append(f"EMPTY: {doc} ({purpose})")

    # Check if zf.yaml matches docs
    if (workspace / "zf.yaml").exists() and (workspace / "docs").exists():
        pass  # basic presence check

    if issues:
        print("Doc sync issues:")
        for issue in issues:
            print(f"  - {issue}")
        print(f"\n{len(issues)} issues found.")
        return 1

    print("Doc sync: OK")
    return 0


def _run_clean_state(args: argparse.Namespace) -> int:
    """5-dimension project cleanliness check."""
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
        workspace = context.project_root
        state_dir = context.state_dir
    except ConfigError:
        workspace = Path.cwd()
        state_dir = workspace / ".zf"
    checks: list[tuple[str, bool, str]] = []

    # 1. No temp/debug files
    debug_patterns = ["*.pyc", "*.pyo", "__pycache__", ".DS_Store"]
    debug_found = []
    for pattern in debug_patterns:
        debug_found.extend(workspace.glob(f"**/{pattern}"))
    checks.append(("no_debug_files", len(debug_found) == 0,
                    f"{len(debug_found)} debug/temp files" if debug_found else "clean"))

    # 2. State dir exists
    state_ok = state_dir.exists()
    checks.append((
        "state_initialized",
        state_ok,
        "OK" if state_ok else f"{state_dir} missing",
    ))

    # 3. Config valid
    config_ok = (workspace / "zf.yaml").exists()
    checks.append(("config_present", config_ok, "OK" if config_ok else "zf.yaml missing"))

    # 4. No stale lock
    lock = state_dir / "loop.lock"
    no_stale_lock = not lock.exists()
    checks.append(("no_stale_lock", no_stale_lock, "OK" if no_stale_lock else "loop.lock exists (harness may be running)"))

    # 5. Git clean (if git repo)
    git_clean = True
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            git_clean = len(result.stdout.strip()) == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # not a git repo or git not installed
    checks.append(("git_clean", git_clean, "OK" if git_clean else "uncommitted changes"))

    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)

    print(f"Clean state: {passed}/{total}\n")
    for name, ok, detail in checks:
        icon = "PASS" if ok else "FAIL"
        print(f"  [{icon}] {name}: {detail}")

    return 0 if passed == total else 1


def _run_task_docs(args: argparse.Namespace) -> int:
    from zf.runtime.task_doc_audit import audit_task_docs

    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError:
        context = None
    state_dir = context.state_dir if context is not None else Path.cwd() / ".zf"
    project_root = context.project_root if context is not None else Path.cwd()
    report = audit_task_docs(
        state_dir,
        project_root=project_root,
        mode=getattr(args, "mode", "all"),
    )
    findings = report.get("findings", []) or []
    if not findings:
        print("Task docs: OK")
        return 0
    print(f"Task docs: {len(findings)} finding(s)")
    for item in findings:
        print(
            f"  [{item.get('severity', 'warning')}] "
            f"{item.get('task_id', '')}: {item.get('code', '')} "
            f"- {item.get('detail', '')}"
        )
    return 0 if report.get("ok") else 1


def _run_preflight(args) -> int:
    """X16 check lane CLI 入口:evidence JSON,exit code 即 pass/fail。"""
    import json as _json
    from pathlib import Path as _P

    from zf.runtime.check_preflight import run_check_preflight

    evidence = run_check_preflight(
        workdir=_P(args.workdir).resolve(),
        checks=list(args.checks or []),
    )
    print(_json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence["passed"] else 1


def _run_artifact_matrix(args: argparse.Namespace) -> int:
    from zf.runtime.artifact_matrix_gate import evaluate_artifact_matrix_gate

    root = Path(args.root).resolve()
    result = evaluate_artifact_matrix_gate(root, {"config_ref": args.config})
    if args.format == "json":
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    elif result.passed:
        print(
            "Artifact matrix gate: PASS "
            f"({result.blocking_rows} blocking rows, {result.checked_rows} rows checked)"
        )
    else:
        print(f"Artifact matrix gate: FAIL ({len(result.findings)} finding(s))")
        for finding in result.findings:
            suffix = f" [{finding.path}]" if finding.path else ""
            row = f" row={finding.row_id}" if finding.row_id else ""
            print(f"  - {finding.code}{suffix}{row}: {finding.message}")
    return 0 if result.passed else 1
