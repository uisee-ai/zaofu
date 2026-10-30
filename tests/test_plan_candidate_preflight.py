from __future__ import annotations

import hashlib
import json

from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
from zf.runtime.plan_synth_runtime import PlanSynthRuntimeMixin


class _PlanSynthHarness(PlanSynthRuntimeMixin):
    def __init__(self, tmp_path, reports):  # noqa: ANN001
        self.state_dir = tmp_path / ".zf"
        self.state_dir.mkdir()
        self.project_root = tmp_path
        self.role = RoleConfig(
            name="plan-critic",
            instance_id="plan-critic",
            backend="mock",
            role_kind="reader",
        )
        self.config = ZfConfig(
            project=ProjectConfig(name="preflight"),
            roles=[self.role, RoleConfig(
                name="dev", instance_id="dev", backend="mock", role_kind="writer",
            )],
            workflow=WorkflowConfig(
                flow_metadata={"flow_kind": "issue"},
                stages=[WorkflowStageConfig(
                    id="issue-triage",
                    attempt_domain="plan",
                    aggregate=FanoutAggregateConfig(
                        success_event="task_map.ready",
                        failure_event="issue.plan.failed",
                        synth_role="plan-critic",
                    ),
                )],
            ),
        )
        self.event_log = EventLog(self.state_dir / "events.jsonl")
        self.event_writer = EventWriter(self.event_log)
        self.reports = reports
        self.critic_dispatch_attempted = False
        self.finalized = None

    def _fanout_roles(self, _roles):  # noqa: ANN001
        return iter([self.role])

    def _fanout_aggregate_started(self, _manifest):  # noqa: ANN001
        return True

    def _fanout_reports(self, _manifest):  # noqa: ANN001
        return self.reports

    def _is_plan_artifact_stage(self, **_kwargs):  # noqa: ANN003
        return True

    def _ensure_fanout_role_dispatchable(self, **_kwargs):  # noqa: ANN003
        self.critic_dispatch_attempted = True
        return True

    def _finalize_fanout_synth(self, event):  # noqa: ANN001
        self.finalized = event


def _task_map() -> dict:
    return {
        "schema_version": "task-map.v1",
        "goal_claims": [{
            "goal_claim_id": "CLAIM-1",
            "text": "The command succeeds.",
            "mandatory": True,
        }],
        "tasks": [{
            "task_id": "TASK-1",
            "title": "Implement the command",
            "goal_claim_ids": ["CLAIM-1"],
            "allowed_paths": ["src/command.py", "tests/test_command.py"],
            "allowed_paths_reason": "Own implementation and focused test.",
            "blocked_by": [],
            "validation": {"commands": [{
                "id": "test-command",
                "command": "pytest -q tests/test_command.py",
                "acceptance_ids": ["AC-1"],
                "owner": "task_verify",
                "tier": "runtime",
                "deterministic": True,
                "reusable": True,
                "timeout_seconds": 60,
            }]},
            "acceptance_criteria": [{
                "id": "AC-1",
                "statement": "The command succeeds.",
                "mandatory": True,
                "verification_owner": "task_verify",
                "verification_tier": "runtime",
                "verification_command_ids": ["test-command"],
            }],
        }],
    }


def _source_index() -> dict:
    return {
        "schema_version": "source-index.v1",
        "tasks": [{
            "task_id": "TASK-1",
            "source_key": "requirement:claim-1",
            "source_ref": "docs/requirement.md",
            "source_excerpt": "The command succeeds.",
        }],
    }


def _plan_ports() -> list[dict]:
    metadata = {"enrichment_contract": {"status": "fulfilled"}}
    return [
        {
            "logical_name": "capability_matrix",
            "body": {
                "schema_version": "capability-matrix.v1",
                "status": "ready",
                "metadata": metadata,
                "capabilities": [{
                    "id": "CAP-1",
                    "task_ids": ["TASK-1"],
                    "acceptance_ids": ["AC-1"],
                }],
            },
        },
        {
            "logical_name": "acceptance_matrix",
            "body": {
                "schema_version": "acceptance-matrix.v1",
                "status": "ready",
                "metadata": metadata,
                "acceptance": [{
                    "id": "AC-1",
                    "capability_id": "CAP-1",
                    "task_id": "TASK-1",
                    "verification_command_ids": ["test-command"],
                }],
            },
        },
        {
            "logical_name": "test_matrix",
            "body": {
                "schema_version": "test-matrix.v1",
                "status": "ready",
                "metadata": metadata,
                "commands": [{
                    "id": "test-command",
                    "command": "pytest -q tests/test_command.py",
                    "acceptance_ids": ["AC-1"],
                }],
            },
        },
    ]


def _product_acceptance_spec() -> dict:
    return {
        "assembly_owner": "product-assembly",
        "entrypoints": [{
            "entrypoint_id": "cli",
            "start_command": "python -m command",
            "health_check": "python -m command --help",
            "owner": "product-assembly",
        }],
        "user_journeys": [{
            "journey_id": "journey-cli",
            "title": "Run the command",
            "verification_tier": "runtime",
            "assertions": [{
                "assertion_id": "assert-cli-output",
                "statement": "The command exits successfully.",
                "observation_kind": "cli",
            }],
        }],
    }


def _matrix_metadata() -> dict:
    return {
        "flow_kind": "prd",
        "artifact_package": {
            "required_ports": [
                "requirement_spec",
                "goal_claim_set",
                "task_map",
                "planning_result",
                "capability_matrix",
                "acceptance_matrix",
                "test_matrix",
            ],
        },
    }


def _previous_task_map_descriptor(tmp_path, task_map: dict) -> dict:
    path = tmp_path / "previous-task-map.json"
    raw = json.dumps(
        task_map,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8")
    path.write_bytes(raw)
    return {
        "ref": path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "kind": "plan_candidate_task_map",
    }


def test_plan_candidate_preflight_accepts_closed_plan_handoff(tmp_path) -> None:
    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": _task_map(),
            "source_index": _source_index(),
            "source_index_ref": "inline:source-index",
            "plan_md": "# Plan\n\nImplement TASK-1.",
        }}],
        manifest={
            "fanout_id": "fanout-plan",
            "stage_id": "prd-plan",
            "trigger_payload": {
                "flow_kind": "prd",
                "prd_ref": "docs/requirement.md",
            },
        },
        metadata={
            "flow_kind": "prd",
            "artifact_package": {
                "required_ports": [
                    "requirement_spec",
                    "goal_claim_set",
                    "task_map",
                    "planning_result",
                ],
            },
        },
    )

    assert result["status"] == "passed"
    assert result["summary"] == {"error_count": 0, "task_count": 1}
    assert result["execution_review"]["graph"]["task_count"] == 1


def test_missing_task_partition_is_rejected_before_critic(tmp_path):
    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / "state", project_root=tmp_path,
        reports=[{"report": {
            "task_map": _task_map(), "source_index": _source_index(),
            "source_index_ref": "inline:source-index", "plan_md": "# Plan",
            "plan_ports": [{"logical_name": "execution_spec", "body": {
                "schema_version": "task-scoped-plan-input.v1", "shared": {}, "tasks": [],
            }}],
        }}],
        manifest={"trigger_payload": {"flow_kind": "prd", "prd_ref": "requirements.md"}},
        metadata={"flow_kind": "prd"},
    )
    assert "task_scoped_input_invalid" in {error["code"] for error in result["errors"]}


def test_valid_candidate_delivers_execution_facts_to_actual_critic_caller(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    import zf.runtime.call_result_runtime as calls
    import zf.runtime.plan_synth_handoff as handoff

    harness = _PlanSynthHarness(tmp_path, reports=[{"child_id": "planner", "report": {
        "task_map": _task_map(), "source_index": _source_index(),
        "source_index_ref": "inline:source-index", "plan_md": "# Plan",
    }}])
    harness._checkout_fanout_reader = lambda *args: None
    from zf.core.cost.tracker import CostTracker
    CostTracker(harness.state_dir / "cost.jsonl").record_usage(
        "planner", 100, 20, model="unknown-model",
    )
    captured = {}

    def prepare(runtime, *, payload, **kwargs):
        captured.update(payload)
        return SimpleNamespace(should_dispatch=False)

    monkeypatch.setattr(calls, "prepare_call_operation", prepare)
    monkeypatch.setattr(handoff, "build_plan_synth_call_payload", lambda **kwargs: {})
    harness._dispatch_fanout_synth("fanout-plan", {
        "fanout_id": "fanout-plan", "trace_id": "run", "stage_id": "issue-triage",
        "trigger_event_id": "evt-input",
        "trigger_payload": {"flow_kind": "issue", "issue_ref": "docs/issue.md"},
        "aggregate_config": {"success_event": "task_map.ready", "synth_role": "plan-critic"},
        "children": [],
    }, "wait_for_all", "plan-critic")
    assert harness.critic_dispatch_attempted
    source = next(r for r in captured["input_refs"] if r["source_id"] == "plan-execution-review")
    assert hydrate_sidecar_ref(harness.state_dir, source).payload["graph"]["task_count"] == 1
    usage = hydrate_sidecar_ref(harness.state_dir, source).payload["usage_observations"]
    assert usage["groups"][0]["total_tokens"] == 120
    assert usage["groups"][0]["estimated_usd"] is None


def test_plan_candidate_preflight_validates_product_acceptance_before_critic(
    tmp_path,
) -> None:
    valid = _product_acceptance_spec()
    invalid = _product_acceptance_spec()
    invalid["entrypoints"][0].pop("health_check")

    def evaluate(spec: dict) -> dict:
        return evaluate_plan_candidate_preflight(
            state_dir=tmp_path / ".zf",
            project_root=tmp_path,
            reports=[{"report": {
                "task_map": _task_map(),
                "source_index": _source_index(),
                "source_index_ref": "inline:source-index",
                "plan_md": "# Plan\n\nImplement TASK-1.",
                "plan_ports": [{
                    "logical_name": "product_acceptance_spec",
                    "body": spec,
                }],
            }}],
            manifest={
                "fanout_id": "fanout-plan-product",
                "stage_id": "prd-plan",
                "workflow_run_id": "run-product",
                "trigger_payload": {
                    "flow_kind": "prd",
                    "prd_ref": "docs/requirement.md",
                },
            },
            metadata={
                "flow_kind": "prd",
                "artifact_package": {"required_ports": [
                    "requirement_spec",
                    "goal_claim_set",
                    "task_map",
                    "planning_result",
                    "product_acceptance_spec",
                ]},
            },
        )

    failed = evaluate(invalid)
    passed = evaluate(valid)

    assert "product_acceptance_spec_invalid" in {
        item["code"] for item in failed["errors"]
    }
    assert any(
        "health_check" in item["message"] for item in failed["errors"]
    )
    assert passed["status"] == "passed"


def test_plan_candidate_preflight_rejects_inherited_goal_contract_weakening(
    tmp_path,
) -> None:
    previous = _task_map()
    previous["goal_claims"][0].update({
        "acceptance_ids": ["AC-1"],
        "verification_command_ids": ["test-command"],
        "verification_owner": "task_verify",
        "verification_tier": "runtime",
    })
    descriptor = _previous_task_map_descriptor(tmp_path, previous)
    current = json.loads(json.dumps(previous))
    current["goal_claims"][0].update({
        "acceptance_ids": ["AC-RELEASE"],
        "verification_command_ids": ["release-audit"],
        "verification_owner": "candidate_verify",
    })

    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": current,
            "source_index": _source_index(),
            "source_index_ref": "inline:source-index",
            "plan_md": "# Plan\n\nDo not weaken inherited claims.",
        }}],
        manifest={"trigger_payload": {
            "flow_kind": "prd",
            "prd_ref": "docs/requirement.md",
            "previous_plan_candidate_refs": [descriptor],
        }},
        metadata={
            "flow_kind": "prd",
            "artifact_package": {"required_ports": [
                "requirement_spec",
                "goal_claim_set",
                "task_map",
                "planning_result",
            ]},
        },
    )

    assert "mandatory_goal_claim_contract_rewritten" in {
        item["code"] for item in result["errors"]
    }


def test_plan_candidate_preflight_accepts_pinned_immutable_e2e_baseline(
    tmp_path,
) -> None:
    task_map = _task_map()
    criterion = task_map["tasks"][0]["acceptance_criteria"][0]
    criterion.update({
        "verification_owner": "candidate_verify",
        "verification_tier": "e2e",
        "evidence_mode": "immutable_baseline_only",
        "evidence_refs": [
            f"git:{'a' * 40}",
            "artifacts/evidence/browser/manifest.json",
        ],
    })
    criterion["verification_command_ids"] = []
    task_map["tasks"][0]["validation"]["commands"][0][
        "acceptance_ids"
    ] = []

    admitted = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": task_map,
            "source_index": _source_index(),
            "source_index_ref": "inline:source-index",
            "plan_md": "# Plan\n\nRetain pinned browser evidence.",
        }}],
        manifest={"trigger_payload": {
            "flow_kind": "prd",
            "prd_ref": "docs/requirement.md",
        }},
        metadata={
            "flow_kind": "prd",
            "artifact_package": {"required_ports": [
                "requirement_spec",
                "goal_claim_set",
                "task_map",
                "planning_result",
            ]},
        },
    )
    assert admitted["status"] == "passed", admitted["errors"]

    criterion["evidence_refs"] = [
        "artifacts/evidence/browser/manifest.json",
    ]
    unpinned = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": task_map,
            "source_index": _source_index(),
            "source_index_ref": "inline:source-index",
            "plan_md": "# Plan\n\nReject unpinned browser evidence.",
        }}],
        manifest={"trigger_payload": {
            "flow_kind": "prd",
            "prd_ref": "docs/requirement.md",
        }},
        metadata={
            "flow_kind": "prd",
            "artifact_package": {"required_ports": [
                "requirement_spec",
                "goal_claim_set",
                "task_map",
                "planning_result",
            ]},
        },
    )
    assert "immutable_baseline_evidence_unpinned" in {
        item["code"] for item in unpinned["errors"]
    }


def test_plan_candidate_preflight_rejects_command_on_immutable_e2e_baseline(
    tmp_path,
) -> None:
    task_map = _task_map()
    task_map["tasks"][0]["acceptance_criteria"][0].update({
        "verification_owner": "candidate_verify",
        "verification_tier": "e2e",
        "evidence_mode": "immutable_baseline_only",
        "evidence_refs": [
            f"git:{'a' * 40}",
            "artifacts/evidence/browser/manifest.json",
        ],
    })

    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": task_map,
            "source_index": _source_index(),
            "source_index_ref": "inline:source-index",
            "plan_md": "# Plan\n\nReuse immutable browser evidence.",
        }}],
        manifest={"trigger_payload": {
            "flow_kind": "prd",
            "prd_ref": "docs/requirement.md",
        }},
        metadata={
            "flow_kind": "prd",
            "artifact_package": {"required_ports": [
                "requirement_spec",
                "goal_claim_set",
                "task_map",
                "planning_result",
            ]},
        },
    )

    assert "immutable_baseline_command_refs_present" in {
        item["code"] for item in result["errors"]
    }


def test_plan_candidate_preflight_accepts_cross_task_command_refs(tmp_path) -> None:
    task_map = _task_map()
    task_map["tasks"][0]["validation"]["commands"][0][
        "acceptance_ids"
    ] = ["AC-1", "AC-2"]
    task_map["tasks"].append({
        "task_id": "TASK-2",
        "title": "Consume the shared command",
        "goal_claim_ids": [],
        "allowed_paths": ["src/consumer.py", "tests/test_consumer.py"],
        "allowed_paths_reason": "Own the consumer and its rolling smoke test.",
        "blocked_by": ["TASK-1"],
        "validation": {"commands": [{
            "id": "test-consumer",
            "command": "pytest -q tests/test_consumer.py",
            "acceptance_ids": ["AC-2"],
            "owner": "task_verify",
            "tier": "runtime",
            "deterministic": True,
            "reusable": True,
            "timeout_seconds": 60,
        }]},
        "acceptance_criteria": [{
            "id": "AC-2",
            "statement": "The consumer preserves the shared behavior.",
            "mandatory": True,
            "verification_owner": "task_verify",
            "verification_tier": "runtime",
            "verification_command_ids": ["test-command", "test-consumer"],
        }],
    })
    source_index = _source_index()
    source_index["tasks"].append({
        "task_id": "TASK-2",
        "source_key": "requirement:consumer",
        "source_ref": "docs/requirement.md",
        "source_excerpt": "The consumer preserves the shared behavior.",
    })
    ports = _plan_ports()
    ports[0]["body"]["capabilities"].append({
        "id": "CAP-2",
        "task_ids": ["TASK-2"],
        "acceptance_ids": ["AC-2"],
    })
    ports[1]["body"]["acceptance"].append({
        "id": "AC-2",
        "capability_id": "CAP-2",
        "task_id": "TASK-2",
        "verification_command_ids": ["test-command", "test-consumer"],
    })
    ports[2]["body"]["commands"][0]["acceptance_ids"] = ["AC-1", "AC-2"]
    ports[2]["body"]["commands"].append({
        "id": "test-consumer",
        "command": "pytest -q tests/test_consumer.py",
        "acceptance_ids": ["AC-2"],
    })

    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": task_map,
            "source_index": source_index,
            "plan_ports": ports,
            "plan_md": "# Plan\n\nUse one global command registry.",
        }}],
        manifest={"trigger_payload": {
            "flow_kind": "prd",
            "prd_ref": "docs/requirement.md",
        }},
        metadata=_matrix_metadata(),
    )

    assert result["status"] == "passed", result["errors"]
    assert result["summary"] == {"error_count": 0, "task_count": 2}


def test_plan_candidate_preflight_uses_runtime_writer_split_policy(
    tmp_path,
) -> None:
    task_map = _task_map()
    task_map["tasks"][0]["acceptance_criteria"].append({
        "id": "AC-2",
        "statement": "The second condition succeeds.",
        "mandatory": True,
        "verification_owner": "task_verify",
        "verification_tier": "runtime",
        "verification_command_ids": ["test-command"],
    })
    task_map["tasks"][0]["validation"]["commands"][0][
        "acceptance_ids"
    ].append("AC-2")

    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": task_map,
            "source_index": _source_index(),
            "source_index_ref": "inline:source-index",
            "plan_md": "# Plan\n\nImplement TASK-1.",
        }}],
        manifest={
            "fanout_id": "fanout-plan",
            "stage_id": "prd-plan",
            "trigger_payload": {
                "flow_kind": "prd",
                "prd_ref": "docs/requirement.md",
            },
        },
        metadata={
            "flow_kind": "prd",
            "artifact_package": {
                "required_ports": [
                    "requirement_spec",
                    "goal_claim_set",
                    "task_map",
                    "planning_result",
                ],
            },
        },
        writer_policy={
            "candidate_quality_source": "auto",
            "work_units": {
                "enabled": True,
                "split_quality": {
                    "mode": "blocking",
                    "max_scope_files": 12,
                    "max_acceptance_criteria": 1,
                },
            },
        },
    )

    assert result["status"] == "failed"
    assert any(
        item["code"] == "writer_fanout_task_map_policy_failed"
        and "2 acceptance criteria, max is 1" in item["message"]
        for item in result["errors"]
    )


def test_v4_plan_candidate_requires_explicit_rolling_smoke_command(tmp_path) -> None:
    metadata = {
        "flow_kind": "issue",
        "task_pipeline": {"candidate": {"rolling_smoke": "required"}},
    }
    task_map = _task_map()
    reports = [{"report": {
        "task_map": task_map,
        "source_index": _source_index(),
        "source_index_ref": "inline:source-index",
        "plan_md": "# Plan\n\nImplement TASK-1.",
    }}]
    manifest = {
        "fanout_id": "fanout-plan",
        "stage_id": "issue-triage",
        "trigger_payload": {
            "flow_kind": "issue",
            "issue_ref": "docs/requirement.md",
        },
    }

    missing = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=reports,
        manifest=manifest,
        metadata=metadata,
    )
    assert missing["status"] == "failed"
    assert "rolling_smoke_command_missing" in {
        item["code"] for item in missing["errors"]
    }

    task_map["tasks"][0]["validation"]["commands"][0][
        "rolling_smoke"
    ] = True
    admitted = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=reports,
        manifest=manifest,
        metadata=metadata,
    )
    assert admitted["status"] == "passed"


def test_plan_candidate_preflight_reports_all_structural_gaps(tmp_path) -> None:
    task_map = _task_map()
    command = task_map["tasks"][0]["validation"]["commands"][0]
    command.pop("owner")
    task_map["tasks"][0]["acceptance_criteria"][0].pop(
        "verification_command_ids"
    )
    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {"task_map": task_map}}],
        manifest={
            "fanout_id": "fanout-plan",
            "stage_id": "issue-triage",
            "trigger_payload": {
                "flow_kind": "issue",
                "issue_ref": "docs/issue.md",
            },
        },
        metadata={"flow_kind": "issue"},
    )

    codes = {item["code"] for item in result["errors"]}
    assert result["status"] == "failed"
    assert {
        "required_plan_port_missing",
        "acceptance_command_missing",
        "command_owner_missing",
        "plan_markdown_producer_missing",
    } <= codes


def test_invalid_candidate_fails_before_plan_critic_dispatch(tmp_path) -> None:
    harness = _PlanSynthHarness(
        tmp_path,
        reports=[{"child_id": "planner", "report": {"task_map": _task_map()}}],
    )
    harness.reports[0]["report"]["task_map"]["tasks"][0][
        "acceptance_criteria"
    ][0].pop("verification_command_ids")

    harness._dispatch_fanout_synth(
        "fanout-plan-preflight",
        {
            "fanout_id": "fanout-plan-preflight",
            "trace_id": "run-plan-preflight",
            "stage_id": "issue-triage",
            "trigger_event_id": "evt-plan-input",
            "trigger_payload": {
                "flow_kind": "issue",
                "issue_ref": "docs/issue.md",
            },
            "aggregate_config": {
                "success_event": "task_map.ready",
                "failure_event": "issue.plan.failed",
                "synth_role": "plan-critic",
            },
            "children": [],
        },
        "wait_for_all",
        "plan-critic",
    )

    assert harness.critic_dispatch_attempted is False
    assert harness.finalized is not None
    assert harness.finalized.payload["failure_class"] == (
        "plan_candidate_preflight"
    )
    assert not any(
        event.type == "fanout.synth.dispatched"
        for event in harness.event_log.read_all()
    )
    assert harness.finalized.payload["report"]["findings"][0]["evidence_refs"]
    previous_refs = harness.finalized.payload["previous_plan_candidate_refs"]
    assert any(
        item.get("kind") == "fanout_child_result"
        for item in previous_refs
    )
    assert any(
        item.get("source_id") == "plan-candidate-preflight"
        for item in previous_refs
    )


def test_plan_candidate_preflight_accepts_closed_matrix_registry(tmp_path) -> None:
    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": _task_map(),
            "source_index": _source_index(),
            "plan_ports": _plan_ports(),
            "plan_md": "# Plan\n\nImplement TASK-1.",
        }}],
        manifest={
            "stage_id": "prd-plan",
            "trigger_payload": {
                "flow_kind": "prd",
                "prd_ref": "docs/requirement.md",
            },
        },
        metadata=_matrix_metadata(),
    )

    assert result["status"] == "passed"


def test_plan_candidate_preflight_rejects_runtime_audit_as_real_e2e(
    tmp_path,
) -> None:
    ports = _plan_ports()
    ports.append({
        "logical_name": "real_e2e_matrix",
        "body": {
            "schema_version": "real-e2e-matrix.v1",
            "status": "ready",
            "metadata": {"enrichment_contract": {"status": "fulfilled"}},
            "rows": [{
                "id": "AUDIT-AS-E2E",
                "acceptance_ids": ["AC-1"],
                "command_id": "test-command",
                "command": "pytest -q tests/test_command.py",
                "command_required": True,
            }],
        },
    })

    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": _task_map(),
            "source_index": _source_index(),
            "plan_ports": ports,
            "plan_md": "# Plan\n\nKeep runtime audits out of real E2E.",
        }}],
        manifest={"trigger_payload": {
            "flow_kind": "prd",
            "prd_ref": "docs/requirement.md",
        }},
        metadata=_matrix_metadata(),
    )

    assert "real_e2e_command_tier_invalid" in {
        item["code"] for item in result["errors"]
    }


def test_plan_candidate_preflight_accepts_immutable_real_e2e_baseline_row(
    tmp_path,
) -> None:
    task_map = _task_map()
    criterion = task_map["tasks"][0]["acceptance_criteria"][0]
    criterion.update({
        "verification_owner": "candidate_verify",
        "verification_tier": "e2e",
        "verification_command_ids": [],
        "evidence_mode": "immutable_baseline_only",
        "evidence_refs": [
            f"git:{'a' * 40}",
            "artifacts/evidence/browser/manifest.json",
        ],
    })
    task_map["tasks"][0]["validation"]["commands"][0][
        "acceptance_ids"
    ] = []
    ports = _plan_ports()
    ports[0]["body"]["capabilities"][0]["command_ids"] = ["test-command"]
    ports[1]["body"]["acceptance"][0]["verification_command_ids"] = []
    ports[2]["body"]["commands"][0]["acceptance_ids"] = []
    ports.append({
        "logical_name": "real_e2e_matrix",
        "body": {
            "schema_version": "real-e2e-matrix.v1",
            "status": "ready",
            "metadata": {"enrichment_contract": {"status": "fulfilled"}},
            "rows": [{
                "id": "BASELINE-AC-1",
                "acceptance_ids": ["AC-1"],
                "execution_mode": "immutable_baseline_only",
                "command_required": False,
                "origin_command": "docker compose up --abort-on-container-exit",
                "target_commit": "a" * 40,
                "evidence_refs": [
                    f"git:{'a' * 40}",
                    "artifacts/evidence/browser/manifest.json",
                ],
            }],
        },
    })

    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": task_map,
            "source_index": _source_index(),
            "plan_ports": ports,
            "plan_md": "# Plan\n\nReuse pinned browser evidence.",
        }}],
        manifest={"trigger_payload": {
            "flow_kind": "prd",
            "prd_ref": "docs/requirement.md",
        }},
        metadata=_matrix_metadata(),
    )

    assert result["status"] == "passed", result["errors"]


def test_plan_candidate_preflight_accepts_tests_that_reference_command_registry(
    tmp_path,
) -> None:
    ports = _plan_ports()
    ports[0]["body"]["capabilities"][0]["test_ids"] = ["TEST-1"]
    ports[2]["body"]["tests"] = [{
        "id": "TEST-1",
        "test_id": "TEST-1",
        "capability_id": "CAP-1",
        "acceptance_ids": ["AC-1"],
        "commands": ["test-command"],
    }]

    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": _task_map(),
            "source_index": _source_index(),
            "plan_ports": ports,
            "plan_md": "# Plan\n\nImplement TASK-1.",
        }}],
        manifest={
            "stage_id": "prd-plan",
            "trigger_payload": {
                "flow_kind": "prd",
                "prd_ref": "docs/requirement.md",
            },
        },
        metadata=_matrix_metadata(),
    )

    assert result["status"] == "passed"


def test_plan_candidate_preflight_accepts_matching_task_map_producers(
    tmp_path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    task_map = _task_map()
    task_map_path = tmp_path / "artifacts" / "plan" / "task_map.json"
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(
        json.dumps(task_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result = evaluate_plan_candidate_preflight(
        state_dir=state_dir,
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": task_map,
            "task_map_ref": "artifacts/plan/task_map.json",
            "source_index": _source_index(),
            "plan_ports": [{
                "logical_name": "task_map",
                "body": task_map,
            }],
            "plan_md": "# Plan\n\nImplement TASK-1.",
        }}],
        manifest={
            "stage_id": "prd-plan",
            "trigger_payload": {
                "flow_kind": "prd",
                "prd_ref": "docs/requirement.md",
            },
        },
        metadata={
            "flow_kind": "prd",
            "artifact_package": {
                "required_ports": [
                    "requirement_spec",
                    "goal_claim_set",
                    "task_map",
                    "planning_result",
                ],
            },
        },
    )

    assert result["status"] == "passed"
    assert result["task_map_producer"] == "report[0].task_map"


def test_plan_candidate_preflight_rejects_task_map_producer_mismatch(
    tmp_path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    inline = _task_map()
    stale = _task_map()
    stale["tasks"][0]["title"] = "Stale task title"
    task_map_path = tmp_path / "artifacts" / "plan" / "task_map.json"
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(
        json.dumps(stale, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result = evaluate_plan_candidate_preflight(
        state_dir=state_dir,
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": inline,
            "task_map_ref": "artifacts/plan/task_map.json",
            "source_index": _source_index(),
            "plan_ports": [{
                "logical_name": "task_map",
                "body": inline,
            }],
            "plan_md": "# Plan",
        }}],
        manifest={"trigger_payload": {
            "flow_kind": "prd",
            "prd_ref": "docs/requirement.md",
        }},
        metadata={"flow_kind": "prd"},
    )

    mismatches = [
        item for item in result["errors"]
        if item["code"] == "task_map_producer_mismatch"
    ]
    assert result["status"] == "failed"
    assert len(mismatches) == 1
    assert "report[0].task_map=" in mismatches[0]["message"]
    assert "report[0].task_map_ref=" in mismatches[0]["message"]
    assert "plan_ports.task_map=" in mismatches[0]["message"]


def test_plan_candidate_preflight_rejects_unknown_test_command_reference(
    tmp_path,
) -> None:
    ports = _plan_ports()
    ports[2]["body"]["tests"] = [{
        "test_id": "TEST-1",
        "capability_id": "CAP-1",
        "acceptance_ids": ["AC-1"],
        "commands": ["missing-command"],
    }]

    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": _task_map(),
            "source_index": _source_index(),
            "plan_ports": ports,
            "plan_md": "# Plan",
        }}],
        manifest={"trigger_payload": {"flow_kind": "prd", "prd_ref": "prd.md"}},
        metadata=_matrix_metadata(),
    )

    errors = result["errors"]
    assert result["status"] == "failed"
    assert any(
        item["code"] == "command_ref_unknown"
        and item["field"] == "test_matrix.tests.TEST-1.command_ids"
        for item in errors
    )


def test_plan_candidate_preflight_rejects_second_test_command_registry(
    tmp_path,
) -> None:
    ports = _plan_ports()
    ports[2]["body"]["tests"] = [{
        "id": "test-command",
        "command": "pytest -q tests/test_command.py",
        "acceptance_ids": ["AC-1"],
    }]

    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": _task_map(),
            "source_index": _source_index(),
            "plan_ports": ports,
            "plan_md": "# Plan",
        }}],
        manifest={"trigger_payload": {
            "flow_kind": "prd",
            "prd_ref": "docs/requirement.md",
        }},
        metadata=_matrix_metadata(),
    )

    codes = {item["code"] for item in result["errors"]}
    assert result["status"] == "failed"
    assert "test_command_registry_noncanonical" in codes
    assert "test_matrix_commands_id_duplicate" not in codes


def test_plan_candidate_preflight_rejects_cross_port_dangling_refs(tmp_path) -> None:
    ports = _plan_ports()
    ports[0]["body"]["capabilities"][0]["task_ids"] = ["TASK-MISSING"]
    ports[1]["body"]["acceptance"][0]["verification_command_ids"] = [
        "command-missing"
    ]
    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": _task_map(),
            "source_index": _source_index(),
            "plan_ports": ports,
            "plan_md": "# Plan",
        }}],
        manifest={"trigger_payload": {"flow_kind": "prd", "prd_ref": "prd.md"}},
        metadata=_matrix_metadata(),
    )

    codes = {item["code"] for item in result["errors"]}
    assert result["status"] == "failed"
    assert "task_ref_unknown" in codes
    assert "acceptance_command_set_mismatch" in codes


def test_plan_candidate_preflight_rejects_command_root_and_self_lock(tmp_path) -> None:
    task_map = _task_map()
    task = task_map["tasks"][0]
    task["blocked_by"] = ["TASK-1"]
    task["validation"]["commands"][0]["cwd"] = "../outside"
    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": task_map,
            "source_index": _source_index(),
            "plan_ports": _plan_ports(),
            "plan_md": "# Plan",
            "summary": "All findings were fixed.",
        }}],
        manifest={"trigger_payload": {"flow_kind": "prd", "prd_ref": "prd.md"}},
        metadata=_matrix_metadata(),
    )

    codes = {item["code"] for item in result["errors"]}
    assert result["status"] == "failed"
    assert "task_map_invalid" in codes
    assert "command_execution_root_invalid" in codes


def test_plan_candidate_preflight_uses_writer_path_ownership_gate(tmp_path) -> None:
    task_map = _task_map()
    task_map["tasks"][0]["exclusive_files"] = ["src/command.py"]
    second = json.loads(json.dumps(task_map["tasks"][0]))
    second.update({
        "task_id": "TASK-2",
        "title": "Integrate the command",
        "goal_claim_ids": [],
        "blocked_by": ["TASK-1"],
        "wave": 2,
        "allowed_paths": ["src/command.py", "tests/test_integration.py"],
        "allowed_paths_reason": "Own the integration test; read TASK-1 output.",
        "exclusive_files": ["tests/test_integration.py"],
    })
    second["validation"]["commands"][0].update({
        "id": "test-integration",
        "command": "pytest -q tests/test_integration.py",
        "acceptance_ids": ["AC-2"],
    })
    second["acceptance_criteria"][0].update({
        "id": "AC-2",
        "verification_command_ids": ["test-integration"],
    })
    task_map["tasks"].append(second)
    source_index = _source_index()
    source_index["tasks"].append({
        "task_id": "TASK-2",
        "source_key": "requirement:claim-1",
        "source_ref": "docs/requirement.md",
        "source_excerpt": "The command succeeds.",
    })

    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": task_map,
            "source_index": source_index,
            "plan_md": "# Plan",
        }}],
        manifest={"trigger_payload": {
            "flow_kind": "issue",
            "issue_ref": "docs/issue.md",
        }},
        metadata={"flow_kind": "issue"},
    )

    writer_errors = [
        item for item in result["errors"]
        if item["code"] == "writer_fanout_task_map_invalid"
    ]
    assert result["status"] == "failed"
    assert len(writer_errors) == 1
    assert "overlapping allowed paths" in writer_errors[0]["message"]


def test_plan_candidate_preflight_returns_skills_required_diagnostic(tmp_path) -> None:
    task_map = _task_map()
    task_map["tasks"][0]["skills_required"] = "can-domain"

    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".zf",
        project_root=tmp_path,
        reports=[{"report": {
            "task_map": task_map,
            "source_index": _source_index(),
            "plan_md": "# Plan",
        }}],
        manifest={"trigger_payload": {
            "flow_kind": "issue",
            "issue_ref": "docs/issue.md",
        }},
        metadata={"flow_kind": "issue"},
    )

    writer_errors = [
        item for item in result["errors"]
        if item["code"] == "writer_fanout_task_map_invalid"
    ]
    assert result["status"] == "failed"
    assert len(writer_errors) == 1
    assert "skills_required must be a list" in writer_errors[0]["message"]
