"""α-3 (2026-05-17): periodic heartbeat sweep over role_sessions.yaml.

Builds on α-2 (`worker.heartbeat` protocol). Each kernel tick (default
~60s), classify every registered instance by:

  - never heartbeated         → noop (not actively dispatched yet)
  - busy + age > silent_threshold → emit `worker.probe.silent`
  - busy + age > stuck_threshold  → escalate to `worker.stuck`
                                     (existing B-NEW-15 path)
  - idle (per last heartbeat) → candidate for proactive dispatch
                                 (caller may pick next backlog item)

Pure / testable: this module just classifies and returns a result.
The orchestrator wraps the call, decides which events to emit, and
runs proactive dispatch when the backlog is non-empty.

Backlog: see backlogs/2026-05-17-1447-zero-touch-alpha-2-3-heartbeat-
and-proactive-dispatch.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from zf.core.state.role_sessions import RoleSessionRegistry


# Defaults tuned to cangjie r-next-9 observations:
# - claude-code typical 60s heartbeat cadence
# - tool calls > 30s are common, so 90s is the first "missing heartbeat"
# - 180s = 3 missed → escalate to stuck (existing handler), replaces the
#   B-NEW-15 pure 4-min wall-clock fallback with a real-signal trigger.
_DEFAULT_SILENT_THRESHOLD_S = 90.0
_DEFAULT_STUCK_THRESHOLD_S = 180.0


@dataclass(frozen=True)
class HeartbeatSweepResult:
    """Outcome of one sweep pass over role_sessions.yaml."""
    silent_instances: list[str] = field(default_factory=list)
    stuck_instances: list[str] = field(default_factory=list)
    idle_instances: list[str] = field(default_factory=list)
    # Diagnostics: every instance examined and its computed age in seconds.
    examined: dict[str, float | None] = field(default_factory=dict)


def _parse_ts(value) -> datetime | None:
    """Best-effort ISO8601 parse. Returns None on failure (never raises)."""
    if not isinstance(value, str) or not value:
        return None
    # Python 3.11 fromisoformat accepts most ISO variants including the
    # 'Z' suffix; pre-strip the 'Z' for safety on older runtimes.
    text = value
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def sweep_heartbeats(
    *,
    registry: RoleSessionRegistry,
    now: datetime | None = None,
    silent_threshold_s: float = _DEFAULT_SILENT_THRESHOLD_S,
    stuck_threshold_s: float = _DEFAULT_STUCK_THRESHOLD_S,
    stuck_thresholds_s: dict[str, float] | None = None,
) -> HeartbeatSweepResult:
    """Classify every instance in ``registry`` by its last heartbeat.

    Idempotent + side-effect-free. The orchestrator decides what to do
    with the result (typically: emit worker.probe.silent + worker.stuck
    events, run proactive dispatch on idle_instances when backlog has
    ready items).
    """
    sweep_now = now or datetime.now(timezone.utc)
    silent: list[str] = []
    stuck: list[str] = []
    idle: list[str] = []
    examined: dict[str, float | None] = {}

    # Iterate every known instance (registered via get_or_create at some
    # point during the project's lifetime).
    for instance_id in sorted(registry._meta.keys()):
        meta = registry._meta.get(instance_id, {})
        kernel_ts_raw = meta.get("last_heartbeat_at")
        payload = meta.get("last_heartbeat_payload") or {}
        state = ""
        if isinstance(payload, dict):
            state = str(payload.get("state") or "").strip().lower()

        if not kernel_ts_raw:
            # Registered but never heartbeated → not actively dispatched.
            examined[instance_id] = None
            continue

        ts = _parse_ts(kernel_ts_raw)
        if ts is None:
            # Malformed timestamp; defensive — never crash.
            examined[instance_id] = None
            continue

        # ZF-LIVENESS-FOLD-01(07-17 UISSE 实弹):worker.heartbeat 依赖
        # LLM 自觉,整轮可为 0;而 last_action_ts 随 agent.usage /
        # codex.hook 客观更新(dev-lane-0 一轮 499 条 hook 证明活着,
        # 却因零自觉心跳被判 stuck ×5 → escalate → quiescent)。
        # 活性 = max(自觉心跳, 客观动作)——对 LLM worker,一切依赖
        # 其自觉的信号必须有客观折算兜底。
        action_ts = _parse_ts(payload.get("last_action_ts")) if isinstance(payload, dict) else None
        if action_ts is not None and action_ts > ts:
            ts = action_ts

        age = (sweep_now - ts).total_seconds()
        examined[instance_id] = age

        if state == "idle":
            # Idle workers may stop emitting frequent heartbeats. The
            # silence is expected; the signal is "ready to take work".
            idle.append(instance_id)
            continue

        if state not in {"busy", "blocked"}:
            # Other states (recycling / respawning / etc.) — leave to
            # the existing per-state handlers; sweep doesn't touch them.
            continue

        instance_stuck_threshold_s = stuck_threshold_s
        if stuck_thresholds_s and instance_id in stuck_thresholds_s:
            try:
                instance_stuck_threshold_s = float(
                    stuck_thresholds_s[instance_id]
                )
            except (TypeError, ValueError):
                instance_stuck_threshold_s = stuck_threshold_s

        if age >= instance_stuck_threshold_s:
            stuck.append(instance_id)
        elif age >= silent_threshold_s:
            silent.append(instance_id)

    return HeartbeatSweepResult(
        silent_instances=silent,
        stuck_instances=stuck,
        idle_instances=idle,
        examined=examined,
    )
