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


def test_alternating_projects_reuse_matches_after_decoder_cache_eviction(monkeypatch):
    tasks = [SimpleNamespace(id="T1")]
    for project in ("busy-a", "busy-b"):
        events = rows("T1", "unrelated")
        index.cached_group_task_events(tasks, events, ["t1", "unrelated"],
                                       source_key=(project, 1), source_fingerprint=(100, project))

    def forbidden(*args, **kwargs):
        raise AssertionError("an unchanged project rescanned its large payloads")

    monkeypatch.setattr(index, "group_task_events", forbidden)
    for project in ("busy-a", "busy-b"):
        # Same ledger and decoder fingerprint, freshly decoded row objects.
        events = rows("T1", "unrelated")
        result = index.cached_group_task_events(
            tasks, events, ["t1", "unrelated"],
            source_key=(project, 2), source_fingerprint=(100, project),
        )
        assert result["T1"] == [events[0]]
        assert result["T1"][0] is events[0]


def test_fingerprint_change_after_eviction_rebuilds_even_with_same_event_ids():
    tasks = [SimpleNamespace(id="T1")]
    before = rows("T1")
    index.cached_group_task_events(tasks, before, ["t1"], source_key=("replace", 1),
                                   source_fingerprint=(100, "old"))
    after = rows("not matched")
    result = index.cached_group_task_events(tasks, after, ["not matched"],
                                           source_key=("replace", 2), source_fingerprint=(100, "new"))
    assert result["T1"] == []


def test_match_index_does_not_retain_large_decoded_payloads():
    import gc
    import weakref

    tasks = [SimpleNamespace(id="T1")]
    events = rows("T1 " + "large body " * 1000, "tail")
    payload_owner = weakref.ref(events[0][1])
    index.cached_group_task_events(tasks, events, ["t1", "tail"],
                                   source_key=("memory", 1), source_fingerprint=(100, "memory"))
    del events
    gc.collect()
    assert payload_owner() is None


def test_append_after_eviction_proves_prefix_and_matches_only_tail(monkeypatch):
    tasks = [SimpleNamespace(id="T1")]
    index.cached_group_task_events(tasks, rows("T1", "unrelated"), ["t1", "unrelated"],
                                   source_key=("evicted-append", 1), source_fingerprint=(100, 1))
    original = index.group_task_events
    visited = []

    def counted(tasks, events, texts):
        visited.extend(seq for seq, _ in events)
        return original(tasks, events, texts)

    monkeypatch.setattr(index, "group_task_events", counted)
    after = rows("T1", "unrelated", "T1 tail")
    result = index.cached_group_task_events(tasks, after, ["t1", "unrelated", "t1 tail"],
                                           source_key=("evicted-append", 2), source_fingerprint=(120, 2))
    assert visited == [3]
    assert result["T1"] == [after[0], after[2]]
    # Same ids, count and payload text, but a changed explicit task_id matters.
    rewritten = rows("T1", "unrelated", "T1 tail")
    rewritten[1][1].task_id = "T1"
    result = index.cached_group_task_events(tasks, rewritten, ["t1", "unrelated", "t1 tail"],
                                           source_key=("evicted-append", 3), source_fingerprint=(120, 3))
    assert result["T1"] == rewritten
