"""Experiment identity and input isolation; these are not semantic quality tests."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def module():
    source = Path(__file__).resolve().parents[1] / 'scripts/eval-spec-handoff.py'
    spec = importlib.util.spec_from_file_location('spec_handoff', source)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_fresh_handoff_gets_only_source_and_exact_author_output(tmp_path, monkeypatch):
    m = module()
    calls = []
    spec = '## Task Spec: T1\n必须保持失败状态。\n'
    answer = {'requirements': [{'source': 'R1', 'assessment': 'covered'}],
              'handoff': '独立复述', 'findings': [], 'questions': [], 'implementation': None}

    def run(directory, prompt, args):
        files = {p.name: p.read_text() for p in (directory / 'project').iterdir() if p.is_file()}
        calls.append(files)
        return {'returncode': 0, 'turn_completed': True, 'timed_out': False,
                'changed_paths': [], 'errors': [], 'seconds': 1, 'usage': {},
                'trace_sha256': 'x', 'answers': [spec if len(calls) == 1 else json.dumps(answer)]}

    monkeypatch.setattr(m, 'helper', lambda: SimpleNamespace(run_one=run))
    row = m.execute({'id': 'case', 'requirement': 'R1: keep state'}, 'candidate', 'method',
                    SimpleNamespace(output=tmp_path, prepare_only=False, transport="docker"))
    assert calls == [{'requirement.md': 'R1: keep state', 'method.md': 'method'},
                     {'requirement.md': 'R1: keep state', 'spec.md': spec}]
    assert row['status'] == 'completed_ungraded'
    assert row['spec_sha256'] == m.sha(spec.encode())


def test_failed_author_never_creates_handoff_or_infers_semantic_pass(tmp_path, monkeypatch):
    m = module()
    result = {'returncode': 1, 'turn_completed': False, 'timed_out': True,
              'changed_paths': [], 'errors': ['timeout'], 'seconds': 240, 'usage': {},
              'trace_sha256': 'x', 'answers': []}
    monkeypatch.setattr(m, 'helper', lambda: SimpleNamespace(run_one=lambda *args: result))
    row = m.execute({'id': 'case', 'requirement': 'R1'}, 'baseline', 'method',
                    SimpleNamespace(output=tmp_path, prepare_only=False, transport="docker"))
    assert row['status'] == 'author_failed'
    assert not (tmp_path / 'case-baseline/handoff').exists()


@pytest.mark.parametrize('value', ['null', '[]', '{"requirements":{}}', '{"requirements":[],"implementation":42}'])
def test_invalid_provider_body_is_not_silently_repaired(value):
    with pytest.raises(ValueError):
        module().parse_handoff(value)


def test_frozen_cases_and_oracles_align_without_answers_in_author_material():
    m = module()
    cases = json.loads((m.FIXTURES / 'cases.json').read_text())
    oracles = json.loads((m.FIXTURES / 'oracle.json').read_text())
    assert len(cases) == 6
    assert {c['id'] for c in cases} == {c['id'] for c in oracles}
    assert sum(c['implementation'] for c in cases) == 2
    assert all(set(c) == {'id', 'title', 'requirement', 'implementation'} for c in cases)
    assert all(len(c['expectations']) == 4 for c in oracles)


def test_text_mode_embeds_only_explicit_inputs_and_keeps_read_only_transport(tmp_path, monkeypatch):
    m = module()
    prompts = []

    def run(directory, prompt, args):
        prompts.append(prompt)
        return {'returncode': 0, 'turn_completed': True, 'timed_out': False,
                'changed_paths': [], 'commands': [], 'errors': [], 'seconds': 1, 'usage': {},
                'trace_sha256': 'x', 'answers': ['SPEC' if len(prompts) == 1 else '{"requirements":[],"implementation":null}']}

    monkeypatch.setattr(m, 'helper', lambda: SimpleNamespace(run_one=run))
    row = m.execute({'id': 'case', 'requirement': 'FROZEN'}, 'candidate', 'METHOD',
                    SimpleNamespace(output=tmp_path, prepare_only=False, transport='host-text'))
    assert row['status'] == 'completed_ungraded'
    assert 'FROZEN' in prompts[0] and 'METHOD' in prompts[0]
    assert 'FROZEN' in prompts[1] and 'SPEC' in prompts[1] and 'METHOD' not in prompts[1]
    assert all('不要调用任何工具' in p for p in prompts)


def test_clarification_is_identical_for_both_arms_and_registered(tmp_path, monkeypatch):
    import sys
    m = module()
    extra = tmp_path / 'clarification.json'
    extra.write_text(json.dumps({'dependency-cycle': 'R4 frozen command facts'}))
    monkeypatch.setattr(m.subprocess, 'check_output', lambda cmd, **kw: 'a' * 40 + '\n' if kw.get('text') else b'BASE_METHOD')
    monkeypatch.setattr(m, 'helper', lambda: pytest.fail('prepare must not call provider'))
    output = tmp_path / 'prepared'
    monkeypatch.setattr(sys, 'argv', ['eval', '--output', str(output), '--model', 'test',
                                    '--prepare-only', '--case', 'dependency-cycle',
                                    '--case-addendum', str(extra)])
    assert m.main() == 0
    a = (output / 'dependency-cycle-baseline/author/project/requirement.md').read_bytes()
    b = (output / 'dependency-cycle-candidate/author/project/requirement.md').read_bytes()
    assert a == b and b'R4 frozen command facts' in a
    manifest = json.loads((output / 'manifest.json').read_text())
    assert manifest['max_provider_calls'] == 4
    assert manifest['addendum_sha256'] == m.sha((output / 'case-addendum.json').read_bytes())
