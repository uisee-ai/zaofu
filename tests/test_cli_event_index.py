"""Bounded event discovery must retain exact access to complete evidence."""

import json

from zf.cli.main import main
from zf.core.events import EventLog, EventWriter, ZfEvent
from tests.test_cli_events import _init_with_state_dir


def test_index_avoids_recursive_tool_output_but_preserves_full_event(tmp_path, monkeypatch, capsys):
    _init_with_state_dir(tmp_path, monkeypatch)
    log = EventLog(tmp_path / 'runtime-state/events.jsonl')
    body = 'RECURSIVE TOOL OUTPUT ' * 10000
    ref = {'ref': 'artifacts/tool-output/full.json', 'sha256': 'a' * 64, 'byte_count': len(body)}
    event = EventWriter(log).append(ZfEvent(
        type='codex.hook.post_tool_use', actor='verify-1', task_id='TASK-1',
        payload={'workflow_run_id': 'wf-1', 'dispatch_id': 'dispatch-1',
                 'tool_name': 'exec_command', 'tool_input': {'cmd': 'python -m unittest'},
                 'output': body, 'refs': {'raw_output': ref}},
    ))
    log.close()
    capsys.readouterr()
    assert main(['events', '--actor', 'verify-1', '--index', '--json']) == 0
    output = capsys.readouterr().out
    data = json.loads(output)['data']
    assert data['content_mode'] == 'index'
    indexed = data['events'][0]
    assert indexed['id'] == event.id
    assert indexed['payload']['dispatch_id'] == 'dispatch-1'
    assert indexed['payload']['refs']['raw_output'] == ref
    assert indexed['tool_input_preview'] == 'python -m unittest'
    assert indexed['payload_omitted'] is True
    assert body not in output and len(output) < 3000
    assert main(['events', '--id', event.id, '--json']) == 0
    full = json.loads(capsys.readouterr().out)['data']['events'][0]
    assert full['payload']['output'] == body


def test_index_filters_before_last_and_bounds_input_preview(tmp_path, monkeypatch, capsys):
    _init_with_state_dir(tmp_path, monkeypatch)
    log = EventLog(tmp_path / 'runtime-state/events.jsonl')
    writer = EventWriter(log)
    wanted = writer.append(ZfEvent(type='tool.result', actor='verify-1', payload={
        'tool_input': {'command': 'x' * 10000}, 'refs': {'raw_output': {'ref': 'body.json', 'sha256': 'b' * 64}},
    }))
    writer.append(ZfEvent(type='tool.result', actor='other', payload={'output': 'unrelated'}))
    before = log.path.read_bytes()
    log.close()
    capsys.readouterr()
    assert main(['events', '--actor', 'verify-1', '--last', '1', '--index', '--json']) == 0
    rows = json.loads(capsys.readouterr().out)['data']['events']
    assert [row['id'] for row in rows] == [wanted.id]
    assert len(rows[0]['tool_input_preview']) <= 240
    assert (tmp_path / 'runtime-state/events.jsonl').read_bytes() == before
