"""Tier-2 诊断性介入(doc 131 §5「Run Manager 作为监工 session」执行体)。

bizsim r4 终局实锚:judge 五审不收敛期间三次破局(judge 看基线树/浏览器
缓存死锁/修复错路由)全靠人肉监工 attach 诊断——本模块把这一格产品化:

- kernel(本模块 + orchestrator sweep):对不收敛升级信号按 stall 指纹
  判重铸 ``diagnosis.requested``;消费 ``diagnosis.completed`` 的
  needs_owner 结论升级 owner。一指纹一诊,复发不诊断循环。
- 诊断执行体:按需 spawn 的 fanout_reader agent(zf.yaml 配置
  diagnostician 角色 + trigger: diagnosis.requested 的 stage),attach 读
  现场(logs/事件窗/worktree),产出结构化 ``diagnosis.completed``。
- 边界(v1):propose-only——诊断报告不直接执行 proposed_commands;
  route_to_lane 结论经 candidate_rework 的 feedback 管线回流 replan。
"""
from __future__ import annotations

from typing import Any

DIAGNOSIS_REQUESTED = "diagnosis.requested"
DIAGNOSIS_COMPLETED = "diagnosis.completed"
DIAGNOSIS_FAILED = "diagnosis.failed"

NEXT_ACTIONS = ("route_to_lane", "fix_target", "needs_owner")

# 触发面:FIX-15③ 的 judge 不收敛升级 + candidate 返工配额耗尽升级。
_NONCONVERGENCE_REASON = "judge_nonconvergence"
_REWORK_EXHAUSTED_MARKER = "rework exhausted"


def _payload(event: Any) -> dict:
    payload = getattr(event, "payload", None)
    return payload if isinstance(payload, dict) else {}


def stall_fingerprint(payload: dict) -> str:
    """稳定的 stall 指纹:同一停滞形态终身只诊断一次。"""
    reason = str(payload.get("reason") or "")
    if reason == _NONCONVERGENCE_REASON:
        return (
            f"judge-nonconv:{payload.get('stage_id') or ''}"
            f":{payload.get('failure_count') or 0}"
        )
    if _REWORK_EXHAUSTED_MARKER in reason:
        anchor = str(
            payload.get("checkpoint_id")
            or payload.get("task_id")
            or payload.get("target_ref")
            or ""
        )
        return f"rework-exhausted:{anchor}"
    return ""


def plan_diagnosis_requests(events: list) -> list[dict[str, Any]]:
    """扫描升级信号,产出待铸的 diagnosis.requested payload 列表。

    判重:已有同指纹的 requested(无论结果)即不再铸——复发路径是
    needs_owner,不是诊断循环。
    """
    seen: set[str] = set()
    for event in events:
        if getattr(event, "type", "") == DIAGNOSIS_REQUESTED:
            fp = str(_payload(event).get("fingerprint") or "")
            if fp:
                seen.add(fp)
    out: list[dict[str, Any]] = []
    for event in events:
        if getattr(event, "type", "") != "human.escalate":
            continue
        payload = _payload(event)
        fingerprint = stall_fingerprint(payload)
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        chain = payload.get("failure_chain")
        if not isinstance(chain, list):
            chain = [str(payload.get("reason") or "")]
        out.append({
            "fingerprint": fingerprint,
            "source_event_id": str(getattr(event, "id", "") or ""),
            "stage_id": str(payload.get("stage_id") or ""),
            "reason": str(payload.get("reason") or ""),
            "failure_chain": chain,
            # attach 现场指针(frozen-worker 教训:先读 pane mirror/logs)
            "log_hints": [
                "logs/<role_instance>.log",
                "projections/run_status_explain.json",
                "briefings/(对应 stage 最新 briefing)",
            ],
            "report_contract": {
                "next_action": list(NEXT_ACTIONS),
                "required": [
                    "root_cause_hypothesis", "next_action",
                    "attribution_evidence",
                ],
            },
        })
    return out


def plan_needs_owner_escalations(events: list) -> list[dict[str, Any]]:
    """diagnosis.completed 判 needs_owner 的结论升级 owner(一结论一次)。"""
    escalated: set[str] = set()
    for event in events:
        if getattr(event, "type", "") != "human.escalate":
            continue
        payload = _payload(event)
        if str(payload.get("reason") or "") == "diagnosis_needs_owner":
            source = str(payload.get("diagnosis_event_id") or "")
            if source:
                escalated.add(source)
    out: list[dict[str, Any]] = []
    for event in events:
        if getattr(event, "type", "") != DIAGNOSIS_COMPLETED:
            continue
        payload = _payload(event)
        report = payload.get("report")
        report = report if isinstance(report, dict) else {}
        if str(report.get("next_action") or "") != "needs_owner":
            continue
        event_id = str(getattr(event, "id", "") or "")
        if not event_id or event_id in escalated:
            continue
        escalated.add(event_id)
        out.append({
            "reason": "diagnosis_needs_owner",
            "diagnosis_event_id": event_id,
            "fingerprint": str(payload.get("fingerprint") or ""),
            "stage_id": str(payload.get("stage_id") or ""),
            "root_cause_hypothesis": str(
                report.get("root_cause_hypothesis") or "",
            )[:400],
            "attribution_evidence": report.get("attribution_evidence"),
            "blocking_scope": "run",
            "suggested_options": [
                "review diagnosis report", "apply fix manually", "safe_halt",
            ],
        })
    return out


def diagnosis_event_schema_rules() -> dict[str, dict[str, Any]]:
    """诊断结果的 canonical 合约(供项目 event_schemas/预设引用)。"""
    return {
        DIAGNOSIS_COMPLETED: {
            "required": ["fingerprint", "report"],
            "nested": {
                "report": {
                    "required": [
                        "root_cause_hypothesis", "next_action",
                        "attribution_evidence",
                    ],
                    "non_empty": [
                        "root_cause_hypothesis", "attribution_evidence",
                    ],
                    "enum": {"next_action": list(NEXT_ACTIONS)},
                },
            },
        },
    }
