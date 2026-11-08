"""Optional presentation must round-trip complete authorized content."""
import hashlib
import json
from copy import deepcopy

import pytest

from zf.runtime.artifact_read_ledger import _encode_selected


def decode(records):
    bodies = {}
    sources = []
    for record in records:
        if record['kind'] == 'body':
            value = deepcopy(record['body'])
            for ref in record['refs']:
                cursor = value
                for part in ref['path'][:-1]:
                    cursor = cursor[part]
                cursor[ref['path'][-1]] = deepcopy(bodies[ref['ref']])
            bodies[record['id']] = value
        elif record['kind'] == 'source':
            content = _encode_selected(bodies[record['body_ref']])
            assert hashlib.sha256(content.encode()).hexdigest() == record['content_sha256']
            sources.append(content)
    return sources


def test_shared_bodies_preserve_exact_content_and_source_identity(tmp_path):
    from zf.runtime.artifact_shared_bodies import shared_body_records

    common = {'正文': '完整条款和边界🙂' * 120, 'refs': None, 'body_ref': ['not-a-reference']}
    values = [{'before': None, 'same': common}, {'same': common, 'after': [common, None]},
              'plain "text"', '{"raw":"text"}', 12, [False, None], {'逆序': 1, 'a': 2}]
    results = []
    for i, value in enumerate(values):
        results.append({'content': _encode_selected(value), 'read': {
            'source_id': f's{i}', 'artifact_id': f'a{i}', 'json_path': '$',
            'artifact_ref': f'artifacts/{i}.json', 'artifact_sha256': 'a' * 64,
            'status': 'read', 'truncated': False, 'attempt_id': 'attempt',
            'occurrence_id': f'occurrence-{i}',
        }})
    records = list(shared_body_records(results))
    assert decode(records) == [r['content'] for r in results]
    assert json.dumps(records, ensure_ascii=False).count(common['正文']) == 1
    sources = [r for r in records if r['kind'] == 'source']
    assert [r['source_id'] for r in sources] == [f's{i}' for i in range(len(results))]
    for r, original in zip(sources, results):
        encoded = json.dumps(original['read'], ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode()
        assert r['read_receipt']['row_sha256'] == hashlib.sha256(encoded).hexdigest()
        assert r['artifact_ref'] == original['read']['artifact_ref']
    assert decode(list(shared_body_records(results))) == decode(records)


@pytest.mark.parametrize('patch', [{'truncated': True}, {'status': 'denied'},
                                   {'artifact_sha256': ''}, {'max_chars': 10}])
def test_shared_bodies_refuse_incomplete_reads(patch):
    from zf.runtime.artifact_shared_bodies import shared_body_records

    read = {'source_id': 'a', 'artifact_id': 'a', 'json_path': '$', 'artifact_ref': 'a.json',
            'artifact_sha256': 'a' * 64, 'status': 'read', 'truncated': False, **patch}
    with pytest.raises(ValueError, match='complete'):
        list(shared_body_records([{'read': read, 'content': 'body'}]))
