"""zf issue — issue/bug 第三入口 CLI(B11,doc 92 §4)。

ingest: issue-candidate md(frontmatter 机器面)→ TaskContract 入
kanban(verification = repro 红→绿)。admission 同 gate:根路径只许
assembly 类持有;缺 repro/allowed_paths fail-closed。
validate: 只校验不写状态。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.config.project_context import resolve_project_context
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "issue", help="issue/bug 第三入口(候选 ingest,doc 92)",
    )
    sub = parser.add_subparsers(dest="issue_cmd", required=True)
    for name, help_text in (
        ("validate", "只校验候选,不写状态"),
        ("ingest", "候选 → TaskContract 入 kanban"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("path")
        cmd.add_argument("--state-dir", default=None)
        cmd.set_defaults(func=_run)


def _extract_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("候选缺 YAML frontmatter(issue-candidate.v1)")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("frontmatter 未闭合")
    data = yaml.safe_load(text[4:end + 1])
    if not isinstance(data, dict):
        raise ValueError("frontmatter 不是映射")
    return data


def _validate(fm: dict) -> list[str]:
    """fail-closed 校验(doc 92 §4):返回错误行,空=通过。"""
    errors: list[str] = []
    if str(fm.get("schema") or "") != "issue-candidate.v1":
        errors.append("schema 必须是 issue-candidate.v1")
    for key in ("bug_id", "dedupe_key", "title"):
        if not str(fm.get(key) or "").strip():
            errors.append(f"{key} 必填")
    if not str(fm.get("repro_command") or "").strip():
        errors.append("repro_command 必填(prose 复现描述不是验收)")
    allowed = [str(p) for p in fm.get("allowed_paths") or [] if str(p).strip()]
    if not allowed:
        errors.append("allowed_paths 必填(写权范围,admission 消费)")
    owner_class = str(fm.get("root_owner_class") or "none")
    for path in allowed:
        if "/" not in path.strip("/") and owner_class != "assembly":
            errors.append(
                f"根级路径 {path!r} 只许 root_owner_class=assembly 持有"
                "(R25 ISSUE-002 语义)"
            )
    return errors


def _run(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"error: 候选文件不存在: {path}", file=sys.stderr)
        return 2
    try:
        fm = _extract_frontmatter(path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    errors = _validate(fm)
    if errors:
        for line in errors:
            print(f"[X] {line}", file=sys.stderr)
        return 1
    bug_id = str(fm["bug_id"])
    print(f"issue {bug_id}: {fm['title']}")
    print(f"  repro: {fm['repro_command']}")
    print(f"  scope: {fm['allowed_paths']}")
    if args.issue_cmd == "validate":
        print("[OK] validate 通过")
        return 0

    ctx = resolve_project_context(
        explicit_state_dir=getattr(args, "state_dir", None),
    )
    state_dir = ctx.state_dir
    if not state_dir.exists():
        print(f"error: state dir {state_dir} 不存在", file=sys.stderr)
        return 2
    task_store = TaskStore(state_dir / "kanban.json")
    if task_store.get(bug_id) is not None:
        print(f"[OK] 幂等: task {bug_id} 已存在,不重复 ingest")
        return 0
    contract = TaskContract(
        behavior=str(fm.get("title") or ""),
        scope=[str(p) for p in fm.get("allowed_paths") or []],
        verification=str(fm.get("repro_command") or ""),
        acceptance=(
            f"repro 红→绿: {fm.get('repro_command')}; "
            f"expected: {fm.get('expected') or 'n/a'}"
        ),
        source_ref=str(path),
        source_key=str(fm.get("dedupe_key") or ""),
    )
    task_store.add(Task(
        id=bug_id,
        title=str(fm.get("title") or bug_id),
        status="ready",
        contract=contract,
    ))
    writer = EventWriter(event_log_from_project(state_dir, config=ctx.config))
    writer.append(ZfEvent(
        type="task.created",
        actor="operator",
        task_id=bug_id,
        payload={
            "source_kind": str(fm.get("source_kind") or ""),
            "dedupe_key": str(fm.get("dedupe_key") or ""),
            "candidate_ref": str(path),
            "affinity_tag": str(fm.get("affinity_tag") or ""),
            "via": "zf issue ingest",
        },
    ))
    print(f"[OK] ingested → kanban task {bug_id}(verification=repro)")
    return 0
