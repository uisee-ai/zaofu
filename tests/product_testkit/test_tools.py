import pytest

from .tools import probe_tool


def test_tool_probe_reports_existing_tool():
    result = probe_tool("sh")
    assert result.available and result.executable and result.verdict == "available"


def test_tool_probe_reports_missing_tool_as_not_run():
    result = probe_tool("zf-tool-that-does-not-exist")
    assert not result.available and result.executable is None and result.verdict == "not_run"


def test_tool_probe_rejects_empty_name():
    with pytest.raises(ValueError):
        probe_tool(" ")
    with pytest.raises(ValueError):
        probe_tool("k6\n--unsafe")
