import pytest

from .integrations import ExternalSinkSpy, run_with_retry


def test_external_sink_records_bounded_retry_and_success():
    sink = ExternalSinkSpy(failures_before_success=2)
    result = run_with_retry(lambda: sink.invoke("publish", {"id": "x"}), max_attempts=3)
    assert result["ok"]
    assert sink.count("publish") == 3


def test_external_sink_failure_cannot_become_success():
    sink = ExternalSinkSpy(failures_before_success=5)
    with pytest.raises(RuntimeError, match="retry limit"):
        run_with_retry(lambda: sink.invoke("merge"), max_attempts=2)
    assert sink.count("merge") == 2
