"""Explicit subprocess probes, not default-discovered product tests."""

import os
import shutil
import signal
import time
from pathlib import Path

import pytest


MODE = os.environ.get("ZF_CASE_MODE", "call")
if MODE in {"strict-xpass", "xpass", "no-run-xfail"}:
    pytestmark = pytest.mark.xfail(reason="lifecycle expected failure", strict=MODE == "strict-xpass", run=MODE != "no-run-xfail")


@pytest.fixture
def owned_state(testkit_case_evidence, tmp_path, request):
    root = tmp_path / "synthetic-state"
    root.mkdir()
    request.addfinalizer(lambda: shutil.rmtree(root) if root.exists() else None)
    if MODE == "multi-finalizer":
        for name in ("first-finalizer", "second-finalizer"):
            request.addfinalizer(lambda name=name: pytest.fail(name))
    evidence = testkit_case_evidence
    evidence.register(root, synthetic_no_secrets=True, metadata={
        "given": "synthetic state", "when": os.environ["ZF_CASE_MODE"],
        "then": "raw evidence survives cleanup", "forbidden": ["outside read"],
        "identity": {"run_id": None, "task_id": None, "attempt": None, "generation": None,
                     "operation_id": None, "reason": "no runtime, lifecycle probe"},
    })
    (root / "raw").write_bytes(b"initial\xff")
    if MODE in {"setup-xfail", "setup-skip"}:
        (pytest.xfail if MODE == "setup-xfail" else pytest.skip)("ordinary setup disposition")
    if MODE == "setup":
        # A previously registered finalizer deletes the partially built state.
        pytest.fail("ordinary setup failure")
    yield root, evidence
    if MODE == "cleanup-change":
        with evidence.guard():
            (root / "raw").write_bytes(b"cleanup-change\xff")
            pytest.fail("cleanup changed state before failure")
    if root.exists():
        shutil.rmtree(root)
    if MODE == "tamper":
        Path(evidence.record()["snapshots"]["call-report"]["path"]).write_text("corrupt snapshot")
    if MODE == "teardown-xfail":
        pytest.xfail("ordinary teardown disposition")
    if MODE in {"teardown", "both"}:
        pytest.fail("ordinary teardown failure after deletion")


def test_lifecycle(owned_state):
    root, evidence = owned_state
    mode = os.environ["ZF_CASE_MODE"]
    (root / "raw").write_bytes(b"call-state\x00\xff")
    if mode in {"pass", "teardown", "teardown-xfail", "strict-xpass", "xpass", "multi-finalizer", "cleanup-change"}:
        return
    if mode == "unguarded-deletion":
        try:
            pytest.fail("original failure before unguarded deletion")
        finally:
            shutil.rmtree(root)
    if mode == "skip":
        pytest.skip("ordinary call skipped")
    if mode == "xfail":
        pytest.xfail("ordinary known bug")
    if mode in {"pytest-exit-zero", "pytest-exit-error"}:
        evidence.checkpoint()
        pytest.exit("explicit ordinary exit", returncode=0 if mode == "pytest-exit-zero" else 17)
    if mode in {"exit", "SIGTERM", "SIGKILL"}:
        evidence.checkpoint("checkpoint")
        if mode == "exit":
            os._exit(42)
        os.kill(os.getpid(), getattr(signal, mode))
    if mode == "timeout":
        evidence.checkpoint("checkpoint")
        Path(os.environ["ZF_CASE_READY"]).write_text(str(os.getpid()))
        while True:
            time.sleep(1)
    if mode == "owner":
        try:
            with evidence.guard():
                pytest.fail("ordinary owner failure")
        finally:
            shutil.rmtree(root)
            root.mkdir()  # Fixture cleanup still owns this path.
    pytest.fail("ordinary call failure")
