from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.render import renderable_config_to_primitive
from zf.core.config.task_pipeline_profile import compile_task_pipeline_profile
from zf.core.config.workflow_profiles import WorkflowProfileError, expand_prd_flow
from zf.core.profile.flows import flow_id_for_intent
from zf.runtime.task_pipeline_reconciler import task_pipeline_policy_partitions


def _task_pipeline(**overrides):
    value = {
        "mode": "shadow",
        "maxActiveTaskPipelines": 4,
        "pools": {
            "impl": {"capacity": 2},
            "verify": {"capacity": 2},
        },
        "backpressure": {
            "maxUnverifiedTasks": 3,
            "maxIntegrationQueue": 2,
        },
        "workerLifecycle": {"mode": "on_demand", "idleSeconds": 120},
        "affinity": {
            "implRework": "prefer_previous_session",
            "verifyIndependence": "different_role",
            "sessionBinding": "task_stage",
            "crossTaskContext": "fresh",
        },
        "integrationAdmission": {
            "default": "verify_admitted",
            "riskReview": {"enabled": False},
        },
        "candidate": {
            "integration": "incremental_serial_cas",
            "integrationCapacity": 1,
            "rollingSmoke": "required",
            "incrementalEvent": "integration.queue.integrated",
            "freezeEvent": "candidate.ready",
            "deliveryEvent": "run.delivery.completed",
            "partialCandidateAutoShip": "forbidden",
            "finalVerifyTarget": "frozen_exact_commit",
        },
    }
    value.update(overrides)
    return value


def _compile(raw=None):
    return compile_task_pipeline_profile(
        flow_kind="prd",
        profile_id="prd-flow-v4-task-pipeline",
        raw=raw or _task_pipeline(),
        default_impl_roles=["dev-lane-0", "dev-lane-1"],
        default_verify_roles=["verify-lane-0", "verify-lane-1"],
    )


def test_v4_profile_compiles_to_frozen_digest_without_changing_lane_shape() -> None:
    first = _compile()
    second = _compile()
    expansion = expand_prd_flow({
        "flowProfile": "prd-flow-v4-task-pipeline",
        "topology": "fanout",
        "lanes": 2,
        "taskPipeline": _task_pipeline(),
    })

    assert first == second
    assert first is not None and len(first["profile_digest"]) == 64
    assert expansion["metadata"]["task_pipeline"] == first
    assert expansion["pipelines"][0]["barriers"]["stage_transition"] == "stage_barrier"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"unknown": True}, "unknown field"),
        ({"maxActiveTaskPipelines": 0}, "between 1 and 32"),
        (
            {"candidate": {**_task_pipeline()["candidate"], "integrationCapacity": 2}},
            "between 1 and 1",
        ),
        (
            {"candidate": {**_task_pipeline()["candidate"], "partialCandidateAutoShip": "allowed"}},
            "must be 'forbidden'",
        ),
        (
            {"candidate": {**_task_pipeline()["candidate"], "freezeEvent": "integration.queue.integrated"}},
            "must be non-empty and distinct",
        ),
    ],
)
def test_v4_profile_fails_closed(override: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _compile(_task_pipeline(**override))


def test_v4_flow_profile_requires_matching_kind_and_fanout() -> None:
    with pytest.raises(WorkflowProfileError, match="must be 'prd-flow"):
        expand_prd_flow({
            "flowProfile": "issue-flow-v4-task-pipeline",
            "taskPipeline": _task_pipeline(),
        })
    with pytest.raises(WorkflowProfileError, match="requires topology: fanout"):
        expand_prd_flow({
            "flowProfile": "prd-flow-v4-task-pipeline",
            "topology": "light",
            "taskPipeline": _task_pipeline(),
        })


def test_v4_metadata_round_trips_through_loader(tmp_path: Path) -> None:
    import yaml

    path = tmp_path / "zf.yaml"
    flow = {
        "apiVersion": "zaofu.dev/v1",
        "kind": "PrdFlow",
        "metadata": {"name": "v4"},
        "spec": {
            "flowProfile": "prd-flow-v4-task-pipeline",
            "topology": "fanout",
            "lanes": 2,
            "taskPipeline": _task_pipeline(),
        },
    }
    config = {
        "apiVersion": "zaofu.dev/v1",
        "kind": "ZfConfig",
        "metadata": {"name": "v4"},
        "spec": {"version": "1.0", "project": {"name": "v4"}},
    }
    path.write_text(
        yaml.safe_dump_all([flow, config], sort_keys=False),
        encoding="utf-8",
    )

    loaded = load_config(path)

    policy = loaded.workflow.flow_metadata["task_pipeline"]
    assert policy["profile_id"] == "prd-flow-v4-task-pipeline"
    assert policy["mode"] == "shadow"


def test_multi_flow_v4_pools_use_scoped_runtime_roles(tmp_path: Path) -> None:
    path = tmp_path / "zf.yaml"
    docs = [
        {"apiVersion": "zaofu.dev/v1", "kind": "IssueFlow",
         "metadata": {"name": "issue"}, "spec": {"lanes": 1}},
        {"apiVersion": "zaofu.dev/v1", "kind": "PrdFlow",
         "metadata": {"name": "prd"}, "spec": {
             "flowProfile": "prd-flow-v4-task-pipeline", "topology": "fanout",
             "lanes": 2, "taskPipeline": _task_pipeline(),
         }},
        {"apiVersion": "zaofu.dev/v1", "kind": "ZfConfig",
         "metadata": {"name": "multi"},
         "spec": {"version": "1.0", "project": {"name": "multi"}}},
    ]
    path.write_text(yaml.safe_dump_all(docs, sort_keys=False), encoding="utf-8")
    config = load_config(path)
    policy = config.workflow.flow_metadata_by_kind["prd"]["task_pipeline"]
    roles = {role.instance_id for role in config.roles}
    assert policy["pools"]["impl"]["role_instances"] == [
        "prd-dev-lane-0", "prd-dev-lane-1",
    ]
    assert policy["pools"]["verify"]["role_instances"] == [
        "prd-verify-lane-0", "prd-verify-lane-1",
    ]
    assert all(
        role in roles for pool in policy["pools"].values()
        for role in pool["role_instances"]
    )
    digest = policy["profile_digest"]
    body = {key: value for key, value in policy.items() if key != "profile_digest"}
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    assert digest == hashlib.sha256(encoded).hexdigest()
    assert load_config(path).workflow.flow_metadata_by_kind["prd"]["task_pipeline"] == policy

    legacy = expand_prd_flow({
        "flowProfile": "prd-flow-v4-task-pipeline", "topology": "fanout",
        "lanes": 2, "taskPipeline": _task_pipeline(),
    })["metadata"]["task_pipeline"]
    contexts = {"T-1": {"flow_kind": "prd", "profile_id": policy["profile_id"],
                         "profile_digest": legacy["profile_digest"]}}
    partition = task_pipeline_policy_partitions(config, contexts)[0]
    bound = partition["policy"]
    assert partition["profile_digest"] == legacy["profile_digest"]
    assert bound["pools"] == policy["pools"]
    assert bound["role_binding_resolution"]["configured_profile_digest"] == digest

    changed = dict(contexts["T-1"], profile_digest="0" * 64)
    with pytest.raises(ValueError, match="admitted profile_digest"):
        task_pipeline_policy_partitions(config, {"T-1": changed})
    policy["pools"]["impl"]["capacity"] = 1
    with pytest.raises(ValueError, match="admitted profile_digest"):
        task_pipeline_policy_partitions(config, contexts)
    policy["pools"]["impl"]["capacity"] = 2
    config.roles = [r for r in config.roles if r.instance_id != "prd-dev-lane-0"]
    with pytest.raises(ValueError, match="admitted profile_digest"):
        task_pipeline_policy_partitions(config, contexts)


def test_unknown_task_pipeline_field_surfaces_as_config_error(tmp_path: Path) -> None:
    path = tmp_path / "zf.yaml"
    path.write_text(
        """apiVersion: zaofu.dev/v1
kind: PrdFlow
metadata: {name: v4}
spec:
  flowProfile: prd-flow-v4-task-pipeline
  taskPipeline: {unexpected: true}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: v4}
spec: {version: '1.0', project: {name: v4}}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unknown field"):
        load_config(path)


def test_enabled_risk_review_requires_dedicated_skill_and_one_turn() -> None:
    value = _task_pipeline()
    value["pools"]["acceptance_review"] = {
        "capacity": 1,
        "role": "integration-reviewer",
        "skills": ["zf-integration-acceptance-review"],
    }
    value["integrationAdmission"] = {
        "default": "verify_admitted",
        "riskReview": {
            "enabled": True,
            "forRisks": ["high", "critical"],
            "maxTurns": 1,
        },
    }

    compiled = _compile(value)

    assert compiled["integration_admission"]["risk_review"]["max_turns"] == 1
    value["integrationAdmission"]["riskReview"]["maxTurns"] = 2
    with pytest.raises(ValueError, match="between 1 and 1"):
        _compile(value)


@pytest.mark.parametrize(
    ("filename", "profile_id", "lane_roles", "role_count", "backend"),
    [
        (
            "issue-task-pipeline-v4-canary.yaml",
            "issue-flow-v4-task-pipeline",
            {"fix-lane-0", "verify-lane-0"},
            7,
            "codex",
        ),
        (
            "issue-task-pipeline-v4-canary-claude.yaml",
            "issue-flow-v4-task-pipeline",
            {"fix-lane-0", "verify-lane-0"},
            7,
            "claude-code",
        ),
        (
            "prd-task-pipeline-v4-canary.yaml",
            "prd-flow-v4-task-pipeline",
            {
                "dev-lane-0",
                "dev-lane-1",
                "verify-lane-0",
                "verify-lane-1",
            },
            11,
            "codex",
        ),
        (
            "prd-task-pipeline-v4-canary-claude.yaml",
            "prd-flow-v4-task-pipeline",
            {
                "dev-lane-0",
                "dev-lane-1",
                "verify-lane-0",
                "verify-lane-1",
            },
            11,
            "claude-code",
        ),
        (
            "refactor-task-pipeline-v4-canary.yaml",
            "refactor-flow-v4-task-pipeline",
            {
                "dev-lane-0",
                "dev-lane-1",
                "verify-lane-0",
                "verify-lane-1",
            },
            13,
            "codex",
        ),
        (
            "refactor-task-pipeline-v4-canary-claude.yaml",
            "refactor-flow-v4-task-pipeline",
            {
                "dev-lane-0",
                "dev-lane-1",
                "verify-lane-0",
                "verify-lane-1",
            },
            13,
            "claude-code",
        ),
    ],
)
def test_v4_controller_examples_keep_orchestrator_resident_and_lanes_on_demand(
    tmp_path: Path,
    filename: str,
    profile_id: str,
    lane_roles: set[str],
    role_count: int,
    backend: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "examples" / "prod" / "controller" / filename

    config = load_config(source)
    policy = config.workflow.flow_metadata["task_pipeline"]
    roles = {role.name: role for role in config.roles}
    candidate_readers = [
        stage
        for stage in config.workflow.stages
        if stage.trigger == "candidate.ready"
    ]

    assert policy["profile_id"] == profile_id
    assert policy["mode"] == "shadow"
    assert policy["integration_admission"]["risk_review"]["enabled"] is False
    assert policy["candidate"]["partial_candidate_auto_ship"] == "forbidden"
    assert len(roles) == role_count
    assert roles["orchestrator"].lifecycle.mode == "resident"
    assert roles["orchestrator"].transport == "tmux"
    assert roles["orchestrator"].backend == backend
    if backend == "claude-code":
        assert config.orchestrator.max_turns == 80
    assert set(roles).issuperset(lane_roles)
    for role_name in lane_roles:
        assert roles[role_name].backend == backend
        lifecycle = roles[role_name].lifecycle
        assert lifecycle.mode == "on_demand"
        assert lifecycle.idle_seconds == 120
        assert lifecycle.preserve_session is True
        assert lifecycle.preserve_workdir is True
    assert config.runtime.git.auto_ship_on_judge_passed is False
    assert candidate_readers
    assert all(
        stage.assignment.strategy == "affinity_stage_slots"
        for stage in candidate_readers
    )

    documents = list(yaml.safe_load_all(source.read_text(encoding="utf-8")))
    catalog = documents[1]["metadata"]["zaofu"]["catalog"]
    assert catalog["preferred"] is False

    rendered_path = tmp_path / filename
    rendered_path.write_text(
        yaml.safe_dump(
            renderable_config_to_primitive(config),
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    rendered = load_config(rendered_path)
    assert rendered.workflow.flow_metadata["task_pipeline"] == policy


@pytest.mark.parametrize(
    "filename",
    [
        "issue-task-pipeline-v4-canary.yaml",
        "issue-task-pipeline-v4-canary-claude.yaml",
        "prd-task-pipeline-v4-canary.yaml",
        "prd-task-pipeline-v4-canary-claude.yaml",
        "refactor-task-pipeline-v4-canary.yaml",
        "refactor-task-pipeline-v4-canary-claude.yaml",
    ],
)
def test_v4_canary_accepts_blocking_mode(
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    source = (
        root
        / "examples"
        / "prod"
        / "controller"
        / filename
    )
    monkeypatch.setenv("ZF_TASK_PIPELINE_MODE", "blocking")
    monkeypatch.setenv("ZF_CLAUDE_CONTEXT_WINDOW_TOKENS", "1000000")

    config = load_config(source)

    assert config.workflow.flow_metadata["task_pipeline"]["mode"] == "blocking"


def test_v4_claude_canary_accepts_k3_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    source = (
        root
        / "examples"
        / "prod"
        / "controller"
        / "prd-task-pipeline-v4-canary-claude.yaml"
    )
    monkeypatch.setenv("ZF_TASK_PIPELINE_MODE", "blocking")
    monkeypatch.setenv("ZF_CLAUDE_CONTEXT_WINDOW_TOKENS", "1000000")

    config = load_config(source)

    candidate_readers = [
        stage
        for stage in config.workflow.stages
        if stage.trigger == "candidate.ready"
    ]
    assert candidate_readers
    assert all(
        stage.assignment.strategy == "static_index"
        for stage in candidate_readers
    )
    assert all(role.context_window_tokens == 1_000_000 for role in config.roles)
    assert config.runtime.run_manager.backend == "claude-code"
    assert (
        config.runtime.run_manager.resident_agent.model_reasoning_effort
        == "high"
    )


def test_v4_shadow_canaries_do_not_replace_v3_catalog_defaults() -> None:
    assert flow_id_for_intent("maintain", "codex") == "issue-fanout-v3-codex"
    assert flow_id_for_intent("build", "codex") == "prd-fanout-v3-codex"
    assert flow_id_for_intent("refactor", "codex") == "refactor-lane-v3-codex"
    assert flow_id_for_intent("maintain", "claude") == "issue-fanout-v3-claude"
    assert flow_id_for_intent("build", "claude") == "prd-fanout-v3-claude"
    assert flow_id_for_intent("refactor", "claude") == "refactor-lane-v3-claude"
