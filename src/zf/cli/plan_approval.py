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
from zf.runtime.plan_preview_artifacts import preview_artifact_path


def _pending(events, workflow_run_id: str | None = None) -> list[dict]:
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
    pending = list(state.values())
    if workflow_run_id:
        pending = [
            item for item in pending
            if str(item.get("workflow_run_id") or "") == workflow_run_id
        ]
    return pending


def _checklist(task_map_path: Path) -> list[str]:
    """机械 checklist(R25 监工预检制度化):返回告警行,空=全绿。"""
    try:
        data = json.loads(task_map_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"task_map is not readable: {task_map_path}"]
    from zf.runtime.plan_digest import plan_digest_checklist

    tasks = data.get("tasks") or []
    return [
        f"[X] {check['label']}: {check['detail']}"
        for check in plan_digest_checklist(tasks)
        if not check["ok"]
    ]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "plan", help="Review, approve, or reject a plan",
    )
    parser.add_argument("--state-dir", default=None)
    sub = parser.add_subparsers(dest="plan_cmd", required=True)
    from zf.cli.plan_execution import register as register_execution

    register_execution(sub)
    review = sub.add_parser("review", help="List plans awaiting review with digest and checklist")
    review.add_argument("--workflow-run-id", default=None)
    review.set_defaults(func=_run)
    ap = sub.add_parser("approve", help="Approve a plan and unlock writer fanout")
    ap.add_argument("plan_id")
    ap.set_defaults(func=_run)
    rj = sub.add_parser("reject", help="Reject a plan and request synthesis replan")
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
        pending = _pending(events, getattr(args, "workflow_run_id", None))
        if not pending:
            print("No plans are awaiting review.")
            return 0
        for item in pending:
            plan_id = item.get("plan_id")
            ref = str(item.get("task_map_ref") or "")
            print(f"plan {plan_id}  stage={item.get('stage_id')}  "
                  f"tasks={item.get('task_count')}  pdd={item.get('pdd_id')}")
            print(
                f"  workflow_run_id: {item.get('workflow_run_id') or '(none)'}  "
                f"task_map_generation: {item.get('task_map_generation') or '(none)'}"
            )
            print(f"  task_map: {ref}")
            digest_ref = str(item.get("digest_ref") or "")
            digest = ""
            if digest_ref:
                try:
                    digest = preview_artifact_path(
                        digest_ref, state_dir=state_dir, project_root=context.project_root,
                    ).read_text(encoding="utf-8")
                except (OSError, ValueError) as exc:
                    print(f"  [X] plan digest unavailable: {exc}")
            if digest:
                print(digest.rstrip())
            elif ref:
                try:
                    path = preview_artifact_path(
                        ref, state_dir=state_dir, project_root=context.project_root,
                    )
                    checks = _checklist(path)
                except (OSError, ValueError) as exc:
                    checks = [f"[X] task_map unavailable: {exc}"]
                for line in checks or ["[OK] checklist passed"]:
                    print(f"  {line}")
            print(f"  approve: zf plan approve {plan_id}")
        return 0

    from zf.runtime.plan_approval_state import decide_pending_plan

    decision_payload: dict = {"plan_id": args.plan_id}
    if args.plan_cmd == "reject":
        decision_payload["reason"] = args.reason
    writer = EventWriter(log)
    result, _reason = decide_pending_plan(
        writer,
        state_dir=state_dir,
        plan_id=args.plan_id,
        event_type="plan.approved" if args.plan_cmd == "approve" else "plan.rejected",
        payload=decision_payload,
    )
    if result is None:
        print(f"plan {args.plan_id} is not awaiting review; it was decided or does not exist.")
        return 1
    if args.plan_cmd == "approve":
        print(f"plan {args.plan_id} approved - fanout will start on the next kernel wake.")
        return 0
    print(f"plan {args.plan_id} rejected - synthesis will replan with the supplied reason.")
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
        "note": "Advice is non-binding; the operator makes the decision.",
        "checklist_warnings": warnings,
    }
