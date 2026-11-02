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


def retrieval() -> dict:
    from zf.runtime.artifact_input_index import input_index
    from zf.runtime.artifact_read_ledger import source_manifest_from_payload
    from zf.runtime.artifact_read_snapshot import open_selection
    from zf.runtime.artifact_source_versions import read_source_version
    outcomes = []
    with tempfile.TemporaryDirectory(prefix='zf-context-retrieval-') as directory:
        state = Path(directory)
        body = sample_bundle()['task_execution_input']
        path = state / 'plan.json'
        path.write_text(json.dumps(body, ensure_ascii=False))
        source_size = path.stat().st_size
        for profile, role in [('implementation','dev'),('task-verify','verify'),('candidate-verify','verify')]:
            scoped = profile != 'candidate-verify'
            allowed = ['$.shared','$.tasks[1]'] if scoped else ['$']
            manifest, _ = source_manifest_from_payload(state_dir=state,project_root=state,
                payload={'input_refs':[{'source_id':'plan','ref':'plan.json','allowed_paths':allowed,
                                       'access_scope':{'roles':[role]}}]},
                workflow_run_id='offline',task_id='T1',attempt_id=profile,dispatch_id=profile)
            source = manifest['sources'][0]
            required = [{'source_id':'plan','artifact_id':source['artifact_id'],
                         'artifact_sha256':source['sha256'],'json_path':selector} for selector in allowed]
            identity={'actor':profile,'role':role,'purpose':profile}
            cases=[('task','T1',False),('acceptance','AC-1',False),('source','source.md#exception',False),
                   ('image','screen.png#empty',False),('shared','source.md#shared',False)]
            if not scoped:
                # Large full-input navigation is intentionally capped, so report
                # navigation misses instead of treating an absent hit as no scope.
                cases.append(('last_task','T99',False))
            for category, query, _ in cases:
                start=perf_counter()
                cursor=None; rows=[]; response_bytes=0; complete=all(s.get('navigation',{}).get('complete',False) for s in source['read_selections'])
                while True:
                    page=input_index(manifest=manifest,required=required,identity=identity,reads=[],
                        relation_id=query,navigate=True,cursor=cursor,limit=10)
                    response_bytes+=len(json.dumps(page,ensure_ascii=False).encode())
                    rows.extend(page['items']);cursor=page['next_cursor']
                    complete &= all(row.get('navigation_complete',True) for row in page['items'])
                    if cursor is None:break
                outcomes.append({'profile':profile,'category':category,'query':query,'expected':'hit',
                    'matched':bool(rows),'locators':len(rows),'output_bytes':response_bytes,
                    'host_ms':(perf_counter()-start)*1000,'navigation_complete':complete})
            for category, query, test_identity, expected in [
                ('out_of_scope_task','T2',identity,'miss' if scoped else 'hit'),
                ('unknown_id','AC-UNKNOWN',identity,'miss'),
                ('acl','T1',{**identity,'role':'unrelated'},'miss')]:
                page=input_index(manifest=manifest,required=required,identity=test_identity,reads=[],relation_id=query,navigate=True)
                outcomes.append({'profile':profile,'category':category,'query':query,'expected':expected,
                                 'matched':bool(page['items']),'locators':len(page['items'])})
            page=input_index(manifest=manifest,required=required,identity=identity,reads=[],query='重复请求',navigate=True)
            outcomes.append({'profile':profile,'category':'natural_language','query':'重复请求',
                             'expected':'unsupported','matched':bool(page['items']),
                             'note':'正文含此要求；当前索引只支持显式定位，不是全文/语义搜索。'})
            if scoped:
                read = open_selection(state,manifest=manifest,row=required[1],identity=identity)
                content=read.read()
                # Invalidate only reconstructible blocks and the original ref;
                # the pinned source version must preserve exact historical input.
                for selection in source['read_selections']:
                    for chunk in selection['chunks']:
                        (state/chunk['ref']).unlink(missing_ok=True)
                path.write_text('{"changed":true}')
                recovered=open_selection(state,manifest=manifest,row=required[1],identity=identity).read()
                outcomes.append({'profile':profile,'category':'pinned_recovery','expected':'exact',
                                 'matched':recovered==content,'body_bytes':len(content.encode())})
                original,_=read_source_version(state,source,path=path)
                path.write_bytes(original)
        positives=[row for row in outcomes if row['expected']=='hit']
        negatives=[row for row in outcomes if row['expected']=='miss']
        return {'schema_version':'context-retrieval-eval.v1','tasks':100,'source_bytes':source_size,
                'positive_hits':sum(row['matched'] for row in positives),'positive_cases':len(positives),
                'unexpected_negative_hits':sum(row['matched'] for row in negatives),'negative_cases':len(negatives),
                'outcomes':outcomes,'provider_invoked':False,'semantic_qualification':'not_run',
                'limits':['合成固定样例；不能代替实际项目语义召回。','全文导航存在 512 项上限；未命中不证明没有需求。']}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('score'); p.add_argument('--grader', type=Path, required=True); p.add_argument('--findings', type=Path, required=True)
    sub.add_parser('benchmark')
    sub.add_parser('retrieval')
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.output)
    elif args.command == 'score':
        result = score(json.loads(args.grader.read_text()), json.loads(args.findings.read_text()))
    elif args.command == 'retrieval':
        result = retrieval()
    else:
        result = benchmark()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
