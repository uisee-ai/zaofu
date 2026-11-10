"""The browser oracle must reject a plausible but wrong generated Plan."""
import pytest
from tests.e2e.prd_ui.audit import check_preview

PLAN = '# PRD\nREADME.md\n## SDD\nFiles\n## TDD\nMissing-file error\nLast acceptance\n'


def test_exact_generated_plan_preview():
    check_preview({'plan_id': 'current', 'content_profile': 'derived_plan_full', 'markdown': PLAN},
                  expected_plan_id='current', expected_markdown=PLAN)


@pytest.mark.parametrize('change', [
    {'plan_id': 'previous'},
    {'content_profile': 'summary_only'},
    {'content_profile': 'derived_plan'},
    {'markdown': PLAN.removesuffix('Last acceptance\n')},
    {'markdown': PLAN.replace('Missing-file error', 'Always succeeds')},
])
def test_reject_wrong_or_incomplete_preview(change):
    preview = {'plan_id': 'current', 'content_profile': 'derived_plan_full', 'markdown': PLAN} | change
    with pytest.raises(AssertionError):
        check_preview(preview, expected_plan_id='current', expected_markdown=PLAN)


@pytest.mark.parametrize('role', ['impl', 'unknown', ''])
def test_dispatch_oracle_rejects_writer_or_missing_operation_identity(role):
    from tests.e2e.prd_ui.audit import check_dispatches
    with pytest.raises(AssertionError):
        check_dispatches([{'operation_id': 'op'}], {'op': {'role_instance': role}},
                         {'planner': 'reader', 'impl': 'writer'})


def test_dispatch_oracle_joins_attempt_to_operation():
    from tests.e2e.prd_ui.audit import check_dispatches
    assert check_dispatches([{'operation_id': 'op'}], {'op': {'role_instance': 'planner'}},
                            {'planner': 'reader'}) == {'planner'}
