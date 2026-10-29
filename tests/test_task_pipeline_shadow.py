from types import SimpleNamespace

import pytest

from zf.runtime import task_pipeline_runtime as pipeline


@pytest.mark.parametrize("mode,managed", [("shadow", set()), ("blocking", {"T1"})])
@pytest.mark.parametrize("phase", ["projection", "event_read"])
def test_controller_fault_preserves_mode_authority(monkeypatch, mode, managed, phase):
    events = []
    runtime = SimpleNamespace(
        config=object(),
        event_log=SimpleNamespace(read_all=lambda: []),
        event_writer=SimpleNamespace(append=events.append),
        _dispatch_globally_paused=lambda: False,
    )
    monkeypatch.setattr(pipeline, "task_pipeline_policy", lambda _: {"mode": mode})
    monkeypatch.setattr(pipeline, "task_pipeline_any_blocking", lambda _: mode == "blocking")

    def fail(*args, **kwargs):
        raise RuntimeError("projection or controller failed")

    if phase == "event_read":
        runtime.event_log.read_all = fail
    monkeypatch.setattr("zf.runtime.task_pipeline_projection.write_task_pipeline_projection", fail)
    monkeypatch.setattr(pipeline, "reconcile_task_pipeline_runtime", fail)
    assert pipeline.prepare_task_pipeline_dispatch(runtime, candidate_task_ids=["T1"]) == ([], managed)
    assert events[0].type == "kernel.housekeeping.failed"
