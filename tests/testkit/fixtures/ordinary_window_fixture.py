"""Explicit, self-terminating probes of TestKit evidence commit windows."""

import os
from pathlib import Path

from tests.testkit import case_evidence


def test_snapshot_window(testkit_case_evidence, tmp_path, monkeypatch):
    mode = os.environ["ZF_CASE_WINDOW"]
    evidence = testkit_case_evidence
    root = tmp_path / "owned-window"
    root.mkdir()
    raw = root / "raw"
    raw.write_bytes(b"old checkpoint\x00")
    if mode == "before-registration":
        os._exit(42)
    original = case_evidence.write_json_atomic

    def terminate_at_window(path, payload):
        selected = "registered" if mode == "first-snapshot" else "checkpoint"
        if Path(path).stem != selected:
            return original(path, payload)
        if mode == "partial-write":
            Path(path).with_name(".uncommitted.tmp").write_bytes(b'{"partial":')
            os._exit(42)
        digest = original(path, payload)
        os._exit(42)  # Actual atomic replace completed; no return/ref callback.
        return digest  # pragma: no cover

    if mode == "first-snapshot":
        monkeypatch.setattr(case_evidence, "write_json_atomic", terminate_at_window)
    evidence.register(root, synthetic_no_secrets=True, metadata={
        "given": "owned synthetic bytes", "when": mode,
        "then": "keep committed observations without inventing report binding",
        "forbidden": ["accept unbound diagnostic as verified result"],
        "identity": {"run_id": None, "task_id": None, "attempt": None, "generation": None,
                     "operation_id": None, "reason": "TestKit commit-window probe"},
    })
    if mode in {"replaced-slot", "before-replace", "partial-write"}:
        evidence.checkpoint()
    raw.write_bytes(b"new committed state\xff")
    if mode == "before-replace":
        original_replace = os.replace

        def stop_before_replace(source, destination):
            if Path(destination) == evidence.directory / "checkpoint.json":
                os._exit(42)
            return original_replace(source, destination)

        # Scope the global os module patch to this exact evidence destination;
        # reporter replacements and any unrelated paths still use real os.replace.
        monkeypatch.setattr(os, "replace", stop_before_replace)
    monkeypatch.setattr(case_evidence, "write_json_atomic", terminate_at_window)
    evidence.checkpoint()
    raise AssertionError("window probe did not terminate")
