"""zf start — start the harness loop."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shlex
import sys
from dataclasses import asdict
from pathlib import Path

from zf.core.config.loader import load_config, ConfigError
from zf.core.config.lkg import (
    infer_state_dir,
    lkg_hint,
    promote_last_known_good,
    write_validation_report,
)
from zf.core.config.tool_closure import validate_tool_closure
from zf.core.events import ZfEvent
from zf.core.events.factory import (
    EventSigningConfigError,
    build_event_signer,
    event_log_from_project,
)
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore
from zf.core.skills import validate_skill_sources
from zf.core.security.hash import sha256_file
from zf.core.workflow.inspection import (
    build_workflow_inspection_report,
    inspection_failed,
)
from zf.core.workflow.inspection_render import (
    write_workflow_inspection_artifacts,
)
from zf.runtime.transport import make_transport, TmuxTransport
from zf.runtime.tmux import TmuxError
from zf.runtime.backend import get_adapter
from zf.runtime.codex_hooks import write_codex_hook_settings
from zf.runtime.injection import generate_role_instructions
from zf.runtime.cli_command import set_default_zf_cli_cmd, zf_cli_cmd
from zf.runtime.process_guard import SingleOwnerProcessGuard
from zf.runtime.spawn_coordinator import SpawnCoordinator


def _run_autoresearch_trigger_scan(
    state_dir: Path,
    config: object,
    *,
    event_writer=None,
) -> int:
    from zf.runtime.tick_services import run_autoresearch_trigger_scan

    return run_autoresearch_trigger_scan(
        state_dir,
        config,
        event_writer=event_writer,
    )


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("start", help="Start the harness loop")
    parser.add_argument("--dry-run", action="store_true", help="Record commands without executing tmux")
    parser.add_argument(
        "--foreground", action="store_true",
        help="DEPRECATED no-op alias (watcher now runs in foreground by default)",
    )
    parser.add_argument(
        "--no-watch", action="store_true",
        help="Spawn workers and exit without running the event watcher",
    )
    parser.add_argument(
        "--skip-workflow-inspect",
        action="store_true",
        help="Skip workflow graph/skill preflight inspection",
    )
    parser.set_defaults(func=run)


def _preserved_run_manager_start_context(
    config,
    state_dir: Path,
) -> tuple[dict[str, object] | None, set[str]]:
    try:
        from zf.runtime.run_manager_resident import (
            clear_resident_preserve_marker,
            dedicated_resident_run_manager_role,
            read_resident_preserve_marker,
            resident_run_manager_tmux_session,
        )

        marker = read_resident_preserve_marker(state_dir)
        role = dedicated_resident_run_manager_role(config)
        if marker is None or role is None:
            if marker is not None:
                clear_resident_preserve_marker(state_dir)
            return None, set()
        marker_instance = str(marker.get("instance_id") or "").strip()
        marker_session = str(marker.get("tmux_session") or "").strip()
        if marker_instance != role.instance_id:
            clear_resident_preserve_marker(state_dir)
            return None, set()
        if marker_session and marker_session != resident_run_manager_tmux_session(config):
            clear_resident_preserve_marker(state_dir)
            return None, set()
        return marker, {role.instance_id}
    except Exception:
        return None, set()


def _write_claude_hook_settings(state_dir: Path) -> None:
    """Render the Claude hooks consumed through ``claude --settings``.

    Pre/PostToolUse provide activity and write-scope enforcement. Stop emits
    the round-complete signal after the deterministic stop guard passes.

    All hook invocations pipe the hook JSON through stdin to
    `zf hook-recv`, which resolves session_id → instance_id via
    RoleSessionRegistry and appends a ZfEvent with the correct actor.
    """
    import json as _json
    hooks_dir = state_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    state_dir_arg = shlex.quote(str(state_dir))
    settings = {
        "hooks": {
            "PreToolUse": [{
                "matcher": "*",
                "hooks": [{
                    "type": "command",
                    "command": (
                        f"{zf_cli_cmd()} hook-recv --event claude.hook.pre_tool_use "
                        f"--backend claude-code --state-dir {state_dir_arg}"
                    ),
                }],
            }],
            "PostToolUse": [{
                "matcher": "*",
                "hooks": [{
                    "type": "command",
                    "command": (
                        f"{zf_cli_cmd()} hook-recv --event claude.hook.post_tool_use "
                        f"--backend claude-code --state-dir {state_dir_arg}"
                    ),
                }],
            }],
            # ZF-PWF-STOP-GUARD-001 integration (2026-05-18):
            # provider.stop.check runs BEFORE the existing
            # orchestrator.round.complete emit so a worker stopping
            # without satisfied gates gets exit-code-2 + advice via
            # _evaluate_stop_guard. The second Stop hook entry emits
            # the existing round-complete signal.
            "Stop": [{
                "matcher": "",
                "hooks": [{
                    "type": "command",
                    "command": (
                        f"{zf_cli_cmd()} hook-recv --event provider.stop.check "
                        f"--state-dir {state_dir_arg}"
                    ),
                }, {
                    "type": "command",
                    "command": (
                        f"{zf_cli_cmd()} hook-recv --event orchestrator.round.complete "
                        f"--state-dir {state_dir_arg}"
                    ),
                }],
            }],
            # ZF-PWF-PRECOMPACT-001 (doc 41 §4.4): Claude Code 2026-05
            # added a PreCompact hook fired just before context
            # compaction (manual /compact or autoCompact). Hooking it
            # lets the kernel emit worker.context.precompact and rebuild
            # State Packet / projections before chat history is lost.
            # Hook must exit 0 — never block compaction.
            "PreCompact": [{
                "matcher": "*",
                "hooks": [{
                    "type": "command",
                    "command": (
                        f"{zf_cli_cmd()} hook-recv --event worker.context.precompact "
                        f"--state-dir {state_dir_arg}"
                    ),
                }],
            }],
        },
    }
    settings_path = hooks_dir / "settings.json"
    settings_path.write_text(_json.dumps(settings, indent=2))


def _write_codex_hook_settings(
    state_dir: Path,
    *,
    project_root: Path | None = None,
) -> None:
    """1202-T1: render <project>/.codex/hooks.json for Codex's hook engine.

    Codex's hook engine discovers
    hooks.json under the codex config dir. We write to the project-local
    .codex/ directory so the config is scoped to this project and does
    not leak into unrelated codex invocations.

    Parallels `_write_claude_hook_settings`: five events (SessionStart /
    UserPromptSubmit / PreToolUse / PostToolUse / Stop) each fire
    `zf hook-recv --event codex.hook.<kind> --backend codex` so the
    payload routes through the same bridge as Claude hooks but under a
    distinct event namespace the reactor can dispatch separately.
    """
    write_codex_hook_settings(state_dir, project_root=project_root)


def _build_event_signer(config) -> object | None:
    """Compatibility wrapper; signer construction lives in core/events."""
    return build_event_signer(config)


def _acquire_lock(lock_path: Path) -> object | None:
    """Acquire advisory file lock. Returns file handle or None."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.write(str(os.getpid()))
        fh.flush()
        return fh
    except OSError:
        fh.close()
        return None


def _record_ready_worker_state(
    *,
    event_log,
    registry: RoleSessionRegistry,
    instance_id: str,
) -> None:
    """Clear stale non-dispatchable projections after a pane is ready."""
    if not instance_id:
        return
    _last_at, last_payload = registry.get_last_heartbeat(instance_id)
    previous = ""
    if isinstance(last_payload, dict):
        previous = str(last_payload.get("state") or "")
    if previous and previous not in {
        "respawning",
        "blocked_human",
        "pending_recycle",
        "recycling",
        "stuck",
    }:
        return
    reason = "worker pane ready on zf start"
    event = ZfEvent(
        type="worker.state.changed",
        actor=instance_id,
        payload={
            "instance_id": instance_id,
            "from": previous,
            "to": "idle",
            "reason": reason,
        },
    )
    event_log.append(event)
    registry.record_heartbeat(instance_id, {
        "instance_id": instance_id,
        "state": "idle",
        "current_task_id": "",
        "last_action_ts": event.ts,
        "source": "worker.state.changed",
        "reason": reason,
    })


def _run_workflow_start_preflight(
    *,
    config,
    project_root: Path,
    state_dir: Path,
    config_path: Path,
) -> bool:
    report = build_workflow_inspection_report(
        config,
        project_root=project_root,
        state_dir=state_dir,
    )
    try:
        from zf.core.config.render import _classify_expected_event_sinks
        from zf.core.workflow.inspection import (
            _diagnostic_counts,
            _status_from_diagnostics,
        )

        report = dict(report)
        classified = _classify_expected_event_sinks(
            config,
            list(report.get("diagnostics", []) or []),
        )
        report["diagnostics"] = classified
        # 分诊会把 expected-source 项降级为 INFO;status/summary 必须随
        # 之重算,否则门按分诊前的 STOP 拦且报错列表为空(prod-e2e 实弹
        # 发现:controller v3 的 post-verify discovery 桥接触发器全被
        # 误拦)。
        report["status"] = _status_from_diagnostics(classified)
        summary = dict(report.get("summary") or {})
        summary["diagnostics"] = _diagnostic_counts(classified)
        report["summary"] = summary
    except Exception:
        pass
    artifact_refs = write_workflow_inspection_artifacts(
        report,
        state_dir=state_dir,
    )
    diagnostics = list(report.get("diagnostics", []) or [])
    if inspection_failed(report):
        messages = [
            f"{item.get('severity', 'INFO')} {item.get('kind', '')}: "
            f"{item.get('message', '')}"
            for item in diagnostics
            if item.get("severity") in {"STOP", "WARN"}
        ]
        write_validation_report(
            state_dir=state_dir,
            config_path=config_path,
            status="invalid",
            errors=messages[:20],
        )
        print("Error: workflow preflight failed:", file=sys.stderr)
        for message in messages[:8]:
            print(f"  - {message}", file=sys.stderr)
        if len(messages) > 8:
            print(f"  - ... {len(messages) - 8} more", file=sys.stderr)
        print(
            f"Workflow inspect artifacts: {artifact_refs.get('json', '')}",
            file=sys.stderr,
        )
        print("Run `zf workflow inspect` for the full report.", file=sys.stderr)
        hint = lkg_hint(config_path)
        if hint:
            print(hint, file=sys.stderr)
        return False
    warnings = [
        item for item in diagnostics
        if item.get("severity") == "WARN"
    ]
    if warnings:
        print("Workflow preflight warnings:", file=sys.stderr)
        for item in warnings[:8]:
            print(
                f"  - {item.get('kind', '')}: {item.get('message', '')}",
                file=sys.stderr,
            )
        if len(warnings) > 8:
            print(f"  - ... {len(warnings) - 8} more", file=sys.stderr)
    return True


def _emit_render_lock_drift_warning(
    *,
    state_dir: Path,
    config_path: Path,
    event_log,
) -> None:
    lock_path = state_dir / "config" / "render-lock.json"
    if not lock_path.exists():
        return
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        event_log.append(ZfEvent(
            type="config.render_lock.drift_detected",
            actor="zf-cli",
            payload={
                "severity": "WARN",
                "reason": "render lock is unreadable",
                "render_lock_ref": str(lock_path),
            },
        ))
        return
    source = lock.get("input") if isinstance(lock, dict) else {}
    if not isinstance(source, dict):
        source = {}
    locked_path = str(source.get("path") or "")
    locked_sha = str(source.get("sha256") or "")
    current_sha = sha256_file(config_path)
    current_path = str(config_path)
    drift_reasons: list[str] = []
    if locked_path and Path(locked_path).expanduser().resolve(strict=False) != config_path.resolve(strict=False):
        drift_reasons.append("config path changed")
    if locked_sha and locked_sha != current_sha:
        drift_reasons.append("config sha256 changed")
    if not drift_reasons:
        return
    event_log.append(ZfEvent(
        type="config.render_lock.drift_detected",
        actor="zf-cli",
        payload={
            "severity": "WARN",
            "reason": "; ".join(drift_reasons),
            "render_lock_ref": str(lock_path),
            "locked_config_ref": locked_path,
            "locked_sha256": locked_sha,
            "current_config_ref": current_path,
            "current_sha256": current_sha,
            "next_action": "run `zf config render --config zf.yaml` to refresh render-lock",
        },
    ))


def _write_run_contract_snapshot(
    *,
    config,
    project_root: Path,
    state_dir: Path,
    config_path: Path,
    event_log,
) -> bool:
    from zf.runtime.run_contract import (
        build_run_contract,
        load_run_contract,
        run_contract_drift_diagnostics,
        strict_run_contract_drift,
        write_run_contract,
    )

    metadata = dict(getattr(getattr(config, "workflow", None), "flow_metadata", {}) or {})
    strict = str(metadata.get("strictness") or "").strip().lower() in {
        "strict",
        "full-parity",
        "full_parity",
        "release",
        "release_candidate",
    }
    previous = load_run_contract(state_dir)
    previous_refs = (
        previous.get("refs")
        if isinstance(previous, dict) and isinstance(previous.get("refs"), dict)
        else {}
    )
    previous_manifest_refs = previous_refs.get("workflow_input_manifest", [])
    previous_manifest_ref = (
        str(previous_manifest_refs[0] or "")
        if isinstance(previous_manifest_refs, list) and previous_manifest_refs
        else ""
    )
    contract = build_run_contract(
        config,
        config_path=config_path,
        project_root=project_root,
        state_dir=state_dir,
        workflow_input_manifest_ref=previous_manifest_ref,
    )
    strict = strict_run_contract_drift(previous, contract, strict=strict)
    diagnostics = run_contract_drift_diagnostics(previous, contract, strict=strict)
    if diagnostics:
        event_log.append(ZfEvent(
            type="config.run_contract.drift_detected",
            actor="zf-cli",
            payload={
                "severity": "STOP" if strict else "WARN",
                "diagnostics": diagnostics,
                "run_contract_ref": str(state_dir / "config" / "run-contract.json"),
                "next_action": "review config/input drift before resuming this run",
            },
        ))
        if strict:
            return False
    path = write_run_contract(state_dir, contract)
    event_log.append(ZfEvent(
        type="config.run_contract.written",
        actor="zf-cli",
        payload={
            "run_contract_ref": str(path),
            "contract_digest": str(contract.get("contract_digest") or ""),
        },
    ))
    return True


def _run_orchestrator_idle_tick(orchestrator) -> None:
    """Run a periodic orchestrator tick and catch up persisted events.

    Passing ``events=[]`` would force pushed-event mode and advance
    ``session.latest_event_offset`` to the end of ``events.jsonl`` without
    processing events written before the watcher started. The idle tick must
    instead let the orchestrator read from its durable offset.
    """
    orchestrator.run_once()


def _run_startup_orchestrator_catchup(orchestrator, event_log) -> None:
    """Catch up durable events before the watcher starts at EOF.

    ``EventWatcher`` intentionally initializes from the current file end to
    avoid replaying the whole event log. That makes the periodic idle tick
    responsible for reading from ``session.latest_event_offset``. Run the same
    durable-offset path once during startup so events written while a prior
    watcher was down are consumed immediately instead of waiting for a later
    tick or a manual recovery.
    """
    try:
        _run_orchestrator_idle_tick(orchestrator)
    except Exception as exc:
        try:
            event_log.append(ZfEvent(
                type="orchestrator.tick.failed",
                actor="zf-cli",
                payload={
                    "phase": "startup_catchup",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:400],
                },
            ))
        except Exception:
            pass


def _maybe_run_startup_orchestrator_catchup(
    orchestrator,
    event_log,
    *,
    dry_run: bool,
    foreground: bool,
) -> bool:
    """Run startup catch-up only when a real watcher is about to own the loop.

    Dry-run and ``--no-watch`` are diagnostic/spawn-only modes. They must not
    advance durable orchestration state or emit recovery events, otherwise a
    validation command can unexpectedly mutate an active run.
    """
    if dry_run or not foreground:
        return False
    _run_startup_orchestrator_catchup(orchestrator, event_log)
    return True


def _release_lock(fh: object, lock_path: Path) -> None:
    """Release advisory file lock."""
    try:
        fcntl.flock(fh, fcntl.LOCK_UN)  # type: ignore[arg-type]
        fh.close()  # type: ignore[union-attr]
    except Exception:
        pass
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass


def run(args: argparse.Namespace) -> int:
    project_root = Path.cwd()
    # doc 78 O-7 fix: load the project .env so the watcher (and the O-7
    # owner.visible_message auto-delivery in the tick) get FEISHU creds +
    # ZF_OWNER_VISIBLE_CHAT regardless of the launch shell. `zf web` / `zf feishu`
    # already do this; `zf start` did not, so supervisor alerts never delivered
    # (R8 stall: 29 requested, 0 delivered). Non-overriding.
    from zf.core.config.project_context import load_project_env
    load_project_env(project_root)
    config_path = project_root / "zf.yaml"
    dry_run = getattr(args, "dry_run", False)
    if not dry_run:
        set_default_zf_cli_cmd()
    no_watch = getattr(args, "no_watch", False)
    skip_workflow_inspect = getattr(args, "skip_workflow_inspect", False)
    legacy_foreground = getattr(args, "foreground", False)
    if legacy_foreground:
        print(
            "warning: --foreground is deprecated (watcher now runs in foreground by default); "
            "use --no-watch to opt out",
            file=sys.stderr,
        )
    # Watcher runs in foreground unless explicitly opted out via --no-watch.
    foreground = not no_watch

    # 1. Preflight: check zf.yaml exists
    if not config_path.exists():
        print("Error: zf.yaml not found. To fix: run 'zf init'", file=sys.stderr)
        return 1

    # 2. Load config
    try:
        config = load_config(config_path)
    except ConfigError as e:
        write_validation_report(
            state_dir=infer_state_dir(config_path),
            config_path=config_path,
            status="invalid",
            errors=[str(e)],
        )
        print(f"Error: {e}", file=sys.stderr)
        hint = lkg_hint(config_path)
        if hint:
            print(hint, file=sys.stderr)
        return 1
    if config.safety.tool_closure_enabled:
        closure_errors = validate_tool_closure(config)
        if closure_errors:
            write_validation_report(
                state_dir=project_root / config.project.state_dir,
                config_path=config_path,
                status="invalid",
                errors=closure_errors,
            )
            print("Error: tool closure validation failed:", file=sys.stderr)
            for error in closure_errors:
                print(f"  - {error}", file=sys.stderr)
            hint = lkg_hint(config_path)
            if hint:
                print(hint, file=sys.stderr)
            return 1

    # ⑤c(2026-07-08):多 lane fanout_writer 无 quality_gates(且未显式
    # 豁免)不予 start——candidate 合成树不经验证即进 judge 的洞,validate
    # 的 WARN 连打三轮无人理(LB-3 教训同型),升 fail-closed。
    from zf.core.config.candidate_gate import combined_candidate_gate_gap

    candidate_gate_gap = combined_candidate_gate_gap(config)
    if candidate_gate_gap:
        print(
            "Error: combined candidate gate check failed:", file=sys.stderr,
        )
        print(f"  - {candidate_gate_gap}", file=sys.stderr)
        return 1

    # 3. Check .zf/ initialized
    state_dir = project_root / config.project.state_dir
    if not state_dir.exists():
        print(f"Error: {state_dir} not found. To fix: run 'zf init'", file=sys.stderr)
        return 1
    session_path = state_dir / "session.yaml"
    if not session_path.exists():
        print(
            (
                f"Error: {session_path} not found. "
                f"To fix: run 'zf init --state-dir {config.project.state_dir}' "
                "before 'zf start'"
            ),
            file=sys.stderr,
        )
        return 1

    skill_warnings = validate_skill_sources(
        config=config,
        project_root=project_root,
    )
    if skill_warnings:
        if config.runtime.skills.strict:
            write_validation_report(
                state_dir=state_dir,
                config_path=config_path,
                status="invalid",
                errors=skill_warnings,
            )
            print("Error: skill validation failed:", file=sys.stderr)
            for warning in skill_warnings:
                print(f"  - {warning}", file=sys.stderr)
            hint = lkg_hint(config_path)
            if hint:
                print(hint, file=sys.stderr)
            return 1
        print("Skill validation warnings:", file=sys.stderr)
        for warning in skill_warnings:
            print(f"  - {warning}", file=sys.stderr)
    if not skip_workflow_inspect:
        if not _run_workflow_start_preflight(
            config=config,
            project_root=project_root,
            state_dir=state_dir,
            config_path=config_path,
        ):
            return 1
    # P0-5(审计 D1/D2):static dispatch preflight 此前只有手动
    # `zf preflight`,signature drift 带病启动直至真实派发才炸。挂进
    # start 门链,FAIL 拒启(--skip-workflow-inspect 同旗跳过)。
    if not skip_workflow_inspect:
        from zf.runtime.preflight import preflight_ok, run_preflight_checks

        preflight_results = run_preflight_checks(config)
        if not preflight_ok(preflight_results):
            print("Static dispatch preflight FAILED:", file=sys.stderr)
            for result in preflight_results:
                if not result.ok:
                    print(f"  - {result.name}: {result.detail}", file=sys.stderr)
            print(
                "  To fix: run `zf preflight` for details, or start with "
                "--skip-workflow-inspect to bypass (not recommended).",
                file=sys.stderr,
            )
            return 1
    # P0-6(审计 D12,裁决 B):环境层 preflight。硬项(hook 命令/tmux)
    # 拒启;软项(workdir 属主/浏览器依赖)WARN + emit env.preflight.failed
    # ——环境故障从此有事件身份,不再伪装成调度故障。
    if not skip_workflow_inspect:
        from zf.runtime.env_preflight import run_env_preflight

        env_checks = run_env_preflight(
            zf_cmd=zf_cli_cmd(),
            state_dir=state_dir,
            project_root=project_root,
        )
        hard_failures = [c for c in env_checks if not c.ok and c.hard]
        soft_failures = [c for c in env_checks if not c.ok and not c.hard]
        if hard_failures:
            print("Environment preflight FAILED:", file=sys.stderr)
            for check in hard_failures:
                print(f"  - {check.name}: {check.detail}", file=sys.stderr)
            return 1
        for check in soft_failures:
            print(
                f"Environment preflight WARN - {check.name}: {check.detail}",
                file=sys.stderr,
            )
            try:
                event_log_from_project(state_dir, config=config).append(ZfEvent(
                    type="env.preflight.failed",
                    actor="zf-cli",
                    payload={
                        "check": check.name,
                        "detail": check.detail,
                        "hard": False,
                        "problem_class": "environment",
                    },
                ))
            except Exception:
                pass
    promote_last_known_good(
        config_path=config_path,
        state_dir=state_dir,
        warnings=skill_warnings,
    )

    # 4. Acquire lock
    lock_path = state_dir / "loop.lock"
    lock_fh = _acquire_lock(lock_path)
    if lock_fh is None:
        print("Error: Another harness is running (lock held). To fix: run 'zf stop' first", file=sys.stderr)
        return 1
    watcher_guard = SingleOwnerProcessGuard(
        state_dir / "processes" / "watcher.pid.json",
        component="watcher",
    )
    watcher_guard_result = watcher_guard.acquire()
    if not watcher_guard_result.acquired:
        _release_lock(lock_fh, lock_path)
        print(
            (
                "Error: Another watcher owner is alive "
                f"(pid {watcher_guard_result.owner_pid}). "
                "To fix: run 'zf stop' first"
            ),
            file=sys.stderr,
        )
        return 1

    event_log = None
    feishu_inbound_sidecar = None
    autoresearch_resident_sidecar = None
    try:
        # 5. Set up event log and session
        try:
            event_log = event_log_from_project(state_dir, config=config)
        except EventSigningConfigError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        _emit_render_lock_drift_warning(
            state_dir=state_dir,
            config_path=config_path,
            event_log=event_log,
        )
        if not _write_run_contract_snapshot(
            config=config,
            project_root=project_root,
            state_dir=state_dir,
            config_path=config_path,
            event_log=event_log,
        ):
            print(
                "Error: run contract drift detected for a strict run. "
                "Review config/input drift before restart or resume.",
                file=sys.stderr,
            )
            return 1
        session_store = SessionStore(state_dir / "session.yaml")
        session_name = config.session.tmux_session

        # Phase 1: generate Claude hook settings (Stop → orchestrator.round.complete).
        _write_claude_hook_settings(state_dir)

        # 1202-T1: if any role uses backend=codex, render the Codex-side
        # hooks.json so its ClaudeHooksEngine bridges into hook_recv too.
        if any(r.backend == "codex" for r in config.roles):
            _write_codex_hook_settings(state_dir, project_root=project_root)

        preserved_run_manager_marker, preserved_run_manager_roles = (
            _preserved_run_manager_start_context(config, state_dir)
        )

        # 6. Initialize transport (creates tmux session today)
        transport = make_transport(config, dry_run=dry_run)
        transport.init(exclude_roles=preserved_run_manager_roles)

        # 7. Spawn workers via SpawnCoordinator: allocate pane/process,
        #    launch agent CLI with --session-id / exec resume as needed,
        #    write instructions.
        registry = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(project_root),
        )
        coordinator = SpawnCoordinator(
            state_dir=state_dir,
            registry=registry,
            transport=transport,
            project_root=str(project_root),
            event_log=event_log,
            config=config,
        )
        # Phase 2/3: session-jsonl tailers for claude and codex roles.
        # Both backends write their message stream to a local jsonl
        # file; the tailer reads it and re-emits each line as an
        # agent.* event to zaofu's events.jsonl, giving kernel-side
        # telemetry without any SDK call.
        from zf.runtime.session_tailer import (
            ClaudeSessionTailer,
            CodexSessionTailer,
            claude_session_path,
            codex_session_path,
        )
        claude_tailer = ClaudeSessionTailer(event_log)
        codex_tailer = CodexSessionTailer(event_log)
        workdir_manager = None
        if config.runtime.workdirs.enabled:
            from zf.runtime.workdirs import WorkdirManager
            workdir_manager = WorkdirManager(
                state_dir=state_dir,
                project_root=project_root,
                config=config,
            )
        instructions_dir = state_dir / "instructions"
        for role in config.roles:
            skip_spawn = role.name == "orchestrator" and role.transport == "stream-json"
            spawn_cwd: Path | None = None
            if workdir_manager is not None and not skip_spawn:
                plan = workdir_manager.prepare(role)
                event_log.append(ZfEvent(
                    type="workdir.prepared",
                    actor="zf-cli",
                    payload=asdict(plan),
                ))
                project_path = Path(plan.project_path)
                if (
                    plan.enabled
                    and plan.mode == "worktree"
                    and plan.role_kind in {"writer", "reader"}
                    and project_path.exists()
                ):
                    spawn_cwd = project_path

            skill_entries = []
            if role.skills:
                from zf.core.skills import (
                    build_skill_lock_entries,
                    materialize_role_skills,
                    upsert_skills_lockfile,
                )

                materialized = materialize_role_skills(
                    config=config,
                    project_root=project_root,
                    state_dir=state_dir,
                    role=role,
                )
                materialized_paths = (
                    materialized.materialized_paths_under(project_root)
                    if materialized is not None else {}
                )
                skill_entries = build_skill_lock_entries(
                    project_root=project_root,
                    state_dir=state_dir,
                    role=role,
                    config=config,
                    materialized_paths=materialized_paths,
                )
                upsert_skills_lockfile(state_dir=state_dir, entries=skill_entries)
                if materialized is not None:
                    event_log.append(ZfEvent(
                        type="skills.materialized",
                        actor="zf-cli",
                        payload=materialized.to_payload(),
                    ))

            # stream-json orchestrator spawns lazily (SDK invoked per
            # dispatch); tmux orchestrator needs an upfront pane like
            # the workers so send-keys has somewhere to land.
            if skip_spawn:
                continue
            coordinator.spawn(role, cwd=spawn_cwd)

            if not dry_run:
                adapter = get_adapter(role.backend)
                ready = (
                    transport.wait_ready(
                        role.instance_id, adapter.ready_pattern, timeout=30.0,
                    )
                    if adapter.requires_ready_wait
                    else True
                )
                if ready:
                    # B-1203-06 R-1: stabilization wait for TUIs (codex)
                    # whose ready_pattern fires before stdin is actually
                    # live. Zero for claude / mock.
                    delay = adapter.post_ready_delay_s
                    if delay > 0:
                        import time as _time
                        _time.sleep(delay)
                    _record_ready_worker_state(
                        event_log=event_log,
                        registry=registry,
                        instance_id=role.instance_id,
                    )
                    print(f"    {role.instance_id}: ready")
                else:
                    print(f"    {role.instance_id}: timeout waiting for ready (continuing)")

            # Write role instructions to .zf/instructions/ for reference
            instructions = generate_role_instructions(
                config,
                role,
                skill_entries=skill_entries,
                state_dir_ref=state_dir,
                project_root=project_root,
            )
            instructions_dir.mkdir(parents=True, exist_ok=True)
            (instructions_dir / f"{role.instance_id}.md").write_text(instructions)

            # Phase 2: start tailing claude session jsonl.
            # Phase 3: same for codex rollout jsonl.
            if role.backend == "claude-code":
                uuid = registry.get(role.instance_id)
                if uuid is not None:
                    path = claude_session_path(str(project_root), str(uuid))
                    claude_tailer.tail(role.instance_id, path)
            elif role.backend == "codex":
                uuid = registry.get(role.instance_id)
                if uuid is not None:
                    cpath = codex_session_path(str(uuid))
                    if cpath is not None:
                        codex_tailer.tail(role.instance_id, cpath)
                    # If path not yet discovered (codex creates file on
                    # first turn), rely on SpawnCoordinator.notify_
                    # first_dispatch → observe_codex_session to resolve
                    # it; tailer can be re-attached lazily if needed.

        from zf.runtime.run_manager_resident import (
            clear_resident_preserve_marker,
            dedicated_resident_run_manager_role,
            rebind_preserved_resident_run_manager,
            spawn_resident_run_manager,
        )

        resident_role = None
        if preserved_run_manager_marker:
            resident_role = rebind_preserved_resident_run_manager(
                config=config,
                state_dir=state_dir,
                project_root=project_root,
                transport=transport,
                event_log=event_log,
                instructions_dir=instructions_dir,
                marker_payload=preserved_run_manager_marker,
                dry_run=dry_run,
            )
            if resident_role is not None:
                clear_resident_preserve_marker(state_dir)
            else:
                fallback_role = dedicated_resident_run_manager_role(config)
                if fallback_role is not None:
                    try:
                        transport.for_role(fallback_role.instance_id).init()
                    except Exception:
                        pass
                clear_resident_preserve_marker(state_dir)

        if resident_role is None:
            resident_role = spawn_resident_run_manager(
                config=config,
                state_dir=state_dir,
                project_root=project_root,
                coordinator=coordinator,
                transport=transport,
                event_log=event_log,
                instructions_dir=instructions_dir,
                dry_run=dry_run,
            )
        if resident_role is not None and resident_role.backend == "claude-code":
            uuid = registry.get(resident_role.instance_id)
            if uuid is not None:
                path = claude_session_path(str(project_root), str(uuid))
                claude_tailer.tail(resident_role.instance_id, path)
        elif resident_role is not None and resident_role.backend == "codex":
            uuid = registry.get(resident_role.instance_id)
            if uuid is not None:
                cpath = codex_session_path(str(uuid))
                if cpath is not None:
                    codex_tailer.tail(resident_role.instance_id, cpath)

        try:
            from zf.runtime.briefing_hydration import (
                build_briefing_hydration_report,
                write_briefing_hydration_report,
            )

            hydration_report = build_briefing_hydration_report(
                state_dir,
                instructions_dir=instructions_dir,
            )
            hydration_ref = write_briefing_hydration_report(
                state_dir,
                hydration_report,
            )
            event_log.append(ZfEvent(
                type="briefing.hydration.checked",
                actor="zf-cli",
                payload={
                    "schema_version": "briefing-hydration.checked.v1",
                    "status": str(hydration_report.get("status") or ""),
                    "report_ref": str(hydration_ref),
                    "diagnostic_count": len(hydration_report.get("diagnostics") or []),
                },
            ))
        except Exception as exc:
            event_log.append(ZfEvent(
                type="briefing.hydration.failed",
                actor="zf-cli",
                payload={
                    "schema_version": "briefing-hydration.failed.v1",
                    "reason": "briefing_hydration_projection_failed",
                    "error": str(exc)[:400],
                },
            ))

        # 8. Emit session.started here; loop.started is emitted only when
        # the watcher actually starts running (foreground mode).
        #
        # B-1203-03: session.started was declared in known_types but
        # never emitted, so every phase report showed P0=not-reached.
        # Semantics: session.started = "tmux session / transport up";
        # loop.started = "reactor reading events". Splitting the two so
        # `--no-watch` does not falsely claim the reactor is running.
        event_log.append(ZfEvent(type="session.started", actor="zf-cli"))

        # 9. Update session state
        session_store.update(runtime_state="active")

        if foreground or dry_run:
            from zf.runtime.autoresearch_resident_sidecar import (
                start_autoresearch_resident_sidecar,
            )
            from zf.runtime.feishu_inbound_sidecar import (
                start_feishu_inbound_sidecar,
            )

            autoresearch_resident_sidecar = start_autoresearch_resident_sidecar(
                config=config,
                state_dir=state_dir,
                project_root=project_root,
                event_log=event_log,
                dry_run=dry_run,
            )
            feishu_inbound_sidecar = start_feishu_inbound_sidecar(
                config=config,
                state_dir=state_dir,
                project_root=project_root,
                event_log=event_log,
                dry_run=dry_run,
            )

        # 10. Set up watcher + orchestrator
        from zf.runtime.watcher import EventWatcher
        from zf.runtime.orchestrator import Orchestrator
        from zf.runtime.wake_patterns import (
            WakeRateLimiter,
            compute_effective_wake_patterns,
            rate_limits_for_config,
            wake_worthy,
        )

        # Wake patterns = base set + YAML-enabled extensions (P3).
        # `workflow.wake_extensions.{hooks,agent}.enabled` opt-in.
        wake_patterns = sorted(compute_effective_wake_patterns(config))
        rate_limiter = WakeRateLimiter(rate_limits_for_config(config))
        # ZF-E2E-RACING-P1: boot-side twin of the graceful-stop in-flight
        # cleanup. Start (re)spawns worker panes, so a dispatch that was in
        # flight before this process can never complete; requeue it unless
        # its current dispatch already has stage progress (pending-handoff
        # reconciliation owns those). Must run BEFORE the Orchestrator
        # constructor revives active_dispatch_ids from kanban.json.
        from zf.runtime.shutdown import requeue_stale_inflight_tasks

        try:
            requeue_stale_inflight_tasks(
                state_dir,
                event_log_from_project(state_dir, config=config),
                source="zf_start_boot_reconcile",
                reason=(
                    "zf start boot reconcile — worker sessions do not "
                    "survive restart; release in-flight WIP "
                    "(ZF-E2E-RACING-P1 2026-07-11)"
                ),
            )
        except Exception:
            pass
        orchestrator = Orchestrator(
            state_dir, config, transport, project_root=project_root,
        )

        def _on_event(line: str) -> None:
            # Parse the line and push the event object into run_once so the
            # orchestrator does not re-read events.jsonl from disk.
            try:
                from zf.core.events.model import ZfEvent
                event = ZfEvent.from_json(line)
            except Exception:
                return
            if not any(p == event.type for p in wake_patterns):
                return
            # avbs-r4 F3: 高频观察型 hook 不逐条唤醒(no-op handler /
            # 仅 deny 有意义)。事件照常在 events.jsonl,唤醒是纯成本。
            if not wake_worthy(event):
                return
            # P3: rate-limited extension events may be dropped (event
            # still persisted in events.jsonl — only wake is suppressed).
            if not rate_limiter.allow(event.type):
                return
            orchestrator.run_once(events=[event])

        # α-3 + β-1 periodic-sweep throttles: the EventWatcher tick fires
        # every ~5s but heartbeat/supervisor/autoresearch services have
        # separate intervals. The service runner is shared with diagnostic
        # watchers so patched monitors do not accidentally omit supervisor /
        # autoresearch / self-repair.
        import time as _periodic_time
        from zf.runtime.tick_services import (
            TickServiceIntervals,
            TickServiceState,
            run_standard_tick_services,
        )

        _tick_service_state = TickServiceState()
        _tick_service_intervals = TickServiceIntervals()

        def _on_tick() -> None:
            # A fully silent worker produces no events, so an event-only
            # watcher cannot observe stale pane output. This lightweight
            # maintenance tick drives stuck/orphan/recycle sweeps even when
            # events.jsonl is quiet.
            #
            # 2026-05-15 r-next backlog B-2: previous `except Exception: pass`
            # silently swallowed crashes in tick, making the watcher look
            # alive while no work was happening. Surface failures as
            # `orchestrator.tick.failed` events so operators can see them
            # in events.jsonl and the web dashboard.
            try:
                watcher_guard.heartbeat()
                _run_orchestrator_idle_tick(orchestrator)
            except Exception as exc:
                try:
                    event_log.append(ZfEvent(
                        type="orchestrator.tick.failed",
                        actor="zf-cli",
                        payload={
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:400],
                        },
                    ))
                except Exception:
                    # If even emitting the failure event fails, fall back to
                    # the silent behavior — keep the watcher alive.
                    pass

            now = _periodic_time.monotonic()
            run_standard_tick_services(
                orchestrator,
                state=_tick_service_state,
                now=now,
                intervals=_tick_service_intervals,
            )

        watcher = EventWatcher(
            state_dir / "events.jsonl",
            on_event=_on_event,
            on_tick=_on_tick,
            wake_patterns=wake_patterns,
            event_log=event_log,
            shutdown_marker=state_dir / "shutdown-requested",
        )
        _maybe_run_startup_orchestrator_catchup(
            orchestrator,
            event_log,
            dry_run=dry_run,
            foreground=foreground,
        )

        if dry_run:
            event_log.append(ZfEvent(type="loop.started", actor="zf-cli"))
            watcher.poll_once()
            print(f"Started harness (dry-run). Session: {session_name}")
            print(f"  Roles: {[r.name for r in config.roles]}")
            if isinstance(transport, TmuxTransport):
                print(f"  Commands recorded: {len(transport.tmux.command_log)}")
            print(f"  Watcher: configured with {len(wake_patterns)} wake patterns")
            transport.shutdown()
            return 0

        # Real mode
        print(f"Started harness. Session: {session_name}")
        print(f"  Roles: {[r.name for r in config.roles]}")
        for role in config.roles:
            print(f"    {role.name}: {get_adapter(role.backend).build_command(role)}")
        print(f"  Instructions: {state_dir / 'instructions'}/")
        print(f"  Attach: tmux attach -t {session_name}")
        print("  Stop:   zf stop")

        if foreground:
            print("  Watcher: running in foreground (Ctrl+C to stop)")
            event_log.append(ZfEvent(type="loop.started", actor="zf-cli"))
            try:
                watcher.run(poll_interval=1.0, tick_interval=5.0)
            except KeyboardInterrupt:
                print("\nStopping watcher...")
            finally:
                # Phase 2/3: stop tailer threads so we don't leak pollers.
                claude_tailer.stop()
                codex_tailer.stop()
        else:
            print("  Watcher: skipped (--no-watch). Re-run without --no-watch to drive the loop.")
            # --no-watch mode — tailer runs as daemon, dies with process.
            # Don't call stop() or it'll immediately cancel the threads
            # we just started.

    except TmuxError as exc:
        # ZF-E2E-PRDCTL-P2-7-6:boot spawn 竞态(capture-pane 撞 pane 消失)
        # 此前未捕获,异常带着半建的 tmux 会话与被持有的 loop.lock 冒泡
        # ——前台路径 finally 不释放锁,残余线程让进程僵活(deepwater
        # boot 僵尸持锁实证)。fail-closed:清会话、放锁、非零退出。
        print(f"Error: tmux boot failed: {exc}", file=sys.stderr)
        import subprocess as _subprocess
        session_name = str(getattr(config.session, "tmux_session", "") or "")
        for name in {session_name, f"{session_name}-run-manager"} - {""}:
            _subprocess.run(
                ["tmux", "kill-session", "-t", name],
                capture_output=True,
                check=False,
            )
        watcher_guard.release()
        _release_lock(lock_fh, lock_path)
        return 1
    finally:
        if autoresearch_resident_sidecar is not None:
            from zf.runtime.autoresearch_resident_sidecar import (
                stop_autoresearch_resident_sidecar,
            )

            stop_autoresearch_resident_sidecar(
                autoresearch_resident_sidecar,
                event_log=event_log,
            )
        if feishu_inbound_sidecar is not None:
            from zf.runtime.feishu_inbound_sidecar import (
                stop_feishu_inbound_sidecar,
            )

            stop_feishu_inbound_sidecar(
                feishu_inbound_sidecar,
                event_log=event_log,
            )
        if dry_run or not foreground:
            # Release lock when not blocking
            watcher_guard.release()
            _release_lock(lock_fh, lock_path)
        # If foreground, lock released on exit/Ctrl+C via finally

    return 0
