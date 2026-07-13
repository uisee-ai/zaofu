"""B6(b): chat-orchestrator 确定性快答的主题扩展(verification/合同/证据/负责人/历史)。"""

from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.web.projections.workspace import _projection_reply_if_requested


def _state(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    store = TaskStore(sd / "kanban.json")
    store.add(Task(
        id="T-QA", title="qa target", status="in_progress", assigned_to="dev-api",
        retry_count=2,
        contract=TaskContract(behavior="修复计费四舍五入", verification="pytest tests/test_billing.py -q"),
    ))
    log = EventLog(sd / "events.jsonl")
    log.append(ZfEvent(type="dev.build.done", actor="dev-api", task_id="T-QA",
                       payload={"evidence_refs": ["artifacts/T-QA/report.md"]}))
    return sd


def test_verification_question_returns_command(tmp_path: Path) -> None:
    reply = _projection_reply_if_requested(_state(tmp_path), {}, "这个任务怎么验证?", "T-QA")
    assert reply is not None
    assert "pytest tests/test_billing.py -q" in reply["answer"]


def test_contract_question_returns_behavior(tmp_path: Path) -> None:
    reply = _projection_reply_if_requested(_state(tmp_path), {}, "合同要求是什么", "T-QA")
    assert reply is not None
    assert "修复计费四舍五入" in reply["answer"]


def test_evidence_question_returns_refs(tmp_path: Path) -> None:
    reply = _projection_reply_if_requested(_state(tmp_path), {}, "证据在哪", "T-QA")
    assert reply is not None
    assert "artifacts/T-QA/report.md" in reply["answer"]


def test_who_and_history(tmp_path: Path) -> None:
    reply = _projection_reply_if_requested(_state(tmp_path), {}, "谁负责?返工几次了", "T-QA")
    assert reply is not None
    assert "dev-api" in reply["answer"]
    assert "retry_count=2" in reply["answer"]


def test_unrelated_message_still_passes_through(tmp_path: Path) -> None:
    # 不含任何触发词 → None(交给 LLM agent),不误伤自由问答
    reply = _projection_reply_if_requested(_state(tmp_path), {}, "帮我把这段代码重构一下", "T-QA")
    assert reply is None


def test_status_baseline_unchanged(tmp_path: Path) -> None:
    reply = _projection_reply_if_requested(_state(tmp_path), {}, "当前状态?", "T-QA")
    assert reply is not None
    assert "in_progress" in reply["answer"]


def test_archived_done_task_still_answered(tmp_path: Path) -> None:
    """终态任务归档后 active 投影找不到——须回落归档查找(racing 实锚)。"""
    sd = _state(tmp_path)
    TaskStore(sd / "kanban.json").update("T-QA", status="review")
    TaskStore(sd / "kanban.json").update("T-QA", status="testing")
    TaskStore(sd / "kanban.json").update("T-QA", status="done")  # → 归档
    reply = _projection_reply_if_requested(sd, {"mode": "projection_first"}, "总结当前状态", "T-QA")
    assert reply is not None
    assert "T-QA is done" in reply["answer"]
    reply2 = _projection_reply_if_requested(sd, {}, "怎么验证", "T-QA")
    assert reply2 is not None and "pytest tests/test_billing.py -q" in reply2["answer"]
