"""Ordinary evidence plus a deterministic rerun outcome."""

import os
from pathlib import Path

from tests.testkit.runtime_case_evidence import attach_runtime_case_evidence


def test_ordinary_fails_once_then_passes(tmp_path, request):
    root = tmp_path / "state"
    root.mkdir()
    evidence = attach_runtime_case_evidence(
        request, root, given="ordinary rerun owns a private state",
        when="run a test that fails once and is retried",
        then="both rerun status and raw evidence remain available",
        forbidden=["flaky pass treated as green"],
    )
    (root / "raw").write_bytes(b"ordinary-rerun\xff")
    marker = Path(os.environ["ZF_RERUN_MARKER"])
    if not marker.exists():
        marker.write_text("first", encoding="utf-8")
        raise AssertionError("ordinary first attempt failure")
