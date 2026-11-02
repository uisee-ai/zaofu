import importlib.util
import json
from pathlib import Path

import pytest
from zf.runtime.task_scoped_plan_inputs import task_input_paths
from zf.runtime.plan_input_review import inspect_plan_inputs

spec = importlib.util.spec_from_file_location('context_eval', Path(__file__).parents[1]/'scripts/eval-context-inputs.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_blind_cases_preserve_ids_while_semantic_omissions_require_critic(tmp_path):
    result = module.prepare(tmp_path/'eval')
    assert result['tasks'] == 100 and result['semantic_qualification'] == 'not_run'
    blind=json.loads((tmp_path/'eval/blind.json').read_text())
    assert 'expected' not in blind
    assert len(blind['cases']) == 5
    for case in blind['cases']:
        assert task_input_paths(case['plan']['task_execution_input'], task_id='T1') == ['$.shared','$.tasks[1]']
        report=inspect_plan_inputs(case['plan'],task_id='T1')
        assert not report['items'][0]['issues']
        assert report['items'][0]['semantic_review']=='required'


def test_scores_explicit_findings_with_bound_evidence_not_keyword_coverage(tmp_path):
    module.prepare(tmp_path/'eval')
    rubric=json.loads((tmp_path/'eval/grader.json').read_text())
    findings={'evaluation_digest':rubric['evaluation_digest'],'cases':[
        {'case_id':case['case_id'],'findings':[{**row,'explanation':'本任务输入未保留原始要求'} for row in case['findings']]}
        for case in rubric['cases']]}
    report=module.score(rubric,findings)
    assert report['matched_findings']==4 and report['exact_cases']==5
    assert report['semantic_qualification']=='requires_independent_review'
    for case in findings['cases']: case['findings']=[]
    report=module.score(rubric,findings)
    assert report['missed_findings']==4 and report['exact_cases']==1
    findings['evaluation_digest']='wrong'
    with pytest.raises(ValueError,match='digest'):module.score(rubric,findings)


def test_host_benchmark_reports_prepared_pages_without_claiming_provider_savings():
    result=module.benchmark()
    assert result['source_bytes'] > result['prepared_body_bytes']
    assert result['pages'] > 1 and result['build_snapshot_navigation_ms'] > 0
    assert result['provider_usage'] is None


def test_retrieval_qualification_requires_complete_sets_and_bounded_output():
    result=module.retrieval(100)
    assert result['passed_queries']==result['total_queries']==1805
    assert result['failed_queries']==0 and result['exact_lookup_passed']
    assert result['byte_budget_passed'] and result['shared_query_cost_passed']
    assert all(result['compatibility'].values())
    candidate=result['profiles']['candidate-verify']
    assert [row['returned'] for row in candidate['shared_queries']]==[100,200]
    assert all(row['recall']==row['precision']==1 for row in result['profiles'].values())
    assert result['natural_language'][0]['matched'] is False
    assert result['semantic_qualification']=='not_run' and result['provider_invoked'] is False


def test_location_score_rejects_missing_wrong_duplicate_and_truncated_results():
    expected={('T1','/task_id'),('T2','/task_id')}
    assert module.score_locations(expected,list(expected))['passed']
    for actual,complete in [([('T1','/task_id')],True),
            ([('T1','/task_id'),('wrong','/task_id')],True),
            ([*expected,('T1','/task_id')],True),(list(expected),False)]:
        assert module.score_locations(expected,actual,complete=complete)['passed'] is False
