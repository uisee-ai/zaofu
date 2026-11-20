from copy import deepcopy

from zf.runtime.artifact_read_requirements import build_canonical_required_reads


def test_exact_plan_aliases_share_body_but_remain_expandable():
    first = {'source_id': 'child-artifact-a', 'artifact_id': 'a.json', 'sha256': 'a' * 64,
             'ref': 'artifacts/a.json', 'allowed_paths': ['$']}
    alias = {**first, 'source_id': 'child-artifact-b', 'artifact_id': 'b.json', 'ref': 'artifacts/b.json'}
    manifest = {'sources': [first, alias]}
    read = lambda value, **kw: build_canonical_required_reads(value, output_profile_id='plan-synth', **kw)
    assert len(read(manifest)) == 1
    assert len(manifest['sources']) == 2  # IDs and controlled expansion are retained.
    for change in ({'sha256': 'b' * 64}, {'access_scope': {'roles': ['critic']}},
                   {'source_event_id': 'new'}, {'retention': {'kind': 'required'}}, {'allowed_paths': ['$.body']}):
        changed = deepcopy(manifest)
        changed['sources'][1].update(change)
        assert len(read(changed)) == 2
    explicit = {'source_id': alias['source_id'], 'artifact_id': alias['artifact_id'],
                'artifact_sha256': alias['sha256'], 'json_path': '$'}
    assert len(read(manifest, explicit=[explicit])) == 2
