"""Frozen oracles and trace binding precede execution of generated code."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


def module():
    path = Path(__file__).resolve().parents[1] / 'scripts/verify-spec-handoff.py'
    spec = importlib.util.spec_from_file_location('spec_verify', path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def fixture(tmp_path, m):
    oracle = tmp_path / 'repo/tests/fixtures/spec_handoff_eval/oracle.json'
    oracle.parent.mkdir(parents=True)
    oracle.write_text(json.dumps([{'id': 'save-failure', 'test_code': 'ORACLE'}]))
    m.ROOT = tmp_path / 'repo'
    run = tmp_path / 'run'; handoff = run / 'save-failure-candidate/handoff'
    handoff.mkdir(parents=True)
    trace = handoff / 'trace.jsonl'
    trace.write_text(json.dumps({'type': 'item.completed', 'item': {
        'type': 'agent_message', 'text': json.dumps({'implementation': 'BOUND_CODE'})}}) + '\n')
    (handoff / 'parsed.json').write_text('{"implementation":"UNBOUND_CODE"}')
    manifest = {'schema_version': 'spec-handoff-eval.v1', 'oracle_sha256': m.sha(oracle),
                'runs': [{'case': 'save-failure', 'arm': 'candidate', 'status': 'completed_ungraded',
                          'handoff': {'trace_sha256': m.sha(trace)}}]}
    (run / 'manifest.json').write_text(json.dumps(manifest))
    return run, oracle, trace


def test_execution_uses_bound_trace_and_networkless_read_only_container(tmp_path, monkeypatch):
    m = module(); run, _, _ = fixture(tmp_path, m)
    monkeypatch.setattr(sys, 'argv', ['verify', '--run', str(run)])
    monkeypatch.setattr(m.subprocess, 'check_output', lambda *a, **k: 'sha256:frozen\n')
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(m.subprocess, 'run', execute)
    assert m.main() == 0
    command = calls[0]
    assert command[command.index('--network') + 1] == 'none'
    assert '--read-only' in command and '--cap-drop=ALL' in command
    assert (run / 'implementation-verification/save-failure-candidate/implementation.mjs').read_text() == 'BOUND_CODE'
    assert not any('auth.json' in s for s in command)


@pytest.mark.parametrize('changed', ['oracle', 'trace'])
def test_changed_evidence_is_rejected_before_code_execution(tmp_path, monkeypatch, changed):
    m = module(); run, oracle, trace = fixture(tmp_path, m)
    (oracle if changed == 'oracle' else trace).write_text('changed')
    monkeypatch.setattr(sys, 'argv', ['verify', '--run', str(run)])
    monkeypatch.setattr(m.subprocess, 'check_output', lambda *a, **k: 'sha256:frozen\n')
    monkeypatch.setattr(m.subprocess, 'run', lambda *a, **k: pytest.fail('code must not execute'))
    with pytest.raises(ValueError):
        m.main()
