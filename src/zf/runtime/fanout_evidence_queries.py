"""Fanout evidence queries — K1 切片 4(2026-06-11)。

从 orchestrator.py verbatim 迁出的 fanout/runtime 只读查询与数据
提取簇:manifest/child/affinity/任务项读取、payload 提取工具、
路径越权评估(纯评估,裁决留宿主)。模式同前三片。
"""

from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.runtime.transport import DispatchContext


def _fanout_report_evidence_fallback(payload: dict[str, object]) -> list[str]:
    """Return deterministic evidence refs for pure aggregate/synth reports."""
    refs: list[str] = []
    for key in (
        "report_paths",
        "child_report_paths",
        "source_report_paths",
        "input_report_paths",
    ):
        value = payload.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            ref = str(item or "").strip()
            if ref and ref not in refs:
                refs.append(ref)
    return refs


class FanoutEvidenceQueriesMixin:
    def _dispatch_context(
        self,
        *,
        role: RoleConfig,
        briefing_path: Path,
        task_id: str | None = None,
        trace_id: str | None = None,
    ) -> DispatchContext:
        return DispatchContext(
            trace_id=trace_id or self._trace_id_for_task(task_id),
            run_id=self._current_run_id(),
            task_id=task_id,
            role_name=role.name,
            instance_id=role.instance_id,
            backend=role.backend,
            briefing_path=briefing_path,
            dispatch_id=getattr(self, "_active_dispatch_ids", {}).get(task_id or "", ""),
        )

    def _current_run_id(self) -> str | None:
        try:
            return self.session_store.load().session_id
        except Exception:
            return None

    def _trace_id_for_task(self, task_id: str | None) -> str | None:
        if not task_id:
            return None
        try:
            events = self.event_log.read_all()
        except Exception:
            return None
        for event in reversed(events):
            if event.task_id == task_id and event.correlation_id:
                return event.correlation_id
        return None

    # -- event reactions --

    def _role_backends(self) -> dict[str, str]:
        """1204: role_type → backend map built from config. Used by
        cost housekeeping so record_usage gets the backend dimension."""
        return {r.name: r.backend for r in self.config.roles}

    def _fanout_identity_stale_reason(
        self,
        fanout_id: str,
    ) -> tuple[str, str]:
        if not fanout_id:
            return "", ""
        try:
            from zf.runtime.fanout_identity import fanout_current_status

            status = fanout_current_status(
                self.event_log.read_all(),
                fanout_id,
            )
        except Exception:
            return "", ""
        if status.current:
            return "", ""
        return (
            status.stale_reason or "fanout_instance_not_current",
            status.superseded_by,
        )

    def _fanout_identity_current_sibling(self, fanout_id: str) -> dict | None:
        """BF-1:同 logical_key 的当前代实例(跨代收编目标)。"""
        try:
            from zf.runtime.fanout_identity import current_sibling_instance

            return current_sibling_instance(
                self.event_log.read_all(),
                fanout_id,
            )
        except Exception:
            return None

    def _fanout_child_idle_threshold(self, child: dict) -> float:
        """Idle (no-progress) deadline for a fanout child, reusing the child
        role's ``stuck_threshold_seconds`` — the same "no output for N seconds =
        stuck" notion the heartbeat sweep applies to persistent workers. Returns
        0.0 (idle detection off) when the role is unknown or its threshold is 0.
        """
        role_instance = str(child.get("role_instance") or "")
        role = next(iter(self._fanout_roles([role_instance])), None)
        if role is None:
            return 0.0
        threshold = float(getattr(role, "stuck_threshold_seconds", 0.0) or 0.0)
        if str(getattr(role, "backend", "") or "") in {
            "claude", "claude-code", "codex",
        }:
            try:
                lease_grace = float(
                    getattr(self.config.workflow, "attempt_lease_grace_s", 900.0)
                    or 900.0
                )
            except (TypeError, ValueError):
                lease_grace = 900.0
            threshold = max(threshold, lease_grace)
        return threshold

    def _fanout_child_last_activity(
        self, child: dict, events: list[ZfEvent], baseline_epoch: float
    ) -> float:
        """Epoch of the child's last sign of life: the most recent event it is
        the actor of (agent.usage / worker.state.changed / its own results),
        floored at the dispatch epoch so a freshly-dispatched child that has not
        emitted anything yet is not treated as instantly idle.
        """
        role_instance = str(child.get("role_instance") or "")
        latest = baseline_epoch
        if role_instance:
            for event in events:
                if event.actor == role_instance:
                    epoch = self._event_epoch(event)
                    if epoch > latest:
                        latest = epoch
        return latest

    @staticmethod
    def _event_epoch(event: ZfEvent) -> float:
        from datetime import datetime

        try:
            return datetime.fromisoformat(event.ts).timestamp()
        except ValueError:
            return 0.0

    def _fanout_child_payloads(self, manifest: dict) -> list[dict]:
        payloads: list[dict] = []
        for child in manifest.get("children", []) or []:
            if not isinstance(child, dict):
                continue
            payload = dict(child)
            result_payload = self._read_fanout_child_result_payload(
                str(manifest.get("fanout_id") or ""),
                str(child.get("child_id") or ""),
            )
            payload.update(result_payload)
            payloads.append(payload)
        return payloads

    def _read_fanout_child_result_payload(
        self,
        fanout_id: str,
        child_id: str,
    ) -> dict:
        if not fanout_id or not child_id:
            return {}
        try:
            import json

            path = (
                self.state_dir
                / "fanouts"
                / fanout_id
                / "children"
                / child_id
                / "result.json"
            )
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        payload = data.get("payload") if isinstance(data, dict) else {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _fanout_aggregate_started(manifest: dict) -> bool:
        aggregate = manifest.get("aggregate")
        return isinstance(aggregate, dict) and bool(aggregate.get("last_event_id"))

    @staticmethod
    def _fanout_synth_dispatched(manifest: dict) -> bool:
        synth = manifest.get("synth")
        return isinstance(synth, dict) and synth.get("status") in {
            "dispatched",
            "completed",
            "failed",
        }

    def _fanout_started(self, stage_id: str, trigger_event_id: str) -> bool:
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        for event in events:
            if event.type != "fanout.started" or not isinstance(event.payload, dict):
                continue
            if (
                event.payload.get("stage_id") == stage_id
                and event.payload.get("trigger_event_id") == trigger_event_id
            ):
                return True
        return False

    def _equivalent_rework_fanout_started(
        self,
        stage_id: str,
        trigger_event: ZfEvent,
    ) -> bool:
        payload = trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
        rework_of = str(payload.get("rework_of") or "").strip()
        if not rework_of:
            return False
        key = (
            rework_of,
            str(payload.get("pdd_id") or payload.get("feature_id") or "").strip(),
            str(payload.get("task_map_ref") or "").strip(),
        )
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        task_map_keys: dict[str, tuple[str, str, str]] = {}
        for event in events:
            if event.type != "task_map.ready" or not isinstance(event.payload, dict):
                continue
            event_rework_of = str(event.payload.get("rework_of") or "").strip()
            if not event_rework_of:
                continue
            task_map_keys[event.id] = (
                event_rework_of,
                str(
                    event.payload.get("pdd_id")
                    or event.payload.get("feature_id")
                    or ""
                ).strip(),
                str(event.payload.get("task_map_ref") or "").strip(),
            )
        for event in events:
            if event.type != "fanout.started" or not isinstance(event.payload, dict):
                continue
            if event.payload.get("stage_id") != stage_id:
                continue
            trigger_id = str(event.payload.get("trigger_event_id") or "")
            if trigger_id == trigger_event.id:
                continue
            if task_map_keys.get(trigger_id) == key:
                return True
        return False

    def _equivalent_task_map_writer_fanout_started(
        self,
        stage_id: str,
        trigger_event: ZfEvent,
    ) -> bool:
        payload = trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
        if any(
            payload.get(key)
            for key in (
                "rework_of",
                "rework_attempt",
                "operator_authorized",
                "amend_of",
                "gap_plan_ref",
                "resume_scope",
            )
        ):
            return False
        task_map_ref = str(payload.get("task_map_ref") or "").strip()
        if not task_map_ref:
            return False
        key = (
            str(payload.get("pdd_id") or payload.get("feature_id") or "").strip(),
            task_map_ref,
            str(payload.get("target_ref") or "").strip(),
        )
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        task_map_keys: dict[str, tuple[str, str, str]] = {}
        for event in events:
            if event.id == trigger_event.id:
                continue
            if event.type != "task_map.ready" or not isinstance(event.payload, dict):
                continue
            event_payload = event.payload
            if any(
                event_payload.get(field)
                for field in (
                    "rework_of",
                    "rework_attempt",
                    "operator_authorized",
                    "amend_of",
                    "gap_plan_ref",
                    "resume_scope",
                )
            ):
                continue
            event_task_map_ref = str(event_payload.get("task_map_ref") or "").strip()
            if not event_task_map_ref:
                continue
            task_map_keys[event.id] = (
                str(
                    event_payload.get("pdd_id")
                    or event_payload.get("feature_id")
                    or ""
                ).strip(),
                event_task_map_ref,
                str(event_payload.get("target_ref") or "").strip(),
            )
        for event in events:
            if event.type != "fanout.started" or not isinstance(event.payload, dict):
                continue
            if event.payload.get("stage_id") != stage_id:
                continue
            trigger_id = str(event.payload.get("trigger_event_id") or "")
            if trigger_id == trigger_event.id:
                continue
            existing_key = task_map_keys.get(trigger_id)
            if existing_key is None:
                continue
            if existing_key == key or existing_key[:2] == key[:2]:
                return True
        return False

    def _fanout_roles(self, targets: list[str]) -> list[RoleConfig]:
        out: list[RoleConfig] = []
        seen: set[str] = set()
        for target in targets:
            matches = [
                role for role in self.config.roles
                if role.instance_id == target
            ] or [
                role for role in self.config.roles
                if role.name == target
            ]
            for role in matches:
                if role.instance_id not in seen:
                    seen.add(role.instance_id)
                    out.append(role)
        return out

    def _fanout_stage_by_id(self, stage_id: str):
        for stage in getattr(self.config.workflow, "stages", []):
            if getattr(stage, "id", "") == stage_id:
                return stage
        return None

    @staticmethod
    def _fanout_assignment_strategy(stage) -> str:
        assignment = getattr(stage, "assignment", None)
        return str(getattr(assignment, "strategy", "") or "static_index")

    def _fanout_affinity_profile(self, stage):
        assignment = getattr(stage, "assignment", None)
        profile_id = str(getattr(assignment, "lane_profile", "") or "")
        if not profile_id:
            return None
        profiles = getattr(self.config.workflow, "affinity_lanes", {}) or {}
        profile = profiles.get(profile_id) if isinstance(profiles, dict) else None
        return profile

    def _fanout_affinity_lane_roles(self, stage) -> list[tuple[str, RoleConfig]]:
        assignment = getattr(stage, "assignment", None)
        stage_slot = str(getattr(assignment, "stage_slot", "") or "")
        profile = self._fanout_affinity_profile(stage)
        if profile is None or not stage_slot:
            return []
        out: list[tuple[str, RoleConfig]] = []
        for lane in getattr(profile, "lanes", []) or []:
            target = str(getattr(lane, stage_slot, "") or "")
            if not target:
                continue
            roles = self._fanout_roles([target])
            if roles:
                out.append((str(getattr(lane, "id", "") or ""), roles[0]))
        return out

    def _fanout_affinity_lane_role(
        self,
        stage,
        *,
        lane_id: str,
        stage_slot: str = "",
    ) -> RoleConfig | None:
        assignment = getattr(stage, "assignment", None)
        slot = stage_slot or str(getattr(assignment, "stage_slot", "") or "")
        profile = self._fanout_affinity_profile(stage)
        if profile is None or not lane_id or not slot:
            return None
        for lane in getattr(profile, "lanes", []) or []:
            if str(getattr(lane, "id", "") or "") != lane_id:
                continue
            target = str(getattr(lane, slot, "") or "")
            if not target:
                return None
            return next(iter(self._fanout_roles([target])), None)
        return None

    def _fanout_affinity_key(self, stage) -> str:
        profile = self._fanout_affinity_profile(stage)
        return str(getattr(profile, "affinity_key", "") or "affinity_tag")

    @staticmethod
    def _fanout_payload_metadata_value(
        payload: dict,
        child: dict | None,
        key: str,
    ) -> str:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
        if child is not None:
            value = child.get(key)
            if value not in (None, ""):
                return str(value)
            child_payload = child.get("payload")
            if isinstance(child_payload, dict):
                value = child_payload.get(key)
                if value not in (None, ""):
                    return str(value)
        return ""

    def _fanout_pdd_id(self, event: ZfEvent) -> str:
        if isinstance(event.payload, dict):
            for key in ("pdd_id", "feature_id"):
                value = str(event.payload.get(key) or "")
                if value:
                    return value
        return event.task_id or "default"

    def _task_ref_entry(self, task_id: str) -> dict:
        if not task_id:
            return {}
        try:
            import json

            data = json.loads(
                (self.state_dir / "refs" / "task-index.json").read_text(
                    encoding="utf-8",
                )
            )
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        entry = data.get(task_id)
        return entry if isinstance(entry, dict) else {}

    def _fanout_manifest(self, fanout_id: str) -> dict:
        try:
            import json

            path = self.state_dir / "fanouts" / fanout_id / "manifest.json"
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _fanout_child(manifest: dict, child_id: str) -> dict | None:
        for child in manifest.get("children", []) or []:
            if isinstance(child, dict) and child.get("child_id") == child_id:
                return child
        return None

    def _fanout_child_report(
        self,
        *,
        child_id: str,
        event: ZfEvent,
        success: bool,
    ):
        from zf.runtime.fanout import report_audit_field_keys, validate_fanout_report

        payload = event.payload if isinstance(event.payload, dict) else {}
        # P1-5: derive the promotion set from the event schema (superset of the
        # static keys) so a new top-level contract field flows into the report
        # projection automatically, matching the briefing education side.
        audit_keys = report_audit_field_keys(
            getattr(self, "config", None), event.type,
        )
        raw_report = payload.get("report")
        if isinstance(raw_report, dict):
            raw_report = dict(raw_report)
            for key in audit_keys:
                if key not in raw_report and key in payload:
                    raw_report[key] = payload[key]
        else:
            raw_report = None
        result = validate_fanout_report(
            raw_report,
            child_id=child_id,
            default_status="passed" if success else "failed",
            default_recommendation="approve" if success else "reject",
            default_summary=str(payload.get("summary") or payload.get("reason") or ""),
        )
        for key in audit_keys:
            if key not in result.report and key in payload:
                result.report[key] = payload[key]
        if "evidence_refs" not in result.report:
            inherited = _fanout_report_evidence_fallback(payload)
            if inherited:
                result.report["evidence_refs"] = inherited
        return result

    def _fanout_reports(self, manifest: dict) -> list[dict]:
        reports: list[dict] = []
        for child in manifest.get("children", []) or []:
            if not isinstance(child, dict):
                continue
            row = {
                "child_id": str(child.get("child_id") or ""),
                "role_instance": str(child.get("role_instance") or ""),
                "status": str(child.get("status") or ""),
                "report_path": str(child.get("report_path") or ""),
                "report": child.get("report") if isinstance(child.get("report"), dict) else {},
                "report_diagnostics": (
                    child.get("report_diagnostics")
                    if isinstance(child.get("report_diagnostics"), list)
                    else []
                ),
                "operation_id": str(child.get("operation_id") or ""),
                "request_hash": str(child.get("request_hash") or ""),
                "result_protocol_mode": str(child.get("result_protocol_mode") or "shadow"),
                "admitted_call_result_ref": (
                    dict(child["admitted_call_result_ref"])
                    if isinstance(child.get("admitted_call_result_ref"), dict)
                    else {}
                ),
                "control_result_ref": (
                    dict(child["control_result_ref"])
                    if isinstance(child.get("control_result_ref"), dict)
                    else {}
                ),
            }
            if (
                row["result_protocol_mode"] in {"warning", "blocking"}
                and row["admitted_call_result_ref"]
            ):
                from zf.runtime.call_result_runtime import hydrate_admitted_control_result

                row["control_result"] = hydrate_admitted_control_result(
                    self.state_dir,
                    row["admitted_call_result_ref"],
                )
            reports.append(row)
        return reports

    def _fanout_child_idle_grace_active(
        self,
        child: dict,
        *,
        dispatch_epoch: float,
        idle_threshold: float,
        now: float,
    ) -> bool:
        """E5/F15: thinking backend 的闲置宽限(与 worker 侧
        _provider_stuck_grace_active 同语义)。

        codex xhigh 深读大文件的单回合可安静 5-15 分钟且零事件输出;
        avbs-r5 实测 reader 默认 300s 阈值在派发后 429s 即被误杀(pane
        实际存活)。宽限 = 自派发起 max(idle_threshold, 配置宽限);
        宽限外仍按事件沉默判闲置(真死的 child 最终会被收割)。
        131-P2-3:宽限值走 workflow.attempt_lease_grace_s(出厂 900s)。
        """
        role_instance = str(child.get("role_instance") or "")
        role = next(iter(self._fanout_roles([role_instance])), None)
        if role is None or getattr(role, "backend", "") not in {
            "claude", "claude-code", "codex",
        }:
            return False
        lease_grace = 900.0
        try:
            lease_grace = float(getattr(
                getattr(self.config, "workflow", None),
                "attempt_lease_grace_s",
                900.0,
            ) or 900.0)
        except (TypeError, ValueError):
            pass
        grace = max(float(idle_threshold), lease_grace)
        return (now - dispatch_epoch) < grace
