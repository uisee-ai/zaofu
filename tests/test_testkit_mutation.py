from __future__ import annotations

import pytest

from tests.testkit.mutation_audit import (
    MutationAuditError,
    MutationSummary,
    parse_mutmut_results,
    verdict_for_mutation,
)


def test_parse_mutmut_results_preserves_status_counts() -> None:
    summary = parse_mutmut_results(
        """
    src/zf/core/security/signing.py:EventSigner.verify.1: killed
    src/zf/core/security/signing.py:EventSigner.verify.2: survived
    src/zf/core/security/signing.py:EventSigner.verify.3: timeout
    src/zf/core/security/signing.py:EventSigner.verify.4: no tests
    """
    )

    assert summary.total == 4
    assert summary.killed == 1
    assert summary.survived == 1
    assert summary.timeout == 1
    assert summary.no_tests == 1


def test_mutation_verdict_requires_all_mutants_killed() -> None:
    summary = MutationSummary(total=2, killed=2)
    assert verdict_for_mutation(summary) == "pass"
    assert verdict_for_mutation(MutationSummary(total=2, killed=1, survived=1)) == "fail"


@pytest.mark.parametrize(
    "status",
    ["timeout", "no_tests", "suspicious", "skipped", "not_checked", "segfault"],
)
def test_non_kill_statuses_fail_closed(status: str) -> None:
    summary = MutationSummary(total=1, **{status: 1})
    assert verdict_for_mutation(summary) == "fail"


def test_empty_or_unparseable_results_are_harness_errors() -> None:
    with pytest.raises(MutationAuditError):
        parse_mutmut_results("mutmut run failed before producing results")


def test_summary_rejects_inconsistent_counts() -> None:
    with pytest.raises(MutationAuditError):
        MutationSummary(total=1, killed=2)
