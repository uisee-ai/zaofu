"""Fail-closed result classification for product-project test runs."""

from __future__ import annotations

from enum import StrEnum


class ProductVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    ENVIRONMENT_FAILURE = "environment_failure"
    HARNESS_FAILURE = "harness_failure"
    FLAKY = "flaky"
    NOT_RUN = "not_run"


def classify(*, command_exit: int | None = 0, timed_out: bool = False,
             product_assertion_failed: bool = False, environment_error: bool = False,
             harness_error: bool = False, flaky: bool = False,
             applicable: bool = True) -> ProductVerdict:
    """Apply precedence rules; no incomplete execution can become PASS."""

    if not applicable:
        return ProductVerdict.NOT_APPLICABLE
    if flaky:
        return ProductVerdict.FLAKY
    if harness_error:
        return ProductVerdict.HARNESS_FAILURE
    if environment_error or timed_out:
        return ProductVerdict.ENVIRONMENT_FAILURE
    if product_assertion_failed or command_exit != 0 or command_exit is None:
        return ProductVerdict.FAIL
    return ProductVerdict.PASS

