"""Small pytest-asyncio cases used to verify ordinary evidence hooks."""

import asyncio
import os
from pathlib import Path

import pytest
import pytest_asyncio

from tests.testkit.runtime_case_evidence import attach_runtime_case_evidence


@pytest_asyncio.fixture
async def async_state(tmp_path: Path, request: pytest.FixtureRequest):
    root = tmp_path / "async-state"
    root.mkdir()
    (root / "raw").write_bytes(b"async-before\xff")
    evidence = attach_runtime_case_evidence(
        request, root, given="async fixture owns a private state root",
        when="await one deterministic async operation",
        then="phase outcome and raw bytes remain classifiable",
        forbidden=["fixture cleanup before evidence", "invented runtime identity"],
    )
    # attach_runtime_case_evidence performs the single registration.  Do not
    # add a competing finalizer: pytest-asyncio teardown ordering is itself
    # part of this contract and the parent tmp_path owns eventual cleanup.
    mode = os.environ.get("ZF_ASYNC_CASE", "pass")
    if mode == "setup-fail":
        pytest.fail("async setup failure")
    if mode == "setup-cancel":
        raise asyncio.CancelledError("async setup cancellation")
    yield root, evidence
    if mode == "teardown-fail":
        if evidence is not None:
            with evidence.guard():
                (root / "raw").write_bytes(b"async-teardown\xff")
                pytest.fail("async teardown failure")
        pytest.fail("async teardown failure")
    if mode == "teardown-cancel":
        raise asyncio.CancelledError("async teardown cancellation")


@pytest.mark.asyncio
async def test_async_case(async_state):
    root, evidence = async_state
    mode = os.environ.get("ZF_ASYNC_CASE", "pass")
    (root / "raw").write_bytes(b"async-call\x00\xff")
    if evidence is not None:
        evidence.checkpoint("checkpoint")
    await asyncio.sleep(0)
    if mode == "call-fail":
        pytest.fail("async call failure")
    if mode == "call-cancel":
        raise asyncio.CancelledError("async call cancellation")
