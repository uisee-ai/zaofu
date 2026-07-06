"""zf doctor — operator diagnostics."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from zf.core.config.loader import ConfigError, load_config
from zf.core.events.factory import event_log_from_project
from zf.core.task.store import TaskStore
from zf.runtime.pane_bindings import PaneBindingManager
from zf.runtime.sidecar_refs import doctor_sidecar_refs
from zf.runtime.workdirs import WorkdirManager


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("doctor", help="Run operator diagnostics")
    sub = parser.add_subparsers(dest="doctor_cmd")

    workdirs = sub.add_parser("workdirs", help="Check runtime workdirs")
    workdirs.set_defaults(func=_run_workdirs)

    panes = sub.add_parser("panes", help="Check pane-grid role bindings")
    panes.set_defaults(func=_run_panes)

    provider = sub.add_parser("provider", help="Check provider CLI preflight")
    provider.add_argument("--backend", choices=["codex"], default="codex")
    provider.add_argument("--json", action="store_true", dest="as_json")
    provider.set_defaults(func=_run_provider)

    sidecar = sub.add_parser("sidecar", help="Check sidecar refs referenced by events")
    sidecar.add_argument("--json", action="store_true", dest="as_json")
    sidecar.add_argument("--no-orphans", action="store_true", help="Skip orphan sidecar scan")
    sidecar.set_defaults(func=_run_sidecar)

    event_contract = sub.add_parser(
        "event-contract",
        help="Check workflow event producer/consumer contracts",
    )
    event_contract.add_argument("--path", type=str, default=None, help="Path to zf.yaml")
    event_contract.add_argument("--json", action="store_true", dest="as_json")
    event_contract.add_argument(
        "--runtime-events",
        action="store_true",
        help="Also inspect runtime events for actionable scope gaps",
    )
    event_contract.set_defaults(func=_run_event_contract)

    parser.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> int:
    try:
        project_root, state_dir, config = _load_runtime()
    except (ConfigError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    issues: list[str] = []
    warnings: list[str] = []
    print("ZF Doctor")
    print(f"project_root: {project_root}")
    print(f"state_dir: {state_dir}")

    if not state_dir.exists():
        issues.append("state_dir does not exist")
    else:
        _check_events(state_dir, config, issues, warnings)
        _check_tasks(state_dir, issues)
        _check_skills(state_dir, config, issues, warnings)
        _check_recent_blockers(state_dir, config, warnings)
        try:
            for issue in WorkdirManager(
                state_dir=state_dir,
                project_root=project_root,
                config=config,
            ).doctor():
                if config.runtime.workdirs.enabled:
                    issues.append(f"workdir: {issue}")
                else:
                    warnings.append(f"workdir: {issue}")
        except Exception as exc:
            warnings.append(f"workdir doctor skipped: {exc}")

    if issues:
        print("Issues:")
        for issue in issues:
            print(f"  - {issue}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    if not issues and not warnings:
        print("OK: runtime diagnostics clean")
    elif not issues:
        print("OK: runtime diagnostics clean with warnings")
    return 1 if issues else 0


def _run_workdirs(args: argparse.Namespace) -> int:
    try:
        project_root, state_dir, config = _load_runtime()
        issues = WorkdirManager(
            state_dir=state_dir,
            project_root=project_root,
            config=config,
        ).doctor()
    except (ConfigError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not issues:
        print("OK: workdirs")
        return 0
    print("Workdir issues:")
    for issue in issues:
        print(f"  - {issue}")
    return 1


def _run_panes(args: argparse.Namespace) -> int:
    try:
        project_root, state_dir, config = _load_runtime()
        issues = PaneBindingManager(
            project_root=project_root,
            state_dir=state_dir,
            config=config,
        ).doctor()
    except (ConfigError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not issues:
        print("OK: panes")
        return 0
    print("Pane issues:")
    for issue in issues:
        print(f"  - {issue}")
    return 1


def _run_provider(args: argparse.Namespace) -> int:
    if args.backend != "codex":
        print(f"Error: unsupported provider backend: {args.backend}", file=sys.stderr)
        return 1
    report = _codex_provider_preflight()
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_provider_report(report)
    return 0 if report["ok"] else 1


def _run_sidecar(args: argparse.Namespace) -> int:
    try:
        _project_root, state_dir, config = _load_runtime()
        events = event_log_from_project(state_dir, config=config).read_all()
    except (ConfigError, RuntimeError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    report = doctor_sidecar_refs(
        state_dir,
        events,
        include_orphans=not bool(args.no_orphans),
    )
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_sidecar_report(report)
    return 0 if report["ok"] else 1


def _run_event_contract(args: argparse.Namespace) -> int:
    try:
        project_root, state_dir, config = _load_runtime_from_path(
            Path(args.path) if getattr(args, "path", None) else None
        )
        events = None
        if getattr(args, "runtime_events", False) and state_dir.exists():
            events = event_log_from_project(state_dir, config=config).read_all()
        from zf.runtime.event_contracts import build_event_contract_report

        report = build_event_contract_report(config, events=events)
    except (ConfigError, RuntimeError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    report["project_root"] = str(project_root)
    report["state_dir"] = str(state_dir)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_event_contract_report(report)
    return 0 if report["ok"] else 1


def _load_runtime():
    return _load_runtime_from_path(None)


def _load_runtime_from_path(config_path: Path | None):
    path = config_path or (Path.cwd() / "zf.yaml")
    project_root = path.parent
    config = load_config(path)
    raw_state = Path(config.project.state_dir)
    state_dir = raw_state if raw_state.is_absolute() else project_root / raw_state
    return project_root, state_dir, config


def _codex_provider_preflight() -> dict[str, Any]:
    report: dict[str, Any] = {
        "backend": "codex",
        "ok": True,
        "codex": {
            "available": False,
            "path": "",
            "version": "",
        },
        "sandbox": {
            "status": "unknown",
            "method": "",
            "diagnostic": "",
        },
        "issues": [],
        "warnings": [],
        "recommendations": [],
    }
    codex_path = shutil.which("codex")
    if not codex_path:
        report["ok"] = False
        report["issues"].append("codex command not found on PATH")
        report["recommendations"].append("Install/login Codex CLI before running real provider E2E.")
        return report

    report["codex"]["available"] = True
    report["codex"]["path"] = codex_path
    version = _run_probe([codex_path, "--version"], timeout_s=5)
    if version["returncode"] == 0:
        report["codex"]["version"] = _first_line(version["stdout"] or version["stderr"])
    else:
        report["warnings"].append(
            f"codex --version failed: {_probe_summary(version)}"
        )

    unshare_path = shutil.which("unshare")
    if unshare_path:
        probe = _run_probe([unshare_path, "-n", "true"], timeout_s=5)
        report["sandbox"]["method"] = "unshare -n true"
        if probe["returncode"] == 0:
            report["sandbox"]["status"] = "ok"
            return report
        text = f"{probe['stdout']}\n{probe['stderr']}".lower()
        if "operation not permitted" in text or "not permitted" in text:
            report["ok"] = False
            report["sandbox"]["status"] = "unsupported"
            report["sandbox"]["diagnostic"] = _probe_summary(probe)
            report["issues"].append(
                "network namespace is not available; Codex sandbox may fail before replying"
            )
            report["recommendations"].append(
                "For explicit real E2E only, rerun Codex with a documented sandbox bypass or fix host namespace permissions."
            )
            return report
        report["sandbox"]["status"] = "unknown"
        report["sandbox"]["diagnostic"] = _probe_summary(probe)
        report["warnings"].append(
            "network namespace probe failed with an unclassified error"
        )
        return report

    bwrap_path = shutil.which("bwrap")
    if bwrap_path:
        report["sandbox"]["method"] = "bwrap present; unshare unavailable"
        report["warnings"].append(
            "unshare command is unavailable; sandbox support could not be proven before real Codex run"
        )
    else:
        report["sandbox"]["method"] = "no unshare/bwrap probe"
        report["warnings"].append(
            "neither unshare nor bwrap is available on PATH; sandbox support could not be proven"
        )
    return report


def _print_provider_report(report: dict[str, Any]) -> None:
    print("ZF Provider Doctor")
    print(f"backend: {report['backend']}")
    codex = report.get("codex", {})
    print(f"codex: {'available' if codex.get('available') else 'missing'}")
    if codex.get("path"):
        print(f"codex_path: {codex['path']}")
    if codex.get("version"):
        print(f"codex_version: {codex['version']}")
    sandbox = report.get("sandbox", {})
    print(f"sandbox: {sandbox.get('status', 'unknown')}")
    if sandbox.get("method"):
        print(f"sandbox_probe: {sandbox['method']}")
    if sandbox.get("diagnostic"):
        print(f"sandbox_diagnostic: {sandbox['diagnostic']}")
    if report.get("issues"):
        print("Issues:")
        for issue in report["issues"]:
            print(f"  - {issue}")
    if report.get("warnings"):
        print("Warnings:")
        for warning in report["warnings"]:
            print(f"  - {warning}")
    if report.get("recommendations"):
        print("Recommendations:")
        for item in report["recommendations"]:
            print(f"  - {item}")
    if report.get("ok"):
        print("OK: provider preflight passed")


def _print_sidecar_report(report: dict[str, Any]) -> None:
    print("ZF Sidecar Doctor")
    print(f"checked_refs: {report.get('checked_ref_count', 0)}")
    print(f"issues: {report.get('issue_count', 0)}")
    print(f"orphans: {report.get('orphan_count', 0)}")
    if report.get("issues"):
        print("Issues:")
        for issue in report["issues"]:
            print(
                "  - "
                f"{issue.get('code', 'unknown')} "
                f"{issue.get('kind', '')}:{issue.get('ref', '')} "
                f"{issue.get('message', '')}"
            )
    if report.get("orphans"):
        print("Orphans:")
        for item in report["orphans"][:20]:
            print(f"  - {item.get('ref', '')}")
        if len(report["orphans"]) > 20:
            print(f"  ... {len(report['orphans']) - 20} more")
    if report.get("ok"):
        print("OK: sidecar refs")


def _print_event_contract_report(report: dict[str, Any]) -> None:
    summary = report.get("summary", {})
    print("ZF Event Contract Doctor")
    print(f"project_root: {report.get('project_root', '')}")
    print(f"state_dir: {report.get('state_dir', '')}")
    print(
        "summary: "
        f"producers={summary.get('producers', 0)} "
        f"event_types={summary.get('producer_event_types', 0)} "
        f"errors={summary.get('errors', 0)} "
        f"warnings={summary.get('warnings', 0)}"
    )
    errors = report.get("errors") or []
    warnings = report.get("warnings") or []
    if errors:
        print("Errors:")
        for item in errors:
            print(
                "  - "
                f"{item.get('kind')}: {item.get('event_type')} — "
                f"{item.get('message')}"
            )
    if warnings:
        print("Warnings:")
        for item in warnings:
            print(
                "  - "
                f"{item.get('kind')}: {item.get('event_type')} — "
                f"{item.get('message')}"
            )
    if not errors and not warnings:
        print("OK: event contracts clean")


def _run_probe(argv: list[str], *, timeout_s: float) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "probe timed out",
        }
    except OSError as exc:
        return {
            "returncode": 127,
            "stdout": "",
            "stderr": str(exc),
        }
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
    }


def _probe_summary(result: dict[str, Any]) -> str:
    text = "\n".join(
        part.strip()
        for part in [str(result.get("stdout") or ""), str(result.get("stderr") or "")]
        if part and part.strip()
    )
    text = " ".join(text.split())
    return text[:300] or f"exit {result.get('returncode')}"


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _check_events(state_dir: Path, config, issues: list[str], warnings: list[str]) -> None:
    try:
        events = event_log_from_project(state_dir, config=config).read_all()
    except Exception as exc:
        issues.append(f"events unreadable: {exc}")
        return
    malformed = [event for event in events if event.type == "event.malformed"]
    if malformed:
        issues.append(f"event.malformed count={len(malformed)}")
    if not (state_dir / "events.jsonl").exists():
        warnings.append("events.jsonl missing")


def _check_tasks(state_dir: Path, issues: list[str]) -> None:
    try:
        TaskStore(state_dir / "kanban.json").list_all_with_archive(last_days=1)
    except Exception as exc:
        issues.append(f"kanban unreadable: {exc}")


def _check_skills(state_dir: Path, config, issues: list[str], warnings: list[str]) -> None:
    any_skills = any(getattr(role, "skills", []) for role in config.roles)
    lock_path = state_dir / "skills.lock.json"
    if any_skills and not lock_path.exists():
        warnings.append("skills.lock.json missing; skills may not be materialized yet")
    if lock_path.exists():
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"skills.lock.json unreadable: {exc}")
            data = []
        entries = data if isinstance(data, list) else []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name", "")
            role = entry.get("role", "")
            status = entry.get("status", "")
            if status in {"missing", "invalid"}:
                issues.append(f"skill {role}/{name} status={status}")
            if entry.get("collision_candidates"):
                warnings.append(f"skill {role}/{name} has collision candidates")
    for role in config.roles:
        if not getattr(role, "skills", []):
            continue
        manifest = (
            state_dir / "workdirs" / role.instance_id / "runtime" / "skills-manifest.json"
        )
        if not manifest.exists():
            warnings.append(f"{role.instance_id}: skills-manifest.json missing")


def _check_recent_blockers(state_dir: Path, config, warnings: list[str]) -> None:
    blocker_types = {
        "hook.orphan_event",
        "task.done.blocked",
        "discriminator.failed",
        "task.rework.capped",
        "cost.budget.exceeded",
        "runtime.action.rejected",
    }
    try:
        events = event_log_from_project(state_dir, config=config).read_all()
    except Exception:
        return
    counts: dict[str, int] = {}
    for event in events[-300:]:
        if event.type in blocker_types:
            counts[event.type] = counts.get(event.type, 0) + 1
    for event_type, count in sorted(counts.items()):
        warnings.append(f"recent {event_type} count={count}")
