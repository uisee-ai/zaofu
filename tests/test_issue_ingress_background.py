"""HTTP completion must not wait for evidence capture or canonical intake."""
import asyncio
import json
from threading import Event
from types import SimpleNamespace

from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient

from tests.test_issue_triage_mirror import configured_context
from zf.core.config.schema import ExternalIssueIngressConfig
from zf.runtime.external_issue_ingress import ExternalIssueIngressService, ExternalIssuePollResult
from zf.web.issue_ingress_background import IssueIngressBackground
from zf.web.issue_triage_routes import build_issue_triage_router


def app_for(tmp_path):
    ctx = configured_context(tmp_path)
    ctx.config.self_issue.ingress = ExternalIssueIngressConfig(enabled=True)
    class Reconciler:
        def refresh(self, **kwargs):
            return {'ok':True, 'status':'fresh', 'changed':0}
    app = FastAPI()
    app.include_router(build_issue_triage_router(resolve_ctx=lambda _:ctx,
        reconciler_factory=lambda *_:Reconciler(),
        mutation_auth_error=lambda *args, **kwargs: None))
    return app


def test_http_response_precedes_slow_evidence_processing(tmp_path, monkeypatch):
    body_sent = Event()
    began = Event()
    release = Event()
    def process(self):
        assert body_sent.is_set()
        began.set()
        assert release.wait(5)
        return ExternalIssuePollResult(ok=True,status='fresh')
    monkeypatch.setattr(ExternalIssueIngressService, 'process_mirrors', process)
    app = app_for(tmp_path)
    async def run():
        responses = []
        async def receive():
            return {'type':'http.request','body':b'', 'more_body':False}
        async def send(message):
            responses.append(message)
            if message['type'] == 'http.response.body':
                body_sent.set()
        scope = {'type':'http','asgi':{'version':'3.0'},'method':'POST','scheme':'http',
                 'path':'/api/projects/test/issue-triage/refresh','query_string':b'force=true',
                 'root_path':'','headers':[],'server':('test',80),'client':('test',1234),'http_version':'1.1'}
        task = asyncio.create_task(app(scope, receive, send))
        try:
            assert await asyncio.to_thread(began.wait, 3)
            body = next(item['body'] for item in responses if item['type']=='http.response.body')
            assert json.loads(body)['intake']['status'] == 'processing'
            assert not task.done()
        finally:
            release.set()
            await task
    asyncio.run(run())


def test_background_error_is_separate_from_successful_mirror_sync(tmp_path, monkeypatch):
    def process(self):
        raise ValueError('Evidence capture failed')
    monkeypatch.setattr(ExternalIssueIngressService, 'process_mirrors', process)
    client = TestClient(app_for(tmp_path))
    response = client.post('/api/projects/test/issue-triage/refresh?force=true')
    assert response.status_code == 200
    assert response.json()['status'] == 'fresh'
    intake = client.get('/api/projects/test/issue-triage/summary').json()['intake']
    assert intake == {'status':'failed','error':'Evidence capture failed'}


def test_refresh_during_processing_is_coalesced_and_processes_latest_mirror(tmp_path):
    jobs = IssueIngressBackground()
    began, release = Event(), Event()
    calls = []
    def process():
        calls.append(1)
        if len(calls)==1:
            began.set()
            assert release.wait(5)
        return ExternalIssuePollResult(ok=True,status='fresh')
    service = SimpleNamespace(state_dir=tmp_path, process_mirrors=process)
    async def run():
        first = BackgroundTasks()
        jobs.schedule(service, first)
        worker = asyncio.create_task(first())
        try:
            assert await asyncio.to_thread(began.wait, 3)
            for _ in range(4):
                extra = BackgroundTasks()
                jobs.schedule(service, extra)
                assert not extra.tasks
        finally:
            release.set()
            await worker
        assert len(calls)==2
        assert jobs.status(tmp_path)['status']=='ready'
    asyncio.run(run())


def test_busy_intake_retries_latest_request_without_reporting_failure(tmp_path, monkeypatch):
    jobs = IssueIngressBackground()
    calls = []
    def process():
        calls.append("busy")
        return ExternalIssuePollResult(ok=True, status="syncing")
    first = SimpleNamespace(state_dir=tmp_path, process_mirrors=process)
    def completed():
        calls.append("latest")
        return ExternalIssuePollResult(ok=True, status="fresh")
    latest = SimpleNamespace(state_dir=tmp_path, process_mirrors=completed)
    def retry_wait(_):
        assert jobs.status(tmp_path) == {"status": "processing"}
        extra = BackgroundTasks()
        jobs.schedule(latest, extra)
        assert not extra.tasks
    monkeypatch.setattr("zf.web.issue_ingress_background.sleep", retry_wait)
    tasks = BackgroundTasks()
    jobs.schedule(first, tasks)
    asyncio.run(tasks())
    assert calls == ["busy", "latest"]
    assert jobs.status(tmp_path) == {"status": "ready", "error": ""}
