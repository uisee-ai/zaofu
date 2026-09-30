"""Budget validation stays usable without the optional native test chain."""

import pytest

from tests.testkit.native_run import _native_pipeline_timeout_seconds


@pytest.mark.parametrize("value,expected", [(None, 720.0), ("1", 1.0), ("1800", 1800.0)])
def test_native_budget(value, expected, monkeypatch):
    if value is None:
        monkeypatch.delenv("ZF_NATIVE_CLAUDE_PIPELINE_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("ZF_NATIVE_CLAUDE_PIPELINE_TIMEOUT_SECONDS", value)
    assert _native_pipeline_timeout_seconds() == expected


@pytest.mark.parametrize("value", ["0", "-1", "invalid", "nan", "inf", "1801"])
def test_native_budget_rejects_invalid(value, monkeypatch):
    monkeypatch.setenv("ZF_NATIVE_CLAUDE_PIPELINE_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="ZF_NATIVE_CLAUDE_PIPELINE_TIMEOUT_SECONDS"):
        _native_pipeline_timeout_seconds()
