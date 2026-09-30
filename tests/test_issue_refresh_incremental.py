"""Incremental cursor, comment cache and full-repair regressions."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zf.integrations.forge.github_issues import GitHubIssueReconciler
from tests.test_issue_triage_mirror import issue_payload, comment_payload


@pytest.fixture
def sync(tmp_path):
    payload = issue_payload(labels=())
    payload['comments'] = 1
    clock = [datetime(2026, 8, 25, 3, tzinfo=timezone.utc)]
    calls = []
    comments = [comment_payload()]
    fail = [False]

    def transport(method, url, headers):
        calls.append((url, headers))
        if url.endswith('/repos/rzwu-uisee/zaofu-self-issue'):
            return 200, json.dumps({'id':123,'full_name':'rzwu-uisee/zaofu-self-issue'}).encode(), {}
        if '/comments?' in url:
            if fail[0]:
                return 500, b'{}', {}
            return 200, json.dumps(comments).encode(), {}
        if '/issues/7' in url:
            return 200, json.dumps(payload).encode(), {}
        return 200, json.dumps([payload]).encode(), {'etag':'"page"'}

    reconciler = GitHubIssueReconciler(tmp_path, 'rzwu-uisee/zaofu-self-issue', transport=transport, now=lambda:clock[0])
    assert reconciler.refresh(force=True)['ok']
    calls.clear()
    return reconciler, payload, clock, calls, comments, fail


def test_force_bypasses_debounce_but_reuses_unchanged_comments(sync):
    reconciler, _, _, calls, _, _ = sync
    path = reconciler.store.issues_path
    before = path.stat().st_mtime_ns
    assert reconciler.refresh(force=True)['changed'] == 0
    assert len(calls) == 2
    assert 'since=' in calls[-1][0]
    assert path.stat().st_mtime_ns == before


def test_changed_issue_downloads_comments_and_failure_does_not_advance_cursor(sync):
    reconciler, payload, clock, calls, comments, fail = sync
    previous = reconciler.store.sync_state().last_success_at
    clock[0] += timedelta(minutes=5)
    payload['updated_at'] = clock[0].isoformat()
    comments[0]['body'] = 'edited comment, same count'
    fail[0] = True
    assert not reconciler.refresh(force=True)['ok']
    assert reconciler.store.sync_state().last_success_at == previous
    fail[0] = False
    assert reconciler.refresh(force=True)['ok']
    assert reconciler.store.read_comments(reconciler.store.get(7))[0].body == 'edited comment, same count'
    assert len([url for url, _ in calls if '/comments?' in url]) == 2


@pytest.mark.parametrize('explicit', [False, True])
def test_full_check_repairs_comment_edit_even_if_issue_timestamp_did_not_change(sync, explicit):
    reconciler, _, clock, calls, comments, _ = sync
    if not explicit:
        clock[0] += timedelta(hours=24)
    comments[0]['body'] = 'comment edit without Issue timestamp change'
    assert reconciler.refresh(force=True, full=explicit)['ok']
    assert not any('since=' in url for url, _ in calls)
    assert not any('If-None-Match' in headers for _, headers in calls)
    assert reconciler.store.read_comments(reconciler.store.get(7))[0].body == comments[0]['body']


@pytest.mark.parametrize('corrupt', [False, True])
def test_missing_or_corrupt_comment_cache_is_repaired(sync, corrupt):
    reconciler, _, _, calls, _, _ = sync
    item = reconciler.store.get(7)
    path = reconciler.store.state_dir / item.comments_ref
    if corrupt:
        path.write_text('corrupt')
    else:
        path.unlink()
    assert reconciler.refresh(force=True)['ok']
    assert len(reconciler.store.read_comments(reconciler.store.get(7))) == 1
    assert len(calls) == 3


def test_single_issue_lookup_does_not_advance_repository_cursor(sync):
    reconciler, _, clock, _, _, _ = sync
    previous = reconciler.store.sync_state()
    clock[0] += timedelta(minutes=5)
    assert reconciler.refresh_issue(7)['ok']
    current = reconciler.store.sync_state()
    assert current.last_success_at == previous.last_success_at
    assert current.last_full_sync_at == previous.last_full_sync_at


@pytest.mark.parametrize("ingress", [False, True])
def test_explicit_full_route_passes_through(tmp_path: Path, ingress):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tests.test_issue_triage_mirror import configured_context
    from zf.web.issue_triage_routes import build_issue_triage_router
    ctx = configured_context(tmp_path)
    if ingress:
        from zf.core.config.schema import ExternalIssueIngressConfig
        ctx.config.self_issue.ingress = ExternalIssueIngressConfig(enabled=True)
    calls = []
    class Reconciler:
        def refresh(self, **kwargs):
            calls.append(kwargs)
            return {'ok':True,'status':'fresh','changed':0}
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda _:ctx,
        reconciler_factory=lambda *_:Reconciler(),
        mutation_auth_error=lambda *args, **kwargs: None))
    assert TestClient(app).post('/api/projects/test/issue-triage/refresh?force=true&full=true').status_code == 200
    assert calls == [{'force':True,'full':True}]


def test_deleted_last_comment_clears_cache_without_comment_request(sync):
    reconciler, payload, _, calls, _, _ = sync
    payload['comments'] = 0
    payload['updated_at'] = '2026-08-25T04:00:00Z'
    assert reconciler.refresh(force=True)['ok']
    assert reconciler.store.read_comments(reconciler.store.get(7)) == []
    assert len(calls) == 2


def test_sync_state_reads_legacy_data_and_round_trips_full_timestamp():
    from dataclasses import replace
    from zf.core.issue_triage.models import SyncState
    legacy = SyncState.from_dict({'last_success_at':'2026-08-25T04:00:00Z'})
    assert legacy.last_full_sync_at == ''
    current = replace(legacy, last_full_sync_at=legacy.last_success_at)
    assert SyncState.from_dict(current.to_dict()) == current
