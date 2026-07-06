"""Projections layer: common (moved verbatim from web/server.py)."""
from __future__ import annotations

from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from zf.core.config.schema import ZfConfig
from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.workspace import WorkspaceProject
from zf.core.workspace import stable_project_id
from zf.web.headless_agent import canonical_headless_backend
import hashlib
import json
import os
import subprocess


class _GitResult:
    def __init__(self, text: str = "", error: str = "", truncated: bool = False) -> None:
        self.text = text
        self.error = error
        self.truncated = truncated


def _resolve_project_root_for_state(
    state_dir: Path,
    project_root: Path | None = None,
) -> Path:
    if project_root is not None:
        return Path(project_root).resolve()
    # Legacy test/helper fallback only. CLI and production entry points pass
    # the zf.yaml project root explicitly through create_app().
    return state_dir.parent


def _snapshot_cache_seconds() -> float:
    raw = os.environ.get("ZF_WEB_SNAPSHOT_CACHE_SECONDS", "2").strip()
    try:
        value = float(raw)
    except ValueError:
        return 2.0
    return max(0.0, value)


def _default_project_id(*, config: ZfConfig | None, project_root: Path) -> str:
    name = config.project.name if config is not None and config.project.name else project_root.name
    return stable_project_id(name=name, root=project_root)


def _default_workspace_project(
    *,
    project_id: str,
    state_dir: Path,
    config: ZfConfig | None,
    project_root: Path,
    last_opened_at: str = "",
) -> WorkspaceProject:
    configured_name = (
        config.project.name
        if config is not None and config.project.name
        else project_root.name
    )
    display_name = str(os.environ.get("ZF_WORKSPACE_PROJECT_DISPLAY_NAME") or "").strip()
    name = display_name or configured_name
    return WorkspaceProject(
        project_id=project_id,
        name=name,
        root=str(project_root.resolve()),
        config_path=str((project_root / "zf.yaml").resolve()),
        state_dir_hint=str(state_dir.resolve()),
        last_opened_at=last_opened_at,
    )


def _active_workspace_project_id(
    items: list[dict[str, Any]],
    *,
    default_project_id: str,
) -> str:
    # chat-e2e F1: last_opened_at is GLOBAL registry state shared by every
    # server on this host — another server opening its project must not steer
    # this server's fresh sessions away from the project it was started for.
    # A server with a default project always reports itself as active;
    # global recency only decides in workspace-only mode.
    if default_project_id:
        return default_project_id
    opened = [
        item for item in items
        if str(item.get("last_opened_at") or "").strip()
    ]
    if opened:
        return str(max(
            opened,
            key=lambda item: str(item.get("last_opened_at") or ""),
        ).get("project_id") or "")
    return str(items[0].get("project_id") or "") if items else ""


def _no_default_project_payload() -> dict[str, Any]:
    return {
        "ok": False,
        "status": "no_default_project",
        "reason": "web server was started in workspace-only mode without a default project",
    }


def _artifact_ref_warnings_from_events(task_events: list[tuple[int, ZfEvent]]) -> list[dict]:
    warnings = []
    for _seq, event in task_events:
        if event.type != "task.ref.rejected":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        warning = str(payload.get("fallback_warning") or "").strip()
        if not warning:
            continue
        warnings.append({
            "type": "artifact_manifest_missing",
            "severity": "warning",
            "message": warning,
            "trigger_event_id": str(payload.get("trigger_event_id") or ""),
            "detected_artifacts": [
                str(item)
                for item in payload.get("detected_artifacts", [])
                if str(item).strip()
            ] if isinstance(payload.get("detected_artifacts"), list) else [],
            "required_action": str(payload.get("required_action") or ""),
            "event_id": event.id,
        })
    return warnings


def _first_artifact_ref_path(refs: list[dict], kinds: set[str]) -> str:
    try:
        from zf.runtime.artifact_manifest import normalize_artifact_kind
    except Exception:  # pragma: no cover
        def normalize_artifact_kind(value: str) -> str:  # type: ignore
            return str(value or "").strip()

    for ref in refs:
        if normalize_artifact_kind(str(ref.get("kind") or "")) in kinds:
            path = str(ref.get("path") or "").strip()
            if path:
                return path
    return ""


def _deep_kanban_enabled() -> bool:
    return os.environ.get("ZF_WEB_DEEP_KANBAN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _cost(state_dir: Path) -> dict:
    cost_path = state_dir / "cost.jsonl"
    if not cost_path.exists():
        return {"total_usd": 0.0, "per_role": {}}
    try:
        tracker = CostTracker(cost_path)
        per_role = tracker.per_role_totals()
        return {
            "total_usd": round(tracker.total_usd(), 4),
            "per_role": {
                role: {
                    "usd": round(s.total_usd, 4),
                    "input_tokens": s.input_tokens,
                    "output_tokens": s.output_tokens,
                    "entries": s.entries,
                }
                for role, s in per_role.items()
            },
        }
    except Exception:
        return {"total_usd": 0.0, "per_role": {}}


def _empty_queue_role_summary(role: str) -> dict:
    return {
        "role": role,
        "waiting_task_count": 0,
        "ready_task_count": 0,
        "needs_attention_count": 0,
        "oldest_ready_age_seconds": None,
        "available_capacity": 0,
        "effective_workers": 0,
        "next_expected_event": "",
    }


def _age_seconds(value: str | None, *, now: datetime) -> float:
    if not value:
        return 0.0
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return round(max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds()), 1)
    except Exception:
        return 0.0


def _parent_role_from_instance(instance_id: str) -> str:
    if "-auto-" in instance_id:
        return instance_id.split("-auto-", 1)[0] or instance_id
    base, sep, suffix = instance_id.rpartition("-")
    if sep and suffix.isdigit() and base:
        return base
    return instance_id


def _instance_origin(
    instance_id: str,
    meta: dict,
    *,
    configured: bool,
) -> str:
    raw = str(meta.get("origin") or "").strip()
    if raw in {"static", "autoscale", "runtime"}:
        return raw
    if "-auto-" in instance_id:
        return "autoscale"
    if configured:
        return "static"
    return "runtime"


def _attention_state_needs_operator(attention_state: str) -> bool:
    return attention_state not in {
        "",
        "idle",
        "working",
        "completed_verified",
    }


def _clear_context_attention(record: dict) -> None:
    if record.get("attention_state") in {"context_warning", "context_critical"}:
        record.pop("attention_state", None)
        record.pop("needs_input_reason", None)
    record.pop("context_usage_ratio", None)


def _derive_lifecycle_state(
    runtime_state: str,
    *,
    active_task: str,
    signal: dict,
) -> str:
    state = runtime_state.lower()
    if state in {"stopped", "stopping"}:
        return "stopped"
    if state in {"crashed", "failed", "error"}:
        return "failed"
    if "recycl" in state:
        return "recycling" if state == "recycling" else "pending_recycle"
    if state in {"starting", "created"}:
        return "starting"
    if state in {"blocked", "waiting", "needs_input", "input_required"}:
        return "healthy"
    if active_task or state in {"busy", "working", "running", "running_task"}:
        return "running_task"
    if state in {"idle", "healthy", "active"}:
        return "idle"
    if signal.get("last_event_seq"):
        return "healthy"
    return "unknown"


def _derive_attention_state(
    *,
    lifecycle_state: str,
    runtime_state: str,
    active_task: str,
    signal: dict,
) -> str:
    signaled = str(signal.get("attention_state") or "")
    if signaled:
        return signaled
    state = runtime_state.lower()
    if lifecycle_state == "stopped":
        return "stopped_resumable"
    if lifecycle_state == "failed":
        return "failed_resumable"
    if state in {"blocked", "waiting", "needs_input", "input_required"}:
        return "needs_input"
    if state in {"done", "completed"} and active_task:
        return "completed_unverified"
    if active_task or lifecycle_state == "running_task":
        return "working"
    return "idle"


def _allowed_worker_actions(
    *,
    origin: str,
    lifecycle_state: str,
    attention_state: str,
    active_task: str,
) -> list[str]:
    actions = ["peek", "logs", "attach"]
    if _attention_state_needs_operator(attention_state):
        actions.append("reply")
    if lifecycle_state in {"stopped", "failed", "dead"}:
        actions.append("respawn")
    if attention_state in {"context_warning", "context_critical"}:
        actions.append("recycle_after_task")
    if origin == "autoscale":
        actions.append("drain")
    if active_task:
        actions.extend(["open_task", "open_evidence", "request_review"])
    return actions


def _attention_from_provider_stop_reason(reason: str) -> str:
    lowered = reason.lower()
    if "rate" in lowered:
        return "blocked_rate_limit"
    if "auth" in lowered or "login" in lowered or "credential" in lowered:
        return "blocked_auth"
    if "permission" in lowered or "review" in lowered or "approval" in lowered:
        return "blocked_permission"
    if "completed" in lowered:
        return "completed_unverified"
    return "needs_input"


def _payload_first_string(payload: dict, keys: list[str]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _payload_context_ratio(payload: dict) -> float | None:
    for key in (
        "context_usage_ratio",
        "usage_ratio",
        "ratio",
        "context_ratio",
    ):
        value = payload.get(key)
        if isinstance(value, int | float):
            return float(value)
    used = payload.get("used_tokens")
    window = payload.get("window_tokens") or payload.get("context_window_tokens")
    if isinstance(used, int | float) and isinstance(window, int | float) and window:
        return round(float(used) / float(window), 4)
    usage = payload.get("usage")
    if isinstance(usage, dict):
        nested = _payload_context_ratio(usage)
        if nested is not None:
            return nested
    return None


def _briefing_paths_for_instance(state_dir: Path, instance_id: str) -> list[Path]:
    root = state_dir / "briefings"
    if not root.exists() or not instance_id:
        return []
    matches = [
        path for path in root.glob("*.md")
        if path.name.startswith(f"{instance_id}-") or instance_id in path.name
    ]
    return sorted(matches)[-8:]


def _display_path(
    state_dir: Path,
    path: Path,
    project_root: Path | None = None,
) -> str:
    root = _resolve_project_root_for_state(state_dir, project_root)
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _canonical_operator_backend(value: Any) -> str:
    backend = str(value or "").strip()
    if backend == "claude":
        return "claude-code"
    if backend in {"deterministic", "codex", "claude-code"}:
        return backend
    headless = canonical_headless_backend(backend)
    if headless:
        return headless
    return ""


def _action_payload(payload: dict) -> dict:
    out = dict(payload)
    out.pop("idempotency_key", None)
    out.pop("request_id", None)
    return out


def _payload_hash(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _message_allows_create_task_proposal(message: str) -> bool:
    """Whether the operator explicitly asked to create trackable work.

    Empty input preserves helper/test compatibility. Runtime calls pass the
    original user message so read-only turns cannot promote example JSON into
    a real create-task proposal.
    """
    text = str(message or "").strip().lower()
    if not text:
        return True
    explicit_phrases = (
        "create task",
        "create a task",
        "create issue",
        "create ticket",
        "create backlog",
        "add task",
        "add a task",
        "new task",
        "make a task",
        "turn this into a task proposal",
        "make this a task proposal",
        "convert this into a task proposal",
        "track this",
        "track it",
        "创建任务",
        "创建一个任务",
        "新建任务",
        "新增任务",
        "建任务",
        "建个任务",
        "建一个任务",
        "生成任务",
        "生成backlog",
        "生成 backlog",
        "整理成一个 task proposal",
        "整理成 task proposal",
        "整理为 task proposal",
        "整理成任务提案",
        "整理为任务提案",
        "创建backlog",
        "创建 backlog",
        "新增backlog",
        "新增 backlog",
        "建卡",
        "开卡",
        "建工单",
        "创建工单",
        "跟踪这个问题",
        "跟踪这个bug",
        "跟踪这个 bug",
    )
    return any(phrase in text for phrase in explicit_phrases)


def _scope_entry_is_path_like(entry: object) -> bool:
    """A writer-fanout scope entry is a path or glob, never a prose sentence.

    Path scopes are consumed as globs downstream; whitespace or the absence of
    any path marker (``/``, ``*``, or a short file extension) means the entry
    is prose, not a path.
    """
    text = str(entry or "").strip()
    if not text or any(ch.isspace() for ch in text):
        return False
    has_extension = _has_short_file_extension(text)
    if any("一" <= ch <= "鿿" for ch in text):
        # CJK prose carries no whitespace, so "修改src/core下的文件" would
        # otherwise pass on its "/" alone. Require a stronger path signal.
        return "*" in text or has_extension
    if "/" in text or "*" in text:
        return True
    return has_extension


def _has_short_file_extension(text: str) -> bool:
    dot = text.rfind(".")
    return 0 < dot < len(text) - 1 and text[dot + 1:].isalnum() and len(text) - dot - 1 <= 5


# LLMs drift on the verification field name (chat-e2e F3: an `acceptance`
# list was silently dropped and the task landed with no acceptance criteria).
_CONTRACT_VERIFICATION_SYNONYMS = (
    "acceptance",
    "acceptance_criteria",
    "verification_steps",
    "criteria",
)


def _task_contract_field_names() -> frozenset[str]:
    import dataclasses

    from zf.core.task.schema import TaskContract

    return frozenset(f.name for f in dataclasses.fields(TaskContract))


def _contract_text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item).strip() for item in value if str(item or "").strip())
    return str(value or "").strip()


def normalize_proposed_task_contract(payload: dict[str, Any]) -> dict[str, Any]:
    """Coerce an LLM-authored task contract into the shapes the kernel stores.

    The kanban agent (a headless LLM) tends to emit ``verification`` as a list
    of prose steps — the contract field is a single string, so ``str([...])``
    would persist an ugly Python-list repr — and ``scope`` as a prose sentence,
    which writer fanout would then silently gate on as an unmatchable path glob
    (the class of downstream break behind the racing-e2e task_map finding).
    Join list verifications into readable lines and keep only path-like scope
    entries, preserving any prose scope in ``notes`` rather than dropping it.
    Field-name drift (``acceptance`` et al) maps into ``verification``, and any
    remaining unknown contract keys are preserved in ``notes`` instead of being
    silently dropped by the schema (chat-e2e F3).
    """
    if not isinstance(payload, dict):
        return payload
    contract = payload.get("contract")
    if not isinstance(contract, dict):
        return payload
    payload = dict(payload)
    contract = dict(contract)
    verification = contract.get("verification")
    if isinstance(verification, (list, tuple)):
        contract["verification"] = "\n".join(
            str(item).strip() for item in verification if str(item or "").strip()
        )
    if not str(contract.get("verification") or "").strip():
        for synonym in _CONTRACT_VERIFICATION_SYNONYMS:
            text = _contract_text(contract.pop(synonym, None))
            if text:
                contract["verification"] = text
                break
    scope = contract.get("scope")
    if scope is not None:
        entries = list(scope) if isinstance(scope, (list, tuple)) else [scope]
        paths: list[str] = []
        prose: list[str] = []
        for entry in entries:
            text = str(entry or "").strip()
            if not text:
                continue
            (paths if _scope_entry_is_path_like(text) else prose).append(text)
        contract["scope"] = paths
        if prose:
            _append_note(payload, "scope(non-path): " + "; ".join(prose))
    known = _task_contract_field_names()
    for key in [k for k in contract if k not in known]:
        text = _contract_text(contract.pop(key))
        if text:
            _append_note(payload, f"contract.{key}(unmapped): {text}")
    payload["contract"] = contract
    return payload


def _append_note(payload: dict[str, Any], note: str) -> None:
    existing = str(payload.get("notes") or "").strip()
    payload["notes"] = f"{existing}\n{note}" if existing else note


def _message_allows_idea_to_product_proposal(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return True
    explicit_phrases = (
        "idea to product",
        "productize",
        "build this product",
        "ship this product",
        "from idea to product",
        "从 idea 到产品",
        "从想法到产品",
        "跑成产品",
        "做成产品",
        "完整交付",
        "从0到交付",
        "从 0 到交付",
        "触发工作流",
        "启动工作流",
    )
    return any(phrase in text for phrase in explicit_phrases)


def _is_lifecycle_probe_request(payload: dict, message: str) -> bool:
    mode = str(payload.get("mode") or "").strip().lower()
    if mode in {"lifecycle_probe", "kanban_agent_lifecycle_probe"}:
        return True
    lowered = message.lower()
    if "kanban agent lifecycle probe" in lowered:
        return True
    wants_create = (
        "create" in lowered
        or "add" in lowered
        or "创建" in message
        or "新增" in message
    )
    mentions_task = "task" in lowered or "任务" in message
    mentions_sequence = (
        "backlog" in lowered
        and "in_progress" in lowered
        and "done" in lowered
    )
    return wants_create and mentions_task and mentions_sequence


def _emit_action_completed(
    writer: EventWriter,
    *,
    requested: ZfEvent,
    event: ZfEvent,
    action: str,
    requested_action: str,
    status: str,
    task_id: str | None,
    extra: dict | None = None,
) -> None:
    payload = {
        "action": action,
        "requested_action": requested_action,
        "status": status,
        **(extra or {}),
    }
    writer.emit(
        "runtime.action.completed",
        actor="web",
        task_id=task_id,
        causation_id=event.id,
        correlation_id=event.correlation_id or requested.correlation_id,
        payload=payload,
    )
    writer.emit(
        "web.action.completed",
        actor="web",
        task_id=task_id,
        causation_id=event.id,
        correlation_id=event.correlation_id or requested.correlation_id,
        payload=payload,
    )


def _action_failed(
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    task_id: str | None,
    reason: str,
    status_code: int = 422,
    status: str = "failed",
) -> dict:
    payload = {
        "action": action,
        "requested_action": requested_action,
        "reason": reason,
    }
    event = writer.emit(
        "runtime.action.failed",
        actor="web",
        task_id=task_id,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload=payload,
    )
    writer.emit(
        "web.action.failed",
        actor="web",
        task_id=task_id,
        causation_id=event.id,
        correlation_id=event.correlation_id,
        payload=payload,
    )
    return {
        "_status_code": status_code,
        "ok": False,
        "status": status,
        "action": action,
        "requested_action": requested_action,
        "reason": reason,
        "event_id": event.id,
    }


def _optional_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _read_jsonl_dicts(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _append_jsonl(path: Path, item: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")


def _raw_event_has_task_id(line: str, task_id: str) -> bool:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return False
    if isinstance(data, dict) and "event" in data and "sig" in data:
        data = data.get("event")
    if not isinstance(data, dict):
        return False
    return data.get("task_id") == task_id


def _cost_by_instance(state_dir: Path) -> dict[str, dict]:
    cost_path = state_dir / "cost.jsonl"
    if not cost_path.exists():
        return {}
    try:
        totals = CostTracker(cost_path).per_instance_totals()
    except Exception:
        return {}
    return {
        instance_id: {
            "usd": round(summary.total_usd, 4),
            "input_tokens": summary.input_tokens,
            "output_tokens": summary.output_tokens,
            "entries": summary.entries,
        }
        for instance_id, summary in totals.items()
    }


def _empty_cost_summary() -> dict:
    return {
        "usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "entries": 0,
    }


def _git(
    cwd: Path,
    args: list[str],
    *,
    max_bytes: int = 30_000,
    timeout: float = 5,
) -> _GitResult:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        return _GitResult(error=str(exc))

    text = completed.stdout
    error = completed.stderr.strip()[:1_000] if completed.returncode else ""
    encoded = text.encode("utf-8", errors="replace")
    truncated = len(encoded) > max_bytes
    if truncated:
        text = encoded[:max_bytes].decode("utf-8", errors="replace")
    return _GitResult(text=text, error=error, truncated=truncated)


def _git_branch_or_ref(cwd: Path) -> str:
    if not (cwd / ".git").exists():
        return ""
    result = _git(cwd, ["rev-parse", "--abbrev-ref", "HEAD"])
    return result.text.strip() if not result.error else ""


def _git_commit(cwd: Path) -> str:
    if not (cwd / ".git").exists():
        return ""
    result = _git(cwd, ["rev-parse", "--short", "HEAD"])
    return result.text.strip() if not result.error else ""


def _git_dirty(cwd: Path) -> bool:
    if not (cwd / ".git").exists():
        return False
    result = _git(cwd, ["status", "--porcelain"], max_bytes=12_000)
    return bool(result.text.strip()) if not result.error else False


def _payload_ref(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        if key in payload:
            return payload[key]
        for value in payload.values():
            found = _payload_ref(value, key)
            if found not in (None, ""):
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _payload_ref(value, key)
            if found not in (None, ""):
                return found
    return None


# RF-8: fanout/candidate 投影的 per-event 键集(与 _REF_EVENT_KEYS 分开 —
# 那个喂 _refs_from_events/git_refs 输出,扩键会改其语义)。两视图各自
# fingerprint 缓存+折叠,DFS 只在 build/fold 时发生。
_TOPOLOGY_EVENT_KEYS = frozenset({
    "fanout_id",
    "fanout",
    "parent_run",
    "topology",
    "stage_id",
    "target_ref",
    "trace_id",
    "pdd_id",
    "status",
    "child_id",
    "child_run",
    "child",
    "candidate_id",
    "candidate_ref",
    "candidate_branch",
    "branch",
})

_REF_EVENT_KEYS = frozenset({
    "base_commit",
    "base_ref",
    "source_commit",
    "commit",
    "task_ref",
    "task_branch",
    "worker_branch",
    "branch",
    "candidate_ref",
    "candidate_branch",
    "candidate_id",
    "pdd_id",
    "feature_id",
    "fanout_id",
    "child_id",
    "run_id",
    "workdir",
    "source_branch",
    "task_map_ref",
    "source_index_ref",
    "lane_id",
    "affinity_tag",
    "assignment_strategy",
    "role_instance",
    "instance_id",
})


def _payload_collect(node: Any, keys) -> dict:
    # One DFS resolving many keys at once, equivalent to calling _payload_ref
    # per key but walking the payload a single time. Refs extraction over a
    # large log otherwise re-walks every payload once per key (O(events*keys)).
    out: dict = {}
    if isinstance(node, dict):
        for key in keys:
            if key in node:
                out[key] = node[key]
        remaining = [k for k in keys if k not in out]
        for value in node.values():
            if not remaining:
                break
            child = _payload_collect(value, remaining)
            for k in list(remaining):
                found = child.get(k)
                if found not in (None, ""):
                    out[k] = found
                    remaining.remove(k)
    elif isinstance(node, list):
        remaining = list(keys)
        for value in node:
            if not remaining:
                break
            child = _payload_collect(value, remaining)
            for k in list(remaining):
                found = child.get(k)
                if found not in (None, ""):
                    out[k] = found
                    remaining.remove(k)
    return out


def _payload_mentions(payload: Any, needle: str) -> bool:
    if not needle:
        return False
    return needle.lower() in _payload_search_text(payload)


def _payload_search_text(payload: Any) -> str:
    """Lowered dump used by _payload_mentions — compute once per event when
    matching many needles against the same payload (O(events x tasks) dumps
    otherwise; 57k dumps per _kanban on the r2 archive)."""
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        text = str(payload)
    return text.lower()


def _first_nonempty(values: list[Any]) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _read_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _positive_int(
    value: object,
    *,
    default: int,
    minimum: int = 1,
    maximum: int = 1000,
) -> int:
    try:
        parsed = int(value) if value not in {None, ""} else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _is_failed_event(event: object) -> bool:
    event_type = getattr(event, "type", "")
    if any(token in event_type for token in ("failed", "rejected", "error")):
        return True
    payload = getattr(event, "payload", {}) or {}
    return _payload_mentions(payload, "failed") or _payload_mentions(payload, "rejected")


def _is_blocked_event(event: object) -> bool:
    event_type = getattr(event, "type", "")
    if "blocked" in event_type or "suspend" in event_type:
        return True
    payload = getattr(event, "payload", {}) or {}
    return _payload_mentions(payload, "blocked")


def _parse_search_query(q: str) -> tuple[dict[str, str], list[str]]:
    filters: dict[str, str] = {}
    terms: list[str] = []
    for token in q.split():
        if ":" in token:
            key, value = token.split(":", 1)
            if key in {"task", "trace", "actor", "type", "commit", "ref"} and value:
                filters[key] = value
                continue
        if token:
            terms.append(token)
    return filters, terms


def _matches_task_filters(task: dict, filters: dict[str, str]) -> bool:
    if filters.get("task") and filters["task"] != task.get("id"):
        return False
    if filters.get("actor") and filters["actor"] != task.get("assigned_to"):
        return False
    if filters.get("type"):
        return False
    return True


def _matches_event_filters(event: object, filters: dict[str, str]) -> bool:
    if filters.get("task") and filters["task"] != getattr(event, "task_id", None):
        return False
    if filters.get("trace") and filters["trace"] != getattr(event, "correlation_id", None):
        if not _payload_mentions(getattr(event, "payload", {}) or {}, filters["trace"]):
            return False
    if filters.get("actor") and filters["actor"] != getattr(event, "actor", None):
        return False
    if filters.get("type") and filters["type"] != getattr(event, "type", None):
        return False
    if filters.get("commit") and not _payload_mentions(
        getattr(event, "payload", {}) or {},
        filters["commit"],
    ):
        return False
    if filters.get("ref") and not _payload_mentions(
        getattr(event, "payload", {}) or {},
        filters["ref"],
    ):
        return False
    return True


def _read_events_with_seq(
    path: Path,
    event_log: EventLog,
) -> list[tuple[int, object]]:
    out = []
    seq = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        seq += 1
        event = event_log.decode_line(line)
        if event is None or event.type == "event.malformed":
            continue
        out.append((seq, event))
    return out


# Cache non-blank line counts by (mtime_ns, size) so a single snapshot request
# that asks for the seq several times (snapshot light calls it 2x, plus the
# runtime sub-projection) re-reads the 15MB+ events.jsonl at most once, and a
# steady (non-appending) project pays nothing on repeat requests. Keyed by path
# string; bounded by the number of distinct event logs (≈ project count).
_LINE_COUNT_CACHE: dict[str, tuple[int, int, int]] = {}


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        stat = path.stat()
    except OSError:
        return 0
    key = str(path)
    cached = _LINE_COUNT_CACHE.get(key)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    # Stream line-by-line instead of read_text().splitlines(): same non-blank
    # line semantics (a final line without a trailing newline still counts) but
    # O(1) memory — no 15MB string + 10k-element list per call.
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    _LINE_COUNT_CACHE[key] = (stat.st_mtime_ns, stat.st_size, count)
    return count
