"""Worker liveness evidence — K1 切片 2b(从 observation 再拆,守 ≤500)。

0325 watchdog 语义所在:registry 心跳(mirror 过滤)+ events 活动
allowlist + respawn 熔断登记读取。verbatim,零裁决 —— 裁决(respawn
与否)留在宿主 _capture_logs/_report_stuck_worker。
"""

from __future__ import annotations

import time

from zf.core.events.model import ZfEvent
from zf.runtime.owner_channel_liveness import (
    CHANNEL_DEAD,
    CHANNEL_UNKNOWN,
    channel_liveness,
)
from zf.runtime.remediation_cascade import SAFE_HALTED_EVENT


class LifecycleLivenessEvidenceMixin:
    def _respawn_recent_failure_cooldown_active(self, instance_id: str) -> bool:
        registry = getattr(self, "_respawn_failure_registry", None)
        if registry is None:
            registry = {}
            self._respawn_failure_registry = registry
        entry = registry.get(instance_id)
        if entry is None:
            return False
        count, window_start, cooldown_until = entry
        now = self._now() if hasattr(self, "_now") else 0.0
        if cooldown_until and now < cooldown_until:
            return True
        return False

    def _worker_liveness_stale(self, role: "RoleConfig") -> tuple[bool, str]:
        """Kernel-state liveness staleness (I41, backlog 2026-06-11-0325).

        Inputs are kernel truth only — no pane probe: the heartbeat record
        in role_sessions.yaml, falling back to the instance's latest
        events.jsonl activity. Returns (stale?, basis). No liveness
        evidence at all counts as stale: nothing proves the worker is
        alive, and with an active task that must recover.
        """
        import time as _time
        from datetime import datetime as _dt

        raw_threshold = getattr(role, "stuck_threshold_seconds", None)
        # honor an explicit 0 (tests / aggressive configs); only missing
        # or None falls back to the default
        threshold = 300.0 if raw_threshold is None else float(raw_threshold)
        now = _time.time()
        instance_id = role.instance_id
        try:
            from zf.core.state.role_sessions import RoleSessionRegistry
            registry = RoleSessionRegistry(
                self.state_dir / "role_sessions.yaml",
                project_root=str(self.project_root),
            )
            hb_ts, hb_payload = registry.get_last_heartbeat(instance_id)
        except Exception:
            hb_ts, hb_payload = None, None
        # kernel mirrors are bookkeeping, not the worker proving life:
        # worker.state.changed stamps (e.g. idle-after-respawn) must not
        # mask a dead pane. The task.dispatched seed IS honored — it is
        # the intentional post-dispatch grace window.
        if (
            hb_ts
            and isinstance(hb_payload, dict)
            and hb_payload.get("source") == "worker.state.changed"
        ):
            hb_ts = None
        if hb_ts:
            try:
                age = now - _dt.fromisoformat(str(hb_ts)).timestamp()
                return age > threshold, f"heartbeat_age={int(age)}s"
            except (ValueError, TypeError):
                pass
        # Fallback: latest WORKER-ORIGINATED activity in events.jsonl.
        # Allowlist, not any actor match — kernel-emitted lifecycle events
        # about the worker (pane.dead_observed, state.changed, respawn.*)
        # also carry actor=instance and must not count as liveness proof
        # (the watchdog's own evidence event would otherwise defeat the
        # staleness check forever).
        activity_types = (
            "worker.heartbeat", "worker.progress", "phase.progressed",
            "agent.usage", "agent.text", "agent.tool.use",
        )
        try:
            for event in reversed(list(self.event_log.read_days(1))):
                if getattr(event, "actor", None) != instance_id:
                    continue
                origin = str(getattr(event, "origin", "") or "")
                if origin == "kernel":
                    # 1405:kernel 铸造/镜像事件即使 actor=instance 也
                    # 不是 worker 自证(0325 教训的分类法化)。
                    continue
                etype = str(getattr(event, "type", "") or "")
                if origin == "worker":
                    # origin 优先:worker 自报任何类型都算活性
                    # (自定义事件名项目不再依赖点名集)。
                    age = now - self._event_epoch(event)
                    return age > threshold, f"last_activity_age={int(age)}s"
                # origin 为空 = 历史事件 → 类型 allowlist 兜底(0325 语义)。
                if etype not in activity_types and not etype.startswith(
                    ("codex.hook.", "claude.hook."),
                ):
                    continue
                age = now - self._event_epoch(event)
                return age > threshold, f"last_activity_age={int(age)}s"
        except Exception:
            pass
        return True, "no_liveness_evidence"

    def _consecutive_respawn_failures(self, instance_id: str) -> int:
        """Events-derived consecutive failure count (I41, doc 87 §6 rev4).

        Counts ``worker.respawn.failed`` for this instance since its last
        ``worker.respawned``. The emitter appends the failure event BEFORE
        calling ``_record_respawn_failure``, so the count includes the
        current failure. Events-derived = restart does not lose the count
        (the in-memory registry only caches the active cooldown window).
        """
        try:
            events = self.event_log.read_days(1)
        except Exception:
            return 0
        count = 0
        for event in reversed(list(events)):
            if getattr(event, "actor", None) != instance_id:
                continue
            etype = getattr(event, "type", "")
            if etype == "worker.respawned":
                break
            if etype == "worker.respawn.failed":
                count += 1
        return count

    def _operator_channel_live(self) -> bool:
        """Tier3 liveness: is the owner channel actually reachable?

        Reads recent owner-delivery outcomes — confirmed-dead (R12: token
        invalid, repeated failures, no delivery) → False so the cascade floors
        to safe-halt instead of escalating into the void.

        2026-06-10 review: CHANNEL_UNKNOWN with NO owner channel configured
        in zf.yaml is also not reachable — there is no one for the
        escalation to reach, so escalate-into-void must floor to safe-halt
        in unattended runs (the R12 "286x escalate, nobody received" hole).
        """
        try:
            events = self.event_log.read_days(1)
        except Exception:
            return True
        liveness = channel_liveness(events)
        if liveness == CHANNEL_DEAD:
            return False
        if liveness == CHANNEL_UNKNOWN and not self._owner_channel_configured():
            return False
        return True

    def _owner_channel_configured(self) -> bool:
        """Is any owner-delivery channel wired in zf.yaml?"""
        try:
            integrations = getattr(self.config, "integrations", None)
            bridge = getattr(integrations, "openclaw_feishu_bridge", None)
            return bool(getattr(bridge, "enabled", False))
        except Exception:
            return False

    def _runtime_safe_halted(self) -> bool:
        """True when the latest safe-halt has not been resumed yet.

        2026-06-10 review P1-3: safe_halt previously paused dispatch only;
        the dead-pane watchdog and autoscale kept spawning/respawning every
        tick, so the failure-generating loop continued after the "halt"
        (doc-80: 878 dispatch_skipped during safe-halt).
        """
        try:
            events = self.event_log.read_days(1)
        except Exception:
            return False
        for event in reversed(events):
            if event.type == "dispatch.resumed":
                return False
            if event.type == SAFE_HALTED_EVENT:
                return True
        return False

    def _respawn_success_circuit_active(self, instance_id: str) -> bool:
        opened = getattr(self, "_respawn_success_circuit_opened", set())
        return instance_id in opened

    def _recent_respawn_success_events(self, instance_id: str) -> list[ZfEvent]:
        try:
            events = self.event_log.read_days(1)
        except Exception:
            return []
        now = self._now()
        recent: list[ZfEvent] = []
        for event in events:
            if event.type != "worker.respawned" or event.actor != instance_id:
                continue
            try:
                age = now - self._event_epoch(event)
            except Exception:
                age = 0.0
            if age <= self._RESPAWN_SUCCESS_WINDOW_SECONDS:
                recent.append(event)
        return recent

