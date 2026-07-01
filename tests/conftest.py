"""Shared test fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


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
