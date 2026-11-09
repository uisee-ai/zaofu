import json
import pytest

from zf.runtime.artifact_navigation import build_navigation
from zf.runtime.artifact_navigation_shards import publish_navigation, query_navigation, NavigationWalk
from zf.runtime.artifact_input_index import input_index
from zf.runtime.artifact_input_page import expand_input_items
from zf.runtime.artifact_read_ledger import source_manifest_from_payload,active_read_ledger_path
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

IDENTITY={'actor':'worker','role':'verify','purpose':'candidate-verify'}


def corpus(count=1000):
    return {'tasks':[{'task_id':f'T{i}','acceptance_ids':[f'AC-{i}'],
                     'source_refs':['source.md#rule'],'image_refs':['ui.png#empty']} for i in range(count)]}


def pinned(state, count=1000, aliases=False):
    path=state/'input.json';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(corpus(count),ensure_ascii=False))
    source={'ref':'input.json','source_id':'plan','allowed_paths':['$']}
    sources=[source]
    if aliases:sources.append({**source,'source_id':'hidden','access_scope':{'actor':'other'}})
    manifest,ref=source_manifest_from_payload(state_dir=state,project_root=state,
        payload={'input_refs':sources},workflow_run_id='r',task_id='T1',attempt_id='a',dispatch_id='d',
        manifest_metadata={'read_purpose':'candidate-verify'})
    return manifest,ref


def lookup(state,manifest,**kwargs):
    return input_index(state_dir=state,manifest=manifest,required=[],identity=IDENTITY,reads=[],**kwargs)


def test_all_task_ac_and_shared_locations_survive_sharding_and_pagination(tmp_path):
    manifest,_=pinned(tmp_path)
    nav=manifest['sources'][0]['read_selections'][0]['navigation']
    assert nav['complete'] and nav['entry_count']==4000
    root=hydrate_sidecar_ref(tmp_path,nav['index_ref']).payload
    assert all(p['count']<=128 for pages in root['buckets'].values() for p in pages)
    assert len(json.dumps(nav).encode())<600
    for number in (0,499,999):
        for identifier in (f'T{number}',f'AC-{number}'):
            page=lookup(tmp_path,manifest,relation_id=identifier)
            assert page['navigation_complete'] and page['total']==1
            assert expand_input_items(page)[0]['navigation']['id']==identifier
    cursor=None;locations=[];sizes=[]
    while True:
        page=lookup(tmp_path,manifest,relation_id='source.md#rule',cursor=cursor)
        sizes.append(len(json.dumps(page,ensure_ascii=False).encode())+1)
        assert len(page['sources'])==1 and all(item['source']==0 for item in page['items'])
        locations.extend(item['navigation']['field_pointer'] for item in page['items'])
        cursor=page['next_cursor']
        if cursor is None:break
    assert set(locations)=={f'/tasks/{i}/source_refs/0' for i in range(1000)}
    assert len(locations)==len(set(locations)) and max(sizes)<=8192
    assert not active_read_ledger_path(tmp_path,'a').exists()


@pytest.mark.parametrize('fault',['root','shard','missing','legacy','unrecoverable'])
def test_navigation_recovers_only_pinned_version_without_rewriting_manifest(tmp_path,fault,monkeypatch):
    manifest,ref=pinned(tmp_path)
    original=(tmp_path/ref['ref']).read_bytes()
    source=manifest['sources'][0];snapshot=source['read_selections'][0]
    if fault=='legacy':
        from zf.runtime.artifact_read_snapshot import open_selection
        content=open_selection(tmp_path,manifest=manifest,identity=IDENTITY,row={'source_id':'plan','artifact_id':source['artifact_id'],'json_path':'$'}).read()
        snapshot['navigation']=build_navigation(content)
        assert snapshot['navigation']['complete'] is False
    else:
        root_ref=snapshot['navigation']['index_ref']
        root=hydrate_sidecar_ref(tmp_path,root_ref).payload
        if fault=='root':(tmp_path/root_ref['ref']).write_text('{}')
        else:
            for pages in root['buckets'].values():
                for page in pages:
                    path=tmp_path/page['ref']
                    path.unlink() if fault=='missing' else path.write_text('{}')
    (tmp_path/source['ref']).write_text('{"changed":true}')
    if fault=='unrecoverable':
        for chunk in snapshot['chunks']:(tmp_path/chunk['ref']).write_text('{}')
        (tmp_path/source['recovery_ref']['ref']).write_text('{}')
        with pytest.raises(ValueError):lookup(tmp_path,manifest,relation_id='T999')
    else:
        page=lookup(tmp_path,manifest,relation_id='T999')
        assert page['total']==1 and page['navigation_complete']
    assert (tmp_path/ref['ref']).read_bytes()==original
    if fault=='legacy':
        # The second lookup must use the reconstructed projection, not reparse
        # the original source on every query or continuation page.
        import zf.runtime.artifact_read_snapshot as snapshots
        monkeypatch.setattr(snapshots,'open_selection',lambda *a,**k:pytest.fail('full reread'))
        assert lookup(tmp_path,manifest,relation_id='AC-999')['total']==1


def test_authorization_precedes_navigation_hydration_even_for_same_digest(tmp_path,monkeypatch):
    manifest,_=pinned(tmp_path,aliases=True)
    page=lookup(tmp_path,manifest,relation_id='T999')
    assert page['total']==1 and 'hidden' not in json.dumps(page)
    denied={**manifest,'sources':[manifest['sources'][1]]}
    import zf.runtime.artifact_navigation_shards as shards
    monkeypatch.setattr(shards,'hydrate_sidecar_ref',lambda *a,**k:pytest.fail('unauthorized hydration'))
    assert lookup(tmp_path,denied,relation_id='T999')['total']==0


def test_publication_interruption_never_publishes_a_partial_manifest(tmp_path,monkeypatch):
    import zf.runtime.artifact_navigation_shards as shards
    real=shards.write_immutable_json_sidecar
    def fail(*args,**kwargs):
        if kwargs.get('kind')=='navigation_index':raise OSError('interrupted')
        return real(*args,**kwargs)
    monkeypatch.setattr(shards,'write_immutable_json_sidecar',fail)
    with pytest.raises(OSError):pinned(tmp_path)
    assert not list((tmp_path/'artifacts/attempts').glob('*/source-manifests/*.json'))


def test_wide_nodes_unicode_repeated_headings_and_unsupported_identifiers(tmp_path):
    data={'wide':[{} for _ in range(21000)],'tail':{'task_id':'任务-末尾'}}
    nav=publish_navigation(tmp_path,json.dumps(data,ensure_ascii=False))
    assert query_navigation(tmp_path,nav,relation_id='任务-末尾')[1][0]['field_pointer']=='/tail/task_id'
    content=''.join(f'## 重复\n正文 {i}\n' for i in range(700))
    nav=publish_navigation(tmp_path,content)
    metadata,rows=query_navigation(tmp_path,nav,query='重复')
    assert metadata['complete'] and len(rows)==700
    assert len({row['page_start'] for row in rows})==700
    assert content[rows[-1]['page_start']:rows[-1]['section_end']]=='## 重复\n正文 699\n'
    walk=NavigationWalk(json.dumps({'task_id':'x'*257}));assert list(walk)==[] and walk.complete is False
