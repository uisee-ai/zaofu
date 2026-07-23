from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main
from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.workflow.lane_pipeline import parse_lane_pipeline
from zf.core.workflow.inspection import (
    build_workflow_inspection_report,
    _graph_diagnostics,
)


def _healthy_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="inspect-demo"),
        workflow=WorkflowConfig(
            rework_routing={
                "dev.failed": "dev",
                "review.rejected": "dev",
                "judge.failed": "dev",
            },
        ),
        roles=[
            RoleConfig(
                name="dev",
                triggers=["task.dispatched"],
                publishes=["dev.done", "dev.failed"],
            ),
            RoleConfig(
                name="review",
                triggers=["dev.done"],
                publishes=["review.approved", "review.rejected"],
            ),
            RoleConfig(
                name="judge",
                triggers=["review.approved"],
                publishes=["judge.passed", "judge.failed"],
            ),
        ],
    )


def test_workflow_inspection_reports_go_for_complete_handoff(tmp_path: Path) -> None:
    report = build_workflow_inspection_report(
        _healthy_config(),
        project_root=tmp_path,
    )

    assert report["status"] == "GO"
    assert report["diagnostics"] == []
    assert report["handoff"]["terminal_policy"]["success_events"] == ["judge.passed"]


def test_workflow_inspection_fails_closed_for_reserved_event_and_missing_skill(
    tmp_path: Path,
) -> None:
    cfg = _healthy_config()
    cfg.runtime.skills.strict = True
    cfg.roles[0].triggers.append("task.start")
    cfg.roles[0].skills.append("missing-skill")

    report = build_workflow_inspection_report(cfg, project_root=tmp_path)
    kinds = {item["kind"] for item in report["diagnostics"]}

    assert report["status"] == "STOP"
    assert "role_uses_reserved_trigger" in kinds
    assert "skill_resolution_failed" in kinds


def test_missing_skill_stops_only_when_skills_strict(tmp_path: Path) -> None:
    """`zf start` preflight must honor config.runtime.skills.strict, the same
    flag `zf validate` gates on. Non-strict => WARN (bootable); strict => STOP.

    Regression: autoresearch controlled-stuck-recovery shipped strict=False yet
    `zf start` hard-STOPed on enabled-but-missing skills, so the harness never
    emitted session.started and the seeded run timed out at 0 tasks done.
    """
    def _cfg():
        c = _healthy_config()
        c.roles[0].skills.append("missing-skill")
        return c

    non_strict = _cfg()
    non_strict.runtime.skills.strict = False
    report = build_workflow_inspection_report(non_strict, project_root=tmp_path)
    skill_diags = [
        d for d in report["diagnostics"]
        if d["kind"] == "skill_resolution_failed"
    ]
    assert skill_diags, "missing skill must still surface a diagnostic"
    assert all(d["severity"] == "WARN" for d in skill_diags)
    # A missing skill is the only defect here, so a non-strict config still boots.
    assert report["status"] != "STOP"

    strict = _cfg()
    strict.runtime.skills.strict = True
    report = build_workflow_inspection_report(strict, project_root=tmp_path)
    skill_diags = [
        d for d in report["diagnostics"]
        if d["kind"] == "skill_resolution_failed"
    ]
    assert skill_diags
    assert all(d["severity"] == "STOP" for d in skill_diags)
    assert report["status"] == "STOP"


def test_workflow_inspection_keeps_graph_diagnostic_extra_fields(
    tmp_path: Path,
) -> None:
    cfg = _healthy_config()
    cfg.workflow.rework_routing["review.rejected"] = "missing-role"

    report = build_workflow_inspection_report(cfg, project_root=tmp_path)
    invalid = [
        item for item in report["diagnostics"]
        if item["kind"] == "invalid_rework_target"
    ][0]

    assert report["status"] == "STOP"
    assert invalid["detail"]["target_role"] == "missing-role"


def test_workflow_inspection_warns_when_failure_event_has_consumer_not_route(
    tmp_path: Path,
) -> None:
    cfg = _healthy_config()
    del cfg.workflow.rework_routing["dev.failed"]
    cfg.roles.append(
        RoleConfig(
            name="orchestrator",
            triggers=["dev.failed"],
            publishes=["task.dispatched"],
        )
    )

    report = build_workflow_inspection_report(cfg, project_root=tmp_path)
    kinds = {item["kind"] for item in report["diagnostics"]}

    assert report["status"] == "WARN"
    assert "failure_event_without_explicit_rework_route" in kinds
    assert "explicit_rework_route_missing" not in kinds


def test_workflow_inspection_accepts_lane_pipeline_on_failure_routes(
    tmp_path: Path,
) -> None:
    pipeline = parse_lane_pipeline({
        "id": "lane-refactor",
        "kind": "lane_pipeline",
        "trigger": "task_map.ready",
        "affinity_key": "affinity_tag",
        "lane_count": 1,
        "assembly": {"task": "ASSEMBLY"},
        "stages": [
            {
                "id": "impl",
                "role_pattern": "dev-lane-{lane}",
                "terminal": {
                    "success": "dev.build.done",
                    "failure": "dev.failed",
                },
                "on_failure": {"rework_to": "impl"},
            },
            {
                "id": "verify",
                "role_pattern": "verify-lane-{lane}",
                "terminal": {
                    "success": "verify.child.completed",
                    "failure": "verify.child.failed",
                },
                "on_failure": {"rework_to": "impl"},
            },
        ],
        "final": {
            "when": "all_tasks_verified",
            "role": "judge-refactor",
            "success": "judge.passed",
            "failure": "judge.failed",
        },
    })
    cfg = ZfConfig(
        project=ProjectConfig(name="lane-route-demo"),
        workflow=WorkflowConfig(pipelines=[pipeline]),
        roles=[
            RoleConfig(
                name="dev-lane-0",
                triggers=["task.assigned"],
                publishes=["dev.build.done", "dev.failed"],
            ),
            RoleConfig(
                name="verify-lane-0",
                triggers=["dev.build.done"],
                publishes=["verify.child.completed", "verify.child.failed"],
            ),
            RoleConfig(
                name="judge-refactor",
                triggers=["candidate.ready"],
                publishes=["judge.passed", "judge.failed"],
            ),
        ],
    )

    report = build_workflow_inspection_report(cfg, project_root=tmp_path)
    blocking = [
        item for item in report["diagnostics"]
        if item["event"] in {"dev.failed", "verify.child.failed"}
        and item["kind"] in {
            "missing_rework_route",
            "explicit_rework_route_missing",
        }
    ]

    assert report["status"] == "GO"
    assert blocking == []


def test_workflow_inspection_warns_for_skill_routing_and_duplicate_owner(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills" / "quality-review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: quality-review\n"
        "description: Review-only quality skill.\n"
        "stages: [review]\n"
        "backends: [claude-code]\n"
        "---\n\n"
        "# Quality\n",
        encoding="utf-8",
    )
    cfg = ZfConfig(
        project=ProjectConfig(name="skill-routing"),
        workflow=WorkflowConfig(rework_routing={"judge.failed": "review"}),
        roles=[
            RoleConfig(
                name="review",
                backend="codex",
                stages=["review"],
                triggers=["task.dispatched"],
                publishes=["review.approved"],
                skills=["quality-review"],
            ),
            RoleConfig(
                name="verify",
                backend="codex",
                stages=["verify"],
                triggers=["review.approved"],
                publishes=["verify.passed"],
                skills=["quality-review"],
            ),
            RoleConfig(
                name="judge",
                backend="codex",
                stages=["judge"],
                triggers=["verify.passed"],
                publishes=["judge.passed", "judge.failed"],
            ),
        ],
    )

    report = build_workflow_inspection_report(cfg, project_root=tmp_path)
    kinds = {item["kind"] for item in report["diagnostics"]}
    skill_entry = report["skills"]["enabled"][0]

    assert report["status"] == "WARN"
    assert "skill_routing_warning" in kinds
    assert "skill_duplicate_verification_owner" in kinds
    assert skill_entry["backends"] == ("claude-code",)
    assert skill_entry["stages"] == ("review",)


def test_workflow_inspection_normalizes_verify_lane_skill_owners(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills" / "task-verifier"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: task-verifier\ndescription: Verify a task.\n"
        "stages: [verify]\n---\n\n# Verify\n",
        encoding="utf-8",
    )
    cfg = ZfConfig(
        project=ProjectConfig(name="lane-skill-owner"),
        roles=[
            RoleConfig(
                name=f"verify-lane-{lane}",
                instance_id=f"verify-lane-{lane}",
                stages=["verify"],
                skills=["task-verifier"],
            )
            for lane in range(2)
        ],
    )

    report = build_workflow_inspection_report(cfg, project_root=tmp_path)
    assert not any(
        item["kind"] == "skill_duplicate_verification_owner"
        for item in report["diagnostics"]
    )
    cost = report["skills"]["activation_cost"]
    assert cost["indexed_count"] == 2
    assert cost["invoked_count"] is None
    assert cost["full_skill_body_bytes_charged"] == 0


def test_workflow_inspection_reports_pipeline_final_thin_judge_policy(
    tmp_path: Path,
) -> None:
    pipeline = parse_lane_pipeline({
        "id": "prd-lanes",
        "kind": "lane_pipeline",
        "trigger": "task_map.ready",
        "task_source": {"task_map_ref": "artifacts/task-map.json"},
        "affinity_key": "lane_affinity",
        "lane_count": 1,
        "assembly": "none",
        "stages": [{"id": "impl"}],
        "final": {
            "when": "all_tasks_verified",
            "role": "judge-prd",
            "success": "goal.closure.synthesized",
            "failure": "goal.closure.synthesis.failed",
        },
    })
    cfg = ZfConfig(
        project=ProjectConfig(name="thin-judge-inspection"),
        workflow=WorkflowConfig(pipelines=[pipeline]),
        roles=[RoleConfig(
            name="judge-prd",
            instance_id="judge-prd",
            backend="codex",
            role_kind="reader",
            permission_mode="bypass",
        )],
    )

    report = build_workflow_inspection_report(cfg, project_root=tmp_path)
    policy = next(
        item for item in report["diagnostics"]
        if item["kind"] == "goal_closure_judge_runner_policy_applied"
    )
    assert policy["detail"]["policy_id"] == "goal_closure_judge_readonly.v1"
    assert policy["detail"]["changes"]["permission_mode"]["to"] == "restricted"


def test_workflow_inspection_does_not_report_codex_interactive_narrowing(
    tmp_path: Path,
) -> None:
    cfg = ZfConfig(
        project=ProjectConfig(name="synth-policy"),
        roles=[
            RoleConfig(name="review-a", backend="mock", role_kind="reader"),
            RoleConfig(
                name="review-synth",
                backend="codex",
                role_kind="reader",
                permission_mode="bypass",
                allowed_tools=["Read"],
            ),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="review",
                topology="fanout_reader",
                roles=["review-a"],
                aggregate=FanoutAggregateConfig(
                    success_event="review.approved",
                    failure_event="review.rejected",
                    synth_role="review-synth",
                ),
            ),
        ]),
    )

    report = build_workflow_inspection_report(cfg, project_root=tmp_path)
    policies = [
        item for item in report["diagnostics"]
        if item["kind"] == "pure_aggregator_runner_policy_applied"
    ]

    assert policies == []


def test_workflow_inspect_cli_emits_json(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: inspect-cli\n"
        "workflow:\n"
        "  rework_routing:\n"
        "    dev.failed: dev\n"
        "    judge.failed: dev\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: python\n"
        "    triggers: [task.dispatched]\n"
        "    publishes: [dev.done, dev.failed]\n"
        "  - name: judge\n"
        "    backend: python\n"
        "    triggers: [dev.done]\n"
        "    publishes: [judge.passed, judge.failed]\n",
        encoding="utf-8",
    )

    result = main([
        "workflow",
        "inspect",
        "--format",
        "json",
        "--write-artifact",
    ])
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert result == 0
    assert data["schema_version"] == "workflow-inspection.v1"
    assert data["status"] == "GO"
    assert data["project"]["name"] == "inspect-cli"
    assert (tmp_path / ".zf" / "artifacts" / "workflow-inspect" / "inspect.json").exists()
    assert (tmp_path / ".zf" / "artifacts" / "workflow-inspect" / "inspect.md").exists()


class TestKernelSweptFailureExemption:
    """2026-06-11 决策 A:candidate 级失败由 kernel sweep 兜底,缺 route = INFO 非 STOP。"""

    def test_set_stays_in_sync_with_runtime_sweep(self):
        from zf.core.workflow.inspection import KERNEL_SWEPT_FAILURE_EVENTS
        from zf.runtime.candidate_rework import CANDIDATE_FAIL_EVENTS

        assert KERNEL_SWEPT_FAILURE_EVENTS == frozenset(CANDIDATE_FAIL_EVENTS)

    def test_swept_failure_without_route_is_info_not_stop(self):
        config = ZfConfig(
            project=ProjectConfig(name="swept-demo"),
            workflow=WorkflowConfig(rework_routing={"dev.failed": "dev"}),
            roles=[
                RoleConfig(
                    name="dev",
                    triggers=["task.dispatched"],
                    publishes=["dev.done", "dev.failed"],
                ),
                RoleConfig(
                    name="review",
                    triggers=["dev.done"],
                    publishes=["review.approved", "review.rejected"],
                ),
            ],
        )
        report = build_workflow_inspection_report(config)
        diags = report["diagnostics"]
        swept = [d for d in diags if d.get("kind") == "kernel_swept_failure_event"]
        assert any(d.get("event") == "review.rejected" for d in swept)
        assert all(d.get("severity") == "INFO" for d in swept)
        assert not any(
            d.get("kind") == "explicit_rework_route_missing"
            and d.get("event") == "review.rejected"
            for d in diags
        )


class TestKernelProducedTriggerExemption:
    """Runtime bridge events are producers even when no stage emits them directly."""

    def test_goal_loop_kernel_bridge_triggers_are_not_false_stop(self):
        items = [
            {
                "kind": "trigger_without_producer",
                "stage_id": "flow-verify-bridge",
                "event": "test.passed",
            },
            {
                "kind": "trigger_without_producer",
                "stage_id": "flow-module-parity-scan",
                "event": "verify.parity_scan.requested",
            },
            {
                "kind": "trigger_without_producer",
                "stage_id": "flow-final-judge",
                "event": "module.parity.closed",
            },
            {
                "kind": "trigger_without_producer",
                "stage_id": "broken",
                "event": "unknown.ready",
            },
        ]
        diags = _graph_diagnostics(items, event_consumers={})

        assert [diag["event"] for diag in diags] == ["unknown.ready"]
        assert diags[0]["severity"] == "STOP"
