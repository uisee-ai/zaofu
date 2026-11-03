import copy
import json

from fastapi.testclient import TestClient
from zf.cli.main import main
from zf.runtime.plan_input_review import inspect_plan_inputs
from zf.web.server import create_app


def artifacts(count=100):
    return {
        'task_map': {'tasks':[{'task_id':f'T{i}','title':f'Task {i}',
            'blocked_by':[f'T{i-1}'] if i else [],
            'acceptance_criteria':[{'id':f'AC-{i}','source_ref':f'source.md#req{i}'}],
            'evidence_contract':{'plan_input_selection':{'schema_version':'task-plan-input-selection.v1',
                'implementation':['task_execution_input'],'task-verify':['task_execution_input','test_matrix']}}} for i in range(count)]},
        'task_execution_input':{'schema_version':'task-scoped-plan-input.v1','shared':{'rule':'接口 v1，不得重复扣减'},
            'tasks':[{'task_id':f'T{i}','body':{'acceptance_ids':[f'AC-{i}'],
                'behavior':'授权用户修改库存；失败保持原值','exception':'重复请求不得再次扣减',
                'source_refs':[f'source.md#req{i}'],'image_refs':[f'image-{i}.png#empty']}} for i in range(count)]},
        'source_index':{'rows':[{'source_id':f'REQ-{i}','source_ref':f'source.md#req{i}','task_ids':[f'T{i}']} for i in range(count)]},
        'source_inventory':{'pages':[{'id':f'UI-{i}','task_ids':[f'T{i}'],'reference_images':[f'image-{i}.png#empty']} for i in range(count)]},
        'coverage_ledger':{'rows':[{'source_id':'REQ-0','disposition':'explicit_non_goal','reason':'本次排除导出','task_ids':['T0']}]},
    }


def test_hundred_task_inspection_pagination_and_profile_boundaries():
    bundle=artifacts()
    page=inspect_plan_inputs(bundle,task_id='T7')
    assert page['total']==1 and len(json.dumps(page,ensure_ascii=False).encode())<8192
    task=page['items'][0]
    assert task['issues']==[]
    assert task['acceptance_ids']==['AC-7'] and task['image_refs']==['image-7.png#empty']
    assert task['dependencies']==['T6']
    assert task['profiles'][0]['paths']==['$.shared','$.tasks[7]']
    assert all(p['ports'] is None for p in task['profiles'][2:])
    assert task['semantic_review']=='required' and task['approval']=='not_evaluated'
    all_ids=[]; offset=0
    while True:
        page=inspect_plan_inputs(bundle,offset=offset)
        all_ids.extend(row['task_id'] for row in page['items'])
        offset=page['next_offset']
        if offset is None: break
    assert all_ids==[f'T{i}' for i in range(100)]


def test_mechanical_gaps_are_not_confused_with_undetectable_semantics():
    bundle=artifacts(2)
    body=bundle['task_execution_input']['tasks'][1]['body']
    body.pop('exception')  # AC IDs alone cannot detect a semantic omission.
    row=inspect_plan_inputs(bundle,task_id='T1')['items'][0]
    assert row['issues']==[] and row['semantic_review']=='required'
    body['acceptance_ids']=[]; body['image_refs']=[]
    row=inspect_plan_inputs(bundle,task_id='T1')['items'][0]
    assert 'acceptance_reference_missing:AC-1' in row['issues']
    assert 'image_reference_missing:image-1.png#empty' in row['issues']
    bundle['task_execution_input']['tasks'].append(copy.deepcopy(bundle['task_execution_input']['tasks'][1]))
    assert any(i.startswith('partition_invalid:') for i in inspect_plan_inputs(bundle,task_id='T1')['items'][0]['issues'])
    bundle.pop('source_index')
    assert inspect_plan_inputs(bundle)['source_index_available'] is False


def test_producer_cli_and_bound_preview_api(tmp_path,capsys):
    bundle=artifacts(2)
    mapping={}
    for name,body in bundle.items():
        path=tmp_path/f'{name}.json';path.write_text(json.dumps(body));mapping[name]=path.name
    (tmp_path/'bundle.json').write_text(json.dumps(mapping))
    assert main(['plan','inspect-inputs','--bundle',str(tmp_path/'bundle.json'),'--task','T1'])==0
    assert json.loads(capsys.readouterr().out)['items'][0]['task_id']=='T1'
    from tests.plan_human_review_fixture import add_human_review_fixture
    from zf.core.events import EventLog,EventWriter
    state=tmp_path/'state';(state/'artifacts').mkdir(parents=True)
    (state/'kanban.json').write_text(json.dumps({'tasks': []}))
    add_human_review_fixture(state,EventWriter(EventLog(state/'events.jsonl')))
    client=TestClient(create_app(state,project_root=tmp_path))
    response=client.get('/api/projects/default/plans/evt-preview-review/input-review?task=T-STOCK')
    assert response.status_code==200,response.text
    assert response.json()['items'][0]['task_id']=='T-STOCK'
    assert 'task_execution_input_unavailable' in response.json()['items'][0]['issues']
    assert client.get('/api/projects/default/plans/evt-preview-review/input-review?limit=0').status_code==400


def test_unknown_refs_members_and_navigation_limits_remain_explicit():
    bundle=artifacts(2)
    bundle['task_map']['tasks'][1]['blocked_by']=['T404']
    bundle['task_execution_input']['tasks'][1]['body']['source_refs']=['wrong.md#ref']
    bundle['task_execution_input']['tasks'].append({'task_id':'unknown','body':{}})
    report=inspect_plan_inputs(bundle,task_id='T1',plan_id='plan-1')
    assert report['extra_partition_ids']==['unknown']
    assert report['items'][0]['profiles'][1]['unavailable_ports']==['test_matrix']
    assert 'unknown_dependency:T404' in report['items'][0]['issues']
    assert 'source_reference_unknown:wrong.md#ref' in report['items'][0]['issues']
    bundle=artifacts(2)
    bundle['task_execution_input']['shared']['requirement_ids']=[f'REQ-{i}' for i in range(600)]
    report=inspect_plan_inputs(bundle,task_id='T1')
    assert report['items'][0]['navigation_complete'] is False
    assert 'reference_lookup_incomplete:AC-1' in report['items'][0]['issues']
    assert not any(issue.startswith('acceptance_reference_missing') for issue in report['items'][0]['issues'])
