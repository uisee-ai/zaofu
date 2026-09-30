"""Writer permission containment binds to the original admitted invoke."""

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent


@pytest.mark.parametrize("paths,valid", [
    (["src/app"], True), (["src/app/file.py"], True),
    (["src/app/**"], True), (["./src/app/file.py"], True),
    (["src/app/../app/file.py"], True),
    (["src/app", "tests"], False), (["src/application"], False),
    (["src"], False), (["**"], False), (["src/*"], False),
    (["src/app/../../escape"], False), (["/tmp/escape"], False),
    ([r"C:\escape"], False), ([], False),
])
def test_writer_scope_containment(tmp_path, paths, valid):
    # Resolve at execution time so a missing v7 capability is a visible test
    # failure, not a collection error aborting unrelated framework stages.
    from zf.runtime.run_write_scope import validate_run_write_scopes

    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    invoke = writer.append(ZfEvent(type="workflow.invoke.requested", payload={
        "workflow_run_id": "RUN-A", "scope": ["src/app"],
    }))
    writer.append(ZfEvent(type="run.admission.admitted", payload={
        "workflow_run_id": "RUN-A", "source_event_id": invoke.id, "policy_mode": "concurrent",
    }))
    # A later unadmitted request must not replace the approved permission.
    writer.append(ZfEvent(type="workflow.invoke.requested", payload={
        "workflow_run_id": "RUN-A", "scope": ["tests"],
    }))
    task = {"task_id": "TASK-A", "allowed_paths": paths, "verification_read_paths": ["tests"]}
    if valid:
        validate_run_write_scopes(tmp_path, run_id="RUN-A", task_items=[task])
    else:
        with pytest.raises(ValueError, match="outside admitted request scope"):
            validate_run_write_scopes(tmp_path, run_id="RUN-A", task_items=[task])


@pytest.mark.parametrize("case,expected", [
    ("serial", ""), ("unscoped", ""),
    ("missing_source", "no admitted request scope binding"),
    ("invalid_scope", "invalid admitted request scope"),
])
def test_writer_scope_legacy_and_fail_closed_bindings(tmp_path, case, expected):
    from zf.runtime.run_write_scope import validate_run_write_scopes

    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    invoke = writer.append(ZfEvent(type="workflow.invoke.requested", payload={
        "workflow_run_id": "RUN-A", "scope": [] if case == "unscoped" else ["../escape"],
    }))
    writer.append(ZfEvent(type="run.admission.admitted", payload={
        "workflow_run_id": "RUN-A", "policy_mode": "serial" if case == "serial" else "concurrent",
        "source_event_id": "missing" if case == "missing_source" else invoke.id,
    }))
    task = {"task_id": "TASK-A", "allowed_paths": ["**"]}
    if expected:
        with pytest.raises(ValueError, match=expected):
            validate_run_write_scopes(tmp_path, run_id="RUN-A", task_items=[task])
    else:
        validate_run_write_scopes(tmp_path, run_id="RUN-A", task_items=[task])
