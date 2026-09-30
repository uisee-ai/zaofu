import pytest

from .verdict import ProductVerdict, classify


@pytest.mark.parametrize(("kwargs", "expected"), [
    ({"applicable": False}, ProductVerdict.NOT_APPLICABLE),
    ({"flaky": True}, ProductVerdict.FLAKY),
    ({"harness_error": True, "command_exit": 1}, ProductVerdict.HARNESS_FAILURE),
    ({"environment_error": True}, ProductVerdict.ENVIRONMENT_FAILURE),
    ({"timed_out": True}, ProductVerdict.ENVIRONMENT_FAILURE),
    ({"product_assertion_failed": True}, ProductVerdict.FAIL),
    ({"command_exit": 1}, ProductVerdict.FAIL),
    ({}, ProductVerdict.PASS),
])
def test_product_verdict_is_fail_closed(kwargs, expected):
    assert classify(**kwargs) is expected


def test_not_run_is_explicit_enum():
    assert ProductVerdict.NOT_RUN.value == "not_run"
