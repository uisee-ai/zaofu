"""Fanout context and rebuildable manifest projection."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
REPORT_STATUSES = {"passed", "failed", "blocked", "suspended"}
REPORT_SEVERITIES = {"info", "low", "medium", "high", "critical"}
REPORT_SEVERITY_ALIASES = {
    "blocker": "high",
    "blocking": "high",
    "error": "high",
    "warn": "medium",
    "warning": "medium",
}
REPORT_RECOMMENDATIONS = {"approve", "reject", "needs_rework", "abstain"}
SUCCESS_RECOMMENDATIONS = {"approve"}
FANOUT_TERMINAL_CHILD_STATUSES = {
    "blocked",
    "cancelled",
    "completed",
    "failed",
    "timed_out",
}


@dataclass(frozen=True)
class FanoutChild:
    child_id: str
    role_instance: str
    target_ref: str = ""
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FanoutContext:
    fanout_id: str
    stage_id: str
    topology: str
    trace_id: str
    trigger_event_id: str
    target_ref: str
    expected_children: list[FanoutChild] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        stage_id: str,
        topology: str,
        trace_id: str,
        trigger_event_id: str,
        target_ref: str,
        role_instances: Iterable[str],
    ) -> "FanoutContext":
        safe_stage = _safe_id(stage_id)
        safe_trigger = _safe_id(trigger_event_id)[:12]
        fanout_id = f"fanout-{safe_stage}-{safe_trigger}"
        seen: dict[str, int] = {}
        children: list[FanoutChild] = []
        for role_instance in role_instances:
            child_id = cls.child_id(role_instance, seen.get(role_instance, 0))
            seen[role_instance] = seen.get(role_instance, 0) + 1
            children.append(FanoutChild(
                child_id=child_id,
                role_instance=role_instance,
                target_ref=target_ref,
            ))
        return cls(
            fanout_id=fanout_id,
            stage_id=stage_id,
            topology=topology,
            trace_id=trace_id,
            trigger_event_id=trigger_event_id,
            target_ref=target_ref,
            expected_children=children,
        )

    @staticmethod
    def child_id(role_instance: str, ordinal: int = 0, scope: str = "") -> str:
        base = _safe_id(role_instance)
        if scope:
            base = f"{base}-{_safe_id(scope)}"
        if ordinal:
            base = f"{base}-{ordinal + 1}"
        return base

    def started_event(self, *, actor: str = "zf-cli") -> ZfEvent:
        return ZfEvent(
            type="fanout.started",
            actor=actor,
            payload={
                "fanout_id": self.fanout_id,
                "stage_id": self.stage_id,
                "topology": self.topology,
                "trace_id": self.trace_id,
                "trigger_event_id": self.trigger_event_id,
                "target_ref": self.target_ref,
                "expected_children": [
                    asdict(child) for child in self.expected_children
                ],
            },
            causation_id=self.trigger_event_id,
            correlation_id=self.trace_id,
        )

    def child_dispatched_event(
        self,
        child: FanoutChild,
        *,
        run_id: str,
        actor: str = "zf-cli",
    ) -> ZfEvent:
        return ZfEvent(
            type="fanout.child.dispatched",
            actor=actor,
            payload={
                "fanout_id": self.fanout_id,
                "trace_id": self.trace_id,
                "stage_id": self.stage_id,
                "child_id": child.child_id,
                "run_id": run_id,
                "role_instance": child.role_instance,
                "target_ref": child.target_ref or self.target_ref,
            },
            causation_id=self.trigger_event_id,
            correlation_id=self.trace_id,
        )

    def aggregate_started_event(
        self,
        *,
        mode: str,
        actor: str = "zf-cli",
    ) -> ZfEvent:
        return ZfEvent(
            type="fanout.aggregate.started",
            actor=actor,
            payload={
                "fanout_id": self.fanout_id,
                "trace_id": self.trace_id,
                "stage_id": self.stage_id,
                "mode": mode,
            },
            causation_id=self.trigger_event_id,
            correlation_id=self.trace_id,
        )


@dataclass(frozen=True)
class FanoutReportValidation:
    report: dict
    diagnostics: list[str] = field(default_factory=list)
    valid: bool = True
    synthetic: bool = False


# Pure audit/evidence keys promoted from a child completion payload into its
# report projection. Additive — not part of the event schema contract.
_PURE_AUDIT_FIELD_KEYS = (
    "checks",
    "probes",
    "artifact_refs",
    "evidence_refs",
    "test_refs",
    "e2e_refs",
    "demo_refs",
    "regression_refs",
    "parity_refs",
    "provider_refs",
    "scores",
    "acceptance_evidence_update",
)

# canonical-dag/v3 读者报告契约字段(FIX-14/LB-4)。These are event-schema
# contract fields, historically hand-added here. 2026-07-08 live 轮实锚:verify
# 事件里矩阵 3 行(schema 机械验证过),本白名单漏收 → children/*/report.json
# 投影丢矩阵 → judge 读盘按纪律拒绝 → 烧掉一整轮返工(cb700c32 是此病第 N 次
# 创可贴)。P1-5:report_audit_field_keys() 现在也从 EventSchemaRegistry 派生
# 契约字段,新增契约字段自动流入投影,不必再手加到这里。
_V3_CONTRACT_FALLBACK_KEYS = (
    "requirement_understanding",
    "requirement_coverage_matrix",
    "gap_findings",
    "replan_recommendation",
)

# Static superset — back-compat for any static consumer + forcing-test baseline.
REPORT_AUDIT_FIELD_KEYS = _PURE_AUDIT_FIELD_KEYS + _V3_CONTRACT_FALLBACK_KEYS


def report_audit_field_keys(config, event_type: str) -> tuple[str, ...]:
    """Fields promoted from a child completion payload into its report.json
    projection: the static superset above ∪ the event schema's top-level
    contract fields (required∪non_empty, minus ``report``).

    P1-5 (2026-07-09): the projection promotion used to iterate only the
    hardcoded REPORT_AUDIT_FIELD_KEYS while the briefing *education* side
    (_schema_education_toplevel_fields) derived required/non_empty from the
    EventSchemaRegistry — so every new top-level contract field had to be
    hand-added here or it was silently dropped from children/*/report.json,
    diverging disk projection from event truth. Deriving from the same registry
    keeps the two sides in sync. Superset semantics preserve current behavior
    even when event-schema validation is not configured (rule is None).
    """
    keys = list(REPORT_AUDIT_FIELD_KEYS)
    try:
        from zf.core.verification.event_schema import EventSchemaRegistry

        rule = EventSchemaRegistry.from_config(config).rule_for(event_type)
    except Exception:
        rule = None
    if rule is not None:
        for field_name in (*rule.required, *rule.non_empty):
            if field_name != "report" and field_name not in keys:
                keys.append(field_name)
    return tuple(keys)


def validate_fanout_report(
    raw: object,
    *,
    child_id: str,
    default_status: str = "passed",
    default_recommendation: str = "approve",
    default_summary: str = "",
) -> FanoutReportValidation:
    """Normalize a child/synth report into the canonical fanout report shape."""
    if raw in (None, ""):
        return FanoutReportValidation(
            report={
                "child_id": child_id,
                "status": _safe_choice(default_status, REPORT_STATUSES, "passed"),
                "summary": default_summary,
                "findings": [],
                "recommendation": _safe_choice(
                    default_recommendation,
                    REPORT_RECOMMENDATIONS,
                    "approve",
                ),
            },
            synthetic=True,
        )

    diagnostics: list[str] = []
    if not isinstance(raw, dict):
        diagnostics.append("report must be a JSON object")
        return FanoutReportValidation(
            report=_malformed_report(child_id, diagnostics),
            diagnostics=diagnostics,
            valid=False,
        )

    report_child_id = str(raw.get("child_id") or child_id)
    status = str(raw.get("status") or default_status)
    if status not in REPORT_STATUSES:
        diagnostics.append(
            f"status must be one of {sorted(REPORT_STATUSES)}; got {status!r}"
        )
        status = "failed"

    summary = raw.get("summary", default_summary)
    if not isinstance(summary, str):
        diagnostics.append("summary must be a string")
        summary = str(summary)

    findings: list[dict] = []
    raw_findings = raw.get("findings", [])
    if not isinstance(raw_findings, list):
        diagnostics.append("findings must be a list")
        raw_findings = []
    for index, raw_finding in enumerate(raw_findings):
        if not isinstance(raw_finding, dict):
            diagnostics.append(f"findings[{index}] must be an object")
            continue
        finding = _normalize_finding(raw_finding, index, diagnostics)
        findings.append(finding)

    recommendation = str(raw.get("recommendation") or default_recommendation)
    if recommendation not in REPORT_RECOMMENDATIONS:
        diagnostics.append(
            "recommendation must be one of "
            f"{sorted(REPORT_RECOMMENDATIONS)}; got {recommendation!r}"
        )
        recommendation = "reject"

    report = {
        "child_id": report_child_id,
        "status": status,
        "summary": summary,
        "findings": findings,
        "recommendation": recommendation,
    }
    # 投影必须忠实:归一化字段(上面五个)优先,其余原始键一律透传。
    # 2026-07-08 教训:枚举白名单决定投影字段 = 与 scheme 打地鼠同构——
    # v3 契约刚补 4 字段,下次契约再加字段 children/*/report.json 又丢
    # (事件真相与磁盘投影分叉,judge 读盘按纪律误拒烧返工)。
    # REPORT_AUDIT_FIELD_KEYS 保留给 fanout_evidence_queries 的聚合消费。
    for key, value in raw.items():
        report.setdefault(key, value)
    if diagnostics:
        report["status"] = "failed"
        report["recommendation"] = "reject"
        report["report_diagnostics"] = diagnostics
    return FanoutReportValidation(
        report=report,
        diagnostics=diagnostics,
        valid=not diagnostics,
    )


def recommendation_is_success(recommendation: str) -> bool:
    return recommendation in SUCCESS_RECOMMENDATIONS


class FanoutManifestProjector:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir

    def project_event(self, event_log: EventLog, event: ZfEvent) -> dict | None:
        if not event.type.startswith("fanout."):
            return None
        fanout_id = _payload_str(event.payload, "fanout_id")
        if not fanout_id:
            return None
        return self.write_manifest(fanout_id, event_log.read_all())

    def write_manifest(self, fanout_id: str, events: list[ZfEvent]) -> dict:
        manifest = self.rebuild(fanout_id, events)
        path = self.state_dir / "fanouts" / fanout_id / "manifest.json"
        atomic_write_text(
            path,
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        return manifest

    def rebuild(self, fanout_id: str, events: list[ZfEvent]) -> dict:
        manifest: dict = {
            "fanout_id": fanout_id,
            "trace_id": "",
            "stage_id": "",
            "topology": "",
            "trigger_event_id": "",
            "target_ref": "",
            "pdd_id": "",
            "feature_id": "",
            "task_map_ref": "",
            "source_index_ref": "",
            "task_id": "",
            "channel_id": "",
            "thread_id": "",
            "pattern_id": "",
            "workflow_run_id": "",
            "workflow_input_manifest_ref": "",
            "workflow_prompt_ref": "",
            "prompt_kind": "",
            "trigger_payload": {},
            "source_refs": {},
            "artifact_refs": [],
            "children": [],
            "aggregate_config": {},
            "aggregate": {"status": "pending"},
            "barrier": {"status": "pending", "required_children": []},
            "status": "pending",
        }
        children: dict[str, dict] = {}
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if _payload_str(payload, "fanout_id") != fanout_id:
                continue
            if event.type == "fanout.requested":
                manifest.update({
                    "trace_id": _payload_str(payload, "trace_id") or event.correlation_id or "",
                    "stage_id": _payload_str(payload, "stage_id"),
                    "topology": _payload_str(payload, "topology"),
                    "trigger_event_id": _payload_str(payload, "trigger_event_id"),
                    "target_ref": _payload_str(payload, "target_ref"),
                    "pdd_id": _payload_str(payload, "pdd_id"),
                    "feature_id": _payload_str(payload, "feature_id"),
                    "task_map_ref": _payload_str(payload, "task_map_ref"),
                    "source_index_ref": _payload_str(payload, "source_index_ref"),
                    "task_id": _payload_str(payload, "task_id") or event.task_id or "",
                    "channel_id": _payload_str(payload, "channel_id"),
                    "thread_id": _payload_str(payload, "thread_id"),
                    "pattern_id": _payload_str(payload, "pattern_id"),
                    "workflow_run_id": _payload_str(payload, "workflow_run_id"),
                    "workflow_input_manifest_ref": _payload_str(payload, "workflow_input_manifest_ref"),
                    "workflow_prompt_ref": _payload_str(payload, "workflow_prompt_ref"),
                    "prompt_kind": _payload_str(payload, "prompt_kind"),
                    "source_refs": (
                        dict(payload.get("source_refs"))
                        if isinstance(payload.get("source_refs"), dict)
                        else {}
                    ),
                    "artifact_refs": (
                        list(payload.get("artifact_refs"))
                        if isinstance(payload.get("artifact_refs"), list)
                        else []
                    ),
                    "request_event_id": event.id,
                    "requested_by": _payload_str(payload, "requested_by") or event.actor or "",
                    "source_intent_event_id": _payload_str(payload, "source_intent_event_id")
                    or _payload_str(payload, "workflow_invoke_event_id")
                    or _payload_str(payload, "source_event_id"),
                    "reason": _payload_str(payload, "reason"),
                    "expected_output": _payload_str(payload, "expected_output"),
                    "status": "requested",
                })
            elif event.type == "fanout.started":
                manifest.update({
                    "trace_id": _payload_str(payload, "trace_id") or event.correlation_id or "",
                    "stage_id": _payload_str(payload, "stage_id"),
                    "topology": _payload_str(payload, "topology"),
                    "trigger_event_id": _payload_str(payload, "trigger_event_id"),
                    "target_ref": _payload_str(payload, "target_ref"),
                    "pdd_id": _payload_str(payload, "pdd_id"),
                    "feature_id": _payload_str(payload, "feature_id"),
                    "task_map_ref": _payload_str(payload, "task_map_ref"),
                    "source_index_ref": _payload_str(payload, "source_index_ref"),
                    "task_id": _payload_str(payload, "task_id") or event.task_id or manifest.get("task_id", ""),
                    "channel_id": _payload_str(payload, "channel_id") or manifest.get("channel_id", ""),
                    "thread_id": _payload_str(payload, "thread_id") or manifest.get("thread_id", ""),
                    "pattern_id": _payload_str(payload, "pattern_id") or manifest.get("pattern_id", ""),
                    "workflow_run_id": _payload_str(payload, "workflow_run_id") or manifest.get("workflow_run_id", ""),
                    "workflow_input_manifest_ref": (
                        _payload_str(payload, "workflow_input_manifest_ref")
                        or manifest.get("workflow_input_manifest_ref", "")
                    ),
                    "workflow_prompt_ref": _payload_str(payload, "workflow_prompt_ref") or manifest.get("workflow_prompt_ref", ""),
                    "prompt_kind": _payload_str(payload, "prompt_kind") or manifest.get("prompt_kind", ""),
                    "status": "started",
                })
                if isinstance(payload.get("source_refs"), dict):
                    manifest["source_refs"] = dict(payload.get("source_refs"))
                if isinstance(payload.get("artifact_refs"), list):
                    manifest["artifact_refs"] = list(payload.get("artifact_refs"))
                if isinstance(payload.get("trigger_payload"), dict):
                    manifest["trigger_payload"] = dict(payload.get("trigger_payload"))
                aggregate = payload.get("aggregate")
                if isinstance(aggregate, dict):
                    manifest["aggregate_config"] = {
                        "mode": str(aggregate.get("mode") or ""),
                        "success_event": str(aggregate.get("success_event") or ""),
                        "failure_event": str(aggregate.get("failure_event") or ""),
                        "child_success_event": str(
                            aggregate.get("child_success_event")
                            or "workflow.child.completed"
                        ),
                        "child_failure_event": str(
                            aggregate.get("child_failure_event")
                            or "workflow.child.failed"
                        ),
                        "synth_role": str(aggregate.get("synth_role") or ""),
                        "max_retries": int(aggregate.get("max_retries") or 0),
                        "review_strategy": str(
                            aggregate.get("review_strategy") or ""
                        ),
                        # B3: dedicated synth wait budget (0 → stage timeout)
                        "synth_timeout_seconds": int(
                            aggregate.get("synth_timeout_seconds") or 0
                        ),
                    }
                expected = payload.get("expected_children")
                if isinstance(expected, list):
                    for raw_child in expected:
                        if not isinstance(raw_child, dict):
                            continue
                        child_id = str(raw_child.get("child_id") or "")
                        if not child_id:
                            continue
                        children[child_id] = {
                            "child_id": child_id,
                            "role_instance": str(raw_child.get("role_instance") or ""),
                            "target_ref": str(raw_child.get("target_ref") or manifest["target_ref"]),
                            "payload": (
                                dict(raw_child.get("payload"))
                                if isinstance(raw_child.get("payload"), dict)
                                else {}
                            ),
                            "expected_output": _raw_child_payload_str(raw_child, "expected_output"),
                            "owner_claim": _raw_child_payload_str(raw_child, "owner_claim"),
                            "status": "pending",
                            "run_id": "",
                        }
                        _apply_child_metadata(
                            children[child_id],
                            children[child_id]["payload"],
                        )
            elif event.type == "fanout.child.queued":
                child = _child(children, payload)
                child.update({
                    "status": "queued",
                    "run_id": _payload_str(payload, "run_id"),
                    "role_instance": _payload_str(payload, "role_instance"),
                    "target_ref": _payload_str(payload, "target_ref") or child.get("target_ref", ""),
                    "last_event_id": event.id,
                })
                _apply_child_metadata(child, payload)
            elif event.type == "fanout.slot.assigned":
                manifest.setdefault("slot_events", []).append({
                    "status": "assigned",
                    "child_id": _payload_str(payload, "child_id"),
                    "role_instance": _payload_str(payload, "role_instance"),
                    "lane_id": _payload_str(payload, "lane_id"),
                    "stage_slot": _payload_str(payload, "stage_slot"),
                    "event_id": event.id,
                })
            elif event.type == "fanout.slot.released":
                manifest.setdefault("slot_events", []).append({
                    "status": "released",
                    "child_id": _payload_str(payload, "child_id"),
                    "role_instance": _payload_str(payload, "role_instance"),
                    "lane_id": _payload_str(payload, "lane_id"),
                    "stage_slot": _payload_str(payload, "stage_slot"),
                    "event_id": event.id,
                })
            elif event.type == "fanout.assignment.override":
                manifest.setdefault("assignment_overrides", []).append({
                    "child_id": _payload_str(payload, "child_id"),
                    "from_role_instance": _payload_str(payload, "from_role_instance"),
                    "to_role_instance": _payload_str(payload, "to_role_instance"),
                    "reason": _payload_str(payload, "reason"),
                    "event_id": event.id,
                })
            elif event.type == "fanout.child.dispatched":
                child = _child(children, payload)
                child.update({
                    "status": "dispatched",
                    "run_id": _payload_str(payload, "run_id"),
                    "role_instance": _payload_str(payload, "role_instance"),
                    "target_ref": _payload_str(payload, "target_ref"),
                    "last_event_id": event.id,
                })
                _apply_child_metadata(child, payload)
                dispatched_payload = payload.get("payload")
                if isinstance(dispatched_payload, dict):
                    prior_payload = (
                        child.get("payload")
                        if isinstance(child.get("payload"), dict)
                        else {}
                    )
                    child["payload"] = {**prior_payload, **dispatched_payload}
                    _apply_child_metadata(child, dispatched_payload)
            elif event.type == "fanout.child.completed":
                child = _child(children, payload)
                child.update({
                    "status": _payload_str(payload, "status") or "completed",
                    "run_id": _payload_str(payload, "run_id"),
                    "result_event_id": _payload_str(payload, "result_event_id"),
                    "reason": "", "evidence": {},
                    "last_event_id": event.id,
                })
                _apply_child_metadata(child, payload)
                _apply_report_payload(child, payload)
            elif event.type == "fanout.child.failed":
                child = _child(children, payload)
                child.update({
                    "status": "failed",
                    "run_id": _payload_str(payload, "run_id"),
                    "reason": _payload_str(payload, "reason"),
                    "evidence": payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {},
                    "last_event_id": event.id,
                })
                _apply_child_metadata(child, payload)
                _apply_report_payload(child, payload)
            elif event.type == "fanout.aggregate.started":
                manifest["aggregate"] = {
                    "status": "started",
                    "mode": _payload_str(payload, "mode"),
                    "last_event_id": event.id,
                }
            elif event.type == "fanout.aggregate.completed":
                manifest["aggregate"] = {
                    "status": _payload_str(payload, "status") or "completed",
                    "reason": _payload_str(payload, "reason"),
                    "success_event": _payload_str(payload, "success_event"),
                    "failure_event": _payload_str(payload, "failure_event"),
                    "recommendation": _payload_str(payload, "recommendation"),
                    "synth_event_id": _payload_str(payload, "synth_event_id"),
                    "pdd_id": _payload_str(payload, "pdd_id"),
                    "feature_id": _payload_str(payload, "feature_id"),
                    "task_map_ref": _payload_str(payload, "task_map_ref"),
                    "source_index_ref": _payload_str(payload, "source_index_ref"),
                    "completed_task_ids": payload.get("completed_task_ids", []),
                    "failed_children": payload.get("failed_children", []),
                    "pending_children": payload.get("pending_children", []),
                    "timeout_seconds": payload.get("timeout_seconds", 0),
                    "candidate_status": _payload_str(payload, "candidate_status"),
                    "candidate_ref": _payload_str(payload, "candidate_ref"),
                    "recovered_from_aggregate_status": _payload_str(
                        payload, "recovered_from_aggregate_status"
                    ),
                    "recovered_from_aggregate_reason": _payload_str(
                        payload, "recovered_from_aggregate_reason"
                    ),
                    "last_event_id": event.id,
                }
                for key in ("pdd_id", "feature_id", "task_map_ref", "source_index_ref"):
                    if manifest["aggregate"].get(key) and not manifest.get(key):
                        manifest[key] = manifest["aggregate"][key]
                manifest["status"] = manifest["aggregate"]["status"]
            elif event.type == "fanout.synth.dispatched":
                manifest["synth"] = {
                    "status": "dispatched",
                    "role_instance": _payload_str(payload, "role_instance"),
                    "run_id": _payload_str(payload, "run_id"),
                    "briefing_path": _payload_str(payload, "briefing_path"),
                    "report_paths": payload.get("report_paths", []),
                    "last_event_id": event.id,
                }
                manifest["status"] = "synth_dispatched"
            elif event.type == "fanout.synth.completed":
                report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
                manifest["synth"] = {
                    "status": _payload_str(payload, "status") or "completed",
                    "role_instance": _payload_str(payload, "role_instance"),
                    "run_id": _payload_str(payload, "run_id"),
                    "recommendation": _payload_str(payload, "recommendation")
                    or str(report.get("recommendation") or ""),
                    "summary": _payload_str(payload, "summary")
                    or str(report.get("summary") or ""),
                    "report": report,
                    "last_event_id": event.id,
                }
            elif event.type == "fanout.timed_out":
                manifest["status"] = "timed_out"
                manifest["aggregate"] = {
                    "status": "timed_out",
                    "pending_children": payload.get("pending_children", []),
                    "timeout_seconds": payload.get("timeout_seconds", 0),
                    "last_event_id": event.id,
                }
            elif event.type == "fanout.cancelled":
                manifest["status"] = "cancelled"
                manifest["aggregate"] = {
                    "status": "cancelled",
                    "reason": _payload_str(payload, "reason"),
                    "last_event_id": event.id,
                }
        manifest["children"] = [
            children[key] for key in sorted(children)
        ]
        manifest["planned_children"] = [
            str(child.get("child_id") or "")
            for child in manifest["children"]
            if child.get("child_id")
        ]
        manifest["queued_children"] = [
            str(child.get("child_id") or "")
            for child in manifest["children"]
            if str(child.get("status") or "") == "queued"
        ]
        manifest["dispatched_children"] = [
            str(child.get("child_id") or "")
            for child in manifest["children"]
            if str(child.get("status") or "") == "dispatched"
        ]
        manifest["terminal_children"] = [
            str(child.get("child_id") or "")
            for child in manifest["children"]
            if str(child.get("status") or "") in {"completed", "failed"}
        ]
        reconcile_fanout_manifest_terminal_state(manifest, events)
        manifest["slot_state"] = _slot_state_projection(manifest)
        manifest["barrier"] = _barrier_projection(manifest)
        return manifest


def reconcile_fanout_manifest_terminal_state(
    manifest: dict,
    events: list[ZfEvent],
) -> dict:
    """Mark stale fanout manifests non-blocking after terminal run events.

    This is a read-model reconciliation only: it does not delete historical
    fanout events and does not claim children completed. It prevents stale
    reader/final-judge manifests from keeping a passed run visually blocked.
    """

    aggregate = manifest.get("aggregate") if isinstance(manifest.get("aggregate"), dict) else {}
    failed_event = str(aggregate.get("failure_event") or "")
    if failed_event:
        corrected = _corrected_pass_event(events, failed_event)
        if corrected is not None:
            aggregate = {
                **aggregate,
                "status": "corrected_passed",
                "corrected_by_event_id": corrected.id,
                "corrected_event_type": corrected.type,
            }
            manifest["aggregate"] = aggregate
            manifest["status"] = "corrected_passed"
            manifest["non_blocking"] = True
            manifest["reconciled_by"] = "corrected_terminal"
            return manifest

    if not _has_terminal_run_completed(events):
        return manifest
    status = str(manifest.get("status") or "")
    aggregate_status = str(aggregate.get("status") or "")
    if status in {"completed", "corrected_passed", "cancelled", "timed_out"}:
        return manifest
    if aggregate_status in {"completed", "corrected_passed", "cancelled", "timed_out"}:
        return manifest
    manifest["status"] = "closed"
    manifest["non_blocking"] = True
    manifest["reconciled_by"] = "run.completed"
    manifest["aggregate"] = {
        **aggregate,
        "status": "closed",
        "reason": str(aggregate.get("reason") or "run completed"),
    }
    return manifest


def _corrected_pass_event(events: list[ZfEvent], failed_event_id: str) -> ZfEvent | None:
    for event in events:
        if event.type not in {"judge.passed", "verify.passed", "test.passed", "review.approved"}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("correction_of") or "") == failed_event_id:
            return event
    return None


def _has_terminal_run_completed(events: list[ZfEvent]) -> bool:
    for event in reversed(events):
        if event.type in {
            "task_map.ready",
            "task_map.amended",
            "fanout.started",
            "candidate.ready",
            "verify.failed",
            "judge.failed",
            "run.failed",
        }:
            return False
        if event.type == "run.completed":
            payload = event.payload if isinstance(event.payload, dict) else {}
            return str(payload.get("status") or "passed") == "passed"
    return False


def _child(children: dict[str, dict], payload: dict) -> dict:
    child_id = _payload_str(payload, "child_id") or _payload_str(payload, "child_run")
    if not child_id:
        child_id = _payload_str(payload, "role_instance") or _payload_str(payload, "run_id")
    child_id = child_id or "unknown"
    return children.setdefault(child_id, {
        "child_id": child_id,
        "role_instance": _payload_str(payload, "role_instance"),
        "target_ref": _payload_str(payload, "target_ref"),
        "status": "observed",
        "run_id": _payload_str(payload, "run_id"),
    })


def _apply_report_payload(child: dict, payload: dict) -> None:
    report = payload.get("report")
    if isinstance(report, dict):
        child["report"] = report
        child["report_status"] = str(report.get("status") or "")
        child["recommendation"] = str(report.get("recommendation") or "")
    if _payload_str(payload, "report_path"):
        child["report_path"] = _payload_str(payload, "report_path")
    diagnostics = payload.get("report_diagnostics")
    if isinstance(diagnostics, list):
        child["report_diagnostics"] = diagnostics


def _apply_child_metadata(child: dict, payload: dict) -> None:
    for key in (
        "task_id", "scope", "workdir", "source_branch", "source_commit",
        "task_ref", "pdd_id", "feature_id", "task_map_ref", "source_index_ref",
        "retry_of_run_id", "expected_output", "owner_claim", "risk",
        "assignment_strategy", "lane_profile", "lane_id", "stage_slot",
        "affinity_tag", "pipeline_id", "root_fanout_id",
        "upstream_root_fanout_id", "upstream_fanout_id", "upstream_child_id",
        "upstream_task_id", "upstream_stage_slot", "queue_order", "briefing_path",
        "contract_revision", "task_map_generation", "base_commit",
        "contract_snapshot_ref", "contract_snapshot_digest", "target_snapshot_ref",
        "target_commit", "target_snapshot_digest",
        "operation_id", "parent_operation_id", "request_hash", "attempt_id",
        "result_protocol_mode", "attempt_source_manifest_ref",
        "attempt_source_manifest_digest", "input_consumption_policy_digest",
        "admitted_call_result_digest", "semantic_verdict",
    ):
        value = _payload_str(payload, key)
        if value:
            child[key] = value
    if "attempt" in payload:
        child["attempt"] = payload.get("attempt")
    skills = payload.get("skills")
    if isinstance(skills, list):
        child["skills"] = [str(skill) for skill in skills]
    for key in ("artifact_refs", "evidence_refs"):
        values = payload.get(key)
        if isinstance(values, list):
            child[key] = list(values)
    for key in (
        "attempt_source_manifest", "input_consumption_policy_ref",
        "input_consumption_policy", "admitted_call_result_ref", "control_result_ref",
    ):
        value = payload.get(key)
        if isinstance(value, dict):
            child[key] = dict(value)
    if isinstance(payload.get("required_reads"), list):
        child["required_reads"] = list(payload["required_reads"])
    source_refs = payload.get("source_refs")
    if isinstance(source_refs, dict):
        child["source_refs"] = dict(source_refs)
    for key in ("workflow_run_id", "workflow_input_manifest_ref", "workflow_prompt_ref", "prompt_kind", "channel_id", "thread_id", "pattern_id"):
        value = _payload_str(payload, key)
        if value:
            child[key] = value
    verification_result = payload.get("verification_result")
    if isinstance(verification_result, dict):
        child["verification_result"] = dict(verification_result)


def _barrier_projection(manifest: dict) -> dict:
    aggregate_config = (
        manifest.get("aggregate_config")
        if isinstance(manifest.get("aggregate_config"), dict) else {}
    )
    aggregate = manifest.get("aggregate") if isinstance(manifest.get("aggregate"), dict) else {}
    children = [
        child for child in manifest.get("children", [])
        if isinstance(child, dict)
    ]
    required = [
        str(child.get("child_id") or "")
        for child in children
        if child.get("child_id")
    ]
    terminal = {"completed", "failed"}
    completed = [
        str(child.get("child_id") or "")
        for child in children
        if str(child.get("status") or "") in terminal
    ]
    failed = [
        str(child.get("child_id") or "")
        for child in children
        if str(child.get("status") or "") == "failed"
    ]
    return {
        "status": str(aggregate.get("status") or manifest.get("status") or "pending"),
        "mode": str(aggregate.get("mode") or aggregate_config.get("mode") or "wait_for_all"),
        "required_children": required,
        "completed_children": completed,
        "failed_children": failed or list(aggregate.get("failed_children", []) or []),
        "expected_output": str(manifest.get("expected_output") or ""),
        "success_event": str(
            aggregate.get("success_event") or aggregate_config.get("success_event") or ""
        ),
        "failure_event": str(
            aggregate.get("failure_event") or aggregate_config.get("failure_event") or ""
        ),
        "synth_role": str(aggregate.get("synth_role") or aggregate_config.get("synth_role") or ""),
        "max_retries": int(aggregate_config.get("max_retries") or 0),
        "candidate_ref": str(aggregate.get("candidate_ref") or ""),
        "candidate_status": str(aggregate.get("candidate_status") or ""),
    }


def _slot_state_projection(manifest: dict) -> list[dict]:
    active: dict[tuple[str, str], dict] = {}
    for event in manifest.get("slot_events", []) or []:
        if not isinstance(event, dict):
            continue
        lane_id = str(event.get("lane_id") or "")
        stage_slot = str(event.get("stage_slot") or "")
        if not lane_id or not stage_slot:
            continue
        key = (stage_slot, lane_id)
        if str(event.get("status") or "") == "assigned":
            active[key] = {
                "status": "assigned",
                "lane_id": lane_id,
                "stage_slot": stage_slot,
                "child_id": str(event.get("child_id") or ""),
                "role_instance": str(event.get("role_instance") or ""),
                "event_id": str(event.get("event_id") or ""),
            }
        elif str(event.get("status") or "") == "released":
            active.pop(key, None)
    return [active[key] for key in sorted(active)]


def _raw_child_payload_str(raw_child: dict, key: str) -> str:
    value = raw_child.get(key)
    if value not in (None, ""):
        return str(value)
    payload = raw_child.get("payload")
    if isinstance(payload, dict):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _payload_str(payload: object, key: str) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get(key)
    return str(value) if value not in (None, "") else ""


def _safe_id(value: str) -> str:
    safe = _SAFE_RE.sub("-", value.strip()).strip("-")
    return safe or "unknown"


def _safe_choice(value: str, allowed: set[str], fallback: str) -> str:
    return value if value in allowed else fallback


def _normalize_finding(
    raw: dict,
    index: int,
    diagnostics: list[str],
) -> dict:
    severity = str(raw.get("severity") or "info")
    severity = REPORT_SEVERITY_ALIASES.get(severity, severity)
    if severity not in REPORT_SEVERITIES:
        diagnostics.append(
            f"findings[{index}].severity must be one of "
            f"{sorted(REPORT_SEVERITIES)}; got {severity!r}"
        )
        severity = "info"

    category = raw.get("category", raw.get("type", ""))
    if not isinstance(category, str):
        diagnostics.append(f"findings[{index}].category must be a string")
        category = str(category)

    path = raw.get("path", raw.get("file", ""))
    if not isinstance(path, str):
        diagnostics.append(f"findings[{index}].path must be a string")
        path = str(path)
    line = raw.get("line")
    if line in (None, "") and ":" in path:
        maybe_path, maybe_line = path.rsplit(":", 1)
        if maybe_line.isdigit():
            path = maybe_path
            line = maybe_line

    message = raw.get(
        "message",
        raw.get("summary", raw.get("description", raw.get("reason", ""))),
    )
    if not isinstance(message, str) or not message:
        diagnostics.append(f"findings[{index}].message must be a non-empty string")
        message = str(message)

    finding = {
        "severity": severity,
        "category": category,
        "path": path,
        "message": message,
    }
    if line not in (None, ""):
        try:
            line_int = int(line)
        except (TypeError, ValueError):
            diagnostics.append(f"findings[{index}].line must be a positive integer")
        else:
            if line_int < 1:
                diagnostics.append(f"findings[{index}].line must be a positive integer")
            else:
                finding["line"] = line_int
    return finding


def _malformed_report(child_id: str, diagnostics: list[str]) -> dict:
    return {
        "child_id": child_id,
        "status": "failed",
        "summary": "Malformed fanout report.",
        "findings": [
            {
                "severity": "high",
                "category": "report-schema",
                "path": "",
                "message": "; ".join(diagnostics),
            },
        ],
        "recommendation": "reject",
        "report_diagnostics": diagnostics,
    }
