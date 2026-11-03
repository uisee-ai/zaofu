from zf.runtime.plan_review_input_diagnostics import review_diagnostics


def test_diagnostics_reports_qualified_reduction_and_unknown_sizes():
    sources = [
        {'source_id': 'A', 'artifact_id': 'a', 'sha256': 'a' * 64, 'read_selections': [
            {'json_path': '$', 'selected_sha256': 'c' * 64, 'selected_bytes': 120}]},
        {'source_id': 'B', 'artifact_id': 'b', 'sha256': 'b' * 64, 'read_selections': [
            {'json_path': '$', 'selected_sha256': 'd' * 64, 'selected_bytes': 300}]},
        {'source_id': 'C', 'artifact_id': 'c', 'sha256': 'c' * 64},
    ]
    rows = [{'source_id': s['source_id'], 'artifact_id': s['artifact_id'],
             'artifact_sha256': s['sha256'], 'json_path': '$'} for s in sources]
    result = review_diagnostics({'sources': sources}, before=rows, after=[rows[0], rows[2]], explicit=False)
    assert result['mode'] == 'incremental'
    assert result['before'] == {'required_rows': 3, 'distinct_selected_bytes': 420, 'unknown_rows': 1}
    assert result['after'] == {'required_rows': 2, 'distinct_selected_bytes': 120, 'unknown_rows': 1}


def test_fallback_reason_does_not_claim_unobserved_cause():
    rows = [{'source_id': 'A'}]
    assert review_diagnostics({'sources': []}, before=rows, after=rows, explicit=True)['reason'] == 'explicit_required_reads'
    assert review_diagnostics({'sources': []}, before=rows, after=rows, explicit=False)['reason'] == 'no_baseline_package'
    manifest = {'sources': [{'source_id': 'plan-rework-package'}]}
    assert review_diagnostics(manifest, before=rows, after=rows, explicit=False)['reason'] == 'prior_evidence_or_binding_unavailable'
