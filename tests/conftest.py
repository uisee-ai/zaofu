"""Shared test fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def workflow_runtime_launcher(monkeypatch):
    """Keep ordinary action tests from launching installed providers.

    Process integration tests restore ``mock.real`` explicitly; launcher unit
    tests exercise the original function with a fake subprocess boundary.
    """
    from unittest.mock import Mock
    from zf.runtime import workflow_runtime_start

    mock = Mock(return_value={"ok": True, "status": "running", "pid": 123, "launched": False})
    mock.real = workflow_runtime_start.ensure_project_runtime
    monkeypatch.setattr(workflow_runtime_start, "ensure_project_runtime", mock)
    return mock


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary project directory and chdir into it."""
    original = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(original)


@pytest.fixture(scope="session", autouse=True)
def _basetemp_ancestry_has_no_zf_yaml(tmp_path_factory) -> None:
    """Fail fast when a stray zf.yaml shadows pytest temp dirs.

    `_find_project_root` intentionally walks every ancestor of cwd, so a
    leftover `/tmp/zf.yaml` (a sim run violating the CLAUDE.md /tmp
    convention) silently hijacks every CLI test's project root and turns
    ~30 tests into unexplained `assert 0 == 1` reds (2026-06-12 triage).
    """
    base = Path(str(tmp_path_factory.getbasetemp())).resolve()
    polluted = [
        str(parent / "zf.yaml")
        for parent in (base, *base.parents)
        if (parent / "zf.yaml").exists()
    ]
    assert not polluted, (
        f"zf.yaml found above the pytest basetemp: {polluted}. CLI tests "
        f"would resolve their project root to it (silent state hijack). "
        f"Remove the stray file (see CLAUDE.md /tmp 模拟约定) or rerun "
        f"with --basetemp under a clean directory."
    )


@pytest.fixture(scope="session", autouse=True)
def _zf_imports_from_this_repo() -> None:
    """Fail fast when `zf` resolves outside this checkout.

    A stray global editable install (e.g. a leftover
    `__editable__.zaofu-*.pth` in user site-packages pointing at a /tmp
    sim checkout) silently runs week-old code and turns the whole suite
    into a false green (2026-06-12 triage: stuck_dedup looked green for
    three weeks that way)."""
    import zf

    module_path = Path(zf.__file__).resolve()
    repo_root = Path(__file__).resolve().parents[1]
    assert module_path.is_relative_to(repo_root), (
        f"`zf` imported from {module_path}, outside this repo "
        f"({repo_root}). Remove the hijacking install (pip uninstall "
        f"zaofu in the offending interpreter) or fix the venv before "
        f"trusting any test result."
    )


@pytest.fixture(scope="session", autouse=True)
def _real_workspace_registry_untouched(tmp_path_factory) -> None:
    """Isolate tests from the real workspace and verify it stays untouched.

    2026-07-02: three pytest runs of test_web_profile.py (init endpoints
    without ZF_WORKSPACE_HOME isolation) leaked 9 ghost projects with dead
    /tmp roots into ~/.zaofu/workspaces/default/projects.json, which the web
    project picker then showed as duplicates. Web app construction also starts
    background prewarming, so read isolation is required: otherwise the suite
    scans large projects from the operator's real registry and pollutes shared
    projection caches."""
    import hashlib
    import os

    original_home = os.environ.get("ZF_WORKSPACE_HOME")
    real_home = Path(original_home or Path.home() / ".zaofu")
    registry = real_home / "workspaces" / "default" / "projects.json"

    def digest() -> str:
        try:
            return hashlib.md5(registry.read_bytes()).hexdigest()
        except OSError:
            return "absent"

    before = digest()
    isolated_home = tmp_path_factory.mktemp("zf-workspace-home")
    os.environ["ZF_WORKSPACE_HOME"] = str(isolated_home)
    try:
        yield
    finally:
        if original_home is None:
            os.environ.pop("ZF_WORKSPACE_HOME", None)
        else:
            os.environ["ZF_WORKSPACE_HOME"] = original_home
    after = digest()
    assert before == after, (
        f"a test wrote to the real workspace registry ({registry}): "
        f"md5 {before} -> {after}. Isolate ZF_WORKSPACE_HOME "
        f"(monkeypatch.setenv) in the offending test."
    )
