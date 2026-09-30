"""Canonical pre-Draft Self-Issue intake questions and answer validation."""

from __future__ import annotations

from typing import Any

from zf.core.security.redaction import redact_obj


QUESTION_SCHEMA_VERSION = "self-issue-intake.v1"
QUESTION_IDS = (
    "title",
    "bug_description",
    "reproduction_steps",
    "expected_behavior",
    "attachments_context",
    "environment",
    "zaofu_version",
    "additional_context",
)
REQUIRED_QUESTION_IDS = frozenset({
    "title", "bug_description", "reproduction_steps", "zaofu_version",
})


def intake_questions() -> list[dict[str, Any]]:
    """Return the fixed eight-step ZaoFu bug report intake contract."""
    return [
        _question("title", "Add a title", "Enter a clear, concise title.", required=True),
        _question(
            "bug_description",
            "Describe the bug",
            "A clear and concise description of what the bug is.",
            required=True,
            input_kind="textarea",
            options=(
                "The task or workflow is stuck",
                "The page is slow, frozen, or not updating",
                "An error message appeared",
                "The result is incorrect or unexpected",
                "I am not sure what failed",
            ),
        ),
        _question(
            "reproduction_steps",
            "To reproduce",
            "Steps to reproduce: 1. Go to '...' 2. Click on '...' 3. Scroll down to '...' 4. See error '...'",
            required=True,
            input_kind="textarea",
            options=(
                "Always reproducible",
                "Sometimes reproducible",
                "Observed once",
                "I do not know how to reproduce it",
            ),
        ),
        _question(
            "expected_behavior",
            "Expected behavior",
            "Tell us what you expect to see.",
            input_kind="textarea",
            options=(
                "The task or workflow should continue",
                "The page should remain responsive",
                "The requested action should succeed",
                "I am not sure",
            ),
        ),
        _question(
            "attachments_context",
            "Screenshots, videos, and logs",
            "add screenshots/videos/logs to help us understand your problem",
            input_kind="attachments",
            help_text=(
                "Attach up to five safe files. Selected files remain local until the "
                "separate GitLab attachment confirmation."
            ),
        ),
        _question(
            "environment",
            "Operating system and version",
            "tell me your operating system and version.",
            input_kind="environment",
        ),
        _question(
            "zaofu_version",
            "Current ZaoFu version",
            "tell me your current zaofu version",
            required=True,
            help_text=(
                "Run `zf --version` in a terminal, or check the version shown in Settings. "
                "ZaoFu fills the detected version when available."
            ),
        default_value="",
        ),
        _question(
            "additional_context",
            "Additional context",
            "leave a comment.",
            input_kind="textarea",
        ),
    ]


def normalize_intake_answers(value: object, *, complete: bool) -> dict[str, Any]:
    if not isinstance(value, dict) or not set(value) <= set(QUESTION_IDS):
        raise ValueError("intake answers contain unknown question fields")
    answers: dict[str, Any] = {}
    for question_id in QUESTION_IDS:
        raw = value.get(question_id, "")
        if question_id == "environment":
            if raw is None or raw == "":
                answers[question_id] = {"os": "", "version": ""}
                continue
            if not isinstance(raw, dict) or not set(raw) <= {"os", "version"}:
                raise ValueError("environment answer must contain only os and version")
            answers[question_id] = {
                "os": _bounded(raw.get("os"), 80),
                "version": _bounded(raw.get("version"), 120),
            }
            continue
        answers[question_id] = _bounded(raw, 8000 if question_id != "title" else 240)
    if complete:
        missing = [
            question_id for question_id in QUESTION_IDS
            if question_id in REQUIRED_QUESTION_IDS and not answers[question_id]
        ]
        if missing:
            raise ValueError(f"required intake questions are unanswered: {', '.join(missing)}")
    return redact_obj(answers)


def first_missing_required(answers: dict[str, Any]) -> str:
    return next((
        question_id for question_id in QUESTION_IDS
        if question_id in REQUIRED_QUESTION_IDS and not answers.get(question_id)
    ), "")


def default_intake_answers(*, title_seed: str = "") -> dict[str, Any]:
    return normalize_intake_answers({
        "title": title_seed,
        "zaofu_version": "",
    }, complete=False)


def _question(
    question_id: str,
    title: str,
    placeholder: str,
    *,
    required: bool = False,
    input_kind: str = "text",
    help_text: str = "",
    default_value: str = "",
    options: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "id": question_id,
        "title": title,
        "placeholder": placeholder,
        "required": required,
        "input_kind": input_kind,
        "help_text": help_text,
        "default_value": default_value,
        "options": [
            {"value": value, "label": value}
            for value in options
        ],
    }


def _bounded(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]
