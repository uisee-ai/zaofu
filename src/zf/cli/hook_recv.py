"""zf hook-recv — bridge Claude Code hooks into zaofu events.jsonl.

Invoked by Stop / PreToolUse / PostToolUse hooks configured in
``.zf/hooks/settings.json``. Reads the hook JSON payload from stdin,
resolves the session_id back to the zaofu instance_id via
RoleSessionRegistry, and appends a ZfEvent.

LH-3 hardening:
  * **T2 — dead_letter + write_failed**: when EventLog.append raises
    (disk full / permission / corruption), the payload is persisted to
    .zf/hooks/dead_letter.jsonl. A `hook.write_failed` marker is queued
    in .zf/hooks/write_failed_queue.jsonl and flushed into events.jsonl
    by the next successful append so the orchestrator sees the outage.
  * **T3 — causation_id**: the new event inherits the id of the actor's
    most recent `task.dispatched` event so trace commands can follow
    the chain. Failure to resolve → emit `hook.orphan_event`.

Design notes:
  - Short-lived subprocess per hook invocation; keep deps small.
  - Failures must NEVER abort the hook (a hook exit != 0 blocks the
    Claude turn). All exceptions → log + exit 0.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.config.schema import ZfConfig
from zf.core.events.factory import EventSigningConfigError, event_log_from_project
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.provider_stop import classify_provider_stop


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "hook-recv",
        help=(
            "Bridge Claude Code hook → zaofu events.jsonl. "
            "Reads hook JSON from stdin."
        ),
    )
    parser.add_argument(
        "--event",
        required=True,
        help="Event type to emit (e.g. orchestrator.round.complete).",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Path to zaofu state dir. Default project.state_dir from zf.yaml, else .zf.",
    )
    parser.add_argument(
        "--backend",
        default="",
        choices=["", "claude-code", "codex"],
        help=(
            "Backend origin of this hook (claude-code / codex). "
            "1202-T1 reserves the flag; 1202-T2 uses it to select the "
            "payload-field extractor."
        ),
    )
    parser.set_defaults(func=run)


def _log_error(state_dir: Path, message: str) -> None:
    """Structured JSON log line to .zf/hooks/errors.log (never raises)."""
    try:
        logs = state_dir / "hooks"
        logs.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.time(), "message": message}
        with (logs / "errors.log").open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _dead_letter(state_dir: Path, event_type: str, payload: dict,
                 reason: str) -> None:
    """T2: persist the would-be event to dead_letter when append fails."""
    try:
        hooks_dir = state_dir / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        with (hooks_dir / "dead_letter.jsonl").open("a") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "event_type": event_type,
                "payload": payload,
                "reason": reason,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


_WRITE_FAILED_QUEUE = "write_failed_queue.jsonl"


def _queue_write_failed(state_dir: Path, reason: str) -> None:
    """T2: record a pending hook.write_failed to flush on next success."""
    try:
        hooks_dir = state_dir / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        with (hooks_dir / _WRITE_FAILED_QUEUE).open("a") as f:
            f.write(json.dumps(
                {"ts": time.time(), "reason": reason},
                ensure_ascii=False,
            ) + "\n")
    except Exception:
        pass


def _flush_write_failed_queue(state_dir: Path, event_writer: EventWriter) -> None:
    """T2: append queued hook.write_failed markers once writing works
    again. On re-failure the queue stays — will try again next hook."""
    q = state_dir / "hooks" / _WRITE_FAILED_QUEUE
    if not q.exists():
        return
    lines = [l for l in q.read_text().splitlines() if l.strip()]
    if not lines:
        try:
            q.unlink()
        except Exception:
            pass
        return
    survived: list[str] = []
    for line in lines:
        try:
            entry = json.loads(line)
            event_writer.append(ZfEvent(
                type="hook.write_failed",
                actor="zf-cli",
                payload={"reason": entry.get("reason", ""),
                         "queued_at": entry.get("ts", 0)},
            ))
        except Exception:
            survived.append(line)
    if survived:
        q.write_text("\n".join(survived) + "\n")
    else:
        try:
            q.unlink()
        except Exception:
            pass


_ORCHESTRATOR_ACTORS = frozenset({"unknown", "zf-cli", "orchestrator"})


def _should_check_causation(actor: str) -> bool:
    """Only worker / unresolved-worker hooks need task causation lookup.

    orchestrator / zf-cli / "unknown" hooks never carry a dispatched
    task context, so skip the scan (and skip orphan flagging) for them.
    ``unresolved:<prefix>`` does try — the actor was supposed to be a
    worker but the registry didn't match, which itself is useful orphan
    signal.
    """
    if not actor:
        return False
    if actor in _ORCHESTRATOR_ACTORS:
        return False
    return True


def _codex_actor_from_transcript_path(
    state_dir: Path,
    payload: dict,
) -> tuple[str, Path] | None:
    """Resolve Codex hook actor from role-local transcript_path.

    Codex hook payloads include the runtime ``transcript_path``. In ZaoFu that
    path should be under ``<state_dir>/workdirs/<instance>/codex-home``. This
    is stronger evidence than a registry lookup during early turns because the
    background Codex session observer may not have cached the UUID yet.
    """
    raw_path = payload.get("transcript_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    transcript_path = Path(raw_path)
    if not transcript_path.is_absolute():
        return None
    state_root = state_dir if state_dir.is_absolute() else Path.cwd() / state_dir
    try:
        relative = transcript_path.relative_to(state_root / "workdirs")
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) < 3 or parts[1] != "codex-home":
        return None
    instance_id = parts[0]
    if not instance_id or instance_id in {".", ".."}:
        return None
    return instance_id, transcript_path


def _resolve_hook_actor(
    *,
    state_dir: Path,
    project_root: Path,
    event_type: str,
    payload: dict,
    session_id: str,
) -> str:
    actor = "unknown"
    registry: RoleSessionRegistry | None = None
    if session_id:
        try:
            registry = RoleSessionRegistry(
                state_dir / "role_sessions.yaml",
                project_root=str(project_root),
            )
            resolved = registry.get_instance_by_uuid(session_id)
            actor = resolved or f"unresolved:{session_id[:8]}"
        except Exception as e:
            _log_error(state_dir, f"hook-recv: registry lookup failed: {e}")

    if event_type.startswith("codex.hook."):
        transcript_actor = _codex_actor_from_transcript_path(state_dir, payload)
        if transcript_actor is not None:
            actor, transcript_path = transcript_actor
            if session_id:
                try:
                    registry = registry or RoleSessionRegistry(
                        state_dir / "role_sessions.yaml",
                        project_root=str(project_root),
                    )
                    registry.bind_codex_session(
                        actor,
                        session_id,
                        session_path=transcript_path,
                    )
                except Exception as e:
                    _log_error(
                        state_dir,
                        f"hook-recv: codex hook session bind failed: {e}",
                    )

    return actor


def _resolve_causation(
    state_dir: Path, actor: str, event_log: EventLog,
) -> str | None:
    """T3: find the most recent active dispatch event for this actor
    and return its id as the new event's causation_id.

    Hot path under Codex: hundreds of hooks per worker turn each call
    this. Prefer the EventLog's in-process index when available so we
    don't replay the full log per hook. Fall back to a reverse scan
    only when the index has not observed the actor yet (cold start /
    external writer) or when the latest indexed dispatch is a fanout
    child whose terminal state must be checked against later events.
    """
    if not _should_check_causation(actor):
        return None
    index = getattr(event_log, "index", None)
    if index is not None:
        cached = index.latest_dispatch_event_for_actor(actor)
        if cached is not None:
            if cached.type == "task.dispatched":
                return cached.id
            if cached.type == "fanout.child.dispatched":
                try:
                    events = event_log.read_all()
                except Exception:
                    return cached.id
                if _fanout_dispatch_is_active(events, cached):
                    return cached.id
                return _latest_active_dispatch_for_actor(events, actor)
            if cached.type == "fanout.synth.dispatched":
                # B3 (R25 ISSUE-005): synth hooks resolved no causation
                # because this type was unknown here — every synth hook
                # went orphan and the synth looked dead for 40min.
                try:
                    events = event_log.read_all()
                except Exception:
                    return cached.id
                if _synth_dispatch_is_active(events, cached):
                    return cached.id
                return _latest_active_dispatch_for_actor(events, actor)
    try:
        return _latest_active_dispatch_for_actor(event_log.read_all(), actor)
    except Exception:
        return None
    return None


def _latest_active_dispatch_for_actor(
    events: list[ZfEvent],
    actor: str,
) -> str | None:
    terminal_fanout_children: set[tuple[str, str, str]] = set()
    terminal_synth_fanouts: set[str] = set()
    for event in reversed(events):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type in {"fanout.child.completed", "fanout.child.failed"}:
            terminal_fanout_children.update(_fanout_terminal_keys(payload))
            continue
        if event.type in {"fanout.synth.completed", "fanout.aggregate.completed"}:
            fanout_id = str(payload.get("fanout_id") or "")
            if fanout_id:
                terminal_synth_fanouts.add(fanout_id)
            continue
        if event.type == "fanout.timed_out":
            fanout_id = str(payload.get("fanout_id") or "")
            for child_id in payload.get("pending_children", []) or []:
                child_id = str(child_id or "")
                if fanout_id and child_id:
                    terminal_fanout_children.add((fanout_id, child_id, ""))
            continue
        if event.type == "fanout.child.dispatched":
            if str(payload.get("role_instance") or "") != actor:
                continue
            key = _fanout_key(payload)
            child_done = key in terminal_fanout_children
            child_done = child_done or (
                (key[0], key[1], "") in terminal_fanout_children
            )
            if not child_done:
                return event.id
            continue
        if event.type == "fanout.synth.dispatched":
            # B3 (R25 ISSUE-005): a dispatched synth is an active context
            # for its role's hooks until the synth/aggregate completes.
            if str(payload.get("role_instance") or "") != actor:
                continue
            if str(payload.get("fanout_id") or "") not in terminal_synth_fanouts:
                return event.id
            continue
        if event.type != "task.dispatched":
            continue
        if payload.get("assignee") == actor or payload.get("role") == actor:
            return event.id
    return None


def _synth_dispatch_is_active(
    events: list[ZfEvent],
    dispatch: ZfEvent,
) -> bool:
    """B3: a synth dispatch stays active until its fanout's synth or
    aggregate completes (mirrors _fanout_dispatch_is_active for children)."""
    payload = dispatch.payload if isinstance(dispatch.payload, dict) else {}
    fanout_id = str(payload.get("fanout_id") or "")
    if not fanout_id:
        return True
    for event in reversed(events):
        if event.id == dispatch.id:
            break
        if event.type not in {"fanout.synth.completed", "fanout.aggregate.completed"}:
            continue
        event_payload = event.payload if isinstance(event.payload, dict) else {}
        if str(event_payload.get("fanout_id") or "") == fanout_id:
            return False
    return True


def _fanout_dispatch_is_active(
    events: list[ZfEvent],
    dispatch: ZfEvent,
) -> bool:
    payload = dispatch.payload if isinstance(dispatch.payload, dict) else {}
    target_key = _fanout_key(payload)
    if not target_key[0] or not target_key[1]:
        return True
    seen_dispatch = False
    for event in reversed(events):
        if event.id == dispatch.id:
            seen_dispatch = True
            break
        event_payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type in {"fanout.child.completed", "fanout.child.failed"}:
            if target_key in _fanout_terminal_keys(event_payload):
                return False
            if (target_key[0], target_key[1], "") in _fanout_terminal_keys(
                event_payload
            ):
                return False
        elif event.type == "fanout.timed_out":
            fanout_id = str(event_payload.get("fanout_id") or "")
            pending = {str(item or "") for item in event_payload.get("pending_children", []) or []}
            if target_key[0] == fanout_id and target_key[1] in pending:
                return False
    return seen_dispatch


def _fanout_key(payload: dict) -> tuple[str, str, str]:
    return (
        str(payload.get("fanout_id") or ""),
        str(payload.get("child_id") or payload.get("child_run") or ""),
        str(payload.get("run_id") or ""),
    )


def _fanout_terminal_keys(payload: dict) -> set[tuple[str, str, str]]:
    fanout_id, child_id, run_id = _fanout_key(payload)
    if not fanout_id or not child_id:
        return set()
    keys = {(fanout_id, child_id, "")}
    if run_id:
        keys.add((fanout_id, child_id, run_id))
    return keys


def _evaluate_stop_guard(
    state_dir: Path,
    event_log,
    actor: str,
) -> int:
    """ZF-PWF-STOP-GUARD-001 helper: evaluate stop gates for the
    worker's currently active task and return the hook exit code.

    Exit codes follow Claude Code's hook convention:
    - ``0``: allow stop (no gates missing, or no active task at all)
    - ``2``: block stop (advice printed to stderr)

    Looks up the active task via TaskStore and consults events.jsonl
    for matching success events. On any lookup failure, allows the
    stop (post-hoc kernel discriminator still enforces).
    """
    try:
        from zf.core.task.store import TaskStore
        from zf.runtime.stop_guard import evaluate_stop_gates
    except Exception:
        return 0

    try:
        task_store = TaskStore(state_dir / "kanban.json")
        tasks = task_store.list_all()
    except Exception:
        return 0

    # Find the task currently assigned to this actor / role instance.
    target = None
    for task in tasks:
        if task.assigned_to == actor and task.status == "in_progress":
            target = task
            break
    if target is None:
        return 0

    def _has_success_event(task_id: str, event_type: str) -> bool:
        try:
            for ev in event_log.query(event_type=event_type, task_id=task_id):
                return True
        except Exception:
            return False
        return False

    result = evaluate_stop_gates(target, has_success_event=_has_success_event)
    if not result.blocked:
        return 0
    # Block: print continue advice to stderr per Claude Code Stop hook
    # convention (Codex hook surface is similar).
    print(result.advice, file=sys.stderr)
    return 2


_PROTECTED_RUNTIME_FILES = frozenset({
    "events.jsonl",
    "kanban.json",
    "feature_list.json",
    "session.yaml",
    "role_sessions.yaml",
    "progress.md",
})
_PROTECTED_RUNTIME_DIRS = frozenset({
    "task_docs",
    "refs",
})
_WRITE_TOOL_NAMES = frozenset({
    "apply_patch",
    "edit",
    "multiedit",
    "notebookedit",
    "write",
})
_BASH_MUTATING_MARKERS = (
    ">",
    ">>",
    " tee ",
    " sed -i",
    " perl -i",
    " rm ",
    " mv ",
    " cp ",
    " truncate ",
    ".write_text",
    ".write_bytes",
)


def _active_task_id_for_actor(event_log: EventLog, actor: str) -> str:
    try:
        for event in reversed(event_log.read_all()):
            if event.type != "task.dispatched":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if payload.get("assignee") == actor or payload.get("role") == actor:
                return event.task_id or ""
    except Exception:
        return ""
    return ""


def _mentions_protected_runtime_state(
    text: str,
    *,
    state_dir: Path,
) -> list[str]:
    haystack = text.replace("\\/", "/")
    state_text = str(state_dir)
    state_name = state_dir.name
    matches: list[str] = []
    for dirname in sorted(_PROTECTED_RUNTIME_DIRS):
        needles = (
            f"{state_text}/{dirname}",
            f"{state_name}/{dirname}",
            f".zf/{dirname}",
        )
        if any(needle in haystack for needle in needles):
            matches.append(dirname)
    for filename in sorted(_PROTECTED_RUNTIME_FILES):
        needles = (
            f"{state_text}/{filename}",
            f"{state_name}/{filename}",
            f".zf/{filename}",
        )
        if any(needle in haystack for needle in needles):
            matches.append(filename)
    return matches


def _bash_command_looks_mutating(command: str) -> bool:
    normalized = f" {command.strip()} "
    if " zf task-doc ingest " in normalized:
        return True
    return any(marker in normalized for marker in _BASH_MUTATING_MARKERS)


def _evaluate_runtime_write_guard(
    *,
    state_dir: Path,
    event_type: str,
    actor: str,
    event_payload: dict,
    event_log: EventLog,
    event_writer: EventWriter,
    causation_id: str | None,
) -> int:
    """Block worker attempts to mutate kernel-managed runtime projections."""
    if not event_type.endswith(".pre_tool_use"):
        return 0
    if not _should_check_causation(actor):
        return 0

    tool_name = str(event_payload.get("tool_name") or "").strip()
    tool_input = event_payload.get("tool_input")
    input_text = (
        json.dumps(tool_input, ensure_ascii=False)
        if isinstance(tool_input, (dict, list))
        else str(tool_input or "")
    )
    lower_tool = tool_name.lower()
    protected = _mentions_protected_runtime_state(input_text, state_dir=state_dir)
    reason = ""
    if lower_tool in _WRITE_TOOL_NAMES and protected:
        reason = "write_tool_targets_runtime_state"
    elif lower_tool in {"bash", "shell"}:
        command = ""
        if isinstance(tool_input, dict):
            command = str(tool_input.get("command") or "")
        else:
            command = input_text
        if " zf task-doc ingest " in f" {command.strip()} ":
            reason = "worker_task_doc_ingest_forbidden"
        elif protected and _bash_command_looks_mutating(command):
            reason = "shell_write_targets_runtime_state"
    if not reason:
        return 0

    task_id = _active_task_id_for_actor(event_log, actor)
    try:
        event_writer.append(ZfEvent(
            type="worker.runtime_write.rejected",
            actor="zf-cli",
            task_id=task_id or None,
            payload={
                "reason": reason,
                "worker": actor,
                "origin_event": event_type,
                "tool_name": tool_name,
                "protected_targets": protected,
            },
            causation_id=causation_id,
        ))
    except Exception:
        pass
    print(
        "ZaoFu blocked this tool call: workers may read kernel-managed "
        "runtime projections, but must not write task_docs/refs/truth files. "
        "Emit an event or ask the operator/kernel to update the projection.",
        file=sys.stderr,
    )
    return 2


def run(args: argparse.Namespace) -> int:
    config: ZfConfig | None = None
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
            load_config_with_explicit=True,
        )
        state_dir = context.state_dir
        project_root = context.project_root
        config = context.config
    except ConfigError:
        fallback = getattr(args, "state_dir", None) or ".zf"
        state_dir = Path(fallback)
        if not state_dir.is_absolute():
            state_dir = Path.cwd() / state_dir
        project_root = state_dir.parent

    # 1. Read hook input from stdin (JSON).
    raw = ""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        _log_error(state_dir, f"hook-recv: bad stdin JSON: {e}; "
                               f"raw={raw[:200]!r}")
        return 0

    session_id = payload.get("session_id") or ""
    actor = _resolve_hook_actor(
        state_dir=state_dir,
        project_root=project_root,
        event_type=args.event,
        payload=payload,
        session_id=session_id,
    )

    # 2. Build event payload. Keep small — copy only fields reactors use.
    event_payload: dict = {
        "session_id": session_id,
        "hook_event": payload.get("hook_event_name", ""),
    }
    for k in ("tool_name", "tool_input"):
        if k in payload:
            event_payload[k] = payload[k]
    if "tool_response" in payload:
        raw_resp = payload["tool_response"]
        if isinstance(raw_resp, (dict, list)):
            raw_resp = json.dumps(raw_resp, ensure_ascii=False)[:2000]
        event_payload["tool_response"] = str(raw_resp)[:2000]
    if "stop_hook_active" in payload:
        event_payload["stop_hook_active"] = payload["stop_hook_active"]

    # 1202-T2: Codex hook payloads carry extra fields (turn_id,
    # transcript_path, permission_mode) that Claude's do not. Select
    # the extractor by --event namespace (canonical) rather than the
    # --backend hint so payload extraction still works if --backend
    # is omitted by an older hooks.json.
    if args.event.startswith("codex.hook."):
        for k in ("turn_id", "transcript_path", "permission_mode"):
            if k in payload:
                event_payload[k] = payload[k]
    if args.event.endswith(".stop"):
        event_payload["provider_stop_reason"] = classify_provider_stop(
            event_payload | payload,
        )

    try:
        event_log = event_log_from_project(state_dir, config=config)
    except EventSigningConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    event_writer = EventWriter(event_log)
    completed_tail_quiesced = _completed_tail_quiesced(event_log)
    if completed_tail_quiesced and args.event == "provider.stop.check":
        return 0

    # T3: causation_id from the latest task.dispatched for this actor.
    # Orphan flag only applies to actors that *should* have a dispatch
    # context — orchestrator / zf-cli hook events legitimately have none.
    causation_id = _resolve_causation(state_dir, actor, event_log)
    orphan = _should_check_causation(actor) and causation_id is None

    # 3. Emit the main hook event. On failure → dead_letter + queue
    # write_failed marker; do NOT raise (hook must never fail Claude turn).
    try:
        event_writer.append(ZfEvent(
            type=args.event,
            actor=actor,
            payload=event_payload,
            causation_id=causation_id,
        ))
    except Exception as e:
        _log_error(
            state_dir,
            f"hook-recv: emit failed: {e}\n{traceback.format_exc()}",
        )
        _dead_letter(state_dir, args.event, {
            "actor": actor,
            "payload": event_payload,
            "causation_id": causation_id,
        }, reason=str(e))
        _queue_write_failed(state_dir, reason=str(e))
        return 0

    # 4. Flush any queued hook.write_failed markers (T2): now that we
    # know append works, surface the prior outage to the orchestrator.
    try:
        _flush_write_failed_queue(state_dir, event_writer)
    except Exception:
        pass

    # 5. If causation lookup failed, emit hook.orphan_event so the
    # reactor + metrics layer knows this hook had no dispatched context.
    if orphan and not completed_tail_quiesced:
        try:
            event_writer.append(ZfEvent(
                type="hook.orphan_event",
                actor=actor,
                payload={"session_id": session_id,
                         "origin_event": args.event},
            ))
        except Exception:
            pass

    guard_exit = _evaluate_runtime_write_guard(
        state_dir=state_dir,
        event_type=args.event,
        actor=actor,
        event_payload=event_payload,
        event_log=event_log,
        event_writer=event_writer,
        causation_id=causation_id,
    )
    if guard_exit:
        return guard_exit

    # 6. ZF-PWF-STOP-GUARD-001 (doc 41 §4.5): for `provider.stop.check`
    # events, evaluate task gates and return exit 2 (block) when
    # required gates are missing. Audit event was already emitted in
    # step 3; the non-zero exit code tells the provider hook to
    # surface the continue advice and NOT to actually stop.
    if args.event == "provider.stop.check":
        try:
            return _evaluate_stop_guard(state_dir, event_log, actor)
        except Exception as e:
            _log_error(
                state_dir,
                f"hook-recv: stop-guard evaluation failed: {e}",
            )
            # Defensive: on evaluator failure, allow the stop rather
            # than blocking forever — the kernel discriminator still
            # provides post-hoc enforcement.
            return 0

    return 0


def _completed_tail_quiesced(event_log: EventLog) -> bool:
    try:
        from zf.autoresearch.failure_signals import completed_run_quiesced

        return completed_run_quiesced(event_log.read_all())
    except Exception:
        return False
