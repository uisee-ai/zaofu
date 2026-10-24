#!/usr/bin/env python3
"""Plan and run ZaoFu development verification from the current worktree."""

from __future__ import annotations

import argparse
import ast
import fnmatch
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_MARKERS = "not perf and not host and not real_provider"
PARALLEL_MARKERS = f"not serial and {DEFAULT_MARKERS}"
SERIAL_MARKERS = f"serial and {DEFAULT_MARKERS}"
MAX_FOCUSED_TEST_FILES = 80


@dataclass(frozen=True)
class DomainRule:
    name: str
    patterns: tuple[str, ...]


DOMAIN_RULES = (
    DomainRule(
        "docs",
        (
            "*.md",
            "docs/**",
            "ideas/**",
            "tasks/**",
            "AGENTS.md",
            "CLAUDE.md",
            ".claude/rules/**",
        ),
    ),
    DomainRule(
        "skills",
        ("skills/**", ".claude/skills/**", ".codex/skills/**"),
    ),
    DomainRule("web_ui", ("web/**",)),
    DomainRule("web_backend", ("src/zf/web/**",)),
    DomainRule(
        "config",
        (
            "src/zf/core/config/**",
            "src/zf/core/workflow/**",
            "examples/**",
            "zf.yaml",
            "pyproject.toml",
        ),
    ),
    DomainRule(
        "event_store",
        (
            "src/zf/core/events/**",
            "src/zf/core/task/**",
            "src/zf/core/state/**",
            "src/zf/runtime/event_*.py",
        ),
    ),
    DomainRule(
        "orchestration",
        (
            "src/zf/runtime/orchestrator*.py",
            "src/zf/runtime/*fanout*.py",
            "src/zf/runtime/run_manager*.py",
            "src/zf/runtime/*rework*.py",
            "src/zf/runtime/candidate*.py",
            "src/zf/runtime/control_actions.py",
            "src/zf/runtime/tick_services.py",
        ),
    ),
    DomainRule(
        "artifact_handoff",
        (
            "src/zf/runtime/artifact_*.py",
            "src/zf/runtime/artifact_query/**",
            "src/zf/runtime/*handoff*.py",
            "src/zf/runtime/sidecar_refs.py",
            "src/zf/runtime/workflow_operation.py",
            "src/zf/runtime/call_result*.py",
            "src/zf/runtime/*snapshot*.py",
        ),
    ),
    DomainRule(
        "provider_host",
        (
            "src/zf/runtime/backend*.py",
            "src/zf/runtime/*transport*.py",
            "src/zf/runtime/tmux*.py",
            "src/zf/runtime/*session*.py",
            "src/zf/runtime/workdir*.py",
            "src/zf/cli/start.py",
            "src/zf/cli/preflight.py",
        ),
    ),
    DomainRule(
        "tooling",
        ("scripts/**", ".gitlab-ci.yml", ".github/**", "uv.lock"),
    ),
    DomainRule("backend", ("src/zf/**",)),
    DomainRule("tests", ("tests/**",)),
)


DOMAIN_SENTINELS: dict[str, tuple[str, ...]] = {
    "docs": ("tests/test_instruction_stack_contracts.py",),
    "skills": (
        "tests/test_skill_provenance.py",
        "tests/test_structure_discipline.py",
    ),
    "config": (
        "tests/test_config_schema.py",
        "tests/test_loader_field_coverage.py",
        "tests/test_workflow_profiles.py",
    ),
    "event_store": (
        "tests/test_event_contracts.py",
        "tests/test_events_log.py",
        "tests/test_state_locks.py",
    ),
    "orchestration": (
        "tests/test_workflow_spine_projection.py",
        "tests/test_wake_pattern_coverage.py",
    ),
    "artifact_handoff": (
        "tests/test_sidecar_refs.py",
        "tests/test_workflow_operation.py",
        "tests/e2e/test_stale_contract_handoff_mock_e2e.py",
    ),
    "provider_host": (
        "tests/test_backend_adapters.py",
        "tests/test_preflight_dispatch_readiness.py",
    ),
    "web_backend": ("tests/test_web_server.py",),
    "tooling": ("tests/test_dev_verify.py",),
}

SERIAL_TEST_FILES = {
    "tests/test_memory_rotate_concurrency.py",
    "tests/test_session_mutex.py",
    "tests/test_state_locks.py",
}

PREMERGE_DOMAINS = {"config", "event_store", "orchestration", "artifact_handoff"}
FLOW_SMOKE_DOMAINS = {"config", "orchestration"}

# These opt-in runners and seeded products are loaded/copied by path. Static
# Python imports cannot discover their deterministic acceptance-test consumer.
DYNAMIC_TEST_SUPPORT = {
    path: ("tests/test_intake_live_validation.py",)
    for path in (
        "tests/e2e/intake_live_validation.py",
        "tests/e2e/intake_live_acceptance.py",
        "tests/e2e/fixtures/intake-live/issue/counter.py",
        "tests/e2e/fixtures/intake-live/semantic/auth.py",
        "tests/e2e/fixtures/intake-live/semantic/orders.py",
    )
}


@dataclass(frozen=True)
class VerificationStep:
    id: str
    tier: str
    command: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    cwd: str = "."
    automatic: bool = True

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["command"] = list(self.command)
        data["reasons"] = list(self.reasons)
        return data


@dataclass
class VerificationPlan:
    root: str
    changed_files: list[str]
    domains: list[str]
    selected_tests: list[str]
    steps: list[VerificationStep] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    broad_python: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "zf-dev-verification-plan.v1",
            "root": self.root,
            "changed_files": self.changed_files,
            "domains": self.domains,
            "selected_tests": self.selected_tests,
            "steps": [step.to_dict() for step in self.steps],
            "warnings": self.warnings,
            "errors": self.errors,
            "broad_python": self.broad_python,
        }


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def find_repo_root(cwd: Path | None = None) -> Path:
    start = (cwd or Path.cwd()).resolve()
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"not inside a git worktree: {start}")
    return Path(result.stdout.strip()).resolve()


def _normalize_paths(root: Path, paths: Iterable[str]) -> list[str]:
    normalized: set[str] = set()
    for raw in paths:
        value = raw.strip()
        if not value:
            continue
        candidate = Path(value)
        if candidate.is_absolute():
            try:
                candidate = candidate.resolve().relative_to(root)
            except ValueError as exc:
                raise ValueError(f"path is outside current worktree: {value}") from exc
        text = candidate.as_posix()
        if text.startswith("../") or text == "..":
            raise ValueError(f"path is outside current worktree: {value}")
        normalized.add(text.removeprefix("./"))
    return sorted(normalized)


def discover_changed_files(root: Path, base: str | None = None) -> list[str]:
    changed: set[str] = set()
    if base:
        merge_base = _git(root, "merge-base", "HEAD", base)
        changed.update(
            _git(
                root,
                "diff",
                "--no-renames",
                "--name-only",
                "--diff-filter=ACDMRT",
                merge_base,
                "HEAD",
            ).splitlines()
        )
    changed.update(
        _git(
            root,
            "diff",
            "--no-renames",
            "--name-only",
            "--diff-filter=ACDMRT",
        ).splitlines()
    )
    changed.update(
        _git(
            root,
            "diff",
            "--no-renames",
            "--cached",
            "--name-only",
            "--diff-filter=ACDMRT",
        ).splitlines()
    )
    changed.update(
        _git(
            root,
            "ls-files",
            "--others",
            "--exclude-standard",
        ).splitlines()
    )
    return _normalize_paths(root, changed)


def classify_domains(paths: Sequence[str]) -> list[str]:
    domains = {
        rule.name
        for path in paths
        for rule in DOMAIN_RULES
        if any(fnmatch.fnmatch(path, pattern) for pattern in rule.patterns)
    }
    return sorted(domains)


def _module_name(path: Path, source_root: Path) -> str:
    relative = path.relative_to(source_root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _source_modules(root: Path) -> dict[str, Path]:
    source_root = root / "src"
    return {
        _module_name(path, source_root): path
        for path in source_root.rglob("*.py")
        if _module_name(path, source_root)
    }


def _resolve_module(name: str, modules: dict[str, Path]) -> str | None:
    candidate = name
    while candidate:
        if candidate in modules:
            return candidate
        candidate = candidate.rpartition(".")[0]
    return None


def _import_candidates(
    path: Path,
    *,
    current_module: str = "",
) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return set()
    imported: set[str] = set()
    current_parts = current_module.split(".") if current_module else []
    package_parts = current_parts if path.name == "__init__.py" else current_parts[:-1]
    for node in ast.walk(tree):
        candidates: list[str] = []
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level and current_parts:
                trim = max(0, node.level - 1)
                keep = max(0, len(package_parts) - trim)
                base = package_parts[:keep]
                suffix = node.module.split(".") if node.module else []
                if suffix:
                    imported_base = ".".join([*base, *suffix])
                    candidates.append(imported_base)
                    candidates.extend(
                        f"{imported_base}.{alias.name}" for alias in node.names
                    )
                else:
                    candidates.extend(
                        ".".join([*base, alias.name]) for alias in node.names
                    )
            elif node.module:
                candidates.append(node.module)
                candidates.extend(
                    f"{node.module}.{alias.name}" for alias in node.names
                )
        for candidate in candidates:
            if candidate:
                imported.add(candidate)
    return imported


def _imports(
    path: Path,
    modules: dict[str, Path],
    *,
    current_module: str = "",
) -> set[str]:
    imported: set[str] = set()
    for candidate in _import_candidates(path, current_module=current_module):
        resolved = _resolve_module(candidate, modules)
        if resolved:
            imported.add(resolved)
    return imported


def _source_module_for_path(path: str) -> str | None:
    candidate = Path(path)
    if len(candidate.parts) < 3 or candidate.parts[:2] != ("src", "zf"):
        return None
    parts = list(candidate.with_suffix("").parts[1:])
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts else None


@lru_cache(maxsize=8)
def _dependency_graph(
    root_text: str,
) -> tuple[
    dict[str, Path],
    dict[str, set[str]],
    dict[str, set[str]],
    dict[str, set[str]],
]:
    root = Path(root_text)
    modules = _source_modules(root)
    source_imports = {
        name: _imports(path, modules, current_module=name)
        for name, path in modules.items()
    }
    test_imports = {
        path.relative_to(root).as_posix(): _imports(path, modules)
        for path in (root / "tests").rglob("test_*.py")
    }
    test_support_imports = {
        path.relative_to(root).as_posix(): _import_candidates(path)
        for path in (root / "tests").rglob("test_*.py")
    }
    return modules, source_imports, test_imports, test_support_imports


def _test_candidates(
    root: Path,
    changed_files: Sequence[str],
) -> tuple[set[str], dict[str, set[str]], dict[str, set[str]]]:
    selected = {
        path
        for path in changed_files
        if path.startswith("tests/")
        and path.endswith(".py")
        and Path(path).name.startswith("test_")
        and (root / path).exists()
    }
    source_changes = [
        path
        for path in changed_files
        if path.startswith("src/") and path.endswith(".py")
    ]
    support_changes = [
        path
        for path in changed_files
        if path.startswith("tests/")
        and path.endswith(".py")
        and not Path(path).name.startswith("test_")
    ]
    if not source_changes and not support_changes:
        return selected, {}, {}

    _, source_imports, test_imports, test_support_imports = _dependency_graph(
        str(root.resolve())
    )
    per_source: dict[str, set[str]] = {}
    for source_path in source_changes:
        module = _source_module_for_path(source_path)
        if not module:
            per_source[source_path] = set()
            continue
        direct_callers = {
            caller
            for caller, imports in source_imports.items()
            if module in imports
        }
        scope = {module, *direct_callers}
        hits = {
            test_path
            for test_path, imports in test_imports.items()
            if imports.intersection(scope)
        }
        stem = Path(source_path).stem
        hits.update(
            path.relative_to(root).as_posix()
            for path in (root / "tests").rglob(f"test_{stem}.py")
        )
        per_source[source_path] = hits
        selected.update(hits)

    all_tests = set(test_support_imports)
    per_support: dict[str, set[str]] = {}
    for support_path in support_changes:
        support = Path(support_path)
        if support.name == "conftest.py":
            parent = support.parent.as_posix()
            prefix = "" if parent == "." else parent + "/"
            hits = {path for path in all_tests if path.startswith(prefix)}
        else:
            module = ".".join(support.with_suffix("").parts)
            hits = {
                test_path
                for test_path, imports in test_support_imports.items()
                if module in imports
            }
        hits.update(_existing(root, DYNAMIC_TEST_SUPPORT.get(support_path, ())))
        per_support[support_path] = hits
        selected.update(hits)
    return selected, per_source, per_support


def _existing(root: Path, paths: Iterable[str]) -> list[str]:
    return sorted({path for path in paths if (root / path).is_file()})


def _xdist_available() -> bool:
    return importlib.util.find_spec("xdist") is not None


def _worker_count(requested: int | None = None) -> int:
    if requested is not None:
        return max(1, requested)
    configured = os.environ.get("ZF_TEST_WORKERS", "").strip()
    if configured:
        try:
            return max(1, int(configured))
        except ValueError:
            pass
    return max(1, min(8, os.cpu_count() or 1))


def _pytest_command(
    targets: Sequence[str],
    *,
    markers: str,
    parallel: bool,
    workers: int,
) -> tuple[str, ...]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        *targets,
        "-q",
        "--no-cov",
        "-m",
        markers,
    ]
    if parallel and workers > 1 and _xdist_available():
        command.extend(["-n", str(workers), "--dist", "loadfile"])
    return tuple(command)


def build_plan(
    root: Path,
    changed_files: Sequence[str],
    *,
    explicit_tests: Sequence[str] = (),
    workers: int | None = None,
    parallel: bool = True,
) -> VerificationPlan:
    paths = _normalize_paths(root, changed_files)
    domains = classify_domains(paths)
    plan = VerificationPlan(
        root=str(root),
        changed_files=paths,
        domains=domains,
        selected_tests=[],
    )
    if not paths:
        plan.errors.append("no changed files detected; pass --paths or --base")
        return plan

    normalized_explicit = _normalize_paths(root, explicit_tests)
    explicit = _existing(root, normalized_explicit)
    missing_explicit = sorted(set(normalized_explicit) - set(explicit))
    if missing_explicit:
        plan.errors.append(
            "explicit test path does not exist: " + ", ".join(missing_explicit)
        )

    selected, per_source, per_support = _test_candidates(root, paths)
    selected.update(explicit)
    for domain in domains:
        selected.update(_existing(root, DOMAIN_SENTINELS.get(domain, ())))

    if not explicit:
        for source_path, hits in sorted({**per_source, **per_support}.items()):
            if not hits:
                plan.errors.append(
                    f"unmapped Python change {source_path}; add a direct test, "
                    "pass --tests, or extend a shared boundary rule"
                )

    selected = set(_existing(root, selected))
    plan.selected_tests = sorted(selected)
    plan.broad_python = len(selected) > MAX_FOCUSED_TEST_FILES
    worker_count = _worker_count(workers)

    if selected:
        targets = ["tests"] if plan.broad_python else sorted(selected)
        reasons = (
            (
                f"{len(selected)} impacted test files exceed "
                f"{MAX_FOCUSED_TEST_FILES}; run deterministic suite"
            )
            if plan.broad_python
            else "changed tests + direct imports/callers + boundary sentinels"
        )
        plan.steps.append(
            VerificationStep(
                id="python-deterministic",
                tier="deterministic",
                command=_pytest_command(
                    targets,
                    markers=PARALLEL_MARKERS,
                    parallel=parallel,
                    workers=worker_count,
                ),
                reasons=(reasons,),
            )
        )
        serial_targets = (
            sorted(SERIAL_TEST_FILES)
            if plan.broad_python
            else sorted(selected.intersection(SERIAL_TEST_FILES))
        )
        if serial_targets:
            plan.steps.append(
                VerificationStep(
                    id="python-serial",
                    tier="serial",
                    command=_pytest_command(
                        serial_targets,
                        markers=SERIAL_MARKERS,
                        parallel=False,
                        workers=1,
                    ),
                    reasons=("selected process/fixed-resource tests run without xdist",),
                )
            )

    if "web_ui" in domains or "web_backend" in domains:
        plan.steps.extend(
            (
                VerificationStep(
                    id="web-typecheck",
                    tier="web",
                    command=("npm", "run", "typecheck"),
                    cwd="web",
                    reasons=("Web UI/API boundary changed",),
                ),
                VerificationStep(
                    id="web-unit",
                    tier="web",
                    command=("npm", "run", "test:unit"),
                    cwd="web",
                    reasons=("compile Web unit tests once and execute model contracts",),
                ),
            )
        )

    if PREMERGE_DOMAINS.intersection(domains):
        plan.steps.append(
            VerificationStep(
                id="premerge-sentinels",
                tier="contract",
                command=("bash", "scripts/dev-premerge-gate.sh"),
                reasons=(
                    "shared Event/Store/Config/Orchestration contract changed",
                ),
            )
        )
    if FLOW_SMOKE_DOMAINS.intersection(domains):
        plan.steps.append(
            VerificationStep(
                id="flow-smoke",
                tier="mock_e2e",
                command=("bash", "scripts/run-flow-smoke.sh"),
                reasons=("workflow/config route changed",),
            )
        )
    if "provider_host" in domains:
        plan.steps.append(
            VerificationStep(
                id="real-provider",
                tier="real_provider",
                reasons=(
                    "real provider/host proof requires explicit operator approval",
                ),
                automatic=False,
            )
        )

    if not plan.steps and not plan.errors:
        plan.errors.append(
            "changed files have no verification route; pass --tests or extend a domain rule"
        )
    return plan


def _print_plan(plan: VerificationPlan) -> None:
    print(f"worktree: {plan.root}")
    print("changed:")
    for path in plan.changed_files:
        print(f"  - {path}")
    print("domains: " + (", ".join(plan.domains) or "<none>"))
    if plan.selected_tests:
        print(
            f"python tests: {len(plan.selected_tests)}"
            + (" (broad deterministic)" if plan.broad_python else "")
        )
    print("steps:")
    for step in plan.steps:
        command = shlex.join(step.command) if step.command else "<operator explicit>"
        mode = "auto" if step.automatic else "explicit"
        print(f"  - [{mode}/{step.tier}] {step.id}: {command}")
        for reason in step.reasons:
            print(f"      reason: {reason}")
    for warning in plan.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for error in plan.errors:
        print(f"error: {error}", file=sys.stderr)


def run_plan(
    plan: VerificationPlan,
    *,
    keep_going: bool = False,
    capture_output: bool = False,
    skip_tiers: frozenset[str] = frozenset(),
) -> tuple[int, dict[str, object]]:
    receipt: dict[str, object] = {
        "schema_version": "zf-dev-verification-receipt.v1",
        "plan": plan.to_dict(),
        "started_at_epoch": time.time(),
        "results": [],
    }
    if plan.errors:
        receipt["status"] = "plan_failed"
        return 2, receipt

    env = os.environ.copy()
    source = str(Path(plan.root) / "src")
    env["PYTHONPATH"] = (
        source
        if not env.get("PYTHONPATH")
        else source + os.pathsep + env["PYTHONPATH"]
    )
    failed = False
    for step in plan.steps:
        if not step.automatic:
            receipt["results"].append(
                {
                    "id": step.id,
                    "tier": step.tier,
                    "status": "not_run",
                    "reason": "explicit operator tier",
                }
            )
            continue
        if step.tier in skip_tiers:
            receipt["results"].append(
                {
                    "id": step.id,
                    "tier": step.tier,
                    "status": "not_run",
                    "reason": "excluded by --skip-tier",
                }
            )
            continue
        if not capture_output:
            print(f"\n==> {step.id}: {shlex.join(step.command)}", flush=True)
        started = time.monotonic()
        result = subprocess.run(
            step.command,
            cwd=Path(plan.root) / step.cwd,
            env=env,
            check=False,
            capture_output=capture_output,
            text=capture_output,
        )
        record = {
            "id": step.id,
            "tier": step.tier,
            "returncode": result.returncode,
            "duration_s": round(time.monotonic() - started, 3),
            "status": "passed" if result.returncode == 0 else "failed",
        }
        if capture_output:
            record["stdout"] = result.stdout
            record["stderr"] = result.stderr
        receipt["results"].append(record)
        if result.returncode:
            failed = True
            if not keep_going:
                break
    receipt["finished_at_epoch"] = time.time()
    receipt["status"] = "failed" if failed else "passed"
    return (1 if failed else 0), receipt


def _write_receipt(path: Path, receipt: dict[str, object]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or run worktree-local ZaoFu verification",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "run"):
        child = subparsers.add_parser(command)
        child.add_argument("--base", default=None)
        child.add_argument("--paths", nargs="+", default=None)
        child.add_argument("--tests", action="append", default=[])
        child.add_argument("--workers", type=int, default=None)
        child.add_argument("--no-parallel", action="store_true")
        child.add_argument("--json", action="store_true")
        if command == "run":
            child.add_argument("--keep-going", action="store_true")
            child.add_argument("--receipt", type=Path, default=None)
            child.add_argument("--skip-tier", action="append", default=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = find_repo_root()
        changed = (
            _normalize_paths(root, args.paths)
            if args.paths
            else discover_changed_files(root, args.base)
        )
        plan = build_plan(
            root,
            changed,
            explicit_tests=args.tests,
            workers=args.workers,
            parallel=not args.no_parallel,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "plan":
        if args.json:
            print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
        else:
            _print_plan(plan)
        return 2 if plan.errors else 0

    if not args.json:
        _print_plan(plan)
    code, receipt = run_plan(
        plan,
        keep_going=args.keep_going,
        capture_output=args.json,
        skip_tiers=frozenset(args.skip_tier),
    )
    if args.receipt:
        _write_receipt(args.receipt, receipt)
    if args.json:
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
