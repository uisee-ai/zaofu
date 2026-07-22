"""合并候选树质量门缺口判定(2026-07-08,controller review ⑤ 选 c)。

r4 实锚(FIX-10 语境):多 lane 写入型 workflow 没配 quality_gates 时,
candidate 合成树**不经任何验证**即发 candidate.ready——跨 lane 类型偏斜
per-lane verify 原理上不可见,judge 照审坏树。validate 的 WARN 连打三轮
无人理(LB-3 教训同型),故多 lane 升 fail-closed:`zf start`/`zf validate`
拒绝,直到配置 quality_gates 或显式豁免
(`workflow.allow_unverified_candidate: true`,观测型运行的合法出口)。

单 lane(如 light 拓扑)无跨 lane 偏斜面,保持 WARN 不拒。
"""

from __future__ import annotations

from typing import Any


def combined_candidate_gate_gap(config: Any, *, flow_kind: str = "") -> str:
    """返回缺口描述(空串 = 无缺口/已豁免/单 lane)。

    A multi-kind Project is a resident container, not an active Refactor Run.
    Its start/validate path therefore defers this gate until Request preflight,
    where ``flow_kind`` selects the stages that can actually produce a
    combined candidate.
    """
    kind = str(flow_kind or "").strip().lower()
    if kind == "feat":
        kind = "prd"
    scoped_metadata = getattr(
        getattr(config, "workflow", None), "flow_metadata_by_kind", {},
    ) or {}
    if not kind and len(scoped_metadata) > 1:
        return ""
    stages = getattr(getattr(config, "workflow", None), "stages", None) or []
    multi_lane = any(
        str(getattr(stage, "topology", "")).startswith("fanout_writer")
        and len(getattr(stage, "roles", None) or []) > 1
        and (
            not kind
            or str(getattr(stage, "flow_kind", "") or "").strip().lower()
            in {"", kind}
        )
        for stage in stages
    )
    if not multi_lane:
        return ""
    if bool(getattr(
        getattr(config, "workflow", None), "allow_unverified_candidate", False,
    )):
        return ""
    source = str(getattr(
        getattr(config, "workflow", None),
        "candidate_quality_source",
        "auto",
    ) or "auto")
    if source == "task_contract_required":
        # Commands do not exist at cold start. Writer Task Map admission owns
        # proving that every candidate slice supplies one before dispatch.
        return ""
    gates = getattr(config, "quality_gates", None) or {}
    for gate in gates.values():
        if not getattr(gate, "enabled", True):
            continue
        checks = [
            str(check).strip()
            for check in (getattr(gate, "required_checks", None) or [])
            if str(check).strip()
        ]
        if not checks:
            continue
        placeholders = [check for check in checks if "TODO" in check]
        if placeholders:
            return (
                "quality_gates 含未填的 TODO 占位命令 "
                f"{placeholders[:2]} — 用项目真实命令替换模板占位后再 start。"
            )
        return ""
    return (
        "multi-lane fanout_writer workflow 未配置 quality_gates — "
        "candidate 合成树不经任何验证即进 judge(跨 lane 偏斜 per-lane "
        "verify 原理上不可见,r4 F10 实锚)。修复:配置 "
        "quality_gates.<name>.required_checks(如 typecheck + 单测),"
        "或配置 workflow.candidate_quality_source=task_contract_required "
        "并由 Task Map 为每个 writer task 声明 verification,"
        "或显式豁免 workflow.allow_unverified_candidate: true(观测型运行)。"
    )


__all__ = ["combined_candidate_gate_gap"]
