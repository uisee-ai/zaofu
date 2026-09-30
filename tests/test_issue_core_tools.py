from __future__ import annotations

import pytest
import sys

from issue_core import ReadOnlyInvestigationTools


def test_read_only_tools_enforce_allowed_paths_and_search(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("pending = True\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("private", encoding="utf-8")
    tools = ReadOnlyInvestigationTools(tmp_path, allowed_paths=("src",))

    assert tools.read_text("src/app.py") == "pending = True\n"
    assert tools.search("pending", path="src") == ["src/app.py"]
    with pytest.raises(PermissionError):
        tools.read_text("secret.txt")
    with pytest.raises(PermissionError):
        tools.read_text("../secret.txt")


def test_read_only_tools_run_command_in_project_root(tmp_path) -> None:
    tools = ReadOnlyInvestigationTools(tmp_path)
    result = tools.run_read_only((sys.executable, "-c", "print('ok')"), timeout_seconds=5)
    assert result["status"] == "passed"
    assert result["stdout"].strip() == "ok"
