"""Wave 5 batch tests — ROLECTX-001 / FINISH-001 / RESEARCH-001 /
SPEC-PROMOTE-001.

Each kernel module is small + standalone; bundling the test files
keeps the test directory readable. Test classes group by sprint.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.known_types import KNOWN_EVENT_TYPES
from zf.runtime.dirty_tree_gate import (
    DirtyTreeClassification,
    classify_dirty_tree,
)
from zf.runtime.research_artifact import (
    ResearchArtifact,
    ResearchEvidence,
    render_artifact_md,
    validate_artifact,
    write_research_artifact,
)
from zf.runtime.role_context import (
    RoleContext,
    infer_role_context,
    known_role_contexts,
)
from zf.runtime.spec_promote import (
    SPEC_PROMOTE_COMPLETED,
    SPEC_PROMOTE_SKIPPED,
    SpecPromoteDecision,
    decide_promotion,
)


# ---------------------------------------------------------------------------
# ROLECTX-001
# ---------------------------------------------------------------------------


class TestRoleContext:
    def test_known_contexts_lists_seven_values(self) -> None:
        assert sorted(known_role_contexts()) == sorted([
            "coordinator", "planner", "worker", "reviewer",
            "verifier", "judge", "synthesizer",
        ])

    def test_infer_dev_returns_worker(self) -> None:
        assert infer_role_context(role_name="dev") is RoleContext.WORKER

    def test_infer_orchestrator_returns_coordinator(self) -> None:
        assert infer_role_context(role_name="orchestrator") \
            is RoleContext.COORDINATOR

    def test_infer_arch_returns_planner(self) -> None:
        assert infer_role_context(role_name="arch") is RoleContext.PLANNER

    def test_infer_review_returns_reviewer(self) -> None:
        assert infer_role_context(role_name="review") is RoleContext.REVIEWER

    def test_infer_test_returns_verifier(self) -> None:
        assert infer_role_context(role_name="test") is RoleContext.VERIFIER

    def test_infer_judge_returns_judge(self) -> None:
        assert infer_role_context(role_name="judge") is RoleContext.JUDGE

    def test_fanout_override_to_worker(self) -> None:
        """Fanout child of any known role is always WORKER."""
        assert infer_role_context(
            role_name="review", fanout_role=True,
        ) is RoleContext.WORKER

    def test_role_kind_writer_falls_back_to_worker(self) -> None:
        assert infer_role_context(
            role_name="custom-role", role_kind="writer",
        ) is RoleContext.WORKER

    def test_role_kind_reader_falls_back_to_reviewer(self) -> None:
        assert infer_role_context(
            role_name="custom-role", role_kind="reader",
        ) is RoleContext.REVIEWER

    def test_unknown_role_returns_unknown(self) -> None:
        assert infer_role_context(role_name="mystery") is RoleContext.UNKNOWN


# ---------------------------------------------------------------------------
# FINISH-001
# ---------------------------------------------------------------------------


class TestDirtyTreeGate:
    def test_empty_input_returns_empty(self) -> None:
        c = classify_dirty_tree(changed_paths=[])
        assert c == DirtyTreeClassification()
        assert c.auto_ship_safe is True

    def test_task_scope_owned_changes(self) -> None:
        c = classify_dirty_tree(
            changed_paths=["src/zf/runtime/foo.py", "src/zf/cli/bar.py"],
            task_scope=["src/zf/runtime/"],
        )
        assert "src/zf/runtime/foo.py" in c.task_owned_changes
        assert "src/zf/cli/bar.py" in c.unrecognized_changes

    def test_runtime_state_recognized(self) -> None:
        c = classify_dirty_tree(
            changed_paths=[".zf/events.jsonl", ".zf/kanban.json"],
        )
        assert ".zf/events.jsonl" in c.runtime_state_changes
        assert c.auto_ship_safe is True

    def test_generated_artifacts(self) -> None:
        c = classify_dirty_tree(
            changed_paths=["docs/artifacts/x.md", "htmlcov/index.html"],
        )
        assert "docs/artifacts/x.md" in c.generated_artifacts
        assert "htmlcov/index.html" in c.generated_artifacts

    def test_unrecognized_blocks_auto_ship(self) -> None:
        """Doc 39 §2.1.7 invariant."""
        c = classify_dirty_tree(
            changed_paths=["mystery.txt", "another.txt"],
        )
        assert c.has_unrecognized is True
        assert c.auto_ship_safe is False
        assert set(c.unrecognized_changes) == {"mystery.txt", "another.txt"}

    def test_to_dict_serializable(self) -> None:
        c = classify_dirty_tree(
            changed_paths=["src/x.py"], task_scope=["src/"],
        )
        d = c.to_dict()
        assert "task_owned_changes" in d
        assert d["task_owned_changes"] == ["src/x.py"]


# ---------------------------------------------------------------------------
# RESEARCH-001
# ---------------------------------------------------------------------------


def _good_artifact(**kw) -> ResearchArtifact:
    defaults = {
        "task_id": "TASK-R",
        "topic": "cool-thing",
        "research_question": "does X behave like Y?",
        "sources": ("https://example.com/doc",),
        "evidence": (
            ResearchEvidence(
                source_path="src/zf/runtime/foo.py",
                line_range="L10-L20",
                snippet="def foo(): ...",
            ),
        ),
    }
    defaults.update(kw)
    return ResearchArtifact(**defaults)


class TestResearchArtifact:
    def test_valid_artifact_yields_no_errors(self) -> None:
        assert validate_artifact(_good_artifact()) == []

    def test_missing_task_id_errors(self) -> None:
        errors = validate_artifact(_good_artifact(task_id=""))
        assert any("task_id" in e for e in errors)

    def test_missing_sources_errors(self) -> None:
        errors = validate_artifact(_good_artifact(sources=()))
        assert any("sources" in e for e in errors)

    def test_missing_evidence_errors(self) -> None:
        errors = validate_artifact(_good_artifact(evidence=()))
        assert any("evidence" in e for e in errors)

    def test_evidence_without_source_path_errors(self) -> None:
        bad = _good_artifact(evidence=(
            ResearchEvidence(source_path=""),
        ))
        errors = validate_artifact(bad)
        assert any("source_path" in e for e in errors)

    def test_render_md_includes_question_sources_evidence(self) -> None:
        md = render_artifact_md(_good_artifact())
        assert "does X behave like Y?" in md
        assert "https://example.com/doc" in md
        assert "src/zf/runtime/foo.py" in md
        assert "L10-L20" in md

    def test_write_creates_under_research_dir(self, tmp_path: Path) -> None:
        path = write_research_artifact(tmp_path, _good_artifact())
        assert path == tmp_path / "research" / "TASK-R" / "cool-thing.md"
        assert path.exists()

    def test_write_invalid_artifact_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="validation"):
            write_research_artifact(tmp_path, _good_artifact(evidence=()))


# ---------------------------------------------------------------------------
# SPEC-PROMOTE-001
# ---------------------------------------------------------------------------


class TestSpecPromote:
    def test_event_types_registered(self) -> None:
        assert "spec.promote.completed" in KNOWN_EVENT_TYPES
        assert "spec.promote.skipped" in KNOWN_EVENT_TYPES

    def test_promote_when_spec_ref_and_evidence_present(self) -> None:
        d = decide_promotion(task_id="T", spec_ref="docs/spec.md")
        assert d.event_type == SPEC_PROMOTE_COMPLETED
        assert d.is_promotion is True

    def test_skip_when_no_spec_ref(self) -> None:
        d = decide_promotion(task_id="T", spec_ref="")
        assert d.event_type == SPEC_PROMOTE_SKIPPED
        assert d.reason == "no_spec_ref"

    def test_skip_when_bug_fix_only(self) -> None:
        d = decide_promotion(
            task_id="T", spec_ref="docs/x.md", is_bug_fix_only=True,
        )
        assert d.reason == "bug_fix_only"

    def test_skip_when_no_acceptance_evidence(self) -> None:
        d = decide_promotion(
            task_id="T", spec_ref="docs/x.md",
            has_acceptance_evidence=False,
        )
        assert d.reason == "no_acceptance_evidence"

    def test_operator_override_skip(self) -> None:
        d = decide_promotion(
            task_id="T", spec_ref="docs/x.md",
            operator_override_skip=True,
            operator_override_reason="deferred to next sprint",
        )
        assert d.event_type == SPEC_PROMOTE_SKIPPED
        assert "deferred" in d.reason

    def test_decision_is_frozen(self) -> None:
        d = decide_promotion(task_id="T", spec_ref="x.md")
        with pytest.raises((AttributeError, TypeError)):
            d.reason = "tampered"  # type: ignore[misc]
