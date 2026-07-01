from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GUARDS = ROOT / "tests" / "longhorizon" / "guards"


def _run_guard(name: str, state_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(GUARDS / name), str(state_dir)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_cost_equality_guard_parses_compact_json(tmp_path: Path):
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "events.jsonl").write_text(
        json.dumps({"type": "agent.usage"}, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (state / "cost.jsonl").write_text(
        json.dumps({"cost_usd": 1.0}, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    proc = _run_guard("invariant_cost_equality.sh", state)

    assert proc.returncode == 0
    assert proc.stderr == ""


def test_cost_equality_guard_fails_on_count_mismatch(tmp_path: Path):
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "events.jsonl").write_text(
        json.dumps({"type": "agent.usage"}, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (state / "cost.jsonl").write_text("", encoding="utf-8")

    proc = _run_guard("invariant_cost_equality.sh", state)

    assert proc.returncode == 1
    assert "agent.usage=1 cost=0" in proc.stderr


def test_single_truth_guard_rejects_rogue_store(tmp_path: Path):
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "state.json").write_text("{}", encoding="utf-8")

    proc = _run_guard("invariant_single_truth.sh", state)

    assert proc.returncode == 1
    assert "rogue truth store" in proc.stderr
