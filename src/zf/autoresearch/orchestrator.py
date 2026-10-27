"""Outer autoresearch supervisor.

This module runs outside the harness being evaluated. It prepares an isolated
worktree, starts the inner real-provider harness runner, watches deterministic
artifacts, writes a run report, and can upsert a repair backlog task on failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from zf.autoresearch.review_gate import (
    normalize_review_gate_mode,
    prepare_review_gate_summary,
)
from zf.autoresearch.scenarios import AutoresearchScenario, resolve_scenario
from zf.autoresearch.task_completion import count_completed_tasks
from zf.autoresearch.stuck_incident import (
    audit_stuck_incident,
    event_log_for_worktree,
    read_worktree_events,
)
from zf.autoresearch.worktree_preparation import (
    PREPARATION_MANIFEST,
    cleanup_prepared_worktree,
    write_preparation_journal,
)
from zf.core.config.loader import load_config
from zf.core.config.render import renderable_config_to_primitive
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.atomic_io import atomic_write_text
from zf.core.package_source import installed_local_source_root
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


FATAL_EVENT_TYPES = frozenset({
    "orchestrator.dispatch_failed",
    "task.invalid_transition",
    "cost.budget.exceeded",
    "run.failed",
    "ship.failed",
    "task.orphaned",
    "worker.respawn.failed",
    "worker.recycle.failed",
    "worker.stuck.recovery_failed",
})

DEFAULT_CONTROLLER_TEMPLATE = Path(
    "examples/prod/controller/prd-task-pipeline-v4-canary.yaml"
)
DEFAULT_FLOW_SOURCE_REF = "docs/prd/autoresearch.md"


@dataclass(frozen=True)
class AutoresearchRunConfig:
    scenario_name: str = "self-eval-backlog"
    worktree: Path = Path("/tmp/zaofu-autoresearch")
    config_template: Path = DEFAULT_CONTROLLER_TEMPLATE
    branch: str = ""
    seed_file: Path | None = None
    expected_done: int | None = None
    timeout_seconds: int | None = None
    budget_usd: float = 500.0
    confirm: bool = False
    reuse_worktree: bool = False
    keep_running: bool = False
    runner_module: str = "tests.e2e.run_mixed"
    run_id: str = ""
    output_dir: Path | None = None
    backlog_on_failure: bool = False
    backlog_state_dir: Path | None = None
    inject_worker_stuck: bool = False
    inject_worker_stuck_instance: str = "dev-lane-0"
    inject_worker_stuck_timeout_seconds: int = 600
    flow_kind: str = "prd"
    flow_source_ref: str = DEFAULT_FLOW_SOURCE_REF
    flow_target_root: str = "."
    task_pipeline_mode: str = "blocking"
    orchestrator_checkpoint_policy: str = "blocking"
    # Overlay the developer's current uncommitted ACDMRT changes into the
    # autoresearch worktree. Disable for strict HEAD-only evaluation (CI).
    sync_dirty: bool = True
    review_gate: str = "off"
    diagnostic_context: str = ""


@dataclass(frozen=True)
class AutoresearchRunResult:
    status: str
    run_id: str
    worktree: Path
    run_dir: Path
    report_path: Path
    tasks_done: int
    expected_done: int
    returncode: int
    fatal_event: dict[str, Any] | None = None
    backlog_task_id: str = ""
    elapsed_seconds: float = 0.0
    review_gate: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == "passed"


def effective_run_config(
    cfg: AutoresearchRunConfig,
    scenario: AutoresearchScenario,
) -> AutoresearchRunConfig:
    """Apply scenario-owned mechanical requirements to one run."""
    if scenario.requires_worker_stuck_injection and not cfg.inject_worker_stuck:
        return replace(cfg, inject_worker_stuck=True)
    return cfg


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def repo_root() -> Path:
    configured = str(os.environ.get("ZF_AUTORESEARCH_SOURCE_ROOT") or "").strip()
    if configured:
        root = Path(configured).expanduser().resolve()
        if _is_git_source_root(root):
            return root
        raise RuntimeError(
            "ZF_AUTORESEARCH_SOURCE_ROOT is not a ZaoFu Git checkout: "
            f"{root}"
        )

    source_root = Path(__file__).resolve().parents[3]
    if _is_git_source_root(source_root):
        return source_root

    installed_root = installed_local_source_root()
    if installed_root is not None and _is_git_source_root(installed_root):
        return installed_root
    raise RuntimeError(
        "Autoresearch requires a ZaoFu Git source checkout. Install ZaoFu "
        "from a local checkout or set ZF_AUTORESEARCH_SOURCE_ROOT."
    )

def _is_git_source_root(path: Path) -> bool:
    return (path / ".git").exists() and (path / "pyproject.toml").is_file()


def default_run_id(scenario_name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in scenario_name)
    return f"{safe}-{utc_stamp()}"


def _env() -> dict[str, str]:
    from zf.core.config.project_context import isolated_project_subprocess_env

    env = isolated_project_subprocess_env()
    env["PYTHONPATH"] = str(repo_root() / "src")
    return env


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    log_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    if log_path is None:
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=_env(),
            text=True,
            capture_output=True,
            check=False,
        )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("$ " + shlex.join(cmd) + "\n")
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=_env(),
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
        log.write(f"\n[exit {proc.returncode}]\n")
    return proc


def _load_config_template(path: Path) -> dict[str, Any]:
    """Expand legacy or controller YAML into one canonical config mapping."""

    data = renderable_config_to_primitive(load_config(path))
    if not isinstance(data, dict):
        raise ValueError(f"yaml root must be a mapping: {path}")
    return data


def _apply_autoresearch_controller_overrides(
    data: dict[str, Any],
    *,
    cfg: AutoresearchRunConfig,
    run_id: str,
) -> None:
    project = data.setdefault("project", {})
    project["name"] = "zaofu-autoresearch"
    project["state_dir"] = ".zf"
    data.setdefault("session", {})["tmux_session"] = (
        f"zf-autoresearch-{run_id}"
    )
    data["global_budget_usd"] = cfg.budget_usd

    workflow = data.setdefault("workflow", {})
    flow_metadata = workflow.get("_flow_metadata")
    if isinstance(flow_metadata, dict):
        flow_metadata["prd_ref"] = cfg.flow_source_ref
        flow_metadata["target_root"] = cfg.flow_target_root
        task_pipeline = flow_metadata.get("task_pipeline")
        if isinstance(task_pipeline, dict):
            task_pipeline["mode"] = cfg.task_pipeline_mode

    orchestration = workflow.get("orchestration")
    if isinstance(orchestration, dict) and cfg.orchestrator_checkpoint_policy:
        for key in (
            "checkpoints",
            "checkpoint_policies",
            "pilot_id",
            "shadow_sample_percent",
        ):
            orchestration.pop(key, None)
        orchestration["mode"] = "exception_advisor"
        flow_policy = {
            "mode": "semantic_control",
            "checkpoints": ["plan_candidate"],
            "checkpoint_policies": {
                "plan_candidate": cfg.orchestrator_checkpoint_policy,
            },
            "shadow_sample_percent": 100,
        }
        if cfg.orchestrator_checkpoint_policy == "blocking":
            flow_policy["pilot_id"] = "autoresearch-prd-v4"
        orchestration.setdefault("flow_policies", {})[
            cfg.flow_kind
        ] = flow_policy

    run_limits = workflow.get("run_limits")
    if isinstance(run_limits, dict):
        run_limits["cost_budget_usd"] = cfg.budget_usd

    # The diagnostic checkout owns the tested source version. Reusing the
    # launcher checkout's skills adds a second candidate beside project skills.
    sources = []
    seen_roots: set[Path] = set()
    worktree = cfg.worktree.resolve()
    for raw_source in data.get("skill_sources") or []:
        if not isinstance(raw_source, dict):
            continue
        source = dict(raw_source)
        name = str(source.get("name") or "")
        local_root = worktree / {"zaofu-skills": "skills", "yoke": "yoke"}.get(name, "")
        if name in {"zaofu-skills", "yoke"} and local_root.is_dir():
            source["path"] = str(local_root)
        elif name == "zaofu-skills":
            source["path"] = str(repo_root() / "skills")
        root = Path(source["path"]).expanduser()
        root = (root if root.is_absolute() else worktree / root).resolve()
        if root == worktree / "skills" or root in seen_roots:
            continue
        seen_roots.add(root)
        sources.append(source)
    local_yoke = worktree / "yoke"
    if local_yoke.is_dir() and local_yoke.resolve() not in seen_roots:
        sources.append({"name": "yoke", "path": str(local_yoke), "mode": "readonly"})
    data["skill_sources"] = sources

    feishu_path = cfg.worktree.resolve() / "feishu.yaml"
    if feishu_path.is_file():
        external = yaml.safe_load(feishu_path.read_text(encoding="utf-8")) or {}
        integrations = data.get("integrations")
        if isinstance(external, dict) and isinstance(integrations, dict):
            for key in ("feishu_identity", "feishu_routing"):
                if key in external:
                    integrations.pop(key, None)


def ensure_web_dependencies(worktree: Path, *, log_path: Path) -> str:
    """Ensure a freshly-created worktree can run Web quality gates.

    Git worktrees do not include ignored ``web/node_modules``. The harness
    presets run ``npm --prefix web ...`` checks during judge, so autoresearch
    must make those dependencies available before starting the inner run.
    Prefer linking the already-prepared repo dependencies; fall back to
    ``npm ci`` when the source checkout has not installed them.
    """
    web_dir = worktree / "web"
    package_json = web_dir / "package.json"
    package_lock = web_dir / "package-lock.json"
    if not package_json.exists() or not package_lock.exists():
        return "skipped:no-web-package"

    target_node_modules = web_dir / "node_modules"
    target_tsc = target_node_modules / ".bin" / "tsc"
    if target_tsc.exists():
        return "present"

    source_node_modules = repo_root() / "web" / "node_modules"
    source_tsc = source_node_modules / ".bin" / "tsc"
    if source_tsc.exists():
        if target_node_modules.is_symlink():
            target_node_modules.unlink()
        if not target_node_modules.exists():
            target_node_modules.symlink_to(source_node_modules, target_is_directory=True)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                f"linked {target_node_modules} -> {source_node_modules}\n",
                encoding="utf-8",
            )
            return "linked"

    result = _run(
        ["npm", "--prefix", str(web_dir), "ci", "--include=dev"],
        cwd=worktree,
        log_path=log_path,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "npm ci failed while preparing autoresearch worktree; "
            f"see {log_path}"
        )
    return "installed"


def sync_tracked_checkout_changes(
    worktree: Path, *, log_path: Path, enabled: bool = True,
) -> dict[str, Any]:
    """Overlay tracked dirty files (incl. deletes/renames) into worktree.

    Autoresearch evaluates the developer's current candidate, not just the last
    commit. A plain ``git worktree add ... HEAD`` misses uncommitted fixes, so
    targeted validation can keep failing on stale tests/source. Keep this to
    tracked files and never copy ``zf.yaml`` because autoresearch materializes
    its own experiment config there.

    ``enabled=False`` skips the overlay (strict HEAD evaluation) and returns
    an empty summary so the run record still captures the mode choice.

    Returns a summary dict ``{added: [...], modified: [...], deleted: [...],
    renamed: [(old, new), ...], skipped: bool}`` so callers can emit a
    ``autoresearch.candidate.synced`` event and replay the same selection.
    """
    summary: dict[str, Any] = {
        "added": [],
        "modified": [],
        "deleted": [],
        "renamed": [],
        "skipped": not enabled,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if not enabled:
        log_path.write_text("skipped: strict HEAD evaluation\n", encoding="utf-8")
        return summary

    result = _run(
        # ACDMRT: Added / Copied / Deleted / Modified / Renamed / Typechanged.
        # -z keeps NUL-delimited records so rename's "old\0new" pair survives
        # paths with spaces or newlines.
        ["git", "diff", "--name-status", "-z", "--diff-filter=ACDMRT", "HEAD"],
        cwd=repo_root(),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"git diff failed while syncing worktree: {detail}")

    log_entries: list[str] = []
    tokens = [t for t in result.stdout.split("\0") if t]
    i = 0
    while i < len(tokens):
        status = tokens[i]
        i += 1
        # R<score> / C<score> have two path operands; A/D/M/T have one.
        if status.startswith("R") or status.startswith("C"):
            if i + 1 >= len(tokens):
                break
            old, new = tokens[i], tokens[i + 1]
            i += 2
            if new == "zf.yaml" or old == "zf.yaml":
                continue
            old_dst = worktree / old
            new_src = repo_root() / new
            new_dst = worktree / new
            if old_dst.exists():
                try:
                    old_dst.unlink()
                except OSError:
                    pass
            if new_src.is_file():
                new_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(new_src, new_dst)
            summary["renamed"].append([old, new])
            log_entries.append(f"R\t{old}\t{new}")
            continue
        code = status[:1]
        if i >= len(tokens):
            break
        rel = tokens[i]
        i += 1
        if not rel or rel == "zf.yaml":
            continue
        dst = worktree / rel
        if code == "D":
            if dst.exists():
                try:
                    dst.unlink()
                except OSError:
                    pass
            summary["deleted"].append(rel)
            log_entries.append(f"D\t{rel}")
            continue
        src = repo_root() / rel
        if not src.is_file():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if code == "A":
            summary["added"].append(rel)
            log_entries.append(f"A\t{rel}")
        else:
            summary["modified"].append(rel)
            log_entries.append(f"{code}\t{rel}")

    log_path.write_text(
        "\n".join(log_entries) + ("\n" if log_entries else ""),
        encoding="utf-8",
    )
    return summary


def _checkpoint_prepared_flow_baseline(
    worktree: Path,
    *,
    synced_summary: dict[str, Any],
    run_id: str,
) -> str:
    """Commit the explicit candidate/config paths in an isolated Flow worktree."""

    if not (worktree / ".git").exists():
        return ""

    paths = {"zf.yaml"}
    for key in ("added", "modified", "deleted"):
        values = synced_summary.get(key) or []
        if isinstance(values, list):
            paths.update(str(value) for value in values if str(value))
    renamed = synced_summary.get("renamed") or []
    if isinstance(renamed, list):
        for pair in renamed:
            if not isinstance(pair, list):
                continue
            paths.update(str(value) for value in pair if str(value))
    ordered_paths = sorted(paths)

    staged_before = _run(
        ["git", "diff", "--cached", "--name-only", "-z"],
        cwd=worktree,
    )
    if staged_before.returncode != 0:
        raise RuntimeError("git staged-path audit failed before Flow checkpoint")
    unexpected = {
        value for value in staged_before.stdout.split("\0") if value
    } - paths
    if unexpected:
        raise RuntimeError(
            "refusing to checkpoint unrelated staged paths: "
            + ", ".join(sorted(unexpected))
        )

    staged = _run(
        ["git", "add", "-A", "--", *ordered_paths],
        cwd=worktree,
    )
    if staged.returncode != 0:
        detail = (staged.stderr or staged.stdout or "").strip()
        raise RuntimeError(f"git add failed for Flow baseline: {detail}")
    staged_paths_result = _run(
        ["git", "diff", "--cached", "--name-only", "-z"],
        cwd=worktree,
    )
    if staged_paths_result.returncode != 0:
        raise RuntimeError("git staged-path audit failed after Flow checkpoint")
    staged_paths = {
        value
        for value in staged_paths_result.stdout.split("\0")
        if value
    }
    unexpected = staged_paths - paths
    if unexpected:
        raise RuntimeError(
            "Flow checkpoint staged paths outside its ownership: "
            + ", ".join(sorted(unexpected))
        )
    if staged_paths:
        committed = _run(
            [
                "git",
                "-c",
                "user.email=autoresearch@zaofu.dev",
                "-c",
                "user.name=ZaoFu Autoresearch",
                "commit",
                "-q",
                "-m",
                f"chore: checkpoint autoresearch Flow baseline {run_id}",
                "--",
                *sorted(staged_paths),
            ],
            cwd=worktree,
        )
        if committed.returncode != 0:
            detail = (committed.stderr or committed.stdout or "").strip()
            raise RuntimeError(f"git commit failed for Flow baseline: {detail}")

    head = _run(["git", "rev-parse", "HEAD"], cwd=worktree)
    if head.returncode != 0:
        raise RuntimeError("git rev-parse failed after Flow baseline checkpoint")
    return head.stdout.strip()


def prepare_worktree(
    cfg: AutoresearchRunConfig,
    *,
    scenario: AutoresearchScenario,
    run_id: str,
    run_dir: Path,
) -> Path:
    root = repo_root()
    worktree = cfg.worktree.resolve()
    branch = cfg.branch or f"experiment/autoresearch-{run_id}"
    config_src = cfg.config_template
    if not config_src.is_absolute():
        config_src = root / config_src
    if not config_src.exists():
        raise FileNotFoundError(f"config template not found: {config_src}")

    if worktree.exists():
        if not cfg.reuse_worktree:
            raise FileExistsError(
                f"worktree already exists: {worktree} "
                "(use --reuse-worktree to keep it)"
            )
    else:
        created = _run(
            ["git", "worktree", "add", "-b", branch, str(worktree), "HEAD"],
            cwd=root,
        )
        if created.returncode != 0:
            detail = (created.stderr or created.stdout or "").strip()
            raise RuntimeError(f"git worktree add failed: {detail}")

    zf_yaml = worktree / "zf.yaml"
    seed_path = worktree / "autoresearch-seed.txt"
    preparation_dir = run_dir / "worktree-preparation"
    preparation_dir.mkdir(parents=True, exist_ok=True)
    journal_path = preparation_dir / PREPARATION_MANIFEST
    original_zf = preparation_dir / "zf.yaml.original"
    original_seed = preparation_dir / "autoresearch-seed.txt.original"
    zf_existed = zf_yaml.is_file()
    seed_existed = seed_path.is_file()
    if zf_existed:
        shutil.copy2(zf_yaml, original_zf)
    if seed_existed:
        shutil.copy2(seed_path, original_seed)
    target_node_modules = worktree / "web" / "node_modules"
    web_dependencies_preexisting = (
        target_node_modules.exists() or target_node_modules.is_symlink()
    )
    preparation = {
        "schema_version": "autoresearch-worktree-preparation.v2",
        "phase": "initialized",
        "worktree": str(worktree),
        "zf_yaml": str(zf_yaml),
        "zf_existed": zf_existed,
        "original_zf": str(original_zf) if zf_existed else "",
        "generated_zf_sha256": "",
        "seed_file": str(seed_path),
        "seed_existed": seed_existed,
        "original_seed": str(original_seed) if seed_existed else "",
        "generated_seed_sha256": "",
        "baseline_checkpoint_commit": "",
        "web_dependencies": {
            "mode": "pending",
            "path": "web/node_modules",
            "preexisting": web_dependencies_preexisting,
            "symlink_target": "",
        },
    }
    write_preparation_journal(journal_path, preparation)

    try:
        synced_summary = sync_tracked_checkout_changes(
            worktree,
            log_path=run_dir / "synced-current-checkout.log",
            enabled=cfg.sync_dirty,
        )
        preparation["candidate_sync"] = synced_summary
        preparation["phase"] = "candidate_synced"
        write_preparation_journal(journal_path, preparation)

        data = _load_config_template(config_src)
        _apply_autoresearch_controller_overrides(
            data,
            cfg=cfg,
            run_id=run_id,
        )
        rendered_zf = yaml.safe_dump(
            data,
            allow_unicode=True,
            sort_keys=False,
        )
        preparation["generated_zf_sha256"] = hashlib.sha256(
            rendered_zf.encode("utf-8")
        ).hexdigest()
        preparation["phase"] = "zf_write_pending"
        write_preparation_journal(journal_path, preparation)
        atomic_write_text(zf_yaml, rendered_zf)
        preparation["phase"] = "zf_written"
        write_preparation_journal(journal_path, preparation)

        source_node_modules = root / "web" / "node_modules"
        source_tsc = source_node_modules / ".bin" / "tsc"
        web = preparation["web_dependencies"]
        web["mode"] = "preparing"
        web["symlink_target"] = (
            str(source_node_modules) if source_tsc.exists() else ""
        )
        preparation["phase"] = "web_preparing"
        write_preparation_journal(journal_path, preparation)
        web_dependency_mode = ensure_web_dependencies(
            worktree,
            log_path=run_dir / "prepare-web-deps.log",
        )
        web_dependency_link_target = (
            os.readlink(target_node_modules)
            if target_node_modules.is_symlink()
            else ""
        )
        web["mode"] = web_dependency_mode
        web["symlink_target"] = web_dependency_link_target
        preparation["phase"] = "web_prepared"
        write_preparation_journal(journal_path, preparation)

        if cfg.flow_kind:
            preparation["phase"] = "baseline_checkpoint_pending"
            write_preparation_journal(journal_path, preparation)
            preparation["baseline_checkpoint_commit"] = (
                _checkpoint_prepared_flow_baseline(
                    worktree,
                    synced_summary=synced_summary,
                    run_id=run_id,
                )
            )
            preparation["phase"] = "baseline_checkpointed"
            write_preparation_journal(journal_path, preparation)

        seed_text = scenario.seed_text.strip() + "\n"
        if cfg.diagnostic_context:
            seed_text += (
                "\n## 原始故障诊断输入\n"
                "以下为待诊断事件的只读引用；通用场景通过不能证明原故障已修复。"
                "核查原始证据并报告复现或未确认结论，不修改来源项目的运行状态。\n"
                + cfg.diagnostic_context + "\n"
            )
        preparation["generated_seed_sha256"] = hashlib.sha256(
            seed_text.encode("utf-8")
        ).hexdigest()
        preparation["phase"] = "seed_write_pending"
        write_preparation_journal(journal_path, preparation)
        atomic_write_text(seed_path, seed_text)
        preparation["phase"] = "prepared"
        write_preparation_journal(journal_path, preparation)
    except BaseException as exc:
        web = preparation.get("web_dependencies")
        if (
            isinstance(web, dict)
            and not web_dependencies_preexisting
            and (
                target_node_modules.exists()
                or target_node_modules.is_symlink()
            )
        ):
            if target_node_modules.is_symlink():
                web["mode"] = "linked"
                try:
                    web["symlink_target"] = os.readlink(target_node_modules)
                except OSError:
                    web["symlink_target"] = ""
            elif target_node_modules.is_dir():
                web["mode"] = "installed"
        preparation["phase"] = "failed"
        preparation["error"] = f"{type(exc).__name__}: {exc}"
        try:
            write_preparation_journal(journal_path, preparation)
        except OSError:
            pass
        try:
            cleanup_prepared_worktree(worktree=worktree, run_dir=run_dir)
        except OSError:
            pass
        raise

    manifest = {
        "run_id": run_id,
        "scenario": scenario.name,
        "description": scenario.description,
        "worktree": str(worktree),
        "branch": branch,
        "config_template": str(config_src),
        "zf_yaml": str(zf_yaml),
        "seed_file": str(seed_path),
        "expected_done": scenario.expected_done,
        "timeout_seconds": scenario.timeout_seconds,
        "budget_usd": cfg.budget_usd,
        "inject_worker_stuck": cfg.inject_worker_stuck,
        "inject_worker_stuck_instance": cfg.inject_worker_stuck_instance,
        "inject_worker_stuck_timeout_seconds": (
            cfg.inject_worker_stuck_timeout_seconds
        ),
        "requires_worker_stuck_injection": (
            scenario.requires_worker_stuck_injection
        ),
        "web_dependency_mode": web_dependency_mode,
        "synced_current_checkout": synced_summary,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write_text(
            run_dir / "scenario.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        )
    except BaseException as exc:
        preparation["phase"] = "failed"
        preparation["error"] = f"{type(exc).__name__}: {exc}"
        try:
            write_preparation_journal(journal_path, preparation)
        except OSError:
            pass
        try:
            cleanup_prepared_worktree(worktree=worktree, run_dir=run_dir)
        except OSError:
            pass
        raise
    return seed_path


def cleanup_autoresearch_run(
    cfg: AutoresearchRunConfig,
    *,
    run_id: str,
) -> dict[str, Any]:
    run_dir = (
        cfg.output_dir
        if cfg.output_dir is not None
        else cfg.worktree.resolve() / ".zf" / "autoresearch" / "runs" / run_id
    )
    return cleanup_prepared_worktree(worktree=cfg.worktree, run_dir=run_dir)


def build_inner_runner_command(
    cfg: AutoresearchRunConfig,
    *,
    scenario: AutoresearchScenario,
    seed_path: Path,
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        cfg.runner_module,
        "--worktree",
        str(cfg.worktree.resolve()),
        "--seed-file",
        str(seed_path),
        "--expected-done",
        str(scenario.expected_done),
        "--timeout",
        str(scenario.timeout_seconds),
        "--confirm",
    ]
    if cfg.flow_kind:
        cmd.extend([
            "--flow-kind",
            cfg.flow_kind,
            "--flow-source",
            cfg.flow_source_ref,
            "--flow-target-root",
            cfg.flow_target_root,
        ])
    if cfg.keep_running:
        cmd.append("--no-stop")
    return cmd


def read_events(events_path: Path) -> list[dict[str, Any]]:
    return [
        asdict(event)
        for event in EventLog(events_path).read_all()
    ]


def _dispatch_for_stuck_injection(
    events: list[dict[str, Any]],
    *,
    target_instance: str,
) -> dict[str, Any] | None:
    """Return the first task.dispatched event matching the injection target."""
    for event in events:
        if event.get("type") != "task.dispatched":
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        assignee = str(payload.get("assignee") or "")
        role = str(payload.get("role") or "")
        if target_instance and target_instance not in {assignee, role}:
            continue
        if not str(event.get("task_id") or ""):
            continue
        return event
    return None


def _emit_stuck_injection(
    *,
    worktree: Path,
    run_dir: Path,
    dispatch_event: dict[str, Any],
    target_instance: str,
) -> bool:
    payload = dispatch_event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    task_id = str(dispatch_event.get("task_id") or "")
    assignee = str(payload.get("assignee") or target_instance)
    injection_payload = {
        "source": "autoresearch",
        "mode": "deterministic-worker-stuck",
        "instance_id": assignee,
        "target_instance": target_instance,
        "role": str(payload.get("role") or ""),
        "dispatch_id": str(payload.get("dispatch_id") or ""),
        "briefing": str(payload.get("briefing") or ""),
        "trigger_event_id": str(dispatch_event.get("id") or ""),
        "reason": "controlled autoresearch stuck injection",
    }
    payload_path = run_dir / "worker-stuck-injection.json"
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(
        json.dumps(injection_payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    try:
        EventWriter(
            event_log_for_worktree(worktree),
            default_origin="external",
        ).append(ZfEvent(
            type="autoresearch.inject.worker_stuck",
            actor="zf-autoresearch",
            task_id=task_id,
            causation_id=str(dispatch_event.get("id") or "") or None,
            correlation_id=(
                str(dispatch_event.get("correlation_id") or "") or None
            ),
            payload=injection_payload,
        ))
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _run_inner_runner(
    cmd: list[str],
    *,
    cwd: Path,
    log_path: Path,
    cfg: AutoresearchRunConfig,
    run_dir: Path,
) -> subprocess.CompletedProcess[str]:
    if not cfg.inject_worker_stuck:
        return _run(cmd, cwd=cwd, log_path=log_path)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    injected = False
    injection_warning_logged = False
    last_dispatch_count = 0
    last_dispatch_at: float | None = None
    target_wait_seconds = max(1, cfg.inject_worker_stuck_timeout_seconds)
    baseline_event_ids = {
        event.id for event in read_worktree_events(cfg.worktree.resolve())
    }
    with log_path.open("a", encoding="utf-8") as log:
        log.write("$ " + shlex.join(cmd) + "\n")
        log.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=_env(),
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        while proc.poll() is None:
            if not injected:
                event_models = read_worktree_events(cfg.worktree.resolve())
                fresh_events = [
                    event
                    for event in event_models
                    if event.id not in baseline_event_ids
                ]
                session_index = next(
                    (
                        index
                        for index, event in enumerate(fresh_events)
                        if event.type == "session.started"
                    ),
                    None,
                )
                eligible_models = (
                    fresh_events[session_index + 1 :]
                    if session_index is not None
                    else []
                )
                events = [asdict(event) for event in eligible_models]
                dispatch_count = sum(
                    1 for event in events if event.get("type") == "task.dispatched"
                )
                if dispatch_count > last_dispatch_count:
                    last_dispatch_count = dispatch_count
                    last_dispatch_at = time.time()
                dispatch = _dispatch_for_stuck_injection(
                    events,
                    target_instance=cfg.inject_worker_stuck_instance,
                )
                if dispatch is not None:
                    injected = _emit_stuck_injection(
                        worktree=cfg.worktree.resolve(),
                        run_dir=run_dir,
                        dispatch_event=dispatch,
                        target_instance=cfg.inject_worker_stuck_instance,
                    )
                    log.write(
                        "\n[autoresearch] worker_stuck_injection="
                        f"{'emitted' if injected else 'emit_failed'} "
                        f"target={cfg.inject_worker_stuck_instance}\n"
                    )
                    log.flush()
                elif (
                    last_dispatch_at is not None
                    and not injection_warning_logged
                    and time.time() - last_dispatch_at >= target_wait_seconds
                ):
                    injection_warning_logged = True
                    log.write(
                        "\n[autoresearch] worker_stuck_injection=waiting "
                        f"target={cfg.inject_worker_stuck_instance} "
                        "reason=target_dispatch_not_seen_after_timeout\n"
                    )
                    log.flush()
            time.sleep(1.0)
        rc = proc.wait()
        if not injected:
            log.write(
                "\n[autoresearch] worker_stuck_injection=not_emitted "
                f"target={cfg.inject_worker_stuck_instance} "
                "reason=inner_runner_exited_before_target_dispatch\n"
            )
        log.write(f"\n[exit {rc}]\n")
    return subprocess.CompletedProcess(cmd, rc)


def summarize_events(
    worktree: Path,
    expected_done: int,
    *,
    stuck_incident_required: bool = False,
) -> dict[str, Any]:
    event_models = read_worktree_events(worktree)
    events = [asdict(event) for event in event_models]
    stuck_incident_audit = audit_stuck_incident(
        event_models,
        required=stuck_incident_required,
    )
    done = count_completed_tasks(events)
    fatal = None
    fatal_count = 0
    dispatch_by_instance: dict[str, int] = {}
    event_counts: dict[str, int] = {}
    success_seen: dict[tuple[str, str, str], int] = {}
    success_event_types = {
        "dev.build.done",
        "review.approved",
        "test.passed",
        "judge.passed",
    }
    for event in events:
        event_type = str(event.get("type") or "")
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        if event_type in FATAL_EVENT_TYPES:
            fatal_count += 1
            if fatal is None:
                fatal = event
        if event_type == "task.dispatched":
            payload = event.get("payload") or {}
            if isinstance(payload, dict):
                assignee = str(payload.get("assignee") or "?")
                dispatch_by_instance[assignee] = (
                    dispatch_by_instance.get(assignee, 0) + 1
                )
        if event_type in success_event_types:
            payload = event.get("payload") or {}
            dispatch_id = ""
            if isinstance(payload, dict):
                dispatch_id = str(payload.get("dispatch_id") or "")
            if dispatch_id:
                key = (
                    str(event.get("task_id") or ""),
                    event_type,
                    dispatch_id,
                )
                success_seen[key] = success_seen.get(key, 0) + 1

    duplicate_success_event_count = sum(
        count - 1 for count in success_seen.values() if count > 1
    )
    done_evidence = event_counts.get("task.done.evidence", 0)
    terminal_evidence_coverage = 0.0
    if done:
        terminal_evidence_coverage = min(1.0, done_evidence / done)
    dev_replicas = sorted(
        assignee for assignee in dispatch_by_instance if assignee.startswith("dev-")
    )
    test_replicas = sorted(
        assignee for assignee in dispatch_by_instance if assignee.startswith("test-")
    )
    rework_signal_count = sum(
        event_counts.get(name, 0)
        for name in (
            "review.rejected",
            "test.failed",
            "judge.failed",
            "gate.failed",
            "discriminator.failed",
            "task.rework.requested",
            "task.done.blocked",
        )
    )
    derived_metrics = {
        "fatal_count": fatal_count,
        "stuck_injection_requested_count": event_counts.get(
            "autoresearch.inject.worker_stuck", 0,
        ),
        "worker_stuck_count": event_counts.get("worker.stuck", 0),
        "worker_stuck_recovered_count": event_counts.get(
            "worker.stuck.recovered", 0,
        ),
        "worker_stuck_recovery_failed_count": event_counts.get(
            "worker.stuck.recovery_failed", 0,
        ),
        "task_done_blocked_count": event_counts.get("task.done.blocked", 0),
        "done_evidence_count": done_evidence,
        "terminal_evidence_coverage": terminal_evidence_coverage,
        "discriminator_passed_count": event_counts.get("discriminator.passed", 0),
        "discriminator_failed_count": event_counts.get("discriminator.failed", 0),
        "invalid_transition_count": event_counts.get("task.invalid_transition", 0),
        "duplicate_success_event_count": duplicate_success_event_count,
        "rework_signal_count": rework_signal_count,
        "dev_replicas_used": dev_replicas,
        "test_replicas_used": test_replicas,
    }
    derived_metrics["stuck_injection_satisfied"] = bool(
        stuck_incident_required and stuck_incident_audit.status == "passed"
    )
    return {
        "tasks_done": done,
        "expected_done": expected_done,
        "fatal_event": fatal,
        "fatal_count": fatal_count,
        "dispatch_by_instance": dict(sorted(dispatch_by_instance.items())),
        "event_counts": dict(sorted(event_counts.items())),
        "derived_metrics": derived_metrics,
        "stuck_incident_audit": stuck_incident_audit.to_dict(),
    }


def write_iterations(run_dir: Path, row: dict[str, Any]) -> Path:
    path = run_dir / "iterations.tsv"
    header = [
        "run_id",
        "scenario",
        "status",
        "tasks_done",
        "expected_done",
        "returncode",
        "elapsed_seconds",
        "fatal_type",
    ]
    fatal = row.get("fatal_event") or {}
    values = [
        str(row.get("run_id", "")),
        str(row.get("scenario", "")),
        str(row.get("status", "")),
        str(row.get("tasks_done", "")),
        str(row.get("expected_done", "")),
        str(row.get("returncode", "")),
        f"{float(row.get('elapsed_seconds', 0.0)):.1f}",
        str(fatal.get("type") or ""),
    ]
    path.write_text(
        "\t".join(header) + "\n" + "\t".join(values) + "\n",
        encoding="utf-8",
    )
    return path


def write_report(run_dir: Path, row: dict[str, Any]) -> Path:
    fatal = row.get("fatal_event") or {}
    summary = row.get("summary") or {}
    review_gate = row.get("review_gate") or {}
    lines = [
        f"# Autoresearch Run {row.get('run_id', '')}",
        "",
        f"- scenario: `{row.get('scenario', '')}`",
        f"- status: `{row.get('status', '')}`",
        f"- worktree: `{row.get('worktree', '')}`",
        f"- tasks_done: {row.get('tasks_done', 0)} / {row.get('expected_done', 0)}",
        f"- returncode: {row.get('returncode', 0)}",
        f"- elapsed_seconds: {float(row.get('elapsed_seconds', 0.0)):.1f}",
        f"- log: `{row.get('log_path', '')}`",
        "",
        "## Fatal Event",
        "",
    ]
    if fatal:
        lines.extend([
            f"- type: `{fatal.get('type', '')}`",
            f"- task_id: `{fatal.get('task_id') or ''}`",
            f"- actor: `{fatal.get('actor') or ''}`",
            "",
            "```json",
            json.dumps(
                fatal.get("payload") or {},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "```",
            "",
        ])
    else:
        lines.extend(["none", ""])
    if review_gate:
        lines.extend([
            "## Review Gate",
            "",
            f"- mode: `{review_gate.get('mode', '')}`",
            f"- status: `{review_gate.get('status', '')}`",
            f"- triggered: `{review_gate.get('triggered', False)}`",
            f"- route: `{review_gate.get('route', '')}`",
            f"- severity: `{review_gate.get('severity', '')}`",
            f"- reason: {review_gate.get('reason', '')}",
            "",
            "```json",
            json.dumps(
                review_gate.get("artifact_refs") or {},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "```",
            "",
        ])
    lines.extend([
        "## Stuck Incident Audit",
        "",
        "```json",
        json.dumps(
            summary.get("stuck_incident_audit") or {},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "```",
        "",
        "## Dispatch",
        "",
        "```json",
        json.dumps(
            summary.get("dispatch_by_instance") or {},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "```",
        "",
        "## Derived Metrics",
        "",
        "```json",
        json.dumps(
            summary.get("derived_metrics") or {},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "```",
        "",
        "## Event Counts",
        "",
        "```json",
        json.dumps(
            summary.get("event_counts") or {},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "```",
        "",
    ])
    path = run_dir / "report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_review_gate_error_summary(
    *,
    mode: str,
    run_status: str,
    run_dir: Path,
    error: BaseException,
) -> dict[str, Any]:
    out = run_dir / "review-gate" / "summary.json"
    payload = {
        "schema_version": "autoresearch.review_gate.summary.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "status": "error",
        "triggered": False,
        "route": "",
        "reason": str(error),
        "run_status": run_status,
        "artifact_refs": {},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = dict(payload)
    summary["artifact_refs"] = {"summary": str(out)}
    return summary


def _maybe_prepare_review_gate(
    *,
    mode: str,
    run_status: str,
    run_dir: Path,
    worktree: Path,
) -> dict[str, Any] | None:
    if mode == "off":
        return None
    try:
        return prepare_review_gate_summary(
            mode=mode,
            run_status=run_status,
            run_dir=run_dir,
            state_dir=worktree / ".zf",
            source_root=worktree,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return _write_review_gate_error_summary(
            mode=mode,
            run_status=run_status,
            run_dir=run_dir,
            error=exc,
        )


def upsert_failure_backlog(
    *,
    cfg: AutoresearchRunConfig,
    scenario: AutoresearchScenario,
    row: dict[str, Any],
) -> str:
    state_dir = cfg.backlog_state_dir or (repo_root() / ".zf")
    state_dir.mkdir(parents=True, exist_ok=True)
    store = TaskStore(state_dir / "kanban.json")
    token = hashlib.sha256(
        f"{scenario.name}:{cfg.config_template}:{cfg.worktree}".encode("utf-8")
    ).hexdigest()[:12]
    key = f"autoresearch:{token}"
    title = f"Fix autoresearch failure: {scenario.name}"
    reason = ""
    fatal = row.get("fatal_event") or {}
    if fatal:
        reason = f"fatal={fatal.get('type', '')}, task={fatal.get('task_id') or ''}"
    else:
        reason = f"status={row.get('status', '')}, returncode={row.get('returncode', 0)}"

    existing = next(
        (
            task for task in store.list_all()
            if task.key == key and task.status not in {"done", "cancelled"}
        ),
        None,
    )
    verification = (
        "Run the autoresearch supervisor again and require "
        f"tasks_done >= {scenario.expected_done}, no fatal events, and report.md present."
    )
    if existing is None:
        task = Task(
            key=key,
            title=title,
            status="backlog",
            priority=1,
        )
        task.contract.behavior = (
            "Repair the harness/runtime issue exposed by the autoresearch run. "
            f"Failure reason: {reason}. Report: {row.get('report_path', '')}"
        )
        task.contract.verification = verification
        store.add(task)
        task_id = task.id
        event_type = "task.created"
    else:
        task_id = existing.id
        store.update(
            task_id,
            title=title,
            status="backlog",
            priority=1,
        )
        event_type = "task.updated"

    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type=event_type,
        actor="zf-autoresearch",
        task_id=task_id,
        payload={
            "source": "autoresearch",
            "key": key,
            "scenario": scenario.name,
            "reason": reason,
            "report": str(row.get("report_path", "")),
        },
    ))
    return task_id


def run_autoresearch(cfg: AutoresearchRunConfig) -> AutoresearchRunResult:
    review_gate_mode = normalize_review_gate_mode(cfg.review_gate)
    run_id = cfg.run_id or default_run_id(cfg.scenario_name)
    scenario = resolve_scenario(
        cfg.scenario_name,
        seed_file=cfg.seed_file,
        expected_done=cfg.expected_done,
        timeout_seconds=cfg.timeout_seconds,
    )
    cfg = effective_run_config(cfg, scenario)
    run_dir = (
        cfg.output_dir
        if cfg.output_dir is not None
        else cfg.worktree.resolve() / ".zf" / "autoresearch" / "runs" / run_id
    )
    if not cfg.confirm:
        run_dir.mkdir(parents=True, exist_ok=True)
        row = {
            "run_id": run_id,
            "scenario": scenario.name,
            "status": "dry-run",
            "worktree": str(cfg.worktree.resolve()),
            "tasks_done": 0,
            "expected_done": scenario.expected_done,
            "returncode": 0,
            "elapsed_seconds": 0.0,
            "summary": {},
            "log_path": "",
        }
        if review_gate_mode != "off":
            row["review_gate"] = {
                "mode": review_gate_mode,
                "status": "skipped",
                "triggered": False,
                "route": "",
                "reason": "dry-run does not inspect runtime failure evidence",
                "artifact_refs": {},
            }
        report_path = write_report(run_dir, row)
        write_iterations(run_dir, {
            "run_id": run_id,
            "scenario": scenario.name,
            "status": "dry-run",
            "tasks_done": 0,
            "expected_done": scenario.expected_done,
            "returncode": 0,
            "elapsed_seconds": 0.0,
            "fatal_event": None,
        })
        return AutoresearchRunResult(
            status="dry-run",
            run_id=run_id,
            worktree=cfg.worktree.resolve(),
            run_dir=run_dir,
            report_path=report_path,
            tasks_done=0,
            expected_done=scenario.expected_done,
            returncode=0,
            review_gate=row.get("review_gate"),
        )

    start = time.time()
    seed_path = prepare_worktree(cfg, scenario=scenario, run_id=run_id, run_dir=run_dir)
    validate_log = run_dir / "validate.log"
    validate = _run(
        [sys.executable, "-m", "zf.cli.main", "validate", "--path",
         str(cfg.worktree.resolve() / "zf.yaml")],
        cwd=repo_root(),
        log_path=validate_log,
    )
    if validate.returncode != 0:
        elapsed = time.time() - start
        row = {
            "run_id": run_id,
            "scenario": scenario.name,
            "status": "validate_failed",
            "worktree": str(cfg.worktree.resolve()),
            "tasks_done": 0,
            "expected_done": scenario.expected_done,
            "returncode": validate.returncode,
            "elapsed_seconds": elapsed,
            "summary": {},
            "log_path": str(validate_log),
            "fatal_event": None,
        }
        review_gate = _maybe_prepare_review_gate(
            mode=review_gate_mode,
            run_status="validate_failed",
            run_dir=run_dir,
            worktree=cfg.worktree.resolve(),
        )
        if review_gate is not None:
            row["review_gate"] = review_gate
        report_path = write_report(run_dir, row)
        row["report_path"] = str(report_path)
        write_iterations(run_dir, row)
        task_id = (
            upsert_failure_backlog(cfg=cfg, scenario=scenario, row=row)
            if cfg.backlog_on_failure
            else ""
        )
        return AutoresearchRunResult(
            status="validate_failed",
            run_id=run_id,
            worktree=cfg.worktree.resolve(),
            run_dir=run_dir,
            report_path=report_path,
            tasks_done=0,
            expected_done=scenario.expected_done,
            returncode=validate.returncode,
            backlog_task_id=task_id,
            elapsed_seconds=elapsed,
            review_gate=review_gate,
        )

    log_path = run_dir / "inner-runner.log"
    cmd = build_inner_runner_command(cfg, scenario=scenario, seed_path=seed_path)
    proc = _run_inner_runner(
        cmd,
        cwd=repo_root(),
        log_path=log_path,
        cfg=cfg,
        run_dir=run_dir,
    )
    elapsed = time.time() - start
    summary = summarize_events(
        cfg.worktree.resolve(),
        scenario.expected_done,
        stuck_incident_required=cfg.inject_worker_stuck,
    )
    tasks_done = int(summary["tasks_done"])
    fatal = summary.get("fatal_event")
    metrics = summary.get("derived_metrics") or {}
    stuck_injection_failed = bool(
        cfg.inject_worker_stuck
        and not metrics.get("stuck_injection_satisfied")
    )
    if (
        proc.returncode == 0
        and tasks_done >= scenario.expected_done
        and fatal is None
        and not stuck_injection_failed
    ):
        status = "passed"
    elif fatal is not None:
        status = "fatal"
    elif stuck_injection_failed:
        status = "failed"
    elif tasks_done < scenario.expected_done:
        status = "incomplete"
    else:
        status = "failed"

    row = {
        "run_id": run_id,
        "scenario": scenario.name,
        "status": status,
        "worktree": str(cfg.worktree.resolve()),
        "tasks_done": tasks_done,
        "expected_done": scenario.expected_done,
        "returncode": proc.returncode,
        "elapsed_seconds": elapsed,
        "summary": summary,
        "fatal_event": fatal,
        "log_path": str(log_path),
    }
    review_gate = _maybe_prepare_review_gate(
        mode=review_gate_mode,
        run_status=status,
        run_dir=run_dir,
        worktree=cfg.worktree.resolve(),
    )
    if review_gate is not None:
        row["review_gate"] = review_gate
        summary["review_gate"] = review_gate
    events_summary = run_dir / "events-summary.json"
    events_summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    report_path = write_report(run_dir, row)
    row["report_path"] = str(report_path)
    write_iterations(run_dir, row)
    task_id = ""
    if cfg.backlog_on_failure and status != "passed":
        task_id = upsert_failure_backlog(cfg=cfg, scenario=scenario, row=row)
    return AutoresearchRunResult(
        status=status,
        run_id=run_id,
        worktree=cfg.worktree.resolve(),
        run_dir=run_dir,
        report_path=report_path,
        tasks_done=tasks_done,
        expected_done=scenario.expected_done,
        returncode=proc.returncode,
        fatal_event=fatal if isinstance(fatal, dict) else None,
        backlog_task_id=task_id,
        elapsed_seconds=elapsed,
        review_gate=review_gate,
    )


def tmux_supervisor_command(
    argv: list[str],
    *,
    worktree: Path,
    session: str,
) -> list[list[str]]:
    marker_env = "ZF_AUTORESEARCH_IN_TMUX=1"
    pythonpath_env = f"PYTHONPATH={shlex.quote(str(repo_root() / 'src'))}"
    quoted = shlex.join(argv + ["--no-tmux"])
    supervisor = (
        f"cd {shlex.quote(str(repo_root()))} && "
        f"{marker_env} {pythonpath_env} {quoted}"
    )
    events = (
        f"while [ ! -f {shlex.quote(str(worktree / '.zf' / 'events.jsonl'))} ]; "
        "do sleep 2; done; "
        f"tail -f {shlex.quote(str(worktree / '.zf' / 'events.jsonl'))}"
    )
    status = (
        f"while [ ! -d {shlex.quote(str(worktree))} ]; do sleep 2; done; "
        f"cd {shlex.quote(str(worktree))}; "
        "while true; do clear; "
        "PYTHONPATH="
        f"{shlex.quote(str(repo_root() / 'src'))} "
        "python3 -m zf.cli.main status --workers 2>/dev/null || true; "
        "sleep 10; done"
    )
    return [
        [
            "tmux", "new-session", "-d", "-s", session,
            "-n", "supervisor", "bash", "-lc", supervisor,
        ],
        [
            "tmux", "new-window", "-t", session,
            "-n", "events", "bash", "-lc", events,
        ],
        [
            "tmux", "new-window", "-t", session,
            "-n", "status", "bash", "-lc", status,
        ],
    ]


def start_tmux_supervisor(
    argv: list[str],
    *,
    worktree: Path,
    session: str,
) -> None:
    for cmd in tmux_supervisor_command(argv, worktree=worktree, session=session):
        subprocess.run(cmd, check=True)
