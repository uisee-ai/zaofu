"""A bounded CLI batch preserves per-page authorization, output and receipts."""
import hashlib
import json

import pytest

from tests.test_artifact_read_capability import _grant
from zf.cli.main import main
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.artifact_read_ledger import build_attempt_source_manifest, write_attempt_source_manifest


def _fixture(tmp_path, monkeypatch, content='汉字' * 20000):
    state = tmp_path / '.runtime'
    path = state / 'artifacts/input.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'body': content}, ensure_ascii=False))
    source = {'source_id': 's', 'artifact_id': 's', 'ref': 'artifacts/input.json',
              'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'allowed_paths': ['$.body']}
    manifest = build_attempt_source_manifest(workflow_run_id='run', task_id='T', attempt_id='a',
        dispatch_id='a', sources=[source], metadata={'read_purpose': 'implementation'})
    write_attempt_source_manifest(state, manifest)
    _grant(state, monkeypatch, manifest=manifest, operation_id='op')
    log = EventLog(state / 'events.jsonl')
    writer = EventWriter(log)
    for kind in ['workflow.operation.requested', 'workflow.operation.started']:
        writer.append(ZfEvent(type=kind, payload={'operation_id': 'op', 'active_attempt_id': 'a'}))
    writer.append(ZfEvent(type='task.dispatched', payload={'dispatch_id': 'a', 'required_reads': [
        {'source_id': 's', 'artifact_id': 's', 'artifact_sha256': source['sha256'], 'json_path': '$.body'}]}))
    command = ['artifact', 'read', '--attempt', 'a', '--required', '--state-dir', str(state)]
    return state, writer, command


def test_four_page_cli_continues_without_omission_or_unbounded_output(tmp_path, monkeypatch, capsys):
    content = '汉字' * 20000
    state, writer, command = _fixture(tmp_path, monkeypatch, content)
    cursor = None
    chunks = []
    calls = 0
    while True:
        args = [*command, '--pages', '4'] + (['--cursor', cursor] if cursor else [])
        assert main(args) == 0
        raw = capsys.readouterr().out
        pages = [json.loads(line) for line in raw.splitlines()]
        assert 1 <= len(pages) <= 4
        assert len(raw.encode()) <= 4 * 8192
        assert all(len(line.encode()) + 1 <= 8192 for line in raw.splitlines())
        chunks += [item['content'] for page in pages for item in page['items']]
        calls += 1
        cursor = pages[-1]['next_cursor']
        if not cursor:
            break
    assert calls > 1
    assert ''.join(chunks) == content
    reads = [json.loads(s) for s in (state / 'artifacts/attempts/a/read-ledger.active.jsonl').read_text().splitlines()]
    assert len(reads) == len(chunks)
    assert all(r['production_status'] == 'stdout_flushed' and r['provider_delivery_status'] == 'unknown' for r in reads)


@pytest.mark.parametrize('fault', ['cancel', 'tamper', 'broken_pipe'])
def test_batch_stops_at_first_failed_page_without_receipting_it(tmp_path, monkeypatch, capsys, fault):
    from zf.runtime import artifact_output_delivery
    state, writer, command = _fixture(tmp_path, monkeypatch)
    original = artifact_output_delivery.emit_artifact_output
    calls = []
    def emit(*args):
        if calls and fault == 'broken_pipe':
            raise OSError('broken pipe')
        original(*args)
        calls.append(1)
        if fault == 'cancel':
            writer.append(ZfEvent(type='workflow.operation.settled', payload={'operation_id': 'op'}))
        elif fault == 'tamper':
            (state / 'artifacts/input.json').write_text('{"body":"changed"}')
    monkeypatch.setattr(artifact_output_delivery, 'emit_artifact_output', emit)
    assert main([*command, '--pages', '4']) == 1
    output = capsys.readouterr()
    assert len(output.out.splitlines()) == 1 and output.err
    reads = (state / 'artifacts/attempts/a/read-ledger.active.jsonl').read_text().splitlines()
    assert len(reads) == 1


@pytest.mark.parametrize('pages', ['0', '5', '-1'])
def test_page_count_is_bounded_before_any_read(pages, capsys):
    assert main(['artifact', 'read', '--attempt', 'unused', '--required', '--pages', pages]) == 1
    assert '1..4' in capsys.readouterr().err


def test_multi_page_requires_required_mode(capsys):
    assert main(['artifact', 'read', '--attempt', 'unused', '--source', 's', '--artifact', 's', '--pages', '2']) == 1
    assert '--required' in capsys.readouterr().err


def test_completed_small_batch_emits_once(tmp_path, monkeypatch, capsys):
    _, _, command = _fixture(tmp_path, monkeypatch, 'small')
    assert main([*command, '--pages', '4']) == 0
    pages = [json.loads(s) for s in capsys.readouterr().out.splitlines()]
    assert len(pages) == 1 and pages[0]['next_cursor'] is None


def test_batch_reuses_archive_but_checks_live_state_between_pages(tmp_path, monkeypatch, capsys):
    from zf.runtime import artifact_output_delivery
    state, writer, command = _fixture(tmp_path, monkeypatch)
    archive = state / 'events'
    archive.mkdir(exist_ok=True)
    (archive / '2026-10-29.jsonl').write_text(ZfEvent(type='workflow.operation.requested',
        id='archived', payload={'operation_id': 'old'}).to_json() + '\n')
    decoded = []
    original_decode = EventLog.decode_line
    def decode(self, line):
        event = original_decode(self, line)
        if event and event.id == 'archived':
            decoded.append(event.id)
        return event
    monkeypatch.setattr(EventLog, 'decode_line', decode)
    original_emit = artifact_output_delivery.emit_artifact_output
    emitted = []
    def emit(*args):
        original_emit(*args)
        emitted.append(1)
        writer.append(ZfEvent(type='worker.heartbeat', task_id='T'))
    monkeypatch.setattr(artifact_output_delivery, 'emit_artifact_output', emit)
    assert main([*command, '--pages', '4']) == 0
    assert len(capsys.readouterr().out.splitlines()) == 4
    assert len(emitted) == 4 and decoded == ['archived']


def test_all_batch_pages_require_complete_provider_output(tmp_path, monkeypatch, capsys):
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4
    from zf.core.state.role_sessions import RoleSessionRegistry
    from zf.runtime.artifact_output_delivery import observe_artifact_delivery

    state, _, command = _fixture(tmp_path, monkeypatch)
    assert main([*command, '--pages', '4']) == 0
    raw = capsys.readouterr().out
    sid, transcript = str(uuid4()), tmp_path / 'transcript.jsonl'
    RoleSessionRegistry(state / 'role_sessions.yaml', tmp_path).bind_codex_session('dev-1', sid, session_path=transcript)
    reads = [json.loads(s) for s in (state / 'artifacts/attempts/a/read-ledger.active.jsonl').read_text().splitlines()]
    def write_output(body):
        transcript.write_text('\n'.join(json.dumps(r) for r in [
            {'type': 'session_meta', 'payload': {'id': sid}},
            {'type': 'response_item', 'timestamp': (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
             'payload': {'type': 'function_call_output', 'call_id': 'call', 'output': body}},
        ]) + '\n')
    write_output(raw[:len(raw)//2])
    assert not all(r['provider_delivery_status'] == 'observed_in_transcript' for r in observe_artifact_delivery(state, reads))
    write_output(raw)
    assert all(r['provider_delivery_status'] == 'observed_in_transcript' for r in observe_artifact_delivery(state, reads))
