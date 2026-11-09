import json

from tests.test_task_pipeline_worker_liveness import runtime_case
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.stage_execution_card import prepare_writer_execution_card
from zf.runtime.stage_execution_card import prepare_result_file_command


def test_writer_redrive_gets_current_attempt_scratch_and_preserves_old_edits(tmp_path):
    def prepare(attempt):
        return prepare_writer_execution_card(
            state_dir=tmp_path, task_item={"operation_id": "op", "attempt_id": attempt,
                "semantic_result_submit_mode": "blocking", "result_scratch_ref": "tmp/op/result.json"},
            task_payload={}, completion_payload={"impl_self_check": {"attempt_id": attempt}},
            run_id="run", cli_command="zf", completion_command="", blocked_command="")
    prepare("a1")
    path = tmp_path / "tmp/op/result.json"
    old = json.loads(path.read_text())
    old["summary"] = "unfinished evidence; do not erase"
    path.write_text(json.dumps(old))
    prepare("a1")
    assert json.loads(path.read_text()) == old
    prepare("a2")
    assert json.loads(path.read_text())["self_check"]["attempt_id"] == "a2"
    assert any("unfinished evidence; do not erase" in p.read_text() for p in tmp_path.rglob("*.json") if p != path)


def test_cleared_terminal_dispatch_cannot_shadow_new_task_on_same_worker(tmp_path):
    runtime, _, writer, identity = runtime_case(tmp_path, status="settled")
    runtime.task_store.update("T1", active_dispatch_id="")
    assert runtime._active_task_for_instance("verify-1") is None
    runtime.task_store.add(Task(id="T2", status="in_progress", assigned_to="verify-1", active_dispatch_id="d2"))
    current = {**identity, "task_id": "T2", "operation_id": "op2", "dispatch_id": "d2"}
    for kind in ("workflow.operation.requested", "workflow.operation.started", "task.dispatched"):
        writer.append(ZfEvent(type=kind, task_id="T2", payload=current))
    assert runtime._active_task_for_instance("verify-1").id == "T2"


def test_verify_draft_is_not_reused_after_attempt_rotation(tmp_path):
    def prepare(attempt):
        return prepare_result_file_command(
            state_dir=tmp_path, result_scratch_ref="tmp/op/result.json", operation_id="op",
            attempt_id=attempt, cli_command="zf", semantic_template={"probe_receipts": []})[1]
    path = prepare("a1")
    path.write_text('{"probe_receipts": [{"target_commit": "old", "status": "passed"}]}')
    prepare("a2")
    assert json.loads(path.read_text()) == {"probe_receipts": []}


def test_legacy_same_attempt_preserves_draft_and_stale_attempt_is_archived(tmp_path):
    path = tmp_path / "tmp/op/result.json"
    path.parent.mkdir(parents=True)
    old = {"self_check": {"attempt_id": "a1"}, "summary": "preserve"}
    path.write_text(json.dumps(old))
    def prepare(attempt):
        prepare_result_file_command(
            state_dir=tmp_path, result_scratch_ref="tmp/op/result.json", operation_id="op",
            attempt_id=attempt, cli_command="zf", semantic_template={"self_check": {"attempt_id": attempt}})
    prepare("a1")
    assert json.loads(path.read_text()) == old
    path.with_name("result.json.attempt.json").unlink()
    prepare("a2")
    assert json.loads(path.read_text())["self_check"]["attempt_id"] == "a2"
    drafts = list((tmp_path / "artifacts/retired-result-drafts").glob("*.json"))
    assert len(drafts) == 1
    assert json.loads(drafts[0].read_text())["draft_text"] == json.dumps(old)
