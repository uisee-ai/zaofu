"""Invoke rejection stays source-idempotent across replay and a split write."""

from pathlib import Path

import pytest

from tests.test_run_admission import _Transport, _invoke_event, _runtime
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.run_admission import build_run_admission_projection


@pytest.mark.parametrize("interrupt_admission", [False, True])
def test_rejected_invoke_replay_repairs_admission_without_duplicate_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupt_admission: bool,
) -> None:
    from zf.runtime import run_admission

    state, log, runtime = _runtime(tmp_path)
    invoke = _invoke_event(
        runtime, run_id="RUN-A", task_id="TASK-A",
        payload_updates={"open_questions": ["unresolved requirement"]},
    )

    def crash(*args, **kwargs):
        raise RuntimeError("interrupted after invoke rejection")

    if interrupt_admission:
        with monkeypatch.context() as patch:
            patch.setattr(run_admission, "reject_workflow_invoke_admission", crash)
            with pytest.raises(RuntimeError, match="interrupted after invoke rejection"):
                runtime._on_workflow_invoke_requested(invoke)
        assert not any(e.type == "run.admission.rejected" for e in log.read_all())
    else:
        runtime.run_once(events=[invoke])
    [first] = [e for e in log.read_all() if e.type == "workflow.invoke.rejected"]
    assert first.payload["source_event_id"] == invoke.id
    assert first.payload["reason"] == "blocking open questions"

    restarted = Orchestrator(state, runtime.config, _Transport())
    restarted.run_once(events=[invoke])
    restarted._on_workflow_invoke_requested(invoke)
    rows = log.read_all()
    assert [e.id for e in rows if e.type == "workflow.invoke.rejected"] == [first.id]
    assert sum(e.type == "run.admission.rejected" for e in rows) == 1
    assert build_run_admission_projection(rows).runs["RUN-A"].status == "rejected"
    assert not any(e.type in {"workflow.invoke.accepted", "task.fanout.requested"} for e in rows)

    # A different invocation is not swallowed merely because its Task is the same.
    second = _invoke_event(
        restarted, run_id="RUN-B", task_id="TASK-A",
        payload_updates={"open_questions": ["another unresolved requirement"]},
    )
    restarted.run_once(events=[second])
    assert {e.payload["source_event_id"] for e in log.read_all()
            if e.type == "workflow.invoke.rejected"} == {invoke.id, second.id}
