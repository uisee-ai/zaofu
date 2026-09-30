"""Settings changes must reach the actual report/discovery/capture entrypoints."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import subprocess
import os

import pytest
from fastapi.testclient import TestClient

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.self_issue_auto_trigger import auto_trigger_from_event
from zf.runtime.self_issue_service import SelfIssueService
from zf.web.server import create_app
from tests.test_settings_integration import setup, USER, URL
from tests.test_self_issue_browser_evidence import _png, _context


def save(client, changes):
    response = client.get(URL, headers=USER)
    assert response.status_code == 200, response.text
    snapshot = response.json()
    response = client.patch(URL, headers=USER, json={
        'config_digest': snapshot['config_digest'], 'changes': changes,
    })
    assert response.status_code == 200, response.text


@pytest.mark.parametrize('portable', [False, True])
def test_reporting_toggle_gates_web_capture_without_restarting(tmp_path, monkeypatch, portable):
    root, config, settings = setup(tmp_path, monkeypatch)
    state = root / '.state'
    client = TestClient(create_app(state, config=config, project_root=root))
    calls = []
    # Never invoke a real model; the real controlled action must reach this
    # boundary only after the freshly saved policy has permitted creation.
    monkeypatch.setattr('zf.runtime.self_issue_session_host.SelfIssueSessionHost.create_and_schedule',
                        lambda *a, **k: calls.append('portable') or {'status': 'ready', 'session_id': 'fixture'})
    monkeypatch.setattr(SelfIssueService, 'capture', lambda *a, **k: calls.append('legacy') or {'ok': True, 'status': 'intake_created'})
    payload = {'description': 'Settings report check', 'portable_session': portable, 'direct_report': portable}
    save(settings, {'enabled': False})
    rejected = client.post('/api/actions/self-issue-capture', headers=USER, json=payload)
    assert 'self_issue_disabled' in rejected.text
    assert calls == []
    assert not (state / 'self-issues' / 'portable-sessions').exists()
    save(settings, {'enabled': True, 'automatic_detection_enabled': False})
    accepted = client.post('/api/actions/self-issue-capture', headers=USER, json=payload)
    assert accepted.status_code == 200, accepted.text
    assert calls == ['portable' if portable else 'legacy']


@pytest.mark.parametrize('enabled,automatic', [(False, True), (False, False), (True, False), (True, True)])
def test_live_settings_control_automatic_detection(tmp_path, monkeypatch, enabled, automatic):
    root, config, client = setup(tmp_path, monkeypatch)
    save(client, {'enabled': enabled, 'automatic_detection_enabled': automatic})
    state = root / '.state'
    result = auto_trigger_from_event(
        event=ZfEvent(type='kernel.housekeeping.failed', actor='kernel', payload={'step': 'projection', 'reason': 'fixture invariant'}),
        state_dir=state, writer=EventWriter(EventLog(state / 'events.jsonl')), project_root=root, config=config,
    )
    if enabled and automatic:
        assert result and result['status'] == 'auto_intake_created'
    else:
        assert result is None
        assert not (state / 'self-issue-intakes').exists()


def test_saved_capture_policy_reaches_assessment_collector_without_restart(tmp_path, monkeypatch):
    root, config, client = setup(tmp_path, monkeypatch)
    state = root / '.state'
    service = SelfIssueService(state, EventWriter(EventLog(state / 'events.jsonl')), project_root=root, policy=config.self_issue)
    draft = SimpleNamespace(draft_id='policy-check', evidence_run_id='run', runtime_status='live', reporter_context=_context('http://127.0.0.1:8002'))
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        mount = command[command.index('-v') + 1]
        output = Path(mount.split(':/evidence', 1)[0]) / 'playwright-clean-incident.png'
        output.write_bytes(_png())
        return subprocess.CompletedProcess(command, 0, stdout='fixture screenshot', stderr='')

    from zf.runtime.self_issue_browser_evidence import capture_self_issue_browser_evidence
    monkeypatch.setattr('zf.runtime.self_issue_service.capture_self_issue_browser_evidence',
                        lambda **kwargs: capture_self_issue_browser_evidence(**kwargs, runner=runner))
    def capture():
        return service._capture_assessed_browser_evidence(draft=draft, report={'classification': 'web/ui'}, mechanical_evidence={})[1]

    save(client, {'browser_capture_enabled': False})
    assert capture().status == 'not_requested'
    assert calls == []
    save(client, {'browser_capture_enabled': True})
    captured = capture()
    assert captured.status == 'captured'
    assert captured.screenshot_ref and len(calls) == 1
    save(client, {'browser_capture_denied_urls': ['http://127.0.0.1:8002/']})
    assert capture().status == 'blocked_by_policy'
    assert len(calls) == 1
    save(client, {'browser_capture_denied_urls': []})
    assert capture().status == 'captured'
    assert len(calls) == 2


@pytest.mark.skipif(os.environ.get('ZF_TEST_BROWSER_CAPTURE_DOCKER') != '1', reason='explicit isolated Docker browser tier')
def test_capture_policy_with_real_docker_browser_on_isolated_page(tmp_path, monkeypatch):
    """Only a throwaway local HTML fixture is visited, never the user's Web service."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'<!doctype html><title>Isolated capture fixture</title><h1>Capture policy acceptance</h1>')
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('0.0.0.0', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root, config, client = setup(tmp_path, monkeypatch)
        state = root / '.state'
        url = f'http://127.0.0.1:{server.server_port}'
        service = SelfIssueService(state, EventWriter(EventLog(state / 'events.jsonl')), project_root=root, policy=config.self_issue)
        draft = SimpleNamespace(draft_id='docker-policy-check', evidence_run_id='run', runtime_status='live', reporter_context=_context(url))
        def capture():
            return service._capture_assessed_browser_evidence(draft=draft, report={'classification': 'web/ui'}, mechanical_evidence={})[1]

        save(client, {'browser_capture_enabled': False})
        assert capture().status == 'not_requested'
        assert requests == []
        save(client, {'browser_capture_enabled': True})
        result = capture()
        assert result.status == 'captured', result.reason
        assert result.screenshot_ref and (state / result.screenshot_ref['ref']).read_bytes().startswith(b'\x89PNG')
        assert requests
        before = len(requests)
        save(client, {'browser_capture_denied_urls': [url]})
        assert capture().status == 'blocked_by_policy'
        assert len(requests) == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
