from zf.runtime.issue_version_binding import (
    normalize_version_context,
    validate_delivery_version_context,
)


def test_delivery_revision_chain_accepts_distinct_evaluation_and_fix_revisions() -> None:
    valid, failures = validate_delivery_version_context({
        "evaluated_against": "a" * 40,
        "fix_based_on": "b" * 40,
        "candidate_head": "c" * 40,
        "pr_head": "c" * 40,
        "verification_revision": "c" * 40,
    }, require_pr=True)
    assert valid
    assert failures == []


def test_delivery_revision_chain_rejects_stale_pr_head() -> None:
    valid, failures = validate_delivery_version_context({
        "evaluated_source_revision": "a" * 40,
        "fix_base_revision": "b" * 40,
        "candidate_head_revision": "c" * 40,
        "pr_head_revision": "d" * 40,
    }, require_pr=True)
    assert not valid
    assert "pr_head!=candidate_head" in failures


def test_legacy_issue_report_revision_aliases_are_normalized() -> None:
    value = normalize_version_context({
        "evaluated_source_revision": "A" * 40,
        "fix_base_revision": "B" * 40,
        "candidate_head_revision": "C" * 40,
        "merge_revision": "D" * 40,
    })
    assert value["evaluated_against"] == "a" * 40
    assert value["fix_based_on"] == "b" * 40
    assert value["candidate_head"] == "c" * 40
    assert value["merge_sha"] == "d" * 40
