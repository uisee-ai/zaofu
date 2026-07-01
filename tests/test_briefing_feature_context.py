"""α-5 + α-6: feature-context briefing 注入 + judge completion audit.

Per docs/design/36-zero-touch-long-horizon-roadmap.md §7.11 (revised) +
backlogs/2026-05-17-1447-zero-touch-alpha-5-6-feature-context-and-completion-audit.md

α-5: codex 借鉴的 "Keep the full feature scope intact" 纪律必须出现在
**每个 worker role** 的 task briefing 顶部 (不仅 orchestrator briefing)。
zaofu 现在的 generate_task_briefing 完全不引用 Feature → dev 修 typo
就 dev.build.done，agent 链条往局部最小解倒退。

α-6: judge role briefing 必含 codex 风格的 completion audit 段，强制
agent verify requirement-by-requirement，不允许 indirect evidence 当
proof。
"""

from __future__ import annotations

import pytest

from zf.core.config.schema import RoleConfig, ZfConfig, ProjectConfig
from zf.core.feature.schema import Feature
from zf.core.task.schema import Task, TaskContract


# ─── fixtures ────────────────────────────────────────────────────────────


def _make_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t", state_dir=".zf"),
        roles=[],
    )


def _make_dev_role() -> RoleConfig:
    return RoleConfig(
        name="dev",
        backend="mock",
        role_kind="writer",
        publishes=["dev.build.done", "dev.blocked"],
    )


def _make_judge_role() -> RoleConfig:
    return RoleConfig(
        name="judge",
        backend="mock",
        role_kind="reader",
        publishes=["judge.passed", "judge.failed"],
    )


def _make_arch_role() -> RoleConfig:
    return RoleConfig(
        name="arch",
        backend="mock",
        role_kind="reader",
        publishes=["arch.proposal.done"],
    )


def _make_review_role() -> RoleConfig:
    return RoleConfig(
        name="review",
        backend="mock",
        role_kind="reader",
        publishes=["review.approved", "review.rejected"],
    )


def _make_test_role() -> RoleConfig:
    return RoleConfig(
        name="test",
        backend="mock",
        role_kind="reader",
        publishes=["test.passed", "test.failed"],
    )


def _make_critic_role() -> RoleConfig:
    return RoleConfig(
        name="critic",
        backend="mock",
        role_kind="reader",
        publishes=["design.critique.done"],
    )


def _make_orchestrator_role() -> RoleConfig:
    return RoleConfig(
        name="orchestrator",
        backend="mock",
        role_kind="reader",
        publishes=["orchestrator.decision"],
    )


def _make_task(*, feature_id: str = "F-deadbeef") -> Task:
    return Task(
        id="TASK-T1",
        title="implement persistence layer",
        contract=TaskContract(
            feature_id=feature_id,
            behavior="add JSONL session storage",
            verification="pytest tests/test_session_storage.py",
            scope=["packages/core/src/session-storage.ts"],
        ),
    )


def _make_feature() -> Feature:
    return Feature(
        id="F-deadbeef",
        title="JSONL session persistence",
        description=(
            "Make AgentSession transcripts persist across process boundaries "
            "via append-only JSONL files. Sessions can be hydrated on restart "
            "and continue accumulating turns."
        ),
        user_message=(
            "用户原始输入：让 cangjie agent 支持跨进程会话恢复，每个 session 写 JSONL 文件，"
            "重启后能从文件恢复完整 transcript。"
        ),
        status="active",
    )


# ─── α-5: feature context injected at TOP of every worker role briefing ──


def test_feature_context_appears_before_task_section_for_dev():
    from zf.runtime.injection import generate_task_briefing

    config = _make_config()
    role = _make_dev_role()
    task = _make_task()
    feature = _make_feature()

    briefing = generate_task_briefing(config, role, task, feature=feature)

    feature_pos = briefing.find("Feature Context")
    task_pos = briefing.find("## Task Assigned")
    assert feature_pos != -1, "feature context section missing"
    assert task_pos != -1, "task section missing"
    assert feature_pos < task_pos, (
        "feature context must appear BEFORE task section (top of briefing)"
    )


@pytest.mark.parametrize(
    "make_role",
    [
        _make_dev_role,
        _make_arch_role,
        _make_critic_role,
        _make_review_role,
        _make_test_role,
        _make_judge_role,
    ],
)
def test_feature_context_present_for_every_worker_role(make_role):
    from zf.runtime.injection import generate_task_briefing

    config = _make_config()
    role = make_role()
    task = _make_task()
    feature = _make_feature()

    briefing = generate_task_briefing(config, role, task, feature=feature)

    assert "Feature Context" in briefing
    assert feature.id in briefing
    assert feature.title in briefing
    # description and user_message are operator-authored content; must be present
    assert "persist across process boundaries" in briefing
    assert "用户原始输入" in briefing or feature.user_message[:30] in briefing


def test_feature_context_contains_codex_discipline_block():
    """codex continuation.md 借鉴的纪律必须出现在 briefing 顶部，强制
    agent 不要缩水 objective。"""
    from zf.runtime.injection import generate_task_briefing

    config = _make_config()
    role = _make_dev_role()
    task = _make_task()
    feature = _make_feature()

    briefing = generate_task_briefing(config, role, task, feature=feature)

    # Discipline keywords from codex/codex-rs/core/templates/goals/continuation.md
    assert "Keep the full feature scope intact" in briefing or \
        "保持整个 feature scope 不缩水" in briefing
    assert "narrower" in briefing or "更小" in briefing
    assert "end state" in briefing or "最终状态" in briefing


def test_feature_context_omitted_when_feature_is_none():
    """Backward compat: callers that don't yet pass feature get the
    existing briefing shape (no Feature Context section)."""
    from zf.runtime.injection import generate_task_briefing

    config = _make_config()
    role = _make_dev_role()
    task = _make_task()

    briefing = generate_task_briefing(config, role, task, feature=None)

    assert "Feature Context" not in briefing
    # Standard task section still present
    assert "## Task Assigned" in briefing


def test_feature_context_omitted_when_task_has_no_feature_id():
    """When task has no feature_id (orphan tasks), feature lookup yields
    None and the briefing is the original shape."""
    from zf.runtime.injection import generate_task_briefing

    config = _make_config()
    role = _make_dev_role()
    orphan_task = Task(id="TASK-X", title="orphan", contract=TaskContract())

    briefing = generate_task_briefing(config, role, orphan_task, feature=None)

    assert "Feature Context" not in briefing


def test_feature_context_with_minimal_feature_only_has_id_and_title():
    """Feature with no description / user_message renders the section
    but without empty 'description: ' lines."""
    from zf.runtime.injection import generate_task_briefing

    bare_feature = Feature(id="F-12345678", title="minimal", description="", user_message="")
    config = _make_config()
    role = _make_dev_role()
    task = _make_task(feature_id="F-12345678")

    briefing = generate_task_briefing(config, role, task, feature=bare_feature)

    assert "Feature Context" in briefing
    assert "F-12345678" in briefing
    assert "minimal" in briefing
    # No empty description/user_message renders
    assert "Description:\n\n" not in briefing
    assert "User message:\n\n" not in briefing


# ─── α-6: judge completion audit ─────────────────────────────────────────


def test_judge_briefing_contains_completion_audit_section():
    """judge role briefing must contain the codex-style completion audit
    discipline block, which forces requirement-by-requirement verification."""
    from zf.runtime.injection import generate_task_briefing

    config = _make_config()
    role = _make_judge_role()
    task = _make_task()
    feature = _make_feature()

    briefing = generate_task_briefing(config, role, task, feature=feature)

    assert "Completion Audit" in briefing or "完成审计" in briefing
    # Key audit phrases from codex continuation.md
    assert "requirement-by-requirement" in briefing or "逐条要求" in briefing
    assert (
        "must prove completion" in briefing
        or "必须证明完成" in briefing
    )
    # Must instruct judge to emit judge.failed when evidence is weak
    assert "judge.failed" in briefing


def test_non_judge_briefing_omits_completion_audit():
    """Only judge role gets the audit section. dev / test / review etc.
    have their own discipline; don't dilute with judge-specific rules."""
    from zf.runtime.injection import generate_task_briefing

    config = _make_config()
    task = _make_task()
    feature = _make_feature()

    for role in [_make_dev_role(), _make_review_role(), _make_test_role(), _make_arch_role()]:
        briefing = generate_task_briefing(config, role, task, feature=feature)
        assert "Completion Audit" not in briefing, (
            f"{role.name} briefing should not contain Completion Audit "
            f"(only judge does)"
        )


def test_judge_audit_present_even_without_feature():
    """judge completion audit is a discipline applied by role identity,
    not by feature presence. judge without feature still gets the audit."""
    from zf.runtime.injection import generate_task_briefing

    config = _make_config()
    role = _make_judge_role()
    task = _make_task()

    briefing = generate_task_briefing(config, role, task, feature=None)

    assert "Completion Audit" in briefing


# ─── wire-up: caller (orchestrator_dispatch) must pass feature ───────────


def test_wire_up_dispatch_passes_feature_to_generate_task_briefing():
    """α-5 wire-up grep: orchestrator_dispatch.py must look up the
    feature for the task and pass it to generate_task_briefing.

    Without this wire-up, generate_task_briefing has the new feature=
    param but callers don't use it → library-without-callers anti-pattern.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src/zf/runtime/orchestrator_dispatch.py"
    text = src.read_text(encoding="utf-8")
    # Must lookup the feature AND pass it through.
    assert "FeatureStore" in text or "feature_store" in text, (
        "α-5 wire-up missing: orchestrator_dispatch.py does not load Feature"
    )
    assert "feature=" in text or "feature," in text, (
        "α-5 wire-up missing: generate_task_briefing call does not pass feature"
    )
