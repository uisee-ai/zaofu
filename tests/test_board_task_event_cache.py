"""A busy Board reuses exact decoded generations without hiding new facts."""
from types import SimpleNamespace

from zf.core.events.model import ZfEvent
from zf.web.projections import event_task_index as index
from zf.web.projections.common import _payload_search_text


def rows(*values):
    return [(i, ZfEvent(id=f"e{i}", type="worker.progress", payload={"text": value}))
            for i, value in enumerate(values, 1)]


def grouped(tasks, events, source):
    return index.cached_group_task_events(
        tasks, events, [_payload_search_text(event.payload) for _, event in events],
        source_key=source,
    )


def test_warm_board_and_append_only_match_new_rows(monkeypatch):
    tasks = [SimpleNamespace(id="T1"), SimpleNamespace(id="T12")]
    before = rows("T12", "other")
    old = grouped(tasks, before, ("project-append", 1))
    original = index.group_task_events
    visited = []

    def counted(tasks, events, texts):
        visited.extend(events)
        return original(tasks, events, texts)

    monkeypatch.setattr(index, "group_task_events", counted)
    assert grouped(tasks, before, ("project-append", 1)) == old
    assert visited == []
    tail = (3, ZfEvent(id="tail", type="worker.progress", task_id="T1"))
    after = before + [tail]
    result = grouped(tasks, after, ("project-append", 1))
    assert visited == [tail]
    assert result == original(tasks, after, [_payload_search_text(e.payload) for _, e in after])
    assert old["T1"] == [before[0]]  # In-flight readers keep their original value.


def test_replacement_task_changes_and_other_projects_never_reuse_old_matches():
    tasks = [SimpleNamespace(id="T1")]
    before = rows("T1")
    assert grouped(tasks, before, ("project-a", 10))["T1"]
    assert grouped(tasks, rows("unrelated"), ("project-a", 11))["T1"] == []
    assert grouped(tasks, before, ("project-b", 11))["T1"]
    changed_tasks = [*tasks, SimpleNamespace(id="T2")]
    after = [*before, (2, ZfEvent(id="new", type="worker.progress", task_id="T2"))]
    assert grouped(changed_tasks, after, ("project-b", 11))["T2"] == [after[-1]]
    # A stale caller cannot claim another decoded prefix merely by reusing a key.
    assert grouped(tasks, rows("none"), ("project-b", 11))["T1"] == []


def test_append_retains_existing_reference_bound(monkeypatch):
    tasks = [SimpleNamespace(id="T1")]
    monkeypatch.setattr(index, "_MAX_GROUPED_EVENT_REFS", 1)
    before = rows("T1")
    assert grouped(tasks, before, ("project-budget", 1)) is not None
    after = [*before, (2, ZfEvent(id="two", type="worker.progress", task_id="T1"))]
    assert grouped(tasks, after, ("project-budget", 1)) is None


def test_board_caller_reuses_rows_and_exposes_append_and_replacement(tmp_path, monkeypatch):
    from zf.core.events.log import EventLog
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.web.projections.tasks import _kanban

    TaskStore(tmp_path / "kanban.json").add(Task(id="T1", title="one"))
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(id="first", type="task.created", task_id="T1"))
    first = _kanban(tmp_path)
    visited = []
    original = index.group_task_events

    def counted(tasks, events, texts):
        visited.extend(event.id for _, event in events)
        return original(tasks, events, texts)

    monkeypatch.setattr(index, "group_task_events", counted)
    assert _kanban(tmp_path) == first
    assert visited == []
    log.append(ZfEvent(id="second", type="worker.progress", task_id="T1"))
    assert _kanban(tmp_path)[0]["latest_event"]["id"] == "second"
    assert visited == ["second"]
    replacement = tmp_path / "replacement.jsonl"
    EventLog(replacement).append(ZfEvent(id="replacement", type="task.created", task_id="T1"))
    replacement.replace(log.path)
    assert _kanban(tmp_path)[0]["latest_event"]["id"] == "replacement"


def test_board_failure_context_filter_keeps_failure_and_supersession(tmp_path):
    from zf.core.events.log import EventLog
    from zf.core.task.schema import Task, TaskContract
    from zf.core.task.store import TaskStore
    from zf.web.projections.tasks import _kanban

    TaskStore(tmp_path / "kanban.json").add(Task(
        id="T1", title="one", status="in_progress", contract=TaskContract(feature_id="FEATURE-ONE"),
    ))
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(id="start", type="task.created", task_id="T1"))
    log.append(ZfEvent(id="unrelated", type="agent.text", payload={"text": "noise"}))
    log.append(ZfEvent(id="failure", type="prd.blocked", payload={"feature_id": "FEATURE-ONE"}))
    failed = _kanban(tmp_path)[0]
    assert failed["verify_state"] == "failed"
    log.append(ZfEvent(id="recovered", type="prd.ready", payload={"feature_id": "FEATURE-ONE"}))
    assert _kanban(tmp_path)[0]["verify_state"] == "empty"
