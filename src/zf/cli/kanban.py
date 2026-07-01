"""zf kanban — task board management."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, fields
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import ProjectContext, resolve_project_context
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.core.statemachine.task import TaskStateMachine, InvalidTransition
from zf.runtime.feature_completion import close_feature_if_all_tasks_done


_TERMINAL = {"done", "cancelled"}
_HANDOFF_SOURCE = "kanban_handoff"
_sm = TaskStateMachine()
_FEATURE_ID_RE = re.compile(r"^F-[A-Za-z0-9]+$")
_TASK_KEY_CLEAN_RE = re.compile(r"[^A-Za-z0-9]+")


# LH-0.T2: stage transition gate.
#
# Maps the assign target (role type) to the set of events that, when
# present in events.jsonl for this task_id, legitimise the transition.
# Any one is enough; "dev" has several because rework paths feed back.
#
# None (default) means "no gate" — anything (e.g. arch) can be assigned
# freely. Only the dev → review → test → judge chain is gated; stages
# outside the chain stay open to preserve backward-compat with custom
# pipelines.
_ASSIGN_PRED: dict[str, frozenset[str]] = {
    "dev": frozenset({
        "task.created", "review.rejected", "test.failed",
        "judge.failed", "task.orphaned",
    }),
    "review": frozenset({"dev.build.done"}),
    "test": frozenset({"review.approved"}),
    "judge": frozenset({"test.passed"}),
}


def _role_type(target: str, config_roles: list | None) -> str:
    """Resolve 'dev-1' / 'review' / etc to its role type ('dev', 'review').

    Uses zf.yaml config when available (authoritative); otherwise falls
    back to prefix match against the gated role types.
    """
    if config_roles is not None:
        for r in config_roles:
            if r.name == target or r.instance_id == target:
                return r.name
    for t in _ASSIGN_PRED:
        if target == t or target.startswith(t + "-"):
            return t
    return target


def _validate_transition(
    task_id: str,
    target: str,
    kind: str,  # "assign" | "move"
    event_log: EventLog,
    config_context: object | None = None,
) -> tuple[bool, str | None]:
    """Return (allowed, missing_event).

    assign:  target is a role type (dev / review / test / judge / ...)
    move:    target is a status string (done / cancelled / ...)

    Gate is permissive: unknown targets → allowed (don't break
    backward-compat). Only the canonical pipeline transitions are gated.
    """
    config_roles = _config_roles(config_context)
    if kind == "move":
        if target == "done":
            need = _terminal_pred(config_context)
        else:
            return True, None
    else:
        role_type = _role_type(target, config_roles)
        need = _ASSIGN_PRED.get(role_type)
        if need is None:
            return True, None

    # Scan events.jsonl for the latest relevant event on this task.
    # Also track whether we've seen ANY task-scoped event, to give fresh
    # tasks (no lifecycle events yet) a pass on first-dev assign.
    seen_any = False
    for event in reversed(event_log.read_all()):
        if event.task_id != task_id:
            continue
        seen_any = True
        if event.type in need:
            return True, None
    # Fresh task, no events yet → dev assign allowed (first dispatch).
    # review/test/judge always require an explicit predecessor.
    if not seen_any and kind == "assign" and _role_type(target, config_roles) == "dev":
        return True, None
    first = sorted(need)[0]
    return False, first


def _config_roles(config_context: object | None) -> list | None:
    if isinstance(config_context, list):
        return config_context
    roles = getattr(config_context, "roles", None)
    return roles if isinstance(roles, list) else None


def _terminal_pred(config_context: object | None) -> frozenset[str]:
    if _terminal_discriminator_required(config_context):
        return frozenset({"discriminator.passed"})
    config_roles = _config_roles(config_context)
    if config_roles is not None:
        published = {
            event
            for role in config_roles
            for event in getattr(role, "publishes", [])
        }
        for event in ("judge.passed", "verify.passed", "test.passed", "review.approved"):
            if event in published:
                return frozenset({event})
    return frozenset({"judge.passed"})


def _terminal_discriminator_required(config_context: object | None) -> bool:
    if config_context is None or isinstance(config_context, list):
        return False
    quality_gates = getattr(config_context, "quality_gates", {}) or {}
    for gate in quality_gates.values():
        if getattr(gate, "enabled", True) and getattr(gate, "required_checks", []):
            return True
    verification = getattr(config_context, "verification", None)
    if verification is None:
        return False
    for name in ("architecture", "promoted", "semantic"):
        section = getattr(verification, name, None)
        if bool(getattr(section, "enabled", False)):
            return True
    return False


def _load_config_safe() -> object | None:
    """Load zf.yaml if available; swallow all errors.

    The transition gate is optional context. Missing config falls back to
    role-prefix matching and legacy terminal predicates.
    """
    try:
        from zf.core.config.loader import load_config
        cfg_path = Path.cwd() / "zf.yaml"
        if cfg_path.exists():
            return load_config(cfg_path)
    except Exception:
        pass
    return None


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("kanban", help="Task board management")
    parser.add_argument("--board", action="store_true", help="Show columnar board view")
    parser.add_argument("--watch", action="store_true", help="Watch mode (refresh every 2s)")
    parser.add_argument("--all", action="store_true",
                        help="Include every archived task (default: active only)")
    parser.add_argument("--days", type=int, default=None,
                        help="Include archived tasks from the last N days")
    parser.add_argument(
        "--state-dir",
        type=str,
        default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml, else .zf)",
    )
    parser.set_defaults(func=_run_board)

    sub = parser.add_subparsers(dest="kanban_cmd")

    # add
    add_p = sub.add_parser("add", help="Add a task to backlog")
    add_p.add_argument(
        "title",
        nargs="+",
        help=(
            "Task title. Compatibility: `zf kanban add F-xxxx title` "
            "links the task to feature F-xxxx."
        ),
    )
    add_p.add_argument("--key", default="", help="Idempotency key")
    add_p.add_argument(
        "--feature",
        default="",
        help="Parent feature id. Encoded into the task key for projection.",
    )
    add_p.add_argument("--blocked-by", nargs="*", default=[], help="Blocking task IDs")
    add_p.add_argument(
        "--id-only",
        action="store_true",
        help="Print only the created task id for scripts.",
    )
    add_p.add_argument(
        "--json",
        action="store_true",
        help="Print a machine-readable JSON object for scripts.",
    )
    add_p.set_defaults(func=_run_add)

    # move
    move_p = sub.add_parser("move", help="Move a task to a new status")
    move_p.add_argument("task_id", help="Task ID")
    move_p.add_argument("status", help="Target status")
    move_p.set_defaults(func=_run_move)

    # assign
    assign_p = sub.add_parser("assign", help="Assign a task to a role")
    assign_p.add_argument("task_id", help="Task ID")
    assign_p.add_argument("role", help="Role name")
    assign_p.set_defaults(func=_run_assign)

    # handoff
    handoff_p = sub.add_parser(
        "handoff",
        help="Atomically update a task contract and assign the next owner",
    )
    handoff_p.add_argument("task_id", help="Task ID")
    handoff_p.add_argument(
        "--contract-file",
        required=True,
        help="JSON file containing a TaskContract or {'contract': {...}}",
    )
    handoff_p.add_argument(
        "--assign",
        required=True,
        help="Role name or instance_id to receive the task",
    )
    handoff_p.add_argument(
        "--trigger-event",
        default="",
        help="Upstream event id used as the idempotency key",
    )
    handoff_p.add_argument(
        "--json",
        action="store_true",
        help="Print a machine-readable JSON object for scripts.",
    )
    handoff_p.set_defaults(func=_run_handoff)

    # show
    show_p = sub.add_parser("show", help="Show task details")
    show_p.add_argument("task_id", help="Task ID")
    show_p.add_argument(
        "--doc",
        action="store_true",
        help="Show task capsule refs and current revision projection",
    )
    show_p.add_argument(
        "--verify-doc",
        action="store_true",
        help="Verify task capsule freshness against the current kanban task",
    )
    show_p.add_argument(
        "--explain-ready",
        action="store_true",
        help="Explain whether this task is ready for dispatch",
    )
    show_p.set_defaults(func=_run_show)

    # ready
    ready_p = sub.add_parser("ready", help="List ready tasks")
    ready_p.set_defaults(func=_run_ready)

    # open
    open_p = sub.add_parser("open", help="List non-terminal tasks")
    open_p.set_defaults(func=_run_open)

    # pending
    pending_p = sub.add_parser("pending", help="List backlog tasks")
    pending_p.set_defaults(func=_run_pending)

    # export
    export_p = sub.add_parser(
        "export",
        help="Export kanban to a human-readable artifact (default: md)",
    )
    export_p.add_argument(
        "--format",
        choices=("md", "json"),
        default="md",
        help="Output format (md groups by status; json dumps raw kanban list)",
    )
    export_p.add_argument(
        "--output", "-o",
        default="-",
        help="Output path (default: stdout)",
    )
    export_p.add_argument(
        "--state-dir",
        type=str,
        default=argparse.SUPPRESS,
        help=(
            "Path to runtime state dir. Also accepted before the subcommand "
            "as `zf kanban --state-dir <dir> export`."
        ),
    )
    export_p.add_argument(
        "--feature",
        default=None,
        help="Filter to a single feature_id (matches contract.feature_id)",
    )
    export_p.add_argument(
        "--include-archived",
        action="store_true",
        help="Include archived terminal tasks (default: active only)",
    )
    export_p.set_defaults(func=_run_export)

    # EVAL-KANBAN-HEALTH-001 (doc 43 §2.7): main audit entry — aggregates
    # 6 dimensions (THROUGHPUT / WORKFLOW COVERAGE / ROLE HEALTH /
    # FAILURE TAXONOMY / COORDINATOR / METRICS SNAPSHOT) plus
    # deterministic recommendations.
    health_p = sub.add_parser(
        "health",
        help="Aggregated health snapshot (workflow / roles / failures / coordinator / metrics)",
    )
    health_p.add_argument(
        "--since", default=None,
        help="Time window (e.g. 24h, 7d) for aggregations",
    )
    health_p.add_argument(
        "--format", choices=("md", "json"), default="md",
    )
    health_p.add_argument(
        "--fail-on", choices=("any", "warning", "critical", "none"),
        default="none",
        help="Exit code 1 when health degradation matches this level (CI-friendly)",
    )
    health_p.set_defaults(func=_run_health)


def _context(args: argparse.Namespace | None = None) -> ProjectContext:
    explicit = getattr(args, "state_dir", None) if args is not None else None
    return resolve_project_context(explicit_state_dir=explicit)


def _state_dir(args: argparse.Namespace | None = None) -> Path:
    return _context(args).state_dir


def _store(args: argparse.Namespace | None = None) -> TaskStore:
    return TaskStore(_state_dir(args) / "kanban.json")


def _event_log(args: argparse.Namespace | None = None) -> EventLog:
    context = _context(args)
    return event_log_from_project(context.state_dir, config=context.config)


def _event_writer(args: argparse.Namespace | None = None) -> EventWriter:
    return EventWriter(_event_log(args))


def _load_tasks(store: TaskStore, args: argparse.Namespace) -> list:
    """Pick active vs archived view based on --all / --days."""
    show_all = getattr(args, "all", False)
    days = getattr(args, "days", None)
    if show_all:
        return store.list_all_with_archive()
    if days is not None:
        return store.list_all_with_archive(last_days=days)
    return store.list_all()


def _run_board(args: argparse.Namespace) -> int:
    if getattr(args, "kanban_cmd", None) is None:
        try:
            store = _store(args)
        except ConfigError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        tasks = _load_tasks(store, args)
        if not tasks:
            print("(empty board)")
            return 0

        use_board = getattr(args, "board", False)
        watch = getattr(args, "watch", False)

        if use_board or watch:
            from zf.cli.kanban_render import render_board
            if watch:
                import time
                try:
                    while True:
                        print("\033[2J\033[H", end="")  # clear screen
                        tasks = _load_tasks(store, args)
                        print(render_board(tasks))
                        time.sleep(2)
                except KeyboardInterrupt:
                    pass
            else:
                print(render_board(tasks))
        else:
            for task in tasks:
                assigned = f" [{task.assigned_to}]" if task.assigned_to else ""
                print(f"  {task.status:15s} {task.id}  {task.title}{assigned}")
        return 0
    return args.func(args)


def _run_add(args: argparse.Namespace) -> int:
    try:
        store = _store(args)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    try:
        title, key, feature_id = _normalize_add_args(args)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if key:
        task = store.ensure(key=key, title=title, blocked_by=args.blocked_by)
    else:
        task = Task(title=title, blocked_by=args.blocked_by)
        task = store.add(task)

    # LH-5.T2: stamp causation_id from the latest user.message so
    # `zf task trace` can walk the chain from human intent to final done.
    log = _event_log(args)
    causation = None
    try:
        for event in reversed(log.read_all()[-500:]):
            if event.type == "user.message":
                causation = event.id
                break
    except Exception:
        causation = None
    payload = {}
    if feature_id:
        payload["feature_id"] = feature_id
    if task.key:
        payload["key"] = task.key
    _event_writer(args).append(ZfEvent(
        type="task.created", actor="zf-cli", task_id=task.id,
        causation_id=causation,
        payload=payload,
    ))
    if getattr(args, "json", False):
        print(json.dumps({
            "task_id": task.id,
            "title": task.title,
            "key": task.key,
            "feature_id": feature_id,
        }, ensure_ascii=False))
    elif getattr(args, "id_only", False):
        print(task.id)
    else:
        print(f"Added: {task.title} {task.id}")
    return 0


def _normalize_add_args(args: argparse.Namespace) -> tuple[str, str, str]:
    parts = [str(part) for part in getattr(args, "title", [])]
    if not parts:
        raise ValueError("task title is required")

    feature_id = str(getattr(args, "feature", "") or "")
    if parts and _FEATURE_ID_RE.match(parts[0]):
        positional_feature = parts.pop(0)
        if feature_id and feature_id != positional_feature:
            raise ValueError(
                f"conflicting feature ids: {positional_feature} and {feature_id}"
            )
        feature_id = positional_feature

    if not parts:
        raise ValueError("task title is required")

    title = " ".join(parts)
    key = str(getattr(args, "key", "") or "")
    if feature_id and not _key_has_feature_prefix(key, feature_id):
        suffix = _task_key_suffix(key or title)
        key = f"{feature_id}:{suffix}"
    return title, key, feature_id


def _key_has_feature_prefix(key: str, feature_id: str) -> bool:
    return key.startswith(f"{feature_id}:") or key.startswith(f"{feature_id}-")


def _task_key_suffix(value: str) -> str:
    suffix = _TASK_KEY_CLEAN_RE.sub("-", value).strip("-").lower()
    return suffix[:48] or "task"


def _run_move(args: argparse.Namespace) -> int:
    try:
        store = _store(args)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    task = store.get(args.task_id)
    if task is None:
        print(f"Error: Task {args.task_id} not found. To fix: run 'zf kanban' to list tasks", file=sys.stderr)
        return 1

    # LH-0.T2: stage gate — block move to done without judge.passed.
    allowed, missing = _validate_transition(
        args.task_id, args.status, "move", _event_log(args),
        _load_config_safe(),
    )
    if not allowed:
        _event_writer(args).append(ZfEvent(
            type="task.invalid_transition",
            actor="zf-cli",
            task_id=args.task_id,
            payload={
                "kind": "move",
                "target": args.status,
                "missing": missing,
                "from_status": task.status,
            },
        ))
        print(
            f"Error: move {args.task_id} → {args.status} rejected: "
            f"missing prior event '{missing}'. "
            f"Rework the pipeline stage (LH-0.T2 gate).",
            file=sys.stderr,
        )
        return 1

    try:
        _sm.transition(task.status, args.status)
    except InvalidTransition as e:
        # Layer 2 keeps multi-stage tasks in_progress while reassigning
        # them across role instances. Once the topology-derived terminal
        # event exists, allow that specific close path without weakening
        # the stage gate above.
        if not (task.status == "in_progress" and args.status == "done"):
            valid = _sm.valid_transitions().get(task.status, set())
            print(
                f"Error: {e}. Valid targets from '{task.status}': {valid}",
                file=sys.stderr,
            )
            return 1

    store.update(args.task_id, status=args.status)
    writer = _event_writer(args)
    writer.append(ZfEvent(
        type="task.status_changed",
        actor="zf-cli",
        task_id=args.task_id,
        payload={"from": task.status, "to": args.status},
    ))
    if args.status == "done":
        try:
            close_feature_if_all_tasks_done(
                state_dir=_state_dir(args),
                task=task,
                task_store=store,
                event_writer=writer,
                event_log=_event_log(args),
                actor="zf-cli",
                source="kanban_move",
            )
        except Exception:
            pass
    print(f"Moved {args.task_id}: {task.status} -> {args.status}")
    return 0


def _run_assign(args: argparse.Namespace) -> int:
    try:
        store = _store(args)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    task = store.get(args.task_id)
    if task is None:
        print(f"Error: Task {args.task_id} not found. To fix: run 'zf kanban' to list tasks", file=sys.stderr)
        return 1

    # G-INST-9: validate args.role against config.roles. The value may be
    # either a role.name (legacy single-instance) or an instance_id
    # (multi-instance). Either must match some expanded RoleConfig.
    config_roles = None
    try:
        from zf.core.config.loader import load_config
        cfg_path = Path.cwd() / "zf.yaml"
        if cfg_path.exists():
            cfg = load_config(cfg_path)
            config_roles = cfg.roles
            known = {r.instance_id for r in cfg.roles} | {r.name for r in cfg.roles}
            if args.role not in known:
                print(
                    f"Error: unknown role/instance {args.role!r}. "
                    f"Known: {sorted(known)}",
                    file=sys.stderr,
                )
                return 1
    except Exception:
        # If config is unloadable, don't block the assign — legacy behavior.
        pass

    # LH-0.T2: stage transition gate. Refuse to assign to review/test/
    # judge unless the prior stage's completion event is in events.jsonl.
    allowed, missing = _validate_transition(
        args.task_id, args.role, "assign", _event_log(args), config_roles,
    )
    if not allowed:
        _event_writer(args).append(ZfEvent(
            type="task.invalid_transition",
            actor="zf-cli",
            task_id=args.task_id,
            payload={
                "kind": "assign",
                "target": args.role,
                "missing": missing,
                "from_status": task.status,
            },
        ))
        print(
            f"Error: assign {args.task_id} → {args.role} rejected: "
            f"missing prior event '{missing}'. "
            f"Rework the pipeline stage (LH-0.T2 gate).",
            file=sys.stderr,
        )
        return 1

    store.update(args.task_id, assigned_to=args.role)
    # C3: include `assignee` (canonical instance_id field) alongside
    # legacy `role` for forward-compat with the dedup logic in
    # Orchestrator._dispatch_ready (multi-stage handoff).
    _event_writer(args).append(ZfEvent(
        type="task.assigned",
        actor="zf-cli",
        task_id=args.task_id,
        payload={"role": args.role, "assignee": args.role},
    ))
    print(f"Assigned {args.task_id} to {args.role}")
    return 0


def _run_handoff(args: argparse.Namespace) -> int:
    try:
        store = _store(args)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    task = store.get(args.task_id)
    if task is None:
        print(f"Error: Task {args.task_id} not found. To fix: run 'zf kanban' to list tasks", file=sys.stderr)
        return 1

    try:
        contract = _load_contract_file(Path(args.contract_file))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    config_roles = None
    try:
        from zf.core.config.loader import load_config
        cfg_path = Path.cwd() / "zf.yaml"
        if cfg_path.exists():
            cfg = load_config(cfg_path)
            config_roles = cfg.roles
            known = {r.instance_id for r in cfg.roles} | {r.name for r in cfg.roles}
            if args.assign not in known:
                print(
                    f"Error: unknown role/instance {args.assign!r}. "
                    f"Known: {sorted(known)}",
                    file=sys.stderr,
                )
                return 1
    except Exception:
        # If config is unloadable, don't block the handoff — match assign.
        pass

    allowed, missing = _validate_transition(
        args.task_id, args.assign, "assign", _event_log(args), config_roles,
    )
    if not allowed:
        _event_writer(args).append(ZfEvent(
            type="task.invalid_transition",
            actor="zf-cli",
            task_id=args.task_id,
            payload={
                "kind": "handoff",
                "target": args.assign,
                "missing": missing,
                "from_status": task.status,
                "source": _HANDOFF_SOURCE,
            },
        ))
        print(
            f"Error: handoff {args.task_id} → {args.assign} rejected: "
            f"missing prior event '{missing}'. "
            f"Rework the pipeline stage (LH-0.T2 gate).",
            file=sys.stderr,
        )
        return 1

    existing_events = _event_log(args).read_all()
    trigger_event_id = str(getattr(args, "trigger_event", "") or "").strip()
    contract_seen = _handoff_event_seen(
        existing_events,
        task_id=args.task_id,
        event_type="task.contract.update",
        assignee=args.assign,
        trigger_event_id=trigger_event_id,
    )
    assigned_seen = _handoff_event_seen(
        existing_events,
        task_id=args.task_id,
        event_type="task.assigned",
        assignee=args.assign,
        trigger_event_id=trigger_event_id,
    )

    updated = store.update(args.task_id, contract=contract, assigned_to=args.assign)
    if updated is None:
        print(f"Error: Task {args.task_id} not found. To fix: run 'zf kanban' to list tasks", file=sys.stderr)
        return 1

    writer = _event_writer(args)
    emitted: list[str] = []
    if not contract_seen:
        event = writer.append(ZfEvent(
            type="task.contract.update",
            actor="zf-cli",
            task_id=args.task_id,
            payload=_handoff_payload(
                assignee=args.assign,
                trigger_event_id=trigger_event_id,
                contract=asdict(contract),
            ),
        ))
        emitted.append(event.id)
    if not assigned_seen:
        event = writer.append(ZfEvent(
            type="task.assigned",
            actor="zf-cli",
            task_id=args.task_id,
            payload=_handoff_payload(
                assignee=args.assign,
                trigger_event_id=trigger_event_id,
            ),
        ))
        emitted.append(event.id)

    result = {
        "task_id": args.task_id,
        "assigned_to": args.assign,
        "trigger_event_id": trigger_event_id,
        "emitted_event_ids": emitted,
        "idempotent": not emitted,
    }
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False))
    else:
        suffix = " (idempotent)" if not emitted else ""
        print(f"Handoff {args.task_id} to {args.assign}{suffix}")
    return 0


def _load_contract_file(path: Path) -> TaskContract:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"cannot read contract file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("contract file must contain a JSON object")
    data = raw.get("contract") if isinstance(raw.get("contract"), dict) else raw
    if not isinstance(data, dict):
        raise ValueError("contract file must contain a contract JSON object")
    allowed = {field.name for field in fields(TaskContract)}
    filtered = {
        key: value
        for key, value in data.items()
        if key in allowed
    }
    acceptance = filtered.get("acceptance")
    if isinstance(acceptance, list):
        filtered["acceptance"] = "\n".join(str(item) for item in acceptance)
    try:
        return TaskContract(**filtered)
    except TypeError as exc:
        raise ValueError(f"invalid contract fields: {exc}") from exc


def _handoff_payload(
    *,
    assignee: str,
    trigger_event_id: str,
    contract: dict | None = None,
) -> dict:
    payload: dict = {
        "role": assignee,
        "assignee": assignee,
        "source": _HANDOFF_SOURCE,
        "handoff": {"assignee": assignee},
    }
    if contract is not None:
        payload["contract"] = contract
    if trigger_event_id:
        payload["trigger_event_id"] = trigger_event_id
    return payload


def _handoff_event_seen(
    events: list[ZfEvent],
    *,
    task_id: str,
    event_type: str,
    assignee: str,
    trigger_event_id: str,
) -> bool:
    if not trigger_event_id:
        return False
    for event in events:
        if event.task_id != task_id or event.type != event_type:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if payload.get("source") != _HANDOFF_SOURCE:
            continue
        if str(payload.get("trigger_event_id") or "") != trigger_event_id:
            continue
        handoff = payload.get("handoff") if isinstance(payload.get("handoff"), dict) else {}
        observed = str(
            payload.get("assignee")
            or payload.get("role")
            or handoff.get("assignee")
            or ""
        )
        if observed == assignee:
            return True
    return False


def _run_show(args: argparse.Namespace) -> int:
    try:
        store = _store(args)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    task = store.get(args.task_id)
    if task is None:
        print(f"Error: Task {args.task_id} not found. To fix: run 'zf kanban' to list tasks", file=sys.stderr)
        return 1

    print(f"ID:       {task.id}")
    print(f"Title:    {task.title}")
    print(f"Status:   {task.status}")
    print(f"Assigned: {task.assigned_to or '(none)'}")
    print(f"Key:      {task.key or '(none)'}")
    if task.blocked_by:
        print(f"Blocked:  {', '.join(task.blocked_by)}")
    if task.contract.behavior:
        print(f"Behavior: {task.contract.behavior}")
    if task.contract.verification:
        print(f"Verify:   {task.contract.verification}")
    if getattr(args, "doc", False):
        _print_task_doc_projection(_state_dir(args), task)
    if getattr(args, "verify_doc", False):
        _print_task_doc_verification(_state_dir(args), task)
    if getattr(args, "explain_ready", False):
        print(f"Ready:    {_ready_explanation(store, task)}")
    return 0


def _run_ready(args: argparse.Namespace) -> int:
    try:
        store = _store(args)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    ready = store.ready()
    if not ready:
        print("(no ready tasks)")
    for task in ready:
        print(f"  {task.id}  {task.title}")
    return 0


def _run_open(args: argparse.Namespace) -> int:
    try:
        store = _store(args)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    tasks = [t for t in store.list_all() if t.status not in _TERMINAL]
    if not tasks:
        print("(no open tasks)")
    for task in tasks:
        print(f"  {task.status:15s} {task.id}  {task.title}")
    return 0


def _run_pending(args: argparse.Namespace) -> int:
    try:
        store = _store(args)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    tasks = store.filter(status="backlog")
    if not tasks:
        print("(no pending tasks)")
    for task in tasks:
        print(f"  {task.id}  {task.title}")
    return 0


def _print_task_doc_projection(state_dir: Path, task) -> None:
    from zf.runtime.task_doc import (
        compute_task_capsule_revisions,
        task_doc_path,
        task_evidence_path,
        task_progress_path,
        task_source_path,
    )

    revisions = compute_task_capsule_revisions(task)
    print("Task Capsule:")
    print(f"  source:   {task_source_path(state_dir, task.id)}")
    print(f"  task:     {task_doc_path(state_dir, task.id)}")
    print(f"  progress: {task_progress_path(state_dir, task.id)}")
    print(f"  evidence: {task_evidence_path(state_dir, task.id)}")
    print(f"  source_revision:   {revisions['source_revision']}")
    print(f"  contract_revision: {revisions['contract_revision']}")
    print(f"  capsule_revision:  {revisions['capsule_revision']}")


def _print_task_doc_verification(state_dir: Path, task) -> None:
    from zf.runtime.task_doc import verify_task_capsule

    errors = verify_task_capsule(state_dir, task)
    if not errors:
        print("Task Capsule Freshness: ok")
        return
    print("Task Capsule Freshness: stale")
    for error in errors:
        print(f"  - {error}")


def _ready_explanation(store: TaskStore, task) -> str:
    if task.status != "backlog":
        return f"not_ready: status={task.status}"
    unresolved: list[str] = []
    for task_id in task.blocked_by:
        blocker = store.get(task_id)
        if blocker is None:
            unresolved.append(f"{task_id}:missing")
        elif blocker.status not in _TERMINAL:
            unresolved.append(f"{task_id}:{blocker.status}")
    if unresolved:
        return "blocked_by_unresolved: " + ", ".join(unresolved)
    return "ready"


_STATUS_ORDER = (
    "backlog", "ready", "dispatched", "in_progress",
    "review", "test", "judge", "blocked", "blocked_human",
    "done", "cancelled",
)


def _run_health(args: argparse.Namespace) -> int:
    """EVAL-KANBAN-HEALTH-001: aggregated health snapshot.

    Aggregates 6 dimensions:
    - THROUGHPUT (tasks completed/failed/rework-looped)
    - WORKFLOW COVERAGE (per-task audit via EVAL-WORKFLOW-AUDIT-001)
    - ROLE HEALTH (heartbeat freshness per role)
    - FAILURE TAXONOMY (per EVAL-FAILURE-TAXONOMY-001 bucket counts)
    - COORDINATOR (dispatch:no_action ratio per EVAL-COORDINATOR-RATIO-001)
    - METRICS SNAPSHOT (per EVAL-METRIC-DIAGNOSTICS-001)
    plus deterministic recommendations.
    """
    from zf.cli.kanban_health import build_health_snapshot, render_health_md

    try:
        context = _context(args)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    snap = build_health_snapshot(
        state_dir=context.state_dir,
        config=context.config,
        since=getattr(args, "since", None),
    )

    fail_on = getattr(args, "fail_on", "none")
    if args.format == "json":
        print(json.dumps(snap, indent=2, ensure_ascii=False, default=str))
    else:
        print(render_health_md(snap))

    return _health_exit_code(snap, fail_on)


def _health_exit_code(snap: dict, fail_on: str) -> int:
    """Exit code per --fail-on level. Returns 0 unless health crosses
    the requested threshold."""
    if fail_on == "none":
        return 0
    diagnostics = snap.get("metric_diagnostics", []) or []
    critical = any(d.get("health_band") == "critical" for d in diagnostics)
    warning = any(d.get("health_band") == "warning" for d in diagnostics)
    recommendations = snap.get("recommendations", []) or []
    if fail_on == "critical" and critical:
        return 1
    if fail_on == "warning" and (warning or critical):
        return 1
    if fail_on == "any" and (critical or warning or recommendations):
        return 1
    return 0


def _run_export(args: argparse.Namespace) -> int:
    try:
        store = _store(args)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.include_archived:
        tasks = store.list_all_with_archive()
    else:
        tasks = store.list_all()

    feature_filter = getattr(args, "feature", None)
    if feature_filter:
        tasks = [
            t for t in tasks
            if getattr(t.contract, "feature_id", "") == feature_filter
        ]

    if args.format == "json":
        from dataclasses import asdict
        rendered = json.dumps(
            [asdict(t) for t in tasks],
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    else:
        rendered = _render_kanban_markdown(tasks, feature_filter)

    if args.output == "-":
        print(rendered)
    else:
        Path(args.output).write_text(rendered, encoding="utf-8")
        print(f"wrote {len(tasks)} task(s) to {args.output}", file=sys.stderr)
    return 0


def _render_kanban_markdown(
    tasks: list[Task],
    feature_filter: str | None,
) -> str:
    lines: list[str] = []
    header = "# Kanban Snapshot"
    if feature_filter:
        header += f" — feature {feature_filter}"
    lines.append(header)
    lines.append("")
    lines.append(f"_total: {len(tasks)} task(s)_")
    lines.append("")

    by_status: dict[str, list[Task]] = {}
    for task in tasks:
        by_status.setdefault(task.status, []).append(task)

    ordered_statuses = [s for s in _STATUS_ORDER if s in by_status]
    extras = sorted(s for s in by_status if s not in _STATUS_ORDER)
    ordered_statuses.extend(extras)

    for status in ordered_statuses:
        bucket = by_status[status]
        lines.append(f"## {status} ({len(bucket)})")
        lines.append("")
        for task in bucket:
            lines.extend(_render_task_md(task))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_task_md(task: Task) -> list[str]:
    contract = task.contract
    lines: list[str] = [f"### {task.id} — {task.title}"]
    meta: list[str] = []
    if task.assigned_to:
        meta.append(f"assignee=`{task.assigned_to}`")
    if getattr(contract, "owner_role", ""):
        meta.append(f"role=`{contract.owner_role}`")
    if getattr(contract, "feature_id", ""):
        meta.append(f"feature=`{contract.feature_id}`")
    if getattr(contract, "phase", ""):
        meta.append(f"phase=`{contract.phase}`")
    if meta:
        lines.append("- " + " · ".join(meta))
    if getattr(contract, "behavior", ""):
        lines.append(f"- behavior: {contract.behavior.strip().splitlines()[0]}")
    if getattr(contract, "scope", []):
        lines.append("- scope:")
        for path in contract.scope:
            lines.append(f"  - `{path}`")
    if getattr(contract, "verification", ""):
        lines.append(f"- verification: `{contract.verification}`")
    if getattr(contract, "acceptance", "") and contract.acceptance != "exit_code=0":
        accept_lines = [
            ln for ln in contract.acceptance.splitlines() if ln.strip()
        ]
        if accept_lines:
            lines.append("- acceptance:")
            for ln in accept_lines:
                lines.append(f"  - `{ln}`")
    if getattr(contract, "handoff_artifacts", []):
        lines.append("- handoff:")
        for art in contract.handoff_artifacts:
            lines.append(f"  - `{art}`")
    if task.blocked_by:
        lines.append(f"- blocked_by: {', '.join(task.blocked_by)}")
    if task.evidence:
        refs = getattr(task.evidence, "git_refs", []) or []
        if refs:
            lines.append(f"- evidence: {', '.join(refs)}")
    return lines
