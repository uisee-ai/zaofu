"""zf plan — 人工 plan 审核门 CLI(B14-S5,doc 93 §4)。

review: 列 pending 审批(digest 简表 + 机械 checklist);
approve/reject: emit plan.approved / plan.rejected(actor=operator)。
事件经 EventWriter 落 events.jsonl,kernel wake 后重入孵化(approved)
或回喂 synth replan(rejected,B14-S6)。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter


def _pending(events) -> list[dict]:
    state: dict[str, dict] = {}
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        plan_id = str(payload.get("plan_id") or "")
        if not plan_id:
            continue
        if event.type == "plan.approval.requested":
            state[plan_id] = {**payload, "requested_ts": event.ts}
        elif event.type in ("plan.approved", "plan.rejected"):
            state.pop(plan_id, None)
    return list(state.values())


def _checklist(task_map_path: Path) -> list[str]:
    """机械 checklist(R25 监工预检制度化):返回告警行,空=全绿。"""
    try:
        data = json.loads(task_map_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"task_map 不可读: {task_map_path}"]
    from zf.runtime.plan_digest import plan_digest_checklist

    tasks = data.get("tasks") or []
    return [
        f"[X] {check['label']}: {check['detail']}"
        for check in plan_digest_checklist(tasks)
        if not check["ok"]
    ]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "plan", help="人工 plan 审核门(review/approve/reject,doc 93)",
    )
    parser.add_argument("--state-dir", default=None)
    sub = parser.add_subparsers(dest="plan_cmd", required=True)
    review = sub.add_parser("review", help="列出待审 plan(digest+checklist)")
    review.set_defaults(func=_run)
    ap = sub.add_parser("approve", help="批准 plan,解锁 writer fanout")
    ap.add_argument("plan_id")
    ap.set_defaults(func=_run)
    rj = sub.add_parser("reject", help="驳回 plan,回喂 synth 重拆")
    rj.add_argument("plan_id")
    rj.add_argument("--reason", required=True)
    rj.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> int:
    try:
        explicit_state_dir = (
            getattr(args, "state_dir", None)
            or os.environ.get("ZF_STATE_DIR")
            or None
        )
        context = resolve_project_context(
            explicit_state_dir=explicit_state_dir,
            load_config_with_explicit=True,
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stdout)
        return 1
    state_dir = context.state_dir
    log = event_log_from_project(state_dir, config=context.config)
    events = list(log.read_all())

    if args.plan_cmd == "review":
        pending = _pending(events)
        if not pending:
            print("无待审 plan。")
            return 0
        for item in pending:
            plan_id = item.get("plan_id")
            ref = str(item.get("task_map_ref") or "")
            print(f"plan {plan_id}  stage={item.get('stage_id')}  "
                  f"tasks={item.get('task_count')}  pdd={item.get('pdd_id')}")
            print(f"  task_map: {ref}")
            if ref:
                path = Path(ref)
                if not path.is_absolute():
                    path = Path.cwd() / ref
                for line in _checklist(path) or ["[OK] checklist 全绿"]:
                    print(f"  {line}")
            print(f"  approve: zf plan approve {plan_id}")
        return 0

    pending_ids = {str(p.get("plan_id")) for p in _pending(events)}
    if args.plan_id not in pending_ids:
        print(f"plan {args.plan_id} 不在待审列表(已裁决或不存在)。")
        return 1
    requested = next(
        (item for item in _pending(events) if str(item.get("plan_id")) == args.plan_id),
        {},
    )
    decision_payload = {"plan_id": args.plan_id}
    for key in (
        "stage_id",
        "trace_id",
        "pdd_id",
        "feature_id",
        "task_map_ref",
        "digest_ref",
        "task_count",
    ):
        if requested.get(key):
            decision_payload[key] = requested[key]
    writer = EventWriter(log)
    if args.plan_cmd == "approve":
        writer.append(ZfEvent(
            type="plan.approved",
            actor="operator",
            payload=decision_payload,
            causation_id=args.plan_id,
        ))
        print(f"plan {args.plan_id} approved — fanout 将随 kernel wake 孵化。")
        return 0
    decision_payload["reason"] = args.reason
    writer.append(ZfEvent(
        type="plan.rejected",
        actor="operator",
        payload=decision_payload,
        causation_id=args.plan_id,
    ))
    print(f"plan {args.plan_id} rejected — synth 将携 reason 重拆(replan)。")
    return 0


def review_advice(task_map_path: Path) -> dict:
    """B16 (doc 93 §7.2): kanban agent 预审 — 建议非决策。

    跑机械 checklist 产 verdict 建议;`plan.approved` 的 actor 必须是
    operator(人),agent 只能把这个建议渲染给人看,绝不代点。
    """
    warnings = _checklist(task_map_path)
    return {
        "advice": "reject" if warnings else "approve",
        "binding": False,
        "note": "建议非决策 — 签字是 operator 的(doc 93 §7.1 硬规则)",
        "checklist_warnings": warnings,
    }
