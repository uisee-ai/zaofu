"""Non-authoritative recovery never scans source roots or bypasses budgets."""

import json
from pathlib import Path

import pytest

from tests.testkit.case_evidence import CaseEvidence, CaptureBudget
from tests.testkit.evidence import write_json_atomic
from tests.testkit.ordinary_recovery import recover_unbound_snapshots


def orphan(tmp_path):
    root = tmp_path / "owned" / "state"
    root.mkdir(parents=True)
    output = tmp_path / "output"
    evidence = CaseEvidence(nodeid="case", source=Path(__file__), owned_root=root.parent,
                            output=output / "case-evidence", budget=CaptureBudget())
    evidence.register(root, synthetic_no_secrets=True, metadata={
        "given": "empty synthetic state", "when": "drop report binding", "then": "diagnostic only",
        "forbidden": ["source scanning", "fake pass"],
        "identity": {"run_id": None, "task_id": None, "attempt": None, "generation": None,
                     "operation_id": None, "reason": "no runtime"},
    })
    record = evidence.record()
    path = Path(record["snapshots"]["registered"]["path"])
    record["snapshots"] = {}
    return output, ({"nodeid": "case", "ordinary_evidence": record},), path


@pytest.mark.parametrize("kind", ["nodeid", "point", "raw_digest", "symlink", "parent_link", "unknown_slot", "unregistered"])
def test_recovery_does_not_accept_foreign_or_invalid_slots(tmp_path, kind):
    output, cases, path = orphan(tmp_path)
    payload = json.loads(path.read_text())
    if kind == "nodeid":
        payload["nodeid"] = "other-case"
    elif kind == "point":
        payload["capture_point"] = "checkpoint"
    elif kind == "raw_digest":
        payload["files"] = [{"path": "file", "bytes": 1, "base64": "eA==", "digest": "0" * 64}]
    elif kind == "symlink":
        outside = tmp_path / "outside.json"
        path.rename(outside)
        path.symlink_to(outside)
    elif kind == "parent_link":
        outside = tmp_path / "outside"
        path.parent.rename(outside)
        path.parent.symlink_to(outside, target_is_directory=True)
    elif kind == "unknown_slot":
        path.rename(path.with_name("unknown.json"))
    else:
        cases[0]["ordinary_evidence"]["registered"] = False
    if kind in {"nodeid", "point", "raw_digest"}:
        write_json_atomic(path, payload)
    diagnostics, errors = recover_unbound_snapshots(output, cases, [])
    assert diagnostics == []
    if kind not in {"unknown_slot", "unregistered"}:
        assert errors


@pytest.mark.parametrize("budget", ["files", "bytes"])
def test_failed_reads_also_consume_recovery_budget(tmp_path, budget):
    output, cases, path = orphan(tmp_path)
    size = path.stat().st_size
    # Lexically first known slot is invalid JSON, but reading it still costs
    # a file attempt and bytes. Later valid files must not evade the cap.
    (path.parent / "call-report.json").write_bytes(b"x" * (size // 2))
    limits = {"max_files": 1} if budget == "files" else {"max_bytes": size + 100}
    diagnostics, errors = recover_unbound_snapshots(output, cases, [], **limits)
    assert errors and diagnostics == []
