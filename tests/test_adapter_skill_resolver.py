"""Project adapter skill resolver tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

from zf.core.skills import adapter_resolver
from zf.core.skills.adapter_resolver import (
    AdapterSkillResolverInput,
    _canonical_zaofu_candidates,
    _load_adapter_policy,
    build_project_adapter_skill_plan,
)


def _write_skill(
    root: Path,
    name: str,
    description: str = "test skill",
    *,
    stages: tuple[str, ...] = ("verify",),
    roles: tuple[str, ...] = (),
    dependencies: tuple[str, ...] = (),
) -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"stages: [{', '.join(stages)}]\n"
        f"roles: [{', '.join(roles)}]\n"
        f"dependencies: [{', '.join(dependencies)}]\n"
        "---\n"
        "\n"
        "Use this skill in tests.\n",
        encoding="utf-8",
    )
    return path


def test_examples_env_reanchors_installed_adapter_assets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "zaofu-assets"
    policy = root / "examples" / "prod" / "controller" / "common" / "skill-adapter-policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        "apiVersion: zaofu.dev/v1\n"
        "kind: SkillAdapterPolicy\n"
        "metadata: {name: test/v1}\n"
        "spec: {profile_source: profiles.yaml}\n",
        encoding="utf-8",
    )
    skill = _write_skill(root / "skills", "packaged-skill")
    monkeypatch.setenv("ZF_EXAMPLES_DIR", str(root / "examples"))

    loaded = _load_adapter_policy(None)
    candidates = _canonical_zaofu_candidates("packaged-skill", seen=[])

    assert loaded["source_path"] == policy
    assert [candidate.path for candidate in candidates] == [skill]


def test_pep610_source_reanchors_noneditable_adapter_assets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "frozen-source"
    policy = (
        root
        / "examples"
        / "prod"
        / "controller"
        / "common"
        / "skill-adapter-policy.yaml"
    )
    policy.parent.mkdir(parents=True)
    policy.write_text(
        "apiVersion: zaofu.dev/v1\n"
        "kind: SkillAdapterPolicy\n"
        "metadata: {name: test/v1}\n"
        "spec: {profile_source: profiles.yaml}\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ZF_EXAMPLES_DIR", raising=False)
    monkeypatch.setattr(
        adapter_resolver,
        "__file__",
        str(tmp_path / "venv" / "site-packages" / "zf" / "core" / "skills" / "adapter_resolver.py"),
    )
    monkeypatch.setattr(
        adapter_resolver,
        "installed_local_source_root",
        lambda: root,
    )

    assert _load_adapter_policy(None)["source_path"] == policy


def test_refactor_adapter_plan_discovers_project_skills_and_hashes(tmp_path: Path) -> None:
    skill_path = _write_skill(
        tmp_path / "skills",
        "cangjie-hermes-parity-gate",
        "Cangjie parity gate",
    )

    plan = build_project_adapter_skill_plan(AdapterSkillResolverInput(
        kind="refactor",
        project_root=tmp_path,
        project_id="cangjie",
        strictness="full-parity",
        parity_scope=("provider", "webui"),
    ))

    assert plan["schema_version"] == "skill.adapter.plan.v2"
    # 2026-07-08 controller 同步:zf-dynamic-artifact-gate 从 .codex 本地
    # 幽灵入驻 canonical 后,recommended 全解析——旧断言把该缺失导致的
    # WARN 钉为预期,现在名实一致应为 PASS。
    assert plan["status"] == "PASS"
    assert plan["missing_required_skills"] == []
    assert plan["missing_recommended_skills"] == []
    project_names = {
        item["name"] for item in plan["discovered_project_skills"]
    }
    assert "cangjie-hermes-parity-gate" in project_names
    loaded = {item["name"]: item for item in plan["loaded_skills"]}
    assert loaded["cangjie-hermes-parity-gate"]["sha256"] == hashlib.sha256(
        skill_path.read_bytes()
    ).hexdigest()
    verify_bundle = plan["roleSkillBundles"]["verify"]
    assert "cangjie-hermes-parity-gate" in verify_bundle
    assert "zf-provider-contract-parity" in verify_bundle
    assert "zf-webui-tui-parity" in verify_bundle
    assert "zf-verify-gap-producer-contract" not in verify_bundle
    assert "zf-verify-gap-producer-contract" not in plan["roleSkillBundles"]["refactor-verify-bridge"]
    assert "zf-goal-closure-replan-contract" in plan["roleSkillBundles"]["refactor-verify-bridge"]
    assert "zf-verify-gap-producer-contract" in loaded
    assert "zf-verify-gap-producer-contract" not in loaded[
        "zf-yoke-test-evaluator-role-context"
    ]["dependencies"]
    assert loaded["zf-goal-closure-replan-contract"]["dependencies"] == [
        "zf-verify-gap-producer-contract"
    ]


def test_common_workflow_adaptation_skill_is_bound_by_kind(tmp_path: Path) -> None:
    issue_plan = build_project_adapter_skill_plan(AdapterSkillResolverInput(
        kind="issue",
        project_root=tmp_path,
        project_id="issue-demo",
    ))
    prd_plan = build_project_adapter_skill_plan(AdapterSkillResolverInput(
        kind="prd",
        project_root=tmp_path,
        project_id="prd-demo",
    ))

    assert "zf-workflow-adaptation-boundary" in {
        item["name"] for item in issue_plan["loaded_skills"]
    }
    assert "zf-project-adapter-matrix-enrichment" in {
        item["name"] for item in issue_plan["loaded_skills"]
    }
    assert "zf-verify-gap-producer-contract" in {
        item["name"] for item in issue_plan["loaded_skills"]
    }
    assert "zf-workflow-adaptation-boundary" in issue_plan["roleSkillBundles"]["issue-triage"]
    assert "zf-project-adapter-matrix-enrichment" in issue_plan["roleSkillBundles"]["issue-triage"]
    assert "zf-workflow-adaptation-boundary" in issue_plan["roleSkillBundles"]["verify"]
    assert "zf-project-adapter-matrix-enrichment" in issue_plan["roleSkillBundles"]["verify"]
    assert "zf-verify-gap-producer-contract" not in issue_plan["roleSkillBundles"]["verify"]
    assert "zf-goal-closure-replan-contract" in issue_plan["roleSkillBundles"]["discovery"]
    assert "zf-workflow-adaptation-boundary" in prd_plan["roleSkillBundles"]["scan"]
    assert "zf-project-adapter-matrix-enrichment" in prd_plan["roleSkillBundles"]["scan"]
    assert "zf-workflow-adaptation-boundary" in prd_plan["roleSkillBundles"]["planner"]
    assert "zf-project-adapter-matrix-enrichment" in prd_plan["roleSkillBundles"]["planner"]
    assert "zf-verify-gap-producer-contract" not in prd_plan["roleSkillBundles"]["verify"]
    assert "zf-goal-closure-replan-contract" in prd_plan["roleSkillBundles"]["discovery"]


def test_refactor_default_parity_scope_is_project_neutral(tmp_path: Path) -> None:
    plan = build_project_adapter_skill_plan(AdapterSkillResolverInput(
        kind="refactor",
        project_root=tmp_path,
        project_id="demo",
    ))

    assert plan["parity_scope"] == ["core", "cli", "api", "web", "runtime"]


def test_full_parity_stops_without_project_adapter_skill(tmp_path: Path) -> None:
    plan = build_project_adapter_skill_plan(AdapterSkillResolverInput(
        kind="refactor",
        project_root=tmp_path,
        project_id="cangjie",
        strictness="full-parity",
    ))

    assert plan["status"] == "STOP"
    assert any(
        item["kind"] == "project_adapter_skill_missing"
        and item["severity"] == "STOP"
        for item in plan["diagnostics"]
    )
    assert {
        item["skill"] for item in plan["proposed_skill_backlogs"]
    } >= {
        "cangjie-scan-inventory",
        "cangjie-parity-contract",
        "cangjie-verify-rescan",
    }


def test_standard_strictness_warns_without_project_adapter_skill(tmp_path: Path) -> None:
    plan = build_project_adapter_skill_plan(AdapterSkillResolverInput(
        kind="refactor",
        project_root=tmp_path,
        project_id="cangjie",
        strictness="standard",
    ))

    assert plan["status"] == "WARN"
    assert any(
        item["kind"] == "project_adapter_skill_missing"
        and item["severity"] == "WARN"
        for item in plan["diagnostics"]
    )


def test_role_context_wrappers_and_thin_judge_are_bundled(tmp_path: Path) -> None:
    """2026-07-08:yoke 角色边界 wrapper 进 required 集与三流 stage bundles
    (方法论技能经 frontmatter dependencies 闭包物化,不在 bundle 列名)。"""
    for kind, impl_bundle, judge_bundle in (
        ("prd", "impl", "judge-prd"),
        ("issue", "fix", "judge-issue"),
        ("refactor", "impl", "judge-refactor"),
    ):
        plan = build_project_adapter_skill_plan(AdapterSkillResolverInput(
            kind=kind,
            project_root=tmp_path,
            project_id="demo",
        ))
        for wrapper in (
            "zf-yoke-dev-worker-role-context",
            "zf-yoke-test-evaluator-role-context",
        ):
            assert wrapper in plan["required_skills"], (kind, wrapper)
            assert wrapper not in plan["missing_required_skills"], (kind, wrapper)
        bundles = plan["roleSkillBundles"]
        assert "zf-yoke-dev-worker-role-context" in bundles[impl_bundle], kind
        assert "zf-yoke-test-evaluator-role-context" in bundles["verify"], kind
        assert bundles[judge_bundle] == ["zf-goal-closure-judge-contract"], kind
        assert "zf-yoke-quality-gate-role-context" not in plan["required_skills"]


def test_project_goal_acceptance_overlay_is_bound_only_to_thin_judge(
    tmp_path: Path,
) -> None:
    _write_skill(
        tmp_path / "skills",
        "demo-goal-acceptance",
        "Project-specific top-level Goal acceptance semantics.",
        stages=("judge",),
    )

    plan = build_project_adapter_skill_plan(AdapterSkillResolverInput(
        kind="prd",
        project_root=tmp_path,
        project_id="demo",
    ))

    assert "demo-goal-acceptance" in plan["roleSkillBundles"]["judge-prd"]
    assert "demo-goal-acceptance" not in plan["roleSkillBundles"]["verify"]


def test_adapter_policy_and_profile_own_skill_names(tmp_path: Path) -> None:
    _write_skill(
        tmp_path / "skills",
        "custom-wrapper",
        stages=("fix",),
        dependencies=("custom-contract",),
    )
    _write_skill(
        tmp_path / "skills",
        "custom-contract",
        stages=("fix",),
    )
    profiles = tmp_path / "profiles.yaml"
    profiles.write_text(
        "apiVersion: zaofu.dev/v1\n"
        "kind: ConfigProfile\n"
        "metadata: {name: custom/v1}\n"
        "spec:\n"
        "  flow_defaults:\n"
        "    issue:\n"
        "      roleSkillBundles:\n"
        "        fix: [custom-wrapper]\n",
        encoding="utf-8",
    )
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "apiVersion: zaofu.dev/v1\n"
        "kind: SkillAdapterPolicy\n"
        "metadata: {name: custom/v1}\n"
        "spec:\n"
        "  profile_source: profiles.yaml\n"
        "  recommended_skills: {common: [], flows: {issue: []}}\n"
        "  parity: {default_scopes: {}, skills: {}}\n"
        "  project_adapter:\n"
        "    strictness_stop_values: [strict]\n"
        "    backlog_suffixes: {}\n",
        encoding="utf-8",
    )

    plan = build_project_adapter_skill_plan(AdapterSkillResolverInput(
        kind="issue",
        project_root=tmp_path,
        policy_path=policy,
    ))

    assert plan["roleSkillBundles"] == {"fix": ["custom-wrapper"]}
    assert plan["required_skills"] == ["custom-wrapper", "custom-contract"]
    assert plan["missing_required_skills"] == []
    assert {
        item["name"]: item["requirement"] for item in plan["loaded_skills"]
    } == {
        "custom-wrapper": "required",
        "custom-contract": "required",
    }
