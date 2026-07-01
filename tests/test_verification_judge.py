"""Tests for judge role evaluation."""

from __future__ import annotations

from zf.core.verification.judge import JudgeEvaluator
from zf.core.verification.evidence import Evidence
from zf.core.task.schema import Task, TaskContract, TaskEvidence


class TestJudgeEvaluator:
    def test_all_gates_pass_gives_high_grade(self):
        judge = JudgeEvaluator()
        task = Task(title="Build auth", contract=TaskContract(behavior="JWT works"),
                    evidence=TaskEvidence(commit="abc"))
        evidence = [
            Evidence(gate_name="typecheck", passed=True, output_summary="OK"),
            Evidence(gate_name="test", passed=True, output_summary="10 passed"),
        ]
        result = judge.evaluate(task, evidence)
        assert result.passed is True
        assert result.overall_grade in ("A", "B")

    def test_no_evidence_fails(self):
        judge = JudgeEvaluator()
        task = Task(title="Build auth")
        result = judge.evaluate(task, [])
        assert result.passed is False
        assert result.overall_grade in ("C", "D")
        assert len(result.recommendations) > 0

    def test_mixed_gates_lowers_grade(self):
        judge = JudgeEvaluator()
        task = Task(title="Build auth", contract=TaskContract(behavior="JWT"))
        evidence = [
            Evidence(gate_name="typecheck", passed=True, output_summary="OK"),
            Evidence(gate_name="test", passed=False, output_summary="3 failed"),
            Evidence(gate_name="lint", passed=False, output_summary="lint errors"),
        ]
        result = judge.evaluate(task, evidence)
        assert result.overall_grade in ("C", "D")
        assert any("test" in r.lower() or "gate" in r.lower() for r in result.recommendations)

    def test_custom_pass_threshold(self):
        judge = JudgeEvaluator(pass_threshold="A")
        task = Task(title="t", contract=TaskContract(behavior="x"))
        evidence = [
            Evidence(gate_name="check", passed=True, output_summary="ok"),
        ]
        result = judge.evaluate(task, evidence)
        # Without commit evidence, completeness drops to C, so overall won't be A
        assert result.overall_grade != "A" or result.passed is True

    def test_dimensions_cover_all_areas(self):
        judge = JudgeEvaluator()
        task = Task(title="t")
        evidence = [Evidence(gate_name="test-unit", passed=True, output_summary="ok")]
        result = judge.evaluate(task, evidence)
        dim_names = {d.dimension for d in result.dimensions}
        assert "correctness" in dim_names
        assert "completeness" in dim_names
        assert "code_quality" in dim_names
        assert "test_coverage" in dim_names
