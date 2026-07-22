"""ConfigProfile / RoleSet composition tests."""

from __future__ import annotations

import pytest

from zf.core.config.loader import ConfigError, load_config


def test_config_profile_uses_merges_before_project_override(tmp_path):
    text = """\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: prod-runtime/v1}
spec:
  workflow:
    harness_profile: strict
  runtime:
    run_manager:
      backend: codex
      resident_agent:
        enabled: true
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  uses: [prod-runtime/v1]
  version: "1.0"
  project: {name: demo}
  workflow:
    harness_profile: baseline
"""
    path = tmp_path / "zf.yaml"
    path.write_text(text)
    cfg = load_config(path)
    assert cfg.workflow.harness_profile == "baseline"
    assert cfg.runtime.run_manager.backend == "codex"
    assert cfg.runtime.run_manager.resident_agent.enabled is True
    assert getattr(cfg, "config_sources")[0]["name"] == "prod-runtime/v1"


def test_unknown_config_profile_fails_closed(tmp_path):
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  uses: [missing/v1]
  version: "1.0"
  project: {name: demo}
""")
    with pytest.raises(ConfigError, match="unknown profile"):
        load_config(path)


def test_conflicting_profiles_fail_closed(tmp_path):
    text = """\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: a/v1}
spec: {workflow: {harness_profile: baseline}}
---
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: b/v1}
spec: {workflow: {harness_profile: strict}}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  uses: [a/v1, b/v1]
  project: {name: demo}
"""
    path = tmp_path / "zf.yaml"
    path.write_text(text)
    with pytest.raises(ConfigError, match="conflicting profile value"):
        load_config(path)


def test_roleset_uses_generates_lane_roles(tmp_path):
    text = """\
apiVersion: zaofu.dev/v1
kind: RoleSet
metadata: {name: codex-lanes/v1}
spec:
  backend: codex
  lanes: 2
  stages:
    impl:
      role_pattern: dev-lane-{lane}
      skills: [implementation]
    verify:
      role_pattern: verify-lane-{lane}
      skills: [verification]
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  uses: [codex-lanes/v1]
  version: "1.0"
  project: {name: demo}
"""
    path = tmp_path / "zf.yaml"
    path.write_text(text)
    cfg = load_config(path)
    names = {role.name for role in cfg.roles}
    assert {"dev-lane-0", "dev-lane-1", "verify-lane-0", "verify-lane-1"} <= names
    dev0 = next(role for role in cfg.roles if role.name == "dev-lane-0")
    verify0 = next(role for role in cfg.roles if role.name == "verify-lane-0")
    assert dev0.role_kind == "writer"
    assert verify0.role_kind == "reader"
    assert dev0.backend == "codex"


def test_config_profile_can_include_profiles_and_rolesets(tmp_path):
    text = """\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: runtime/v1}
spec:
  runtime:
    run_manager:
      backend: claude-code
      resident_agent: {enabled: true, session_mode: dedicated}
---
apiVersion: zaofu.dev/v1
kind: RoleSet
metadata: {name: lanes/v1}
spec:
  backend: claude-code
  lanes: 1
  stages:
    impl:
      role_pattern: dev-lane-{lane}
    verify:
      role_pattern: verify-lane-{lane}
---
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: bundle/v1}
spec:
  uses: [runtime/v1, lanes/v1]
  workflow:
    plan_approval: false
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  uses: [bundle/v1]
  version: "1.0"
  project: {name: demo}
"""
    path = tmp_path / "zf.yaml"
    path.write_text(text)

    cfg = load_config(path)

    assert cfg.runtime.run_manager.backend == "claude-code"
    assert cfg.runtime.run_manager.resident_agent.enabled is True
    assert cfg.workflow.plan_approval_enabled is False
    assert {role.name for role in cfg.roles} == {"dev-lane-0", "verify-lane-0"}
    assert [source["name"] for source in getattr(cfg, "config_sources")] == [
        "runtime/v1",
        "lanes/v1",
        "bundle/v1",
    ]


def test_config_profile_include_cycle_fails_closed(tmp_path):
    text = """\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: a/v1}
spec: {uses: [b/v1]}
---
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: b/v1}
spec: {uses: [a/v1]}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  uses: [a/v1]
  project: {name: demo}
"""
    path = tmp_path / "zf.yaml"
    path.write_text(text)

    with pytest.raises(ConfigError, match="uses cycle"):
        load_config(path)


def test_config_profile_unknown_include_fails_closed(tmp_path):
    text = """\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: bundle/v1}
spec: {uses: [missing/v1]}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  uses: [bundle/v1]
  project: {name: demo}
"""
    path = tmp_path / "zf.yaml"
    path.write_text(text)

    with pytest.raises(ConfigError, match="unknown profile"):
        load_config(path)


def test_external_profile_sources_merge_and_record_sources(tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "common.yaml").write_text("""\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: prod-runtime/v1}
spec:
  runtime:
    run_manager:
      backend: codex
      resident_agent: {enabled: true}
---
apiVersion: zaofu.dev/v1
kind: RoleSet
metadata: {name: codex-lanes/v1}
spec:
  backend: codex
  lanes: 1
  stages:
    impl:
      role_pattern: dev-lane-{lane}
      skills: [implementation]
---
apiVersion: zaofu.dev/v1
kind: SchemaProfile
metadata: {name: local-schema/v1}
spec:
  events:
    local.done: {required: [task_id, status]}
""")
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  profile_sources: [profiles/*.yaml]
  uses: [prod-runtime/v1, codex-lanes/v1]
  version: "1.0"
  project: {name: demo}
  workflow:
    dag:
      schema_profile: local-schema/v1
""")

    cfg = load_config(path)

    assert cfg.runtime.run_manager.backend == "codex"
    assert cfg.runtime.run_manager.resident_agent.enabled is True
    assert {role.name for role in cfg.roles} == {"dev-lane-0"}
    assert "local.done" in cfg.workflow.dag.event_schemas
    sources = getattr(cfg, "config_sources")
    assert any(source["kind"] == "ProfileSource" for source in sources)
    profile_source = next(source for source in sources if source["kind"] == "ProfileSource")
    assert profile_source["path"].endswith("profiles/common.yaml")
    assert profile_source["sha256"]


def test_missing_external_profile_source_fails_closed(tmp_path):
    path = tmp_path / "zf.yaml"
    path.write_text("""\
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  profile_sources: [profiles/missing.yaml]
  uses: [prod-runtime/v1]
  version: "1.0"
  project: {name: demo}
""")

    with pytest.raises(ConfigError, match="did not match any files"):
        load_config(path)


def test_config_profile_merges_verification_and_skills_strict(tmp_path):
    """2026-07-08 LB-4:verification 执法档与 runtime.skills.strict 经
    uses: 深合并进项目配置(prod controller 预设的接线路径)。"""
    text = """\
apiVersion: zaofu.dev/v1
kind: ConfigProfile
metadata: {name: enforce/v1}
spec:
  runtime:
    skills:
      strict: true
  verification:
    event_schema:
      mode: blocking
    report_evidence_gate: fail_closed
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  uses: [enforce/v1]
  version: "1.0"
  project: {name: demo}
"""
    path = tmp_path / "zf.yaml"
    path.write_text(text)
    cfg = load_config(path)
    assert cfg.runtime.skills.strict is True
    assert cfg.verification.event_schema.mode == "blocking"
    assert cfg.verification.report_evidence_gate == "fail_closed"


def test_prod_controller_profiles_wire_yoke_and_enforcement():
    """内容钉:examples/prod/controller/common/profiles.yaml 的两个 prod
    预设必须携带 yoke wrapper bundles + strict + blocking + fail_closed
    (2026-07-08 skills+yoke 评估第一、二批;漂移即红)。"""
    import yaml
    from pathlib import Path as _Path

    profiles_path = (
        _Path(__file__).resolve().parents[1]
        / "examples" / "prod" / "controller" / "common" / "profiles.yaml"
    )
    docs = list(yaml.safe_load_all(profiles_path.read_text(encoding="utf-8")))
    by_name = {
        doc["metadata"]["name"]: doc["spec"]
        for doc in docs
        if isinstance(doc, dict) and doc.get("kind") == "ConfigProfile"
    }

    prod = by_name["prod-runtime/v1"]
    prd_bundles = prod["flow_defaults"]["prd"]["roleSkillBundles"]
    issue_bundles = prod["flow_defaults"]["issue"]["roleSkillBundles"]
    assert "zf-yoke-dev-worker-role-context" in prd_bundles["impl"]
    assert "zf-yoke-test-evaluator-role-context" in prd_bundles["verify"]
    assert "zf-goal-closure-replan-contract" in prd_bundles["discovery"]
    assert "zf-yoke-planner-role-context" in prd_bundles["planner"]
    assert prd_bundles["judge-prd"] == ["zf-goal-closure-judge-contract"]
    assert "zf-yoke-dev-worker-role-context" in issue_bundles["fix"]
    assert "zf-yoke-test-evaluator-role-context" in issue_bundles["verify"]
    assert "zf-goal-closure-replan-contract" in issue_bundles["discovery"]
    assert "zf-yoke-planner-role-context" in issue_bundles["issue-triage"]
    assert issue_bundles["judge-issue"] == ["zf-goal-closure-judge-contract"]

    refactor = by_name["refactor-controller-runtime/v3"]
    ref_bundles = refactor["flow_defaults"]["refactor"]["roleSkillBundles"]
    assert "zf-yoke-dev-worker-role-context" in ref_bundles["impl"]
    assert "zf-yoke-test-evaluator-role-context" in ref_bundles["verify"]
    assert "zf-verify-rescan-replan" not in ref_bundles["verify"]
    assert "zf-goal-closure-replan-contract" in ref_bundles["refactor-verify-bridge"]
    assert "zf-goal-closure-replan-contract" in ref_bundles["module-parity-scan"]
    assert "zf-yoke-planner-role-context" in ref_bundles["refactor-plan-synth"]
    assert ref_bundles["judge-refactor"] == ["zf-goal-closure-judge-contract"]

    # 139: common 声明角色能力 wrapper，底层 TDD 由 dependencies 闭包物化。
    # Scan/Verify 是独立评估者，不应收到开发型 TDD 方法。
    for bundles, impl_role in (
        (prd_bundles, "impl"),
        (issue_bundles, "fix"),
        (ref_bundles, "impl"),
    ):
        assert "test-driven-development" not in bundles[impl_role]
        assert "tdd-evidence" not in bundles[impl_role]
        assert "test-driven-development" not in bundles["verify"]
        assert "tdd-evidence" not in bundles["verify"]
        assert "zf-harness-done-contract" not in bundles[impl_role]
        for contract in (
            "zf-harness-verification-checklist",
            "zf-mechanical-claim-verifier",
            "zf-verify-gap-producer-contract",
            "zf-goal-closure-replan-contract",
        ):
            assert contract not in bundles["verify"]

    wrapper = (
        _Path(__file__).resolve().parents[1]
        / "skills" / "zf-yoke-dev-worker-role-context" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "dependencies:" in wrapper
    assert "tdd-evidence" in wrapper
    assert "incremental-delivery" in wrapper

    for spec in (prod, refactor):
        assert spec["goal"]["enabled"] is True
        assert spec["goal"]["max_rescans"] == 5
        assert spec["goal"]["idle_progress_ticks"] == 3
        assert spec["goal"]["rework_fingerprint"] is True
        assert spec["goal"]["quiescent_after_escalate"] is True
        assert spec["goal"]["micro_loop"] is False
        assert spec["workflow"]["strict_triggers"]["rework_attempts_gte"] == 2
        assert spec["runtime"]["skills"]["strict"] is True
        assert spec["runtime"]["feishu_inbound"] == {
            "enabled": "${ZF_FEISHU_INBOUND_ENABLED:-false}",
            "mode": "bridge",
            "require_routing": True,
        }
        assert spec["verification"]["event_schema"]["mode"] == "blocking"
        assert spec["verification"]["report_evidence_gate"] == "fail_closed"


def test_refactor_controller_uses_project_neutral_dynamic_skill_overlay():
    """Static examples keep project facts out of the reusable controller."""
    import yaml
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1]
    expected_scope = ["core", "cli", "api", "web", "runtime"]
    for name in ("refactor-lane-v3.yaml", "refactor-lane-v3-claude.yaml"):
        path = root / "examples" / "prod" / "controller" / name
        flow = next(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        assert flow["spec"]["parityScope"] == expected_scope, name
        assert "roleSkillBundles" not in flow["spec"], name


def test_controller_entries_use_goal_scoped_delivery():
    """8 个 controller 入口统一走 Goal claim 的 scoped delivery。"""
    from pathlib import Path as _Path

    from zf.core.config.loader import load_config as _load

    controller = _Path(__file__).resolve().parents[1] / "examples" / "prod" / "controller"
    yamls = sorted(controller.glob("*-v3*.yaml"))
    assert len(yamls) >= 8, [p.name for p in yamls]
    for path in yamls:
        cfg = _load(path)
        assert cfg.goal.enabled is True, path.name
        assert cfg.goal.rework_fingerprint is True, path.name
        assert cfg.goal.quiescent_after_escalate is True, path.name
        assert cfg.workflow.strict_triggers.rework_attempts_gte == 2, path.name
        assert cfg.workflow.flow_metadata["delivery_policy"] == "ship_candidate"
        # Legacy active runs remain resumable during the authority cutover.
        assert cfg.runtime.git.auto_ship_on_judge_passed is True, path.name
