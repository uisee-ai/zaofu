"""Lifecycle observational emitters & liveness evidence — K1 切片 2。

verbatim 迁出(同切片 1 模式):drift/refresh/usage 观测事件发射簇 +
worker 活性证据簇(0325 watchdog 语义所在)。方法体一字未改;
self._* 缓存(去重/冷却/熔断登记)仍由宿主 __init__ 持有,mixin
继承下访问路径不变。
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.owner_channel_liveness import (
    CHANNEL_DEAD,
    CHANNEL_UNKNOWN,
    channel_liveness,
)
from zf.runtime.remediation_cascade import SAFE_HALTED_EVENT


# B-COST-02: consecutive disk-read misses before a claude-code worker's
# usage-capture failure is signalled. Several windows so a transient
# (file mid-write / brief race) or the early-boot observe→cache window
# doesn't false-alarm.
_USAGE_CAPTURE_MISS_THRESHOLD = 3


def _disk_usage_sample_id(
    *,
    actor: str,
    backend: str,
    model: str,
    model_context_window: int,
    usage_timestamp: object,
    usage: dict,
) -> str:
    payload = {
        "actor": actor,
        "backend": backend,
        "model": model,
        "model_context_window": model_context_window,
        "source": "disk_reader",
        "usage": usage,
        "usage_timestamp": usage_timestamp,
    }
    data = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:24]


class LifecycleObservationMixin:
    def _check_refresh_triggers(self) -> None:
        """Compose the 5 refresh trigger types into worker.refresh.triggered
        events. Observation only — actual refresh actions are owned by
        Sprint A's stuck path and Sprint E's recycle path.

        Per-instance dedup via _refresh_already_emitted: each
        (instance_id, reason) only fires once until the orchestrator
        restarts or the underlying counter resets."""
        drift_roles, global_drift = self._recent_drift_refresh_scope()
        for role in self.config.roles:
            if role.name == "orchestrator":
                continue
            instance_id = role.instance_id
            drift_present = (
                global_drift
                or role.name in drift_roles
                or instance_id in drift_roles
            )
            trigger = self._refresh_policy.evaluate(
                turn_count=self._turn_counter.get(instance_id, 0),
                consecutive_failures=self._failure_counter.get(instance_id, 0),
                task_just_completed=False,
                drift_detected=drift_present,
                context_pressure=0.0,  # context recycle owns this signal
            )
            if trigger is None:
                continue
            key = (instance_id, trigger.reason)
            if key in self._refresh_already_emitted:
                continue
            self._refresh_already_emitted.add(key)
            try:
                self.event_writer.append(ZfEvent(
                    type="worker.refresh.triggered",
                    actor=instance_id,
                    payload={
                        "role": role.name,
                        "reason": trigger.reason,
                        "detail": trigger.detail,
                    },
                ))
            except Exception:
                continue

    def _recent_drift_refresh_scope(self) -> tuple[set[str], bool]:
        """Return role-scoped drift refresh targets.

        Historical drift events had no affected_role, so keep them global.
        Node-skip signals emitted after this change carry the specific role and
        should not refresh every pane in the campaign.
        """
        roles: set[str] = set()
        global_drift = False
        try:
            events = self.event_log.read_days(1)
        except Exception:
            return roles, bool(self._drift_last_emit)
        for event in events[-50:]:
            if event.type != "worker.drift.detected":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            affected_role = str(payload.get("affected_role") or "").strip()
            if affected_role:
                roles.add(affected_role)
            else:
                global_drift = True
        if not roles and not global_drift and self._drift_last_emit:
            global_drift = True
        return roles, global_drift

    # -- G-WIRE-2: drift detection observation layer --


    def _check_drift(self) -> None:
        """Run DriftDetector against the recent event tail and emit a
        worker.drift.detected event for each signal, deduped per signal
        via _drift_cooldown_seconds.

        Observation only — does not auto-recycle/respawn. Layer 2 (and
        humans via Feishu) decide what to do with the signal."""
        import time as _time
        try:
            recent = self.event_log.read_days(1)
        except Exception:
            return
        # Take the last 50 events to keep the detector cheap. The detector
        # itself separates decision-shaped events from telemetry; keeping
        # agent.usage here lets node-skip see that an active role is alive.
        events_as_dicts = [
            {
                "type": e.type,
                "task_id": e.task_id or "",
                "actor": e.actor or "",
            }
            for e in recent[-50:]
        ]
        expected_roles = self._active_drift_expected_roles()
        try:
            signals = self._drift_detector.check(
                events_as_dicts, expected_roles=expected_roles,
            )
        except Exception:
            return
        now = _time.time()
        for s in signals:
            last = self._drift_last_emit.get(s.signal, 0.0)
            if now - last < self._drift_cooldown_seconds:
                continue
            self._drift_last_emit[s.signal] = now
            try:
                self.event_writer.append(ZfEvent(
                    type="worker.drift.detected",
                    actor="zf-cli",
                    payload={
                        "signal": s.signal,
                        "severity": s.severity,
                        "detail": s.detail,
                        "recommended_action": s.recommended_action,
                        "affected_role": getattr(s, "affected_role", ""),
                    },
                ))
            except Exception:
                continue

    def _active_drift_expected_roles(self) -> list[str]:
        """Return role names that currently own active work.

        Node-skip drift is meaningful when a role has a live assignment and
        stops appearing in control events. Requiring every configured role to
        appear in every event tail creates false positives in staged pipelines
        where arch/dev/test/judge are intentionally idle during review.
        """
        active_statuses = {"in_progress", "review", "testing", "judge"}
        role_by_instance = {r.instance_id: r for r in self.config.roles}
        role_by_name: dict[str, object] = {}
        for role in self.config.roles:
            role_by_name.setdefault(role.name, role)
        try:
            tasks = self.task_store.list_all()
        except Exception:
            return []
        expected: list[str] = []
        seen: set[str] = set()
        for task in tasks:
            if (
                task.status not in active_statuses
                and not getattr(task, "active_dispatch_id", None)
            ):
                continue
            assignee = task.assigned_to or ""
            if not assignee:
                continue
            if self._heartbeat_worker_finished_current_dispatch(assignee):
                # The writer already emitted a result (dev.build.done) for this
                # task and is awaiting review/integration — legitimately idle in
                # later stages, not "expected active". Without this, node-skip
                # drift fires perpetually for completed writers (R18: 144× "Role
                # dev-lane-X not active"). Same exemption as the stuck fix.
                continue
            role = role_by_instance.get(assignee) or role_by_name.get(assignee)
            if role is None:
                continue
            name = str(getattr(role, "name", ""))
            if name and name != "orchestrator" and name not in seen:
                seen.add(name)
                expected.append(name)
        return expected

    # -- G-RECYCLE-4/5/6/8: context recycle state machine --


    def _synthesize_agent_usage(
        self,
        role: "RoleConfig",
        usage: "UsageReport",
    ) -> bool:
        """Emit an agent.usage event from a disk-read UsageReport so the
        cost tracker gets data for backends that don't go through the
        stream-json SDK path. Dedup via (instance_id, timestamp).

        Returns True only when this usage sample is new enough to drive
        recycle decisions. Reusing a stale session sample after an inline
        recycle can otherwise immediately put the fresh worker back into
        pending_recycle before it has produced a new turn.
        """
        key = (role.instance_id, usage.timestamp)
        if key in self._synth_usage_seen:
            return False
        self._synth_usage_seen.add(key)
        raw = dict(usage.raw or {})
        # CostTracker expects the Claude-style payload shape
        normalised = {
            "input_tokens": raw.get("input_tokens", usage.effective_input_tokens),
            "output_tokens": raw.get("output_tokens", usage.output_tokens),
            "cache_read_input_tokens": raw.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": raw.get("cache_creation_input_tokens", 0),
        }
        task_id = self._active_task_id_for_usage_role(role)
        sample_id = _disk_usage_sample_id(
            actor=role.instance_id,
            backend=role.backend,
            model=usage.model,
            model_context_window=usage.model_context_window,
            usage_timestamp=usage.timestamp,
            usage=normalised,
        )
        event = ZfEvent(
            type="agent.usage",
            actor=role.instance_id,
            task_id=task_id or None,
            payload={
                "task_id": task_id,
                "usage": normalised,
                "source": "disk_reader",
                "context_usage_ratio": round(usage.ratio, 4),
                "ratio": round(usage.ratio, 4),
                "model_context_window": usage.model_context_window,
                # B-COST-01: carry the model id so the cost tracker can pick
                # a per-model rate (disk-reader path has no provider cost).
                "model": usage.model,
                # B-1203-02: tag backend so events.jsonl consumers can
                # split per-backend without a second lookup.
                "backend": role.backend,
                "usage_timestamp": usage.timestamp,
                "usage_sample_id": sample_id,
            },
        )
        try:
            self.event_writer.append(event)
        except Exception:
            return True
        try:
            self._apply_housekeeping(event)
            # E1 fix: prevent double-count when _react_to_events later
            # reads the same event from offset. _apply_housekeeping is
            # idempotent-by-id via this set; the synth + loop double-path
            # otherwise inflates cost.jsonl by 2× (observed in Run 15).
            self._processed_event_ids.add(event.id)
        except Exception:
            pass
        return True

    def _note_usage_capture_miss(
        self, role: "RoleConfig", usage_cwd: str, session_id: str
    ) -> None:
        """B-COST-02: signal a claude-code usage-capture miss.

        Called when ``session_path`` returns None for a worker whose uuid is
        already known (``session_id`` non-empty). Codex is date-bucketed and
        cwd-independent so it isn't path-sensitive; an empty ``session_id`` is
        the legitimate early-boot window (observe hasn't cached the uuid yet).
        Debounced: emits ``cost.usage.capture_miss`` once after
        ``_USAGE_CAPTURE_MISS_THRESHOLD`` consecutive windows so a stuck
        instance leaves a signal in events.jsonl instead of a silent 0 usage.
        Reset on the next successful capture by the caller.
        """
        if role.backend != "claude-code" or not session_id:
            return
        misses = self._usage_capture_misses
        n = misses.get(role.instance_id, 0) + 1
        misses[role.instance_id] = n
        if n != _USAGE_CAPTURE_MISS_THRESHOLD:
            return  # below threshold, or already emitted on the crossing tick
        escaped = "-" + usage_cwd.lstrip("/").replace("/", "-").replace(".", "-")
        event = ZfEvent(
            type="cost.usage.capture_miss",
            actor=role.instance_id,
            payload={
                "backend": role.backend,
                "workdir": usage_cwd,
                "escaped_project_dir": escaped,
                "session_id": session_id,
                "consecutive_misses": n,
                "reason": (
                    "claude session file not found by derived path nor uuid glob"
                ),
            },
        )
        try:
            self.event_writer.append(event)
        except Exception:
            pass

    def _active_task_id_for_usage_role(self, role: "RoleConfig") -> str:
        """Best-effort attribution for disk-reader usage samples."""
        try:
            tasks = self.task_store.list_all()
        except Exception:
            return ""
        instance_id = str(getattr(role, "instance_id", "") or "")
        role_name = str(getattr(role, "name", "") or "")
        if instance_id:
            try:
                active_task = self._active_task_for_instance(instance_id)
            except Exception:
                active_task = None
            if active_task is not None:
                return str(getattr(active_task, "id", "") or "")
        try:
            fanout_events = self.event_log.read_days(1)
        except Exception:
            fanout_events = []
        for task in tasks:
            if str(getattr(task, "status", "") or "") not in {
                "in_progress",
                "review",
                "testing",
            }:
                continue
            task_id = str(getattr(task, "id", "") or "")
            try:
                if (
                    instance_id
                    and self._fanout_task_state_for_instance(
                        instance_id,
                        task_id,
                        events=fanout_events,
                    )
                    == "terminal"
                ):
                    continue
            except Exception:
                pass
            assignee = str(getattr(task, "assigned_to", "") or "")
            if assignee and assignee in {instance_id, role_name}:
                return task_id
        return ""

    def _context_threshold_payload(
        self,
        role: "RoleConfig",
        usage: "UsageReport",
        *,
        session_id: str,
        cached_path: Path | None,
        idle: bool,
        reason: str,
    ) -> tuple[dict, Task | None]:
        active_task = self._active_task_for_instance(role.instance_id)
        dispatch_id = ""
        if active_task is not None:
            dispatch_id = getattr(active_task, "active_dispatch_id", "") or ""
            if not dispatch_id:
                try:
                    dispatch = self._latest_dispatch_event_for_task(active_task.id)
                    payload = dispatch.payload if dispatch is not None else {}
                    if isinstance(payload, dict):
                        dispatch_id = str(payload.get("dispatch_id") or "")
                except Exception:
                    dispatch_id = ""
        snapshot_ref = ""
        if active_task is not None:
            snapshot_ref = self._latest_runtime_snapshot_ref(
                task_id=active_task.id,
                dispatch_id=dispatch_id,
                source="dispatch",
            )
        ratio = round(float(usage.ratio), 4)
        payload = {
            "task_id": active_task.id if active_task is not None else "",
            "dispatch_id": dispatch_id,
            "snapshot_ref": snapshot_ref,
            "role": role.name,
            "instance_id": role.instance_id,
            "backend": role.backend,
            "context_usage_ratio": ratio,
            # Backward-compatible alias for older Web/tests/projections.
            "ratio": ratio,
            "session_ref": session_id or (str(cached_path) if cached_path else ""),
            "source": "session_reader",
            "reason": reason,
            "context_warning_threshold": role.context_warning_threshold,
            "context_compact_threshold": role.context_compact_threshold,
            "context_hard_cap": role.context_hard_cap,
            "effective_tokens": usage.effective_input_tokens,
            "window": usage.model_context_window,
            "model_context_window": usage.model_context_window,
            "idle": idle,
        }
        return payload, active_task

    def _latest_runtime_snapshot_ref(
        self,
        *,
        task_id: str = "",
        dispatch_id: str = "",
        source: str = "",
    ) -> str:
        try:
            events = self.event_log.read_all()
        except Exception:
            events = []
        try:
            from zf.runtime.runtime_snapshot import (
                latest_snapshot_ref_for_dispatch,
            )

            ref = latest_snapshot_ref_for_dispatch(
                events,
                task_id=task_id,
                dispatch_id=dispatch_id,
                source=source,
            )
            if ref:
                return ref
        except Exception:
            pass
        for event in reversed(events):
            if task_id and event.task_id != task_id:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if dispatch_id and str(payload.get("dispatch_id") or "") != dispatch_id:
                continue
            ref = str(payload.get("snapshot_ref") or "")
            if ref:
                return ref
        return ""

    def _context_compact_attempts(self) -> set[str]:
        attempts = getattr(self, "_context_compact_attempted", None)
        if attempts is None:
            attempts = set()
            self._context_compact_attempted = attempts
        return attempts

    def _compact_denial_reason(self, role: "RoleConfig") -> str:
        try:
            output = self.transport.capture_log(role.instance_id, lines=80)
        except Exception:
            return ""
        lower = output.lower()
        if "compact" not in lower:
            return ""
        denial_markers = (
            "disabled while a task is in progress",
            "cannot compact while",
            "compact is disabled",
            "compaction is disabled",
        )
        for marker in denial_markers:
            if marker in lower:
                return marker
        return ""

    def _event_age_seconds(self, ts: str | None) -> float | None:
        if not ts:
            return None
        try:
            parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())
