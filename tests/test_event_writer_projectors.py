from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.projectors import EventProjector, ProjectorRunner
from zf.core.events.writer import EventWriter
from zf.core.verification.event_schema import EventSchemaRegistry


def test_projector_runner_records_order_and_skips(tmp_path: Path):
    log = EventLog(tmp_path / ".zf" / "events.jsonl")
    calls: list[tuple[str, str]] = []
    runner = ProjectorRunner((
        EventProjector(
            name="fanout",
            handler=lambda _log, event: calls.append(("fanout", event.type)),
            event_filter=lambda event: event.type.startswith("fanout."),
        ),
        EventProjector(
            name="stage",
            handler=lambda _log, event: calls.append(("stage", event.type)),
        ),
    ))
    writer = EventWriter(log, projector_runner=runner)

    writer.append(ZfEvent(type="task.created", task_id="T-1"))

    assert calls == [("stage", "task.created")]
    assert [
        (result.name, result.event_type, result.status)
        for result in writer.projector_diagnostics
    ] == [
        ("fanout", "task.created", "skipped"),
        ("stage", "task.created", "ok"),
    ]


def test_default_runner_wraps_existing_fanout_and_stage_projectors(
    tmp_path: Path,
    monkeypatch,
):
    calls: list[tuple[str, str]] = []

    class FakeFanoutManifestProjector:
        def __init__(self, state_dir: Path) -> None:
            self.state_dir = state_dir

        def project_event(self, _log: EventLog, event: ZfEvent) -> None:
            calls.append(("fanout_manifest", event.type))

    def fake_stage_report(state_dir: Path, event: ZfEvent):
        calls.append(("stage_report", event.type))
        return {"state_dir": str(state_dir), "event_type": event.type}

    import zf.runtime.fanout as fanout_module
    import zf.runtime.stage_reports as stage_reports_module

    monkeypatch.setattr(
        fanout_module,
        "FanoutManifestProjector",
        FakeFanoutManifestProjector,
    )
    monkeypatch.setattr(
        stage_reports_module,
        "project_stage_report_for_event",
        fake_stage_report,
    )

    writer = EventWriter(EventLog(tmp_path / ".zf" / "events.jsonl"))
    writer.append(ZfEvent(type="fanout.started", task_id="T-1"))

    assert calls == [
        ("fanout_manifest", "fanout.started"),
        ("stage_report", "fanout.started"),
    ]
    assert [
        (result.name, result.status)
        for result in writer.projector_diagnostics
    ] == [
        ("fanout_manifest", "ok"),
        ("stage_report", "ok"),
    ]


def test_projector_failure_does_not_block_event_append(tmp_path: Path):
    log = EventLog(tmp_path / ".zf" / "events.jsonl")

    def fail(_log: EventLog, _event: ZfEvent) -> None:
        raise RuntimeError("projection down")

    writer = EventWriter(
        log,
        projector_runner=ProjectorRunner((
            EventProjector(name="broken", handler=fail),
        )),
    )

    writer.append(ZfEvent(type="worker.heartbeat", task_id="T-1"))

    assert [event.type for event in log.read_all()] == ["worker.heartbeat"]
    assert len(writer.projector_diagnostics) == 1
    result = writer.projector_diagnostics[0]
    assert result.name == "broken"
    assert result.status == "failed"
    assert result.error_type == "RuntimeError"
    assert result.error == "projection down"


def test_warning_mode_projects_original_and_schema_warning(tmp_path: Path):
    log = EventLog(tmp_path / ".zf" / "events.jsonl")
    projected: list[str] = []
    registry = EventSchemaRegistry.from_dict({
        "arch.proposal.done": {
            "required": ["feature_id"],
        },
    })
    writer = EventWriter(
        log,
        schema_registry=registry,
        schema_mode="warning",
        projector_runner=ProjectorRunner((
            EventProjector(
                name="recorder",
                handler=lambda _log, event: projected.append(event.type),
            ),
        )),
    )

    writer.append(ZfEvent(type="arch.proposal.done", payload={}))

    assert [event.type for event in log.read_all()] == [
        "arch.proposal.done",
        "event.schema.violated",
    ]
    assert projected == ["arch.proposal.done", "event.schema.violated"]
    assert [
        (result.name, result.event_type, result.status)
        for result in writer.projector_diagnostics
    ] == [
        ("recorder", "arch.proposal.done", "ok"),
        ("recorder", "event.schema.violated", "ok"),
    ]
