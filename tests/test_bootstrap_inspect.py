"""BootstrapInspector — detect → setup/gate/doc candidates (Welcome STEP3)."""

from __future__ import annotations

from pathlib import Path

from zf.core.workspace.bootstrap_inspect import inspect_project


def test_python_project_yields_gate_candidate(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n")
    result = inspect_project(tmp_path)
    assert result["confidence"] == "high"
    assert result["stack"] == "python"
    gate = next((c for c in result["candidates"] if c["kind"] == "gate"), None)
    assert gate is not None
    assert any("pytest" in cmd for cmd in gate["values"])  # 补掉空门禁


def test_node_project_setup_and_gate(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"x","scripts":{"test":"jest"}}')
    (tmp_path / "package-lock.json").write_text("{}")
    result = inspect_project(tmp_path)
    setup = next((c for c in result["candidates"] if c["kind"] == "setup"), None)
    assert setup is not None
    assert setup["value"] == "npm install"


def test_empty_dir_low_confidence_no_candidates(tmp_path: Path) -> None:
    result = inspect_project(tmp_path)
    assert result["confidence"] == "low"
    assert result["candidates"] == []


def test_missing_path() -> None:
    result = inspect_project("/no/such/dir/xyz")
    assert result["confidence"] == "low"
    assert result["candidates"] == []
    assert result.get("error") == "path not found"


def test_recommended_flow_is_controller_flow(tmp_path: Path) -> None:
    """python 项目 → recommended_flow 是 controller flow archetype(非通用 preset)。"""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n")
    from zf.core.profile.flows import is_flow_id
    result = inspect_project(tmp_path, backend="codex")
    assert result["recommended_flow"]
    assert is_flow_id(result["recommended_flow"])
    flow_cand = next((c for c in result["candidates"] if c["kind"] == "flow"), None)
    assert flow_cand is not None and flow_cand["value"] == result["recommended_flow"]
