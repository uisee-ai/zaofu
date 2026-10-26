"""Deterministic checks for the opt-in real-provider validation fixtures."""

from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _module(name):
    spec = spec_from_file_location(name, ROOT / "tests/e2e" / f"{name}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_projects_have_executable_routes_before_provider_call(tmp_path):
    runner = _module("intake_live_validation")
    root, evidence = tmp_path / "products", tmp_path / "evidence"
    root.mkdir()
    runner.prepare(root, ROOT, evidence)
    for kind in ("issue", "prd"):
        report = json.loads((evidence / kind / "preflight.json").read_text())
        assert report["status"] in {"GO", "WARN"}
        frozen = json.loads((evidence / kind / "freeze.json").read_text())
        assert any(row["kind"] == kind and row["available"] for row in frozen["catalog"]["routes"])
        assert frozen["config_sha256"] == runner.digest(root / kind / "zf.yaml")
        assert not any(json.loads(line)["type"] == "workflow.invoke.requested"
                       for line in (root / kind / ".zf-validation/events.jsonl").read_text().splitlines())


def test_independent_oracle_rejects_seeded_counter_bug():
    oracle = _module("intake_live_acceptance")
    with pytest.raises(AssertionError):
        oracle.accept("issue", ROOT / "tests/e2e/fixtures/intake-live/issue")


def test_independent_oracle_accepts_counter_behavior(tmp_path):
    (tmp_path / "counter.py").write_text("def advance(current, step=1):\n    return current + step\n")
    assert _module("intake_live_acceptance").accept("issue", tmp_path) == {"passed": True, "checks": 5}


@pytest.mark.parametrize("failure", ["", "failed", "indeterminate", "tampered", "stale", "timeout"])
def test_prd_review_requires_matching_successful_evidence(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace
    import zf.runtime.channel_projection as projection

    runner = _module("intake_live_validation")
    run = runner.EntryRun.__new__(runner.EntryRun)
    run.state, run.evidence = tmp_path, tmp_path / "evidence"
    consensus = {"prd_revision": 1, "artifact_ref": "prd.json", "artifact_digest": "original"}
    calls = []
    projections = iter([consensus, {**consensus, "artifact_digest": "changed"}
                        if failure == "stale" else consensus])
    monkeypatch.setattr(projection, "project_channel", lambda *_: {"consensus": {"main": next(projections)}})
    report = tmp_path / "report.json"
    report.write_text('{"verdict":"PASS"}')
    payload = {"request_id": "current", "verdict": "INDETERMINATE" if failure == "indeterminate" else "PASS",
               "report_ref": "report.json", "report_digest": runner.digest(report)}
    if failure == "tampered":
        report.write_text("altered")
    event = SimpleNamespace(id="terminal", type="output.evaluation.failed" if failure == "failed"
                            else "output.evaluation.completed", payload=payload)
    unrelated = SimpleNamespace(id="other", type="output.evaluation.completed",
                                payload={**payload, "request_id": "other", "verdict": "PASS"})
    run.log = SimpleNamespace(read_all=lambda: [event, unrelated])

    def post(action, body):
        calls.append((action, body))
        return {"request_id": "current"}

    run.post = post
    if failure:
        with pytest.raises(RuntimeError):
            run.evaluate_prd("channel", timeout=0 if failure == "timeout" else 1)
    else:
        assert run.evaluate_prd("channel", timeout=1)["event_id"] == "terminal"
    assert calls == [("channel-prd-evaluate", {"channel_id": "channel", "thread_id": "main",
                                             "role": "full", **consensus})]


@pytest.mark.parametrize("repaired", [False, True])
def test_semantic_fixture_oracle_detects_both_seeded_defects(tmp_path, repaired):
    import shutil
    import subprocess
    import sys

    source = ROOT / "tests/e2e/fixtures/intake-live/semantic"
    for name in ("auth.py", "orders.py", "test_behavior.py"):
        shutil.copy2(source / name, tmp_path / name)
    if repaired:
        (tmp_path / "auth.py").write_text("def login(password):\n    return password == 'secret'\n")
        (tmp_path / "orders.py").write_text(
            "def submit_order(rows, order):\n"
            "    if not any(row['id'] == order['id'] for row in rows):\n"
            "        rows.append(dict(order))\n"
            "    return rows\n"
        )
    result = subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=tmp_path,
                            text=True, capture_output=True, timeout=30)
    if repaired:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert "FAIL: test_valid_login" in result.stderr
        assert "FAIL: test_duplicate_submission" in result.stderr
        assert "FAILED (failures=2)" in result.stderr
