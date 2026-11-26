"""Bounded event discovery must retain exact access to complete evidence."""

import json
from pathlib import Path
import shlex

from zf.cli.main import main
from zf.core.events import EventLog, EventWriter, ZfEvent
from tests.test_cli_events import _init_with_state_dir


def test_fresh_probe_skill_locates_current_reader_even_when_task_id_is_empty(tmp_path, monkeypatch, capsys):
    root = Path(__file__).resolve().parents[1]
    procedure = root / "skills/zf-harness-verification-checklist/references/fresh-candidate-probe.md"
    _init_with_state_dir(tmp_path, monkeypatch)
    state = tmp_path / "runtime-state"
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    for attempt in ("previous", "current"):
        dispatch = writer.append(ZfEvent(
            type="fanout.child.dispatched", correlation_id="run", actor="zf-cli",
            payload={"operation_id": "op-" + attempt, "attempt_id": attempt},
        ))
        hook = writer.append(ZfEvent(
            type="codex.hook.post_tool_use", actor="reader", causation_id=dispatch.id, correlation_id="run",
            payload={"tool_input": {"command": "python3 check.py"}, "tool_response": attempt},
        ))
    log.close()
    capsys.readouterr()
    values = {"<role-instance>": "reader", "<state-dir>": str(state),
              "<hook-event-id>": hook.id, "<dispatch-event-id>": dispatch.id}
    results = []
    for line in procedure.read_text().splitlines():
        if line.strip().startswith("zf events "):
            args = [values.get(arg, arg) for arg in shlex.split(line.strip())[1:]]
            assert main(args) == 0
            results.append(json.loads(capsys.readouterr().out)["data"]["events"])
    index, [evidence], [source] = results
    assert len(index) == 2
    assert [row["id"] for row in index if row["causation_id"] == dispatch.id] == [evidence["id"]]
    assert evidence["task_id"] is None and evidence["payload"]["tool_response"] == "current"
    assert "exit_code" not in evidence["payload"]
    assert evidence["causation_id"] == source["id"]
    assert source["payload"] == {"operation_id": "op-current", "attempt_id": "current"}


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
