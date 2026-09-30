import pytest

from .observability import assert_redacted, validate_log_record


def test_observability_requires_fields_and_redaction():
    record = {"timestamp": "2026-01-01T00:00:00Z", "level": "ERROR", "message": "failed", "token": "[REDACTED]"}
    validate_log_record(record)
    assert_redacted(record)
    with pytest.raises(AssertionError):
        validate_log_record({"level": "INFO"})
    with pytest.raises(AssertionError):
        assert_redacted({**record, "token": "plain-secret"})
