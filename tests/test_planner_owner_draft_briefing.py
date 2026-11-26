from copy import deepcopy

import pytest

from zf.runtime.artifact_read_ledger import render_attempt_source_briefing
from zf.runtime.call_result_envelope import canonical_json_sha256, write_immutable_json_sidecar


@pytest.mark.parametrize('change', ['none', 'unrelated_reason', 'other_decision', 'other_package',
                                    'limited_context', 'wrong_role', 'altered_context', 'duplicate_package'])
def test_owner_returned_invoice_plan_uses_scoped_formal_files_and_current_feedback(tmp_path, change):
    def pin(body):
        return write_immutable_json_sidecar(tmp_path, body, root='invoices', kind='source',
                                           schema_version='fixture.v1', created_by='test')

    package = pin({'revision': 'accepted-invoice-tax-basis'})
    context = pin({'schema_version': 'plan-rework-context.v1', 'rework_source': 'plan.rejected',
                   'rework_of': 'evt-return-invoice', 'rework_feedback': ['Apply the new rounding decision.'],
                   'operator_feedback': ['Start the earlier review from invoice revision two.']})
    rows = [{'source_id': 'plan-rework-package', **package, 'allowed_paths': ['$']},
            {'source_id': 'plan-rework-context', **context, 'allowed_paths': ['$']},
            {'source_id': 'baseline-plan-port-task_map', 'allowed_paths': ['$']},
            {'source_id': 'previous-plan-candidate-1', 'allowed_paths': ['$']}]
    source = {'attempt_id': 'invoice-review', 'plan_artifact_package_ref': package['ref'],
              'plan_artifact_package_digest': package['sha256'], 'sources': rows}
    if change == 'limited_context':
        rows[1]['allowed_paths'] = ['$.schema_version']
    elif change == 'wrong_role':
        rows[1]['access_scope'] = {'roles': ['critic']}
    elif change == 'altered_context':
        rows[1]['sha256'] = '0' * 64
    elif change == 'duplicate_package':
        rows.append(dict(rows[0]))
    manifest = pin(source)
    payload = {'attempt_id': source['attempt_id'], 'attempt_source_manifest_ref': manifest['ref'],
               'attempt_source_manifest_digest': manifest['sha256'], 'plan_artifact_package_digest': package['sha256'],
               'rework_of': 'evt-return-invoice', 'rework_source': 'plan.rejected', 'role_instance': 'invoice-author'}
    if change == 'other_package':
        payload['plan_artifact_package_digest'] = 'b' * 64
    elif change == 'other_decision':
        payload['rework_of'] = 'evt-older-rejection'
    elif change == 'unrelated_reason':
        payload['rework_source'] = 'invoice.review.failed'

    issued = deepcopy(payload)
    old_manifest_bytes = (tmp_path / manifest['ref']).read_bytes()
    text = render_attempt_source_briefing(payload, state_dir=tmp_path, role_name='planner')
    if change == 'none':
        assert f"PlanPackage SHA256 {package['sha256']} for Owner decision evt-return-invoice" in text
        assert '$.rework_feedback' in text and 'Earlier operator_feedback' in text
        assert 'Use the assigned previous-candidate sources' not in text
        assert 'draft-baseline --workspace --attempt invoice-review --port task_map' in text
        assert '--from-rework' not in text and 'Apply the new rounding decision.' not in text
    else:
        assert 'for Owner decision' not in text

    assert payload == issued
    assert (tmp_path / manifest['ref']).read_bytes() == old_manifest_bytes
    assert canonical_json_sha256(source) == manifest['sha256']
    assert 'for Owner decision' not in render_attempt_source_briefing(payload, state_dir=tmp_path, role_name='critic')
