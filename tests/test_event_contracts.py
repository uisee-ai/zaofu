from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main
from zf.core.config.loader import load_config
from zf.core.config.workflow_profiles import (
    expand_issue_flow,
    expand_prd_flow,
    expand_workflow_profile,
)
from zf.core.events.model import ZfEvent
from zf.runtime.event_contracts import (
    build_event_contract_report,
    event_scope_contract_diagnostics,
)
from zf.runtime.event_problem_registry import event_consumer_contract_gaps


def _failure_events(expansion: dict) -> set[str]:
    events: set[str] = set()
    for stage in expansion["stages"]:
        aggregate = stage.get("aggregate") or {}
        for key in ("failure_event", "child_failure_event"):
            value = str(aggregate.get(key) or "")
            if value:
                events.add(value)
    return events


def test_official_flow_profile_failure_events_have_consumer_contracts() -> None:
    events = set()
    events.update(_failure_events(expand_workflow_profile({
        "flowProfile": "refactor-flow/v3",
        "entryTrigger": "refactor.scan.requested",
        "assembly": "none",
    })))
    events.update(_failure_events(expand_issue_flow({
        "entryTrigger": "issue.requested",
    })))
    events.update(_failure_events(expand_prd_flow({
        "entryTrigger": "prd.requested",
    })))

    assert {
        "issue.triage.failed",
        "issue.triage.child.failed",
        "prd.scan.failed",
        "prd.scan.child.failed",
        "prd.plan.failed",
        "prd.plan.child.failed",
    } <= events
    assert event_consumer_contract_gaps(events) == []


def test_event_contract_report_includes_recovery_closeout(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        harness_profile="baseline",
        failure_event="flow.goal.blocked",
    )
    report = build_event_contract_report(load_config(path))

    assert "recovery_closeout" in report
    assert report["recovery_closeout"]["ok"] is True


def test_prod_new_yaml_event_contracts_are_clean() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    for path in (
        repo_root / "examples/prod/new/refactor-lane-v2.yaml",
        repo_root / "examples/prod/new/prd-fanout-v2.yaml",
        repo_root / "examples/prod/new/issue-fanout-v2.yaml",
    ):
        report = build_event_contract_report(load_config(path))
        assert report["ok"], (path, report["errors"], report["warnings"])


def test_event_contract_report_flags_custom_actionable_stage_failure(
    tmp_path: Path,
) -> None:
    path = _write_config(
        tmp_path,
        harness_profile="baseline",
        failure_event="custom.scan.failed",
    )
    config = load_config(path)

    report = build_event_contract_report(config)

    assert report["ok"] is False
    assert any(
        item["kind"] == "missing_consumer_contract"
        and item["event_type"] == "custom.scan.failed"
        for item in report["errors"]
    )


def test_event_contract_report_enforces_stage_child_kernel_boundary(
    tmp_path: Path,
) -> None:
    path = _write_config(
        tmp_path,
        harness_profile="baseline",
        child_failure_event="flow.goal.blocked",
    )
    config = load_config(path)

    report = build_event_contract_report(config)

    assert report["ok"] is False
    assert any(
        item["kind"] == "child_result_owner_boundary_violation"
        and item["event_type"] == "flow.goal.blocked"
        for item in report["errors"]
    )


def test_event_scope_contract_diagnostics_require_actionable_scope() -> None:
    missing = ZfEvent(type="flow.goal.blocked", payload={"reason": "not closed"})
    scoped = ZfEvent(
        type="flow.goal.blocked",
        payload={"trace_id": "trace-r7", "reason": "not closed"},
    )

    diagnostics = event_scope_contract_diagnostics([missing, scoped])

    assert [item.event_id for item in diagnostics] == [missing.id]
    assert diagnostics[0].kind == "missing_actionable_scope"


def test_doctor_event_contract_json_reports_errors(tmp_path: Path, capsys) -> None:
    path = _write_config(
        tmp_path,
        harness_profile="baseline",
        failure_event="custom.scan.failed",
    )

    rc = main(["doctor", "event-contract", "--json", "--path", str(path)])

    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["errors"] == 1
    assert report["errors"][0]["event_type"] == "custom.scan.failed"


def test_validate_cold_start_strict_fails_on_event_contract_error(
    tmp_path: Path,
    capsys,
) -> None:
    path = _write_config(
        tmp_path,
        harness_profile="strict",
        failure_event="custom.scan.failed",
        initialized=True,
    )

    rc = main(["validate", "--path", str(path), "--cold-start"])

    assert rc == 1
    out = capsys.readouterr().out
    assert "Event Contract:" in out
    assert "custom.scan.failed" in out


def _write_config(
    tmp_path: Path,
    *,
    harness_profile: str,
    failure_event: str = "scan.failed",
    child_failure_event: str = "workflow.child.failed",
    initialized: bool = False,
) -> Path:
    for name in ("README.md", "AGENTS.md", "CLAUDE.md"):
        (tmp_path / name).write_text(f"# {name}\n", encoding="utf-8")
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "tests").mkdir(exist_ok=True)
    if initialized:
        state = tmp_path / ".zf-test"
        state.mkdir(exist_ok=True)
        (state / "events.jsonl").write_text("", encoding="utf-8")
    path = tmp_path / "zf.yaml"
    path.write_text(
        f"""\
version: "1.0"
project:
  name: event-contract-test
  state_dir: .zf-test
roles:
- name: scan
  instance_id: scan
  backend: mock
  role_kind: reader
workflow:
  harness_profile: {harness_profile}
  dag:
    external_triggers: [scan.requested]
  stages:
  - id: scan
    trigger: scan.requested
    topology: fanout_reader
    roles: [scan]
    aggregate:
      mode: wait_for_all
      success_event: scan.completed
      failure_event: {failure_event}
      child_success_event: workflow.child.completed
      child_failure_event: {child_failure_event}
""",
        encoding="utf-8",
    )
    return path
