#!/usr/bin/env python3
"""Offline context evaluation fixtures and accounting; never calls a provider."""
from __future__ import annotations

import argparse
import copy
import json
import random
import tempfile
from pathlib import Path
from time import perf_counter

from zf.runtime.call_result_envelope import canonical_json_sha256

CATEGORIES = ('exception', 'shared_rule', 'image_state', 'dependency')
SOURCE = {
    'source.md#exception': '重复请求必须返回首次结果，不得再次扣减。',
    'source.md#shared': '所有修改必须保留审计记录，记录操作者和原值。',
    'screen.png#empty': '空库存页面显示补货入口，禁止显示可出库按钮。',
    'api.md#dependency': 'T1 使用 T0 提供的版本化库存接口；版本冲突返回 409。',
}


def sample_bundle(count: int = 100) -> dict:
    """Illustrative producer fragments using the existing partition contract."""
    return {
        'task_map': {'tasks': [{
            'task_id': f'T{i}', 'title': f'库存操作 {i}',
            'blocked_by': [f'T{i-1}'] if i else [],
            'acceptance_criteria': [{'id': f'AC-{i}', 'source_ref': 'source.md#exception'}],
            'evidence_contract': {'plan_input_selection': {
                'schema_version': 'task-plan-input-selection.v1',
                'implementation': ['task_execution_input'], 'task-verify': ['task_execution_input']}},
        } for i in range(count)]},
        'task_execution_input': {'schema_version': 'task-scoped-plan-input.v1',
            'shared': {'rule': SOURCE['source.md#shared'], 'source_refs': ['source.md#shared']},
            'tasks': [{'task_id': f'T{i}', 'body': {
                'acceptance_ids': [f'AC-{i}'], 'source_refs': ['source.md#exception', 'api.md#dependency', 'screen.png#empty'],
                'image_refs': ['screen.png#empty'],
                'behavior': '授权用户申请出库。',
                'exception': SOURCE['source.md#exception'],
                'image_state': SOURCE['screen.png#empty'],
                'dependency': SOURCE['api.md#dependency'],
            }} for i in range(count)]},
        'source_index': {'rows': [{'source_id': f'REQ-{n}', 'source_ref': ref,
            'task_ids': [f'T{i}' for i in range(count)]} for n, ref in enumerate(SOURCE)]},
        'source_inventory': {'pages': [{'id': 'stock', 'task_ids': [f'T{i}' for i in range(count)],
            'reference_images': ['screen.png#empty']}]},
        'coverage_ledger': {'rows': [{'source_id': f'REQ-{n}', 'disposition': 'implemented_by_plan',
            'task_ids': [f'T{i}' for i in range(count)]} for n in range(len(SOURCE))]},
    }


def prepare(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    bundle = sample_bundle()
    mapping = {}
    for name, body in bundle.items():
        path = output / f'{name}.json'
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2))
        mapping[name] = path.name
    (output / 'bundle.json').write_text(json.dumps(mapping))
    variants = [('complete', copy.deepcopy(bundle))]
    for category in CATEGORIES:
        modified = copy.deepcopy(bundle)
        if category == 'shared_rule':
            modified['task_execution_input']['shared'].pop('rule')
        else:
            modified['task_execution_input']['tasks'][1]['body'].pop(category)
        variants.append((category, modified))
    random.Random(198).shuffle(variants)
    blind, expected = [], []
    refs = dict(zip(CATEGORIES, SOURCE))
    for number, (category, body) in enumerate(variants):
        case_id = f'case-{number+1:02}'
        blind.append({'case_id': case_id, 'review_task': 'T1',
            'original_material': SOURCE, 'plan': body})
        expected.append({'case_id': case_id, 'findings': [] if category == 'complete' else [{
            'task_id': 'T1', 'category': category, 'source_ref': refs[category],
            'source_quote': SOURCE[refs[category]],
        }]})
    protocol = ('独立对账 T1 的原始资料与 shared/owned 输入；即使 AC/来源 ID 齐全，也检查异常、共享规则、'
                '图片状态和依赖接口。输出 cases:[{case_id, findings:[{task_id, category, source_ref, source_quote, explanation}]}]。'
                '无遗漏时 findings=[]；不得读取 grader.json。')
    request = {'schema_version': 'context-input-blind-eval.v1', 'protocol': protocol, 'cases': blind}
    digest = canonical_json_sha256(request)
    request['evaluation_digest'] = digest
    rubric = {'evaluation_digest': digest, 'cases': expected, 'original_material': SOURCE}
    (output / 'blind.json').write_text(json.dumps(request, ensure_ascii=False, indent=2))
    (output / 'grader.json').write_text(json.dumps(rubric, ensure_ascii=False, indent=2))
    return {'cases': len(blind), 'tasks': 100, 'evaluation_digest': digest,
            'semantic_qualification': 'not_run', 'provider_invoked': False}


def score(rubric: dict, findings: dict) -> dict:
    if findings.get('evaluation_digest') != rubric['evaluation_digest']:
        raise ValueError('findings must bind the exact blind evaluation digest')
    cases = findings.get('cases')
    if not isinstance(cases, list) or len(cases) != len(rubric['cases']):
        raise ValueError('all blind cases require explicit findings, including empty findings')
    actual = {}
    for case in cases:
        key = case['case_id']
        if key in actual or not isinstance(case.get('findings'), list):
            raise ValueError('duplicate or invalid case')
        rows = set()
        for finding in case['findings']:
            ref = finding['source_ref']
            quote = finding.get('source_quote')
            if (not isinstance(quote, str) or not quote.strip()
                    or quote not in rubric['original_material'].get(ref, '')
                    or not str(finding.get('explanation') or '').strip()):
                raise ValueError('each finding requires original evidence and an explanation')
            rows.add((finding['task_id'], finding['category'], ref))
        actual[key] = rows
    expected = {case['case_id']: {(row['task_id'], row['category'], row['source_ref']) for row in case['findings']}
                for case in rubric['cases']}
    if actual.keys() != expected.keys():
        raise ValueError('case membership mismatch')
    tp = sum(len(actual[key] & expected[key]) for key in expected)
    fp = sum(len(actual[key] - expected[key]) for key in expected)
    fn = sum(len(expected[key] - actual[key]) for key in expected)
    return {'schema_version': 'context-input-rubric-score.v1',
            'evaluation_digest': rubric['evaluation_digest'],
            'matched_findings': tp, 'unexpected_findings': fp, 'missed_findings': fn,
            'exact_cases': sum(actual[key] == expected[key] for key in expected),
            'total_cases': len(expected), 'scoring_scope': 'explicit_rubric_findings',
            'semantic_qualification': 'requires_independent_review',
            'provider_provenance': findings.get('provider_provenance')}


def benchmark() -> dict:
    from zf.runtime.artifact_read_ledger import source_manifest_from_payload
    from zf.cli.artifact_required_read import prepare_required_read
    # No EventLog or runtime Task mutations. Temporary read snapshots are cleaned.
    with tempfile.TemporaryDirectory(prefix='zf-context-benchmark-') as directory:
        state = Path(directory)
        source = state / 'input.json'
        body = sample_bundle()['task_execution_input']
        # Make each task body substantial enough to exercise continuation.
        for row in body['tasks']:
            row['body']['acceptance_detail'] = '逐项验证原值、失败状态和重复请求。' * 200
        source.write_text(json.dumps(body, ensure_ascii=False))
        start = perf_counter()
        manifest, _ = source_manifest_from_payload(state_dir=state, project_root=state,
            payload={'input_refs': [{'ref': 'input.json', 'source_id': 'plan-port-task_execution_input',
                                    'allowed_paths': ['$.shared', '$.tasks[1]']}]},
            workflow_run_id='offline', task_id='T1', attempt_id='offline-1', dispatch_id='offline-1')
        build_ms = (perf_counter()-start)*1000
        required = [{'source_id': s['source_id'], 'artifact_id': s['artifact_id'],
                     'artifact_sha256': s['sha256'], 'json_path': path}
                    for s in manifest['sources'] for path in s['allowed_paths']]
        start = perf_counter()
        cursor, pages, output_bytes, body_bytes = None, 0, 0, 0
        while True:
            output, _ = prepare_required_read(state, manifest=manifest, rows=required,
                identity={'actor': 'offline', 'role': 'dev', 'purpose': 'implementation'}, cursor=cursor, page_bytes=4096)
            page = json.loads(output)
            pages += 1
            output_bytes += len((output+'\n').encode())
            body_bytes += sum(len(row['content'].encode()) for row in page['items'])
            cursor = page['next_cursor']
            if cursor is None:
                break
        return {'schema_version': 'context-host-benchmark.v1', 'tasks': 100,
            'source_bytes': source.stat().st_size, 'manifest_bytes': len(json.dumps(manifest, ensure_ascii=False).encode()),
            'build_snapshot_navigation_ms': build_ms, 'paged_prepare_ms': (perf_counter()-start)*1000,
            'prepared_output_bytes': output_bytes, 'prepared_body_bytes': body_bytes, 'pages': pages,
            'provider_usage': None, 'semantic_qualification': 'not_run',
            'note': '宿主离线观测，未输出给 worker，不生成正文回执，不证明 token 节约。'}


def score_locations(expected: set[tuple], actual: list[tuple], *, complete: bool = True) -> dict:
    """Compare full location sets, preserving distinct references and duplicates."""
    from collections import Counter
    observed = Counter(actual)
    found = set(observed)
    matched = len(expected & found)
    missing, unexpected = len(expected-found), len(found-expected)
    duplicates = sum(count-1 for count in observed.values())
    return {'expected':len(expected), 'returned':len(actual), 'matched':matched,
            'missing':missing, 'unexpected':unexpected, 'duplicates':duplicates,
            'precision':matched/len(found) if found else (1.0 if not expected else 0.0),
            'recall':matched/len(expected) if expected else 1.0, 'complete':complete,
            'passed':not (missing or unexpected or duplicates) and complete}


def retrieval(count: int = 100) -> dict:
    """Exhaustive Task/AC fixtures with independent expected location sets."""
    import inspect
    import hashlib
    from zf.runtime.artifact_input_index import input_index
    from zf.runtime.artifact_read_ledger import source_manifest_from_payload
    from zf.runtime.artifact_read_snapshot import open_selection
    from zf.runtime.artifact_navigation import build_navigation

    if not 1 <= count <= 10000:
        raise ValueError('offline corpus size must be 1..10000')
    signature = inspect.signature(input_index)
    with tempfile.TemporaryDirectory(prefix='zf-context-retrieval-') as directory:
        state = Path(directory)
        body = sample_bundle(count)['task_execution_input']
        path = state/'plan.json'
        path.write_text(json.dumps(body,ensure_ascii=False))
        source_bytes=path.stat().st_size
        fixture_digest=hashlib.sha256(path.read_bytes()).hexdigest()
        start=perf_counter()
        # One fixture producer prepares all selectors. Each simulated role sees
        # only its declared subset; real credential/CLI admission has separate tests.
        all_paths=['$','$.shared',*[f'$.tasks[{i}]' for i in range(count)]]
        base,_=source_manifest_from_payload(state_dir=state,project_root=state,
            payload={'input_refs':[{'source_id':'plan','ref':'plan.json','allowed_paths':all_paths}]},
            workflow_run_id='offline',task_id='T0',attempt_id='offline',dispatch_id='offline')
        build_ms=(perf_counter()-start)*1000
        source=base['sources'][0]
        snapshots={item['json_path']:item for item in source['read_selections']}
        profiles={}
        failures=[]
        budgets=[]
        navigation_shapes=set()
        for profile,role in [('implementation','dev'),('task-verify','verify'),('candidate-verify','verify')]:
            stats={key:0 for key in ('queries','passed','expected','returned','matched','missing','unexpected','duplicates','incomplete','output_bytes','pages')}
            stats['query_host_ms']=0.0
            stats['shared_queries']=[]
            profiles[profile]=stats
            candidate=profile=='candidate-verify'
            indexes=[None] if candidate else range(count)
            for number in indexes:
                paths=['$'] if candidate else ['$.shared',f'$.tasks[{number}]']
                current_source={**source,'allowed_paths':paths,'read_selections':[snapshots[p] for p in paths],
                                'access_scope':{'roles':[role]}}
                manifest={**base,'task_id':'T0' if candidate else f'T{number}',
                          'sources':[current_source]}
                required=[{'source_id':'plan','artifact_id':source['artifact_id'],
                           'artifact_sha256':source['sha256'],'json_path':p} for p in paths]
                identity={'actor':profile,'role':role,'purpose':profile}
                selected_numbers=range(count) if candidate else [number]

                # The oracle comes from fixture positions, independently of the
                # extractor, its limit, page ordering and matching function.
                gold={}
                def add(identifier, relation, pointer, selector):
                    gold.setdefault(identifier,set()).add(('plan',selector,'relation',relation,identifier,pointer))
                for i in selected_numbers:
                    selector='$' if candidate else f'$.tasks[{i}]'
                    prefix=f'/tasks/{i}' if candidate else ''
                    add(f'T{i}','task',prefix+'/task_id',selector)
                    add(f'AC-{i}','acceptance',prefix+'/body/acceptance_ids/0',selector)
                    add('source.md#exception','source',prefix+'/body/source_refs/0',selector)
                    add('screen.png#empty','source',prefix+'/body/source_refs/2',selector)
                    add('screen.png#empty','image',prefix+'/body/image_refs/0',selector)
                add('source.md#shared','source','/shared/source_refs/0' if candidate else '/source_refs/0',
                    '$' if candidate else '$.shared')

                def run_query(identifier, *, who=None, expected=None):
                    expect=gold.get(identifier,set()) if expected is None else expected
                    rows=[];cursor=None;seen=set();output_bytes=pages=0;complete=True
                    started=perf_counter()
                    while True:
                        kwargs=dict(manifest=manifest,required=required,identity=who or identity,reads=[],
                                    relation_id=identifier,navigate=True,cursor=cursor,limit=50)
                        if 'state_dir' in signature.parameters:kwargs['state_dir']=state
                        page=input_index(**kwargs)
                        navigation_shapes.add(page['schema_version'])
                        byte_count=len((json.dumps(page,ensure_ascii=False)+'\n').encode())
                        budgets.append(byte_count)
                        output_bytes+=byte_count;pages+=1
                        complete &= page.get('navigation_complete') is not False
                        for item in page['items']:
                            parent=page['sources'][item['source']] if page.get('schema_version')=='attempt-input-index.v2' else item
                            entry=item.get('navigation') or {}
                            rows.append((parent['source_id'],parent['json_path'],entry.get('kind'),
                                         entry.get('relation'),entry.get('id'),entry.get('field_pointer')))
                        cursor=page['next_cursor']
                        if cursor is None:break
                        if cursor in seen:raise ValueError('pagination loop')
                        seen.add(cursor)
                    metrics=score_locations(expect,rows,complete=complete)
                    stats['queries']+=1;stats['passed']+=metrics['passed']
                    for key in ('expected','returned','matched','missing','unexpected','duplicates'):
                        stats[key]+=metrics[key]
                    stats['incomplete']+=not complete
                    stats['output_bytes']+=output_bytes;stats['pages']+=pages
                    stats['query_host_ms']+=(perf_counter()-started)*1000
                    if not metrics['passed'] and len(failures)<20:
                        failures.append({'profile':profile,'task':number,'query':identifier,**metrics})
                    if candidate and identifier in ('source.md#exception','screen.png#empty'):
                        stats['shared_queries'].append({'query':identifier,'output_bytes':output_bytes,'pages':pages,**metrics})

                for identifier in gold:run_query(identifier)
                run_query('UNKNOWN',expected=set())
                run_query('T0',who={**identity,'role':'unauthorized'},expected=set())
                if not candidate:
                    run_query(f'T{(number+1)%count}',expected=set()) if count>1 else None
            stats['precision']=stats['matched']/(stats['matched']+stats['unexpected']) if stats['matched']+stats['unexpected'] else 1.0
            stats['recall']=stats['matched']/stats['expected'] if stats['expected'] else 1.0

        # Original text query remains a separate unsupported capability, outside
        # the exact-ID score denominator.
        natural=[]
        kwargs=dict(manifest=manifest,required=required,identity=identity,reads=[],query='重复请求',navigate=True,limit=50)
        if 'state_dir' in signature.parameters:kwargs['state_dir']=state
        page=input_index(**kwargs)
        natural.append({'query':'重复请求','known_in_body':True,'supported':False,'matched':bool(page['items'])})
        # Test historical snapshot reconstruction after original and shard loss.
        from zf.runtime.sidecar_refs import hydrate_sidecar_ref
        snapshot=snapshots['$']
        old_content=open_selection(state,manifest=manifest,row=required[0],identity=identity).read()
        nav=snapshot['navigation']
        if nav.get('index_ref'):
            root=hydrate_sidecar_ref(state,nav['index_ref']).payload
            for pages in root['buckets'].values():
                for descriptor in pages:(state/descriptor['ref']).unlink(missing_ok=True)
        (state/source['ref']).write_text('changed version')
        recover_kwargs=dict(manifest=manifest,required=required,identity=identity,reads=[],relation_id=f'T{count-1}',navigate=True,limit=50)
        if 'state_dir' in signature.parameters:recover_kwargs['state_dir']=state
        recovered=input_index(**recover_kwargs)
        # Legacy inline truncated manifests are rebuilt without mutation by the
        # new runtime; the old baseline faithfully remains truncated.
        snapshot['navigation']=build_navigation(old_content)
        before_legacy=canonical_json_sha256(manifest)
        legacy=input_index(**recover_kwargs)
        compatibility={'damaged_or_missing_shards_tail_hit':recovered['total']==1,
            'legacy_tail_hit':legacy['total']==1,'legacy_manifest_unchanged':before_legacy==canonical_json_sha256(manifest)}
        all_passed=all(p['queries']==p['passed'] for p in profiles.values())
        shared_costs=profiles['candidate-verify']['shared_queries']
        return {'schema_version':'context-retrieval-eval.v2','tasks':count,
            'fixture_digest':fixture_digest,
            'source_bytes':source_bytes,'build_all_fixture_selectors_ms':build_ms,'profiles':profiles,
            'transport_schemas':sorted(navigation_shapes),'failed_examples':failures,
            'failed_queries':sum(p['queries']-p['passed'] for p in profiles.values()),
            'passed_queries':sum(p['passed'] for p in profiles.values()),
            'total_queries':sum(p['queries'] for p in profiles.values()),
            'max_page_bytes':max(budgets),'byte_budget_passed':max(budgets)<=8192,
            'shared_query_cost_passed':all(row['output_bytes']<source_bytes for row in shared_costs),
            'exact_lookup_passed':all_passed,'compatibility':compatibility,'natural_language':natural,
            'provider_invoked':False,'semantic_qualification':'not_run',
            'qualification_scope':'synthetic_declared_scope_with_independent_location_sets'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('score'); p.add_argument('--grader', type=Path, required=True); p.add_argument('--findings', type=Path, required=True)
    sub.add_parser('benchmark')
    p = sub.add_parser('retrieval'); p.add_argument('--tasks', type=int, default=100)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.output)
    elif args.command == 'score':
        result = score(json.loads(args.grader.read_text()), json.loads(args.findings.read_text()))
    elif args.command == 'retrieval':
        result = retrieval(args.tasks)
    else:
        result = benchmark()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
