import json

import pytest

from tests.test_plan_local_review import _inputs
from zf.cli.main import main
from zf.runtime.plan_scope_evidence import prepare_scope_evidence


def inputs():
    old_map, new_map, old_port, new_port = _inputs()
    encode = lambda value: json.dumps(value).encode()
    return ({'task_map': encode(old_map), 'execution_spec': encode(old_port)},
            {'task_map': encode(new_map), 'execution_spec': encode(new_port)},
            {'workflow_run_id': 'run', 'baseline_package_sha256': 'a' * 64, 'affected_task_ids': ['A'],
             'evidence_refs': ['requirement'], 'scope_complete': True, 'verification_responsibility_preserved': True})


def test_cli_computes_existing_certificate_without_inventing_semantic_scope(tmp_path, capsys):
    old, new, claim = inputs()
    for label, bundle in [('old', old), ('new', new)]:
        paths = {}
        for name, raw in bundle.items():
            path = tmp_path / f'{label}-{name}.json'
            path.write_bytes(raw)
            paths[name] = path.name
        (tmp_path / f'{label}.json').write_text(json.dumps(paths))
    (tmp_path / 'claim.json').write_text(json.dumps(claim))
    assert main(['plan', 'prepare-scope-review', '--baseline-bundle', str(tmp_path / 'old.json'),
                 '--candidate-bundle', str(tmp_path / 'new.json'), '--claim', str(tmp_path / 'claim.json')]) == 0
    assert json.loads(capsys.readouterr().out) == prepare_scope_evidence(old, new, claim)


@pytest.mark.parametrize('problem', ['claim', 'shared', 'task_set', 'unaffected', 'global_port'])
def test_scope_evidence_keeps_ambiguous_or_global_changes_full(problem):
    old, new, claim = inputs()
    if problem == 'claim': claim['scope_complete'] = False
    if problem in {'shared', 'unaffected'}:
        body = json.loads(new['execution_spec'])
        if problem == 'shared': body['shared']['interface'] = 'v2'
        else: body['tasks'][2]['body']['evidence'] = 'changed'
        new['execution_spec'] = json.dumps(body).encode()
    if problem == 'task_set':
        body = json.loads(new['task_map']); body['tasks'].pop()
        new['task_map'] = json.dumps(body).encode()
    if problem == 'global_port': old['matrix'] = b'{"v":1}'; new['matrix'] = b'{"v":2}'
    with pytest.raises(ValueError): prepare_scope_evidence(old, new, claim)
