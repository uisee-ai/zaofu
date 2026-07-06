"""Reader stage 级失败的机械 replan(prod-e2e 2026-07-05 定案)。

问题形状(prd/issue 两流实弹):plan/triage 类 reader stage 的
failure_event(prd.plan.failed / issue.triage.failed)发射后,
rework_routing 的映射需要任务载体,而这类失败发生在任务被接纳之前——
映射空转,流程死端,只能 operator 手工重发 trigger。

机械闭环:failure_event → 重发该 stage 的 trigger 事件(原载荷 +
rework_of / rework_attempt / rework_feedback=findings),reader fanout
照常重开,briefing 管线自带 findings 下发(F2 修复面)。决策成为事件,
可审计可回放;cap 之外升级 human。
"""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent

# stage 级 replan 上限:超过即该升级 human,不许无限重合成
# (r5 教训:机械重试无界 = 烧钱活锁)。
STAGE_REPLAN_CAP = 2


def reader_stage_failure_events(config: Any) -> dict[str, Any]:
    """failure_event → stage 映射(仅 fanout_reader,配置驱动零硬编码)。"""
    out: dict[str, Any] = {}
    for stage in getattr(getattr(config, "workflow", None), "stages", []) or []:
        if str(getattr(stage, "topology", "") or "") != "fanout_reader":
            continue
        aggregate = getattr(stage, "aggregate", None)
        failure = str(
            getattr(stage, "failure_event", "")
            or getattr(aggregate, "failure_event", "")
            or ""
        )
        if failure and failure not in out:
            out[failure] = stage
    return out


def plan_reader_stage_replan(
    config: Any,
    events: list[ZfEvent],
    failure_event: ZfEvent,
) -> tuple[ZfEvent | None, str]:
    """返回 (待 append 的 replan 事件, 说明)。None = 不 replan(附原因)。

    - 幂等:同一 failure 事件只 replan 一次(causation_id 判重);
    - cap:同类 failure 达 STAGE_REPLAN_CAP 次后不再 replan(说明含
      "cap_exhausted",调用方据此升级 human)。
    """
    stage = reader_stage_failure_events(config).get(failure_event.type)
    if stage is None:
        return None, "no_reader_stage_for_failure"
    trigger_type = str(getattr(stage, "trigger", "") or "")
    if not trigger_type:
        return None, "stage_has_no_trigger"
    prior_failures = 0
    for event in events:
        if event.type != failure_event.type:
            continue
        if event.id == failure_event.id:
            continue
        prior_failures += 1
        # 本失败已 replan 过?(replay/echo 安全)
    for event in events:
        if (
            event.type == trigger_type
            and str(event.causation_id or "") == failure_event.id
        ):
            return None, "already_replanned"
    if prior_failures >= STAGE_REPLAN_CAP:
        return None, "cap_exhausted"
    origin_payload: dict[str, Any] = {}
    for event in events:
        if event.type == trigger_type and isinstance(event.payload, dict):
            origin_payload = dict(event.payload)
    failure_payload = (
        failure_event.payload if isinstance(failure_event.payload, dict) else {}
    )
    findings = failure_payload.get("findings")
    if not isinstance(findings, list) or not findings:
        findings = [{
            "severity": "high",
            "message": str(failure_payload.get("reason") or failure_event.type),
        }]
    origin_payload.update({
        "rework_of": str(
            failure_payload.get("trigger_event_id") or failure_event.id
        ),
        "rework_attempt": prior_failures + 1,
        "rework_feedback": findings,
        "reason": (
            f"stage replan {prior_failures + 1}/{STAGE_REPLAN_CAP} after "
            f"{failure_event.type}"
        ),
    })
    return ZfEvent(
        type=trigger_type,
        actor="zf-cli",
        payload=origin_payload,
        causation_id=failure_event.id,
        correlation_id=failure_event.correlation_id or None,
    ), f"replan {getattr(stage, 'id', '')}"


__all__ = [
    "STAGE_REPLAN_CAP",
    "plan_reader_stage_replan",
    "reader_stage_failure_events",
]
