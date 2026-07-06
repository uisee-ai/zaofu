from __future__ import annotations

import hashlib
from pathlib import Path

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter


def test_run_closeout_report_cli_is_read_only(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.append(ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-dev",
            "candidate_ref": "cand/CJMIN-R37",
            "candidate_head_commit": "abc123",
            "candidate_base_commit": "base123",
        },
    ))
    writer.append(ZfEvent(
        type="fanout.child.failed",
        actor="zf-cli",
        payload={
            "reason": "missing_upstream_affinity_fanout",
            "child_id": "verify-lane-1",
        },
    ))
    writer.append(ZfEvent(
        type="candidate.quality.failed",
        actor="zf-cli",
        payload={"reason": "candidate-quality-failed"},
    ))
    writer.append(ZfEvent(type="verify.passed", actor="verify"))
    writer.append(ZfEvent(type="judge.passed", actor="judge"))

    before = _sha256(state_dir / "events.jsonl")
    out = tmp_path / "reports" / "hermes-r37.md"

    rc = main([
        "report",
        "run-closeout",
        "--state-dir",
        str(state_dir),
        "--out",
        str(out),
    ])

    assert rc == 0
    assert _sha256(state_dir / "events.jsonl") == before
    text = out.read_text(encoding="utf-8")
    assert "candidate.ready" in text
    assert "verify.passed" in text
    assert "judge.passed" in text
    assert "missing_upstream_affinity_fanout" in text
    assert "candidate-quality-failed" in text
    assert "cand/CJMIN-R37" in text


def test_hermes_run_report_cli_remains_compatible(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.append(ZfEvent(type="judge.passed", actor="judge"))
    out = tmp_path / "reports" / "hermes-r37.md"

    rc = main([
        "report",
        "hermes-run",
        "--state-dir",
        str(state_dir),
        "--out",
        str(out),
    ])

    assert rc == 0
    assert "Hermes Refactor Run Closeout" in out.read_text(encoding="utf-8")


def test_run_closeout_report_includes_product_contract_sections(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.append(ZfEvent(
        type="flow.goal.blocked",
        actor="zf-cli",
        payload={
            "pdd_id": "PRD-1",
            "reason": "demo gap remains",
            "open_p0_p1_gap_count": 1,
            "task_map_ref": ".zf/artifacts/PRD-1/task_map.json",
        },
    ))
    writer.append(ZfEvent(
        type="flow.gap_plan.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "PRD-1",
            "gap_plan_ref": "reports/PRD-1/gap-plan.json",
            "artifact_refs": ["reports/PRD-1/discovery.md"],
            "gap_tasks": [{
                "task_id": "PRD-1-GAP-001",
                "verify_commands": ["npm run test:e2e"],
                "demo_refs": ["reports/PRD-1/demo.md"],
                "source_refs": ["docs/prd.md"],
            }],
        },
    ))
    out = tmp_path / "reports" / "prd-run-closeout.md"

    rc = main([
        "report",
        "run-closeout",
        "--state-dir",
        str(state_dir),
        "--out",
        str(out),
        "--title",
        "PRD Run Closeout",
    ])

    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert "## Open Gaps" in text
    assert "flow.goal.blocked" in text
    assert "## Verification Evidence" in text
    assert "npm run test:e2e" in text
    assert "reports/PRD-1/demo.md" in text
    assert "## Artifact / Source Refs" in text
    assert "reports/PRD-1/discovery.md" in text
    assert "reports/PRD-1/gap-plan.json" in text


def test_run_closeout_report_lists_uncovered_failure_to_eval_candidates(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.append(ZfEvent(
        id="manual-resume-1",
        type="workflow.resume.applied",
        actor="operator",
        task_id="TASK-1",
        payload={"reason": "manual resume after stale gate"},
    ))
    writer.append(ZfEvent(
        id="covered-resume-1",
        type="workflow.resume.applied",
        actor="operator",
        task_id="TASK-2",
        payload={
            "reason": "covered manual resume",
            "test_refs": ["tests/test_resume.py::test_resume"],
        },
    ))
    out = tmp_path / "reports" / "run-closeout.md"

    rc = main([
        "report",
        "run-closeout",
        "--state-dir",
        str(state_dir),
        "--out",
        str(out),
    ])

    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert "failure_to_eval_candidates: 1" in text
    section = (
        text.split("## Failure-to-Eval Candidates", 1)[1]
        .split("## Important Timeline", 1)[0]
    )
    assert "manual-resume-1" in section
    assert "create regression eval/backlog/skill proposal" in section
    assert "covered-resume-1" not in section


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
