"""Regression test for LH-B1: the stale-runtime-snapshot terminal gate must
honor ``verification.snapshot_gate`` (off | shadow | enforced).

Before the fix the gate unconditionally emitted ``task.completion.stale_rejected``
and returned ``action="block"`` regardless of config (the smoke proved it never
read ``self.config``). The fix branches:
  - off      -> no emit, no block (decision None)
  - shadow   -> emit stale_rejected, no block (decision None)
  - enforced -> emit stale_rejected, block (default; preserves old behavior)

Evidence: docs/records/2026-06-16-axisB-code-debt-smoke-REPORT.md
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from zf.core.config.loader import ConfigError, _build_verification
from zf.core.config.schema import VerificationConfig
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_reactor import EventReactorMixin
from zf.runtime.runtime_snapshot import write_runtime_snapshot


def _drive(tmp_path: Path, mode: str) -> tuple[object, list[str]]:
    """Drive the real gate with a STALE snapshot (snap dispatch_id != live
    task dispatch_id) under the given snapshot_gate mode."""
    state_dir = tmp_path
    state_dir.mkdir(parents=True, exist_ok=True)
    project_root = tmp_path.parent
    snap = write_runtime_snapshot(
        {
            "source": "runtime",
            "task": {
                "task_id": "T-LHB1",
                "source_revision": "src-1",
                "contract_revision": "ct-1",
                "capsule_revision": "cap-1",
            },
            "run": {"dispatch_id": "OLD-dispatch-AAA", "run_id": "run-1"},
        },
        state_dir=state_dir,
        project_root=project_root,
    )
    contract = types.SimpleNamespace(
        source_revision="src-1", contract_revision="ct-1", capsule_revision="cap-1"
    )
    task = types.SimpleNamespace(
        id="T-LHB1", active_dispatch_id="NEW-dispatch-ZZZ", contract=contract
    )
    emitted: list[ZfEvent] = []

    class _Writer:
        def append(self, ev: ZfEvent) -> ZfEvent:
            emitted.append(ev)
            return ev

    stub = types.SimpleNamespace(
        event_writer=_Writer(),
        state_dir=state_dir,
        project_root=project_root,
        config=types.SimpleNamespace(
            verification=VerificationConfig(snapshot_gate=mode)
        ),
    )
    event = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="T-LHB1",
        payload={"snapshot_ref": snap.snapshot_ref},
    )
    decision = EventReactorMixin._reject_stale_runtime_snapshot_completion(
        stub, event, task
    )
    return decision, [e.type for e in emitted]


def test_snapshot_gate_enforced_blocks_and_emits(tmp_path: Path) -> None:
    decision, emitted = _drive(tmp_path / "enf", "enforced")
    assert decision is not None and decision.action == "block"
    assert "task.completion.stale_rejected" in emitted


def test_snapshot_gate_shadow_emits_but_does_not_block(tmp_path: Path) -> None:
    decision, emitted = _drive(tmp_path / "shadow", "shadow")
    assert decision is None
    assert "task.completion.stale_rejected" in emitted


def test_snapshot_gate_off_neither_emits_nor_blocks(tmp_path: Path) -> None:
    decision, emitted = _drive(tmp_path / "off", "off")
    assert decision is None
    assert "task.completion.stale_rejected" not in emitted


def test_snapshot_gate_default_is_enforced(tmp_path: Path) -> None:
    # the default must preserve the pre-LH-B1 fail-closed behavior
    assert VerificationConfig().snapshot_gate == "enforced"
    decision, emitted = _drive(tmp_path / "def", VerificationConfig().snapshot_gate)
    assert decision is not None and decision.action == "block"


def test_loader_parses_and_validates_snapshot_gate() -> None:
    assert _build_verification(None).snapshot_gate == "enforced"
    assert _build_verification({"snapshot_gate": "shadow"}).snapshot_gate == "shadow"
    assert _build_verification({"snapshot_gate": "off"}).snapshot_gate == "off"
    with pytest.raises(ConfigError):
        _build_verification({"snapshot_gate": "bogus"})
