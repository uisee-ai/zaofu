"""Every public lifecycle state goes through the same controlled label batch."""
from dataclasses import replace

import pytest

from tests.test_external_issue_ingress import _mirror, _service
from zf.core.events import EventLog, EventWriter
from zf.core.issue_triage.lifecycle_states import REMOTE_LIFECYCLE_STATES
from zf.core.issue_triage.models import derived_triage_group
from zf.runtime.external_issue_label_sync import reconcile_lifecycle_labels


def state_events(writer, mirror, state, *, suffix=""):
    def emit(kind, **payload):
        if "workflow_run_id" in payload:
            payload["workflow_run_id"] += suffix
        return writer.emit(kind, actor="test", task_id="TASK" + suffix, payload=payload)

    if state in {"open", "closed"}:
        return
    emit("external_issue.received", source_key=mirror.issue_key)
    if state == "mirrored":
        return
    emit("external_issue.triage.queued", source_key=mirror.issue_key, workflow_run_id="TRIAGE")
    if state == "triage_queued":
        return
    direct = {
        "triaging": "workflow.invoke.accepted",
        "triage_paused": "run.paused",
        "triage_recovering": "workflow.operation.failed",
        "triage_blocked": "run.goal.blocked",
        "triage_cancelled": "run.cancelled",
        "failed": "run.failed",
        "needs_info": "external_issue.triage.needs_info",
        "needs_info_exhausted": "external_issue.triage.needs_info_exhausted",
        "unverified_candidate": "external_issue.triage.unverified_candidate",
        "awaiting_fix_approval": "external_issue.triage.ready_to_fix",
    }
    if state in direct:
        emit(direct[state], source_key=mirror.issue_key, workflow_run_id="TRIAGE")
        return
    if state == "blocked":
        emit("external_issue.triage.needs_info", source_key=mirror.issue_key, workflow_run_id="TRIAGE")
        emit("run.goal.blocked", workflow_run_id="TRIAGE")
        return
    emit("workflow.invoke.requested", source_key=mirror.issue_key, workflow_run_id="FIX", flow_kind="issue")
    if state == "fix_queued":
        return
    direct = {
        "fixing": "task.dispatched",
        "fix_paused": "run.paused",
        "fix_recovering": "workflow.operation.failed",
        "fix_blocked": "run.goal.blocked",
        "fix_cancelled": "run.cancelled",
    }
    if state in direct:
        emit(direct[state], workflow_run_id="FIX")
        return
    emit("candidate.ready", workflow_run_id="FIX", pdd_id="CANDIDATE", flow_kind="issue")
    if state == "verifying":
        return
    emit("judge.passed", workflow_run_id="FIX", pdd_id="CANDIDATE", flow_kind="issue")
    if state == "verified_candidate":
        return
    direct = {
        "approved_for_pr": "candidate.owner_review.approved",
        "owner_changes_requested": "candidate.owner_review.changes_requested",
        "owner_rejected": "candidate.owner_review.declined",
        "publication_prepared": "candidate.publication.prepared",
        "merged": "delivery.merged",
    }
    if state in direct:
        emit(direct[state], workflow_run_id="FIX")
    else:
        assert state.startswith("pr_")
        emit("forge.pull_request.synced", workflow_run_id="FIX", status=state)


@pytest.mark.parametrize("state", REMOTE_LIFECYCLE_STATES)
@pytest.mark.parametrize("cross_project", [False, True])
def test_all_lifecycle_states_replace_stale_label_and_color(tmp_path, monkeypatch, state, cross_project):
    old = "open" if state in {"closed", "mirrored"} else "closed" if state == "open" else "triage_queued"
    github_state = state if state in {"open", "closed"} else "open"
    labels = ("bug", old)
    mirror = replace(
        _mirror(created_at="2026-01-01T00:00:00Z", labels=labels),
        github_state=github_state,
        derived_group=derived_triage_group(github_state, labels),
    )
    calls = []
    def write(item, desired):
        calls.append(desired)
        return {"status": "reconciled", "label_colors": {desired: "DC2626"}}
    service, _ = _service(tmp_path, mirror, lifecycle_label_writer=write)
    owner = EventWriter(EventLog(tmp_path / "owner" / "events.jsonl")) if cross_project else service.writer
    if cross_project:
        monkeypatch.setattr("zf.runtime.external_issue_label_sync._workspace_event_logs",
                            lambda _: [service.writer.event_log, owner.event_log])
    state_events(owner, mirror, state)
    assert reconcile_lifecycle_labels(service)["status"] == "reconciled"
    assert calls == [state]
    saved = service.mirrors.get(8)
    assert saved.labels == ("bug", state)
    assert saved.label_colors[state] == "DC2626"
    assert reconcile_lifecycle_labels(service)["status"] == "reconciled"
    assert calls == [state]  # no duplicate remote writes


@pytest.mark.parametrize("state", REMOTE_LIFECYCLE_STATES)
def test_failed_sync_keeps_original_snapshot_and_retries(tmp_path, state):
    mirror = _mirror(created_at="2026-01-01T00:00:00Z", labels=("bug", "open"))
    if state in {"open", "closed"}:
        mirror = replace(
            mirror,
            github_state=state,
            derived_group=derived_triage_group(state, mirror.labels),
        )
    failed = True
    def write(item, desired):
        return {"status": "outcome_unknown", "reason": "timeout"} if failed else {
            "status": "reconciled", "label_colors": {desired: "DC2626"},
        }
    service, _ = _service(tmp_path, mirror, lifecycle_label_writer=write)
    state_events(service.writer, mirror, state)
    original = service.mirrors.get(8)
    assert reconcile_lifecycle_labels(service)["pending"] == 1
    assert service.mirrors.get(8) == original
    failed = False
    assert reconcile_lifecycle_labels(service)["status"] == "reconciled"
    assert service.mirrors.get(8).labels == ("bug", state)


@pytest.mark.parametrize("old_event", ["candidate.owner_review.approved", "forge.pull_request.synced", "delivery.merged", "run.failed", "run.paused"])
def test_old_delivery_or_run_event_cannot_overwrite_new_attempt(tmp_path, old_event):
    mirror = _mirror(created_at="2026-01-01T00:00:00Z", labels=("fix_queued",))
    calls = []
    service, _ = _service(tmp_path, mirror, lifecycle_label_writer=lambda _, state: calls.append(state) or {
        "status": "reconciled", "label_colors": {state: "DC2626"},
    })
    state_events(service.writer, mirror, "verified_candidate")
    service.writer.emit("external_issue.triage.queued", actor="test", task_id="TASK", payload={
        "source_key": mirror.issue_key, "workflow_run_id": "NEW",
    })
    service.writer.emit(old_event, actor="test", task_id="TASK", payload={"workflow_run_id": "FIX", "status": "merged"})
    reconcile_lifecycle_labels(service)
    assert calls == ["triage_queued"]


def test_new_receipt_in_viewer_does_not_steal_workflow_ownership(tmp_path, monkeypatch):
    mirror = _mirror(created_at="2026-01-01T00:00:00Z", labels=("triage_queued",))
    calls = []
    service, _ = _service(tmp_path, mirror, lifecycle_label_writer=lambda _, state: calls.append(state) or {
        "status": "reconciled", "label_colors": {state: "DC2626"},
    })
    owner = EventWriter(EventLog(tmp_path / "owner" / "events.jsonl"))
    state_events(owner, mirror, "fix_paused")
    service.writer.emit("external_issue.received", actor="test", payload={"source_key": mirror.issue_key})
    monkeypatch.setattr("zf.runtime.external_issue_label_sync._workspace_event_logs",
                        lambda _: [service.writer.event_log, owner.event_log])
    assert service.reconcile_lifecycle_labels()["status"] == "reconciled"
    assert calls == ["fix_paused"]


def test_mixed_lifecycle_batch_publishes_all_names_and_colors_once(tmp_path, monkeypatch):
    mirror = _mirror(created_at="2026-01-01T00:00:00Z", labels=("bug", "triage_queued"))
    service, _ = _service(tmp_path, mirror, lifecycle_label_writer=lambda _, state: {
        "status": "reconciled", "label_colors": {state: "DC2626"},
    })
    states = ("triage_cancelled", "fixing", "merged")
    for number, state in enumerate(states, 8):
        item = replace(mirror, number=number, issue_key=f"github:123:{number}", node_id=f"I_{number}",
                       html_url=f"https://github.com/{mirror.repository}/issues/{number}")
        service.mirrors.upsert(item, "body")
        state_events(service.writer, item, state, suffix=f"-{number}")
    snapshots = []
    original = service.mirrors.project_lifecycle_labels
    def publish(updates):
        assert all(row.labels == ("bug", "triage_queued") for row in service.mirrors.list())
        snapshots.append(dict(updates))
        return original(updates)
    monkeypatch.setattr(service.mirrors, "project_lifecycle_labels", publish)
    assert service.reconcile_lifecycle_labels()["status"] == "reconciled"
    assert len(snapshots) == 1 and len(snapshots[0]) == 3
    for number, state in enumerate(states, 8):
        assert service.mirrors.get(number).labels == ("bug", state)
        assert service.mirrors.get(number).label_colors[state] == "DC2626"


def test_old_child_dispatch_does_not_start_new_fix_run(tmp_path):
    mirror = _mirror(created_at="2026-01-01T00:00:00Z", labels=("triage_queued",))
    service, _ = _service(tmp_path, mirror, lifecycle_label_writer=lambda _, state: {
        "status": "reconciled", "label_colors": {state: "DC2626"},
    })
    state_events(service.writer, mirror, "fix_queued")
    service.writer.emit("fanout.child.dispatched", actor="kernel", task_id="CHILD",
                        payload={"parent_task_id": "TASK", "workflow_run_id": "OLD-FIX"})
    service.reconcile_lifecycle_labels()
    assert service.mirrors.get(8).labels == ("fix_queued",)


def test_cross_project_admission_suppresses_duplicate_intake_without_remote_label(tmp_path, monkeypatch):
    mirror = _mirror(created_at="2026-09-01T00:00:00Z")
    service, _ = _service(tmp_path, mirror, lifecycle_label_writer=lambda _, state: {
        "status": "reconciled", "label_colors": {state: "DC2626"},
    })
    owner = EventWriter(EventLog(tmp_path / "owner" / "events.jsonl"))
    state_events(owner, mirror, "fixing")
    monkeypatch.setattr("zf.runtime.external_issue_label_sync._workspace_event_logs",
                        lambda _: [service.writer.event_log, owner.event_log])
    result = service.poll_once()
    assert result.triage_queued == result.received == 0
    assert not any(e.type == "workflow.invoke.requested" for e in service.writer.event_log.read_all())
    assert service.mirrors.get(8).labels == ("fixing",)


def test_receipt_alone_does_not_regress_unknown_remote_delivery(tmp_path):
    mirror = replace(_mirror(created_at="2026-01-01T00:00:00Z", labels=("merged",)),
                     label_colors={"merged": "6F42C1"})
    service, _ = _service(tmp_path, mirror, lifecycle_label_writer=lambda *_: pytest.fail("no owned workflow"))
    service.writer.emit("external_issue.received", actor="test", payload={"source_key": mirror.issue_key})
    assert service.reconcile_lifecycle_labels()["status"] == "reconciled"
    assert service.mirrors.get(8).labels == ("merged",)


@pytest.mark.parametrize("remote", ["needs_info", "awaiting_fix_approval", "merged"])
def test_incomplete_old_ledger_cannot_roll_back_newer_remote_state(tmp_path, remote):
    from zf.core.events import ZfEvent
    mirror = replace(_mirror(created_at="2026-01-01T00:00:00Z", labels=(remote,)),
                     updated_at="2026-09-20T00:00:00Z", label_colors={remote: "DC2626"})
    service, _ = _service(tmp_path, mirror, lifecycle_label_writer=lambda *_: pytest.fail("remote is not proven stale"))
    service.writer.event_log.append(ZfEvent(type="external_issue.triage.queued", actor="test",
        ts="2026-09-16T00:00:00Z", task_id="TASK",
        payload={"source_key": mirror.issue_key, "workflow_run_id": "OLD"}))
    assert service.reconcile_lifecycle_labels()["status"] == "reconciled"
    assert service.mirrors.get(8).labels == (remote,)
    # An explicit new admission can start again after remote delivery.
    calls = []
    service.lifecycle_label_writer = lambda _, desired: calls.append(desired) or {
        "status": "reconciled", "label_colors": {desired: "DC2626"},
    }
    service.writer.event_log.append(ZfEvent(type="external_issue.triage.queued", actor="operator",
        ts="2026-09-28T00:00:00Z", task_id="TASK",
        payload={"source_key": mirror.issue_key, "workflow_run_id": "NEW"}))
    assert service.reconcile_lifecycle_labels()["status"] == "reconciled"
    assert calls == ["triage_queued"]


def test_index_keeps_reducer_semantics_and_excludes_unrelated_history(tmp_path):
    from zf.core.events import ZfEvent
    from zf.runtime.issue_lifecycle_projection import _workflow_projection, issue_lifecycle_event_index
    mirror = _mirror(created_at="2026-01-01T00:00:00Z")
    service, _ = _service(tmp_path, mirror)
    state_events(service.writer, mirror, "verified_candidate")
    events = service.writer.event_log.read_all()
    unrelated = [ZfEvent(type="task.dispatched", actor="test", task_id=f"OTHER-{n}",
                        payload={"workflow_run_id": f"OTHER-RUN-{n}"}) for n in range(1000)]
    all_events = events + unrelated
    indexed = issue_lifecycle_event_index(all_events, {mirror.issue_key})[mirror.issue_key]
    assert len(indexed) == len(events)
    assert _workflow_projection(indexed, mirror, prefer_remote=False) == _workflow_projection(all_events, mirror, prefer_remote=False)


def test_inflight_response_cannot_publish_superseded_state(tmp_path):
    mirror = _mirror(created_at="2026-01-01T00:00:00Z", labels=("triage_queued",))
    calls = []
    def write(_, state):
        calls.append(state)
        if len(calls) == 1:
            service.writer.emit("run.paused", actor="operator", payload={"run_id": "FIX"})
        return {"status": "reconciled", "label_colors": {state: "DC2626"}}
    service, _ = _service(tmp_path, mirror, lifecycle_label_writer=write)
    state_events(service.writer, mirror, "fixing")
    original = service.mirrors.get(8)
    assert service.reconcile_lifecycle_labels()["pending"] == 1
    assert service.mirrors.get(8) == original
    assert service.reconcile_lifecycle_labels()["status"] == "reconciled"
    assert calls == ["fixing", "fix_paused"]
    assert service.mirrors.get(8).labels == ("fix_paused",)


@pytest.mark.parametrize("state", REMOTE_LIFECYCLE_STATES)
@pytest.mark.parametrize("ingress_enabled", [False, True])
def test_refresh_api_converges_all_lifecycle_states(tmp_path, monkeypatch, state, ingress_enabled):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tests.test_issue_triage_mirror import configured_context
    from zf.core.issue_triage.store import IssueMirrorStore
    from zf.web.issue_triage_routes import build_issue_triage_router

    ctx = configured_context(tmp_path / "viewer")
    ctx.config.self_issue = replace(ctx.config.self_issue, ingress=replace(ctx.config.self_issue.ingress, enabled=ingress_enabled))
    old = "open" if state in {"closed", "mirrored"} else "closed" if state == "open" else "triage_queued"
    github_state = state if state in {"open", "closed"} else "open"
    item = replace(_mirror(created_at="2026-01-01T00:00:00Z", labels=("bug", old)),
                   github_state=github_state, derived_group=derived_triage_group(github_state, ("bug", old)))
    store = IssueMirrorStore(ctx.state_dir)
    store.upsert(item, "body")
    owner = EventWriter(EventLog(tmp_path / "owner" / "events.jsonl"))
    state_events(owner, item, state)
    monkeypatch.setattr("zf.runtime.external_issue_label_sync._workspace_event_logs", lambda _: [owner.event_log])
    calls = []
    def write(_, desired):
        calls.append(desired)
        return {"status": "reconciled", "label_colors": {desired: "DC2626"}}
    monkeypatch.setattr("zf.web.issue_triage_routes.build_github_lifecycle_label_writer", lambda: write)
    class Reconciler:
        def refresh(self, **_):
            return {"ok": True, "status": "fresh", "changed": 0}
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda _: ctx,
        reconciler_factory=lambda *_: Reconciler(), mutation_auth_error=lambda *args, **kwargs: None))
    client = TestClient(app)
    result = client.post("/api/projects/viewer/issue-triage/refresh?force=true")
    assert result.status_code == 200, result.text
    saved = store.get(8)
    assert calls == [state]
    assert saved.labels == ("bug", state)
    assert saved.label_colors[state] == "DC2626"
