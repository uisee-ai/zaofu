"""Judge role — deterministic rubric-based evaluation of task evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from zf.core.task.schema import Task
from zf.core.verification.evidence import Evidence


@dataclass
class DimensionScore:
    dimension: str  # correctness, completeness, code_quality, test_coverage, documentation
    grade: str  # A, B, C, D
    detail: str = ""


@dataclass
class JudgeResult:
    passed: bool
    overall_grade: str  # A, B, C, D
    dimensions: list[DimensionScore] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


_GRADE_ORDER = {"A": 4, "B": 3, "C": 2, "D": 1}


class JudgeEvaluator:
    """Evaluate task evidence against rubric dimensions.

    Deterministic evaluation based on presence/absence of evidence,
    not LLM-based judgment.
    """

    def __init__(self, *, pass_threshold: str = "B") -> None:
        self.pass_threshold = pass_threshold

    def evaluate(self, task: Task, evidence: list[Evidence]) -> JudgeResult:
        """Evaluate task evidence and return a judge result."""
        dimensions: list[DimensionScore] = []
        recommendations: list[str] = []

        # 1. Correctness: did gates pass?
        gates_passed = sum(1 for e in evidence if e.passed)
        gates_total = len(evidence)
        if gates_total == 0:
            dimensions.append(DimensionScore("correctness", "D", "No evidence provided"))
            recommendations.append("Run verification gates before submitting for review")
        elif gates_passed == gates_total:
            dimensions.append(DimensionScore("correctness", "A", f"All {gates_total} gates passed"))
        elif gates_passed >= gates_total * 0.5:
            dimensions.append(DimensionScore("correctness", "C",
                                              f"{gates_passed}/{gates_total} gates passed"))
            recommendations.append("Fix failing gates before proceeding")
        else:
            dimensions.append(DimensionScore("correctness", "D",
                                              f"Only {gates_passed}/{gates_total} gates passed"))
            recommendations.append("Most gates are failing — investigate root cause")

        # 2. Completeness: does task have contract and was it met?
        if task.contract and task.contract.behavior:
            if task.evidence and task.evidence.commit:
                dimensions.append(DimensionScore("completeness", "A", "Contract fulfilled with commit evidence"))
            elif task.evidence:
                dimensions.append(DimensionScore("completeness", "B", "Evidence provided but no commit"))
            else:
                dimensions.append(DimensionScore("completeness", "C", "Contract defined but no evidence"))
                recommendations.append("Provide evidence of contract fulfillment")
        else:
            dimensions.append(DimensionScore("completeness", "C", "No contract defined"))
            recommendations.append("Define a contract with behavior and verification")

        # 3. Code quality: check for any gate outputs mentioning lint/type issues
        quality_issues = sum(1 for e in evidence if not e.passed and
                            any(kw in e.output_summary.lower() for kw in ("lint", "type", "style")))
        if quality_issues == 0:
            dimensions.append(DimensionScore("code_quality", "A", "No quality issues detected"))
        else:
            dimensions.append(DimensionScore("code_quality", "C", f"{quality_issues} quality issues"))
            recommendations.append("Address lint/type/style issues")

        # 4. Test coverage: check for test-related gates
        test_gates = [e for e in evidence if "test" in e.gate_name.lower()]
        if not test_gates:
            dimensions.append(DimensionScore("test_coverage", "C", "No test gates found"))
            recommendations.append("Add test verification gates")
        elif all(e.passed for e in test_gates):
            dimensions.append(DimensionScore("test_coverage", "A", "All test gates passed"))
        else:
            dimensions.append(DimensionScore("test_coverage", "D", "Test gates failing"))
            recommendations.append("Fix failing tests before proceeding")

        # Calculate overall grade
        if not dimensions:
            overall = "D"
        else:
            avg = sum(_GRADE_ORDER.get(d.grade, 1) for d in dimensions) / len(dimensions)
            if avg >= 3.5:
                overall = "A"
            elif avg >= 2.5:
                overall = "B"
            elif avg >= 1.5:
                overall = "C"
            else:
                overall = "D"

        passed = _GRADE_ORDER.get(overall, 0) >= _GRADE_ORDER.get(self.pass_threshold, 3)

        return JudgeResult(
            passed=passed,
            overall_grade=overall,
            dimensions=dimensions,
            recommendations=recommendations,
        )
