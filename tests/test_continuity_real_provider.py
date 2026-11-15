"""Explicit real Codex tier: two fresh sessions consume bound inputs and dirty work.

Run with ZF_REAL_CONTINUITY=1 and ZF_REAL_CONTINUITY_EVIDENCE_DIR=<empty directory>.
This qualifies an isolated CLI/host journey, not full product workflow delivery.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys

import pytest

from tests.test_artifact_input_recovery import current_dispatch
from tests.test_artifact_read_capability import _grant
from tests.test_artifact_read_snapshot import published
from tests.continuity_provider_command import provider_command
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.environment_usage import assert_workspace_idle, guarded_provider_argv


@pytest.mark.real_provider
@pytest.mark.skipif(os.getenv("ZF_REAL_CONTINUITY") != "1", reason="explicit real-provider qualification")
def test_fresh_terra_sessions_continue_bound_dirty_candidate(tmp_path, monkeypatch):
    evidence = Path(os.environ["ZF_REAL_CONTINUITY_EVIDENCE_DIR"]).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    assert not list(evidence.iterdir()), "evidence directory must be empty"
    root = tmp_path / "product"
    root.mkdir()
    state = tmp_path / "state"
    manifest, required, _ = published(state, body={
        "shared": "Implement Python totals.py: subtotal(values) sums integers; reject bool and non-integers with ValueError. Never change existing integer behavior.",
        "tasks": [{} for _ in range(7)] + [{"next_step": "Add total_with_fee(values, fee): call subtotal and add nonnegative integer fee; reject bool/negative/non-integer fees."}],
    })
    current_dispatch(state, required)
    _grant(state, monkeypatch, manifest=manifest, operation_id="op")
    source = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", str(source / "src"))
    cli = [sys.executable, "-m", "zf.cli.main", "artifact"]
    commands = "\n".join(shlex.join([*cli, *args, "--attempt", "a", "--state-dir", str(state)]) for args in (
        ["index"], ["read", "--required"],
    ))
    thread_ids = []
    try:
        for phase in (1, 2):
            assert_workspace_idle(root)
            prompt = (
                "This is an isolated authorized implementation qualification. Work only in this product directory. "
                "Do not commit or contact any external service. Do not inspect credential files. "
                "First execute these authenticated input commands exactly:\n" + commands + "\n"
                + ("Implement only subtotal in totals.py from the shared contract. Leave the file uncommitted."
                   if phase == 1 else
                   "This is a fresh session; the previous session is unavailable. Read handoff.md and the existing totals.py. "
                   "Preserve subtotal and complete only the next_step from task 7. Leave both functions in totals.py.")
                + "\nRun a small Python assertion for your changed behavior, then report briefly."
            )
            argv = provider_command(root, state, prompt)
            with (evidence / f"phase-{phase}.jsonl").open("w") as output, (evidence / f"phase-{phase}.stderr").open("w") as errors:
                process = subprocess.Popen(guarded_provider_argv(root, argv), cwd=root,
                    stdin=subprocess.DEVNULL, stdout=output, stderr=errors, start_new_session=True)
                try:
                    code = process.wait(timeout=240)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=10)
                    raise
            (evidence / f"phase-{phase}.exit").write_text(str(code))
            assert code == 0, f"provider phase {phase} failed; inspect retained stderr"
            rows = [json.loads(line) for line in (evidence / f"phase-{phase}.jsonl").read_text().splitlines() if line.strip()]
            thread_ids.append(next(row["thread_id"] for row in rows if row.get("type") == "thread.started"))
            candidate = root / "totals.py"
            assert candidate.is_file()
            (evidence / f"phase-{phase}-totals.py").write_bytes(candidate.read_bytes())
            if phase == 1:
                (root / "handoff.md").write_text(
                    "The previous session produced the uncommitted subtotal function. Preserve it.\n"
                    "Failure to reject bool would violate the shared contract. Complete task 7 next_step only.\n"
                    f"Source SHA256: {hashlib.sha256(candidate.read_bytes()).hexdigest()}\n"
                )
        assert len(set(thread_ids)) == 2
        spec = importlib.util.spec_from_file_location("qualified_totals", root / "totals.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.subtotal([2, 3, -1]) == 4
        assert module.total_with_fee([2, 3, -1], 2) == 6
        for values, fee in [([True], 1), ([1.5], 0), ([2], True), ([2], -1)]:
            with pytest.raises(ValueError):
                module.total_with_fee(values, fee)
        observations = [e for e in EventLog(state / "events.jsonl").read_all() if e.type == "worker.runtime.observed"]
        assert len(observations) >= 4
        assert_workspace_idle(root)
        (evidence / "qualification.json").write_text(json.dumps({"status": "passed", "thread_ids": thread_ids,
            "model": "gpt-6.1-terra", "reasoning_effort": "high", "worker_cli_observations": len(observations),
            "scope": "two fresh native sessions, authenticated CLI reads, dirty candidate preservation and next-step output",
            "not_qualified": ["full Task Pipeline recovery", "cross-provider", "8+8", "Datal"]}, indent=2))
    finally:
        EventWriter(EventLog(state / "events.jsonl")).append(ZfEvent(type="simulation.done", origin="kernel",
            payload={"purpose": "continuity-real-provider"}))
        (evidence / "events.jsonl").write_bytes((state / "events.jsonl").read_bytes())
        for directory in ("diagnostics", "artifacts"):
            if (state / directory).exists():
                shutil.copytree(state / directory, evidence / directory, dirs_exist_ok=True)
