import pytest

from tests.test_call_result_admission import _runtime, _persisted_verification_payload
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_operation import load_workflow_operation


def _prepare(tmp_path):
    admission, operations = _runtime(tmp_path)
    for name, kind, parent in [("parent", "agent", ""), ("child", "dynamic_read_only_workflow", "parent")]:
        operations.ensure_operation(
            workflow_run_id="run-1", operation_id=name, operation_type=kind,
            parent_operation_id=parent, request={"prompt": "inspect evidence"}, task_id="T1",
        )
    return admission, operations


def _report(tmp_path, admission, operations, name, verdict="passed"):
    return admission.report_legacy_result(
        ZfEvent(type="verify.child.completed", actor="verify-1", task_id="T1",
                payload=_persisted_verification_payload(tmp_path, verdict=verdict)),
        mode="blocking", operation=load_workflow_operation(operations.event_log, name),
    )


@pytest.mark.parametrize("verdict", ["passed", "rejected"])
def test_parent_success_requires_admitted_successful_child(tmp_path, verdict):
    admission, operations = _prepare(tmp_path)
    early = _report(tmp_path, admission, operations, "parent")
    assert not early.admitted
    assert any(i["code"] == "required_dynamic_child_not_admitted" for i in early.issues)
    child = _report(tmp_path, admission, operations, "child", verdict)
    assert child.admitted
    parent = _report(tmp_path, admission, operations, "parent")
    assert parent.admitted is (verdict == "passed")


def test_cancelled_parent_rejects_late_child_result(tmp_path):
    admission, operations = _prepare(tmp_path)
    parent = load_workflow_operation(operations.event_log, "parent")
    operations.cancel(operation_id="parent", request_hash=parent["request_hash"],
                      workflow_run_id="run-1", reason="owner cancelled")
    result = _report(tmp_path, admission, operations, "child")
    assert not result.admitted
    assert any(i["code"] == "dynamic_parent_terminal" for i in result.issues)
    assert load_workflow_operation(operations.event_log, "child")["status"] != "settled"


def test_child_history_remains_valid_after_parent_closes(tmp_path):
    from zf.runtime.dynamic_continuation_lifecycle import parent_fence
    from zf.runtime.workflow_operation import reduce_workflow_operations

    admission, operations = _prepare(tmp_path)
    assert _report(tmp_path, admission, operations, "child").admitted
    assert _report(tmp_path, admission, operations, "parent").admitted
    assert parent_fence(reduce_workflow_operations(operations.event_log.read_all()), "child") == ("", "")
