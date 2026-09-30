from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from zf.runtime import run_manager_repair_validation as subject


def test_repair_validation_missing_worktree_fails_exactly(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    assert subject.execute_repair_verification_plan(
        worktree=str(missing),
        verification_plan=[{"command": "git diff --check"}],
    ) == {
        "schema_version": "run-manager.repair-validation-result.v1",
        "ok": False,
        "status": "failed",
        "reason": "worktree_missing",
        "worktree": str(missing.resolve()),
        "results": [],
    }


def test_repair_validation_rejects_existing_non_directory(tmp_path: Path) -> None:
    not_directory = tmp_path / "file"
    not_directory.write_text("not a worktree", encoding="utf-8")

    result = subject.execute_repair_verification_plan(
        worktree=str(not_directory),
        verification_plan=[{"command": "git diff --check"}],
    )

    assert result["ok"] is False
    assert result["reason"] == "worktree_missing"
    assert result["results"] == []


def test_repair_validation_missing_plan_fails_exactly(tmp_path: Path) -> None:
    assert subject.execute_repair_verification_plan(
        worktree=str(tmp_path),
        verification_plan=[],
    ) == {
        "schema_version": "run-manager.repair-validation-result.v1",
        "ok": False,
        "status": "failed",
        "reason": "verification_plan_missing",
        "worktree": str(tmp_path.resolve()),
        "results": [],
    }


def test_required_missing_command_fails_and_stops(tmp_path: Path) -> None:
    assert subject.execute_repair_verification_plan(
        worktree=str(tmp_path),
        verification_plan=[
            {"kind": "required-empty", "command": "  ", "required": True},
            {"kind": "must-not-run", "command": "review later"},
        ],
    ) == {
        "schema_version": "run-manager.repair-validation-result.v1",
        "ok": False,
        "status": "failed",
        "reason": "missing_command",
        "worktree": str(tmp_path.resolve()),
        "results": [
            {
                "kind": "required-empty",
                "command": "",
                "required": True,
                "status": "failed",
                "reason": "missing_command",
                "returncode": 1,
            }
        ],
    }


def test_optional_missing_command_continues_to_manual_review(tmp_path: Path) -> None:
    assert subject.execute_repair_verification_plan(
        worktree=str(tmp_path),
        verification_plan=[
            {"command": "", "required": "FALSE"},
            {"kind": "docs_review", "command": "anything", "required": "true"},
            {"kind": "named-review", "command": "review generated docs"},
        ],
    ) == {
        "schema_version": "run-manager.repair-validation-result.v1",
        "ok": True,
        "status": "passed",
        "reason": "",
        "worktree": str(tmp_path.resolve()),
        "results": [
            {
                "kind": "step-0",
                "command": "",
                "required": False,
                "status": "skipped",
                "reason": "missing_command",
                "returncode": 0,
            },
            {
                "kind": "docs_review",
                "command": "anything",
                "required": True,
                "status": "passed",
                "reason": "manual_review_instruction_recorded",
                "returncode": 0,
            },
            {
                "kind": "named-review",
                "command": "review generated docs",
                "required": True,
                "status": "passed",
                "reason": "manual_review_instruction_recorded",
                "returncode": 0,
            },
        ],
    }


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git diff --check", {"argv": ["git", "diff", "--check"], "env": {}}),
        (
            "npm --prefix web run build",
            {"argv": ["npm", "--prefix", "web", "run", "build"], "env": {}},
        ),
        (
            "uv run pytest tests/test_one.py -q",
            {
                "argv": ["uv", "run", "pytest", "tests/test_one.py", "-q"],
                "env": {},
            },
        ),
        (
            "PYTEST_ADDOPTS=--no-cov uv run pytest tests/test_one.py",
            {
                "argv": ["uv", "run", "pytest", "tests/test_one.py"],
                "env": {"PYTEST_ADDOPTS": "--no-cov"},
            },
        ),
        (
            "python scripts/dev-verify.py plan --base feature/test-1",
            {
                "argv": [
                    "python",
                    "scripts/dev-verify.py",
                    "plan",
                    "--base",
                    "feature/test-1",
                ],
                "env": {},
            },
        ),
        (
            "python3 scripts/dev-verify.py run --base dev",
            {
                "argv": [
                    "python3",
                    "scripts/dev-verify.py",
                    "run",
                    "--base",
                    "dev",
                ],
                "env": {},
            },
        ),
        (
            "bash scripts/dev-premerge-gate.sh",
            {"argv": ["bash", "scripts/dev-premerge-gate.sh"], "env": {}},
        ),
        (
            "bash scripts/run-flow-smoke.sh",
            {"argv": ["bash", "scripts/run-flow-smoke.sh"], "env": {}},
        ),
    ],
)
def test_trusted_invocation_exact_allowlist(
    command: str,
    expected: dict[str, object],
) -> None:
    assert subject._trusted_invocation(command) == expected


def test_dev_verify_allowlist_accepts_uppercase_ref_components() -> None:
    assert subject._trusted_invocation(
        "python scripts/dev-verify.py run --base Feature/ABC-123"
    ) == {
        "argv": [
            "python",
            "scripts/dev-verify.py",
            "run",
            "--base",
            "Feature/ABC-123",
        ],
        "env": {},
    }


@pytest.mark.parametrize(
    "command",
    [
        "git diff --check --cached",
        "npm run build",
        "uv run pytest",
        "python scripts/dev-verify.py inspect --base dev",
        "python scripts/dev-verify.py run --base 'dev;touch-pwned'",
        "bash scripts/dev-premerge-gate.sh extra",
        "bash -c 'scripts/run-flow-smoke.sh'",
        "rm -rf /tmp/not-allowed",
    ],
)
def test_trusted_invocation_rejects_near_misses(command: str) -> None:
    assert subject._trusted_invocation(command) is None


def test_successful_command_records_exact_receipt_and_tails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="prefix-" + "o" * 2100,
            stderr="prefix-" + "e" * 2100,
        )

    monkeypatch.setattr(subject.subprocess, "run", fake_run)
    result = subject.execute_repair_verification_plan(
        worktree=str(tmp_path),
        verification_plan=[
            {
                "kind": "pytest",
                "command": "PYTEST_ADDOPTS=--no-cov uv run pytest tests/test_one.py",
                "required": "true",
            }
        ],
        timeout_seconds=37,
    )

    assert result == {
        "schema_version": "run-manager.repair-validation-result.v1",
        "ok": True,
        "status": "passed",
        "reason": "",
        "worktree": str(tmp_path.resolve()),
        "results": [
            {
                "kind": "pytest",
                "command": (
                    "PYTEST_ADDOPTS=--no-cov uv run pytest tests/test_one.py"
                ),
                "required": True,
                "status": "passed",
                "reason": "",
                "returncode": 0,
                "stdout_tail": "o" * 2000,
                "stderr_tail": "e" * 2000,
            }
        ],
    }
    assert calls == [
        {
            "argv": ["uv", "run", "pytest", "tests/test_one.py"],
            "cwd": str(tmp_path.resolve()),
            "env": {**subject.os.environ, "PYTEST_ADDOPTS": "--no-cov"},
            "capture_output": True,
            "text": True,
            "timeout": 37,
            "check": False,
        }
    ]


def test_default_timeout_and_required_success_continue_to_next_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(int(kwargs["timeout"]))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subject.subprocess, "run", fake_run)
    result = subject.execute_repair_verification_plan(
        worktree=str(tmp_path),
        verification_plan=[
            {"kind": "gate", "command": "git diff --check", "required": True},
            {"kind": "review", "command": "review after gate", "required": True},
        ],
    )

    assert calls == [180]
    assert result["ok"] is True
    assert [item["kind"] for item in result["results"]] == ["gate", "review"]


def test_required_untrusted_command_has_complete_fail_closed_receipt(
    tmp_path: Path,
) -> None:
    result = subject.execute_repair_verification_plan(
        worktree=str(tmp_path),
        verification_plan=[
            {"kind": "unsafe", "command": "touch escaped", "required": "true"},
            {"kind": "must-not-run", "command": "review later"},
        ],
    )

    assert result == {
        "schema_version": "run-manager.repair-validation-result.v1",
        "ok": False,
        "status": "failed",
        "reason": "command_not_allowlisted",
        "worktree": str(tmp_path.resolve()),
        "results": [
            {
                "kind": "unsafe",
                "command": "touch escaped",
                "required": True,
                "status": "failed",
                "reason": "command_not_allowlisted",
                "returncode": 126,
            }
        ],
    }


def test_required_command_failure_records_tail_and_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 7, stdout="out", stderr="err")

    monkeypatch.setattr(subject.subprocess, "run", fake_run)
    result = subject.execute_repair_verification_plan(
        worktree=str(tmp_path),
        verification_plan=[
            {"kind": "gate", "command": "git diff --check", "required": True},
            {"kind": "must-not-run", "command": "review later"},
        ],
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["reason"] == "command_failed"
    assert result["results"] == [
        {
            "kind": "gate",
            "command": "git diff --check",
            "required": True,
            "status": "failed",
            "reason": "command_failed",
            "returncode": 7,
            "stdout_tail": "out",
            "stderr_tail": "err",
        }
    ]


def test_optional_untrusted_and_failed_commands_do_not_block_required_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subject.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 9, "out", "err"),
    )
    result = subject.execute_repair_verification_plan(
        worktree=str(tmp_path),
        verification_plan=[
            {"kind": "unsafe", "command": "touch escaped", "required": "false"},
            {"kind": "optional-git", "command": "git diff --check", "required": "false"},
            {"kind": "review", "command": "review result", "required": True},
        ],
    )

    assert result["ok"] is True
    assert result["status"] == "passed"
    assert result["reason"] == ""
    assert [item["status"] for item in result["results"]] == [
        "skipped",
        "failed",
        "passed",
    ]
    assert [item["returncode"] for item in result["results"]] == [126, 9, 0]


def test_timeout_with_byte_streams_is_fail_closed_and_decoded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(
            cmd=["git", "diff", "--check"],
            timeout=5,
            output=b"out-\xff",
            stderr=b"err-\xfe",
        )

    monkeypatch.setattr(subject.subprocess, "run", timeout)
    result = subject.execute_repair_verification_plan(
        worktree=str(tmp_path),
        verification_plan=[{"command": "git diff --check"}],
        timeout_seconds=5,
    )

    assert result["ok"] is False
    assert result["reason"] == "timeout"
    assert result["results"] == [
        {
            "kind": "step-0",
            "command": "git diff --check",
            "required": True,
            "status": "failed",
            "reason": "timeout",
            "returncode": 124,
            "stdout_tail": "out-�",
            "stderr_tail": "err-�",
        }
    ]


def test_spawn_error_is_fail_closed_with_exact_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_spawn(*_args: object, **_kwargs: object) -> None:
        raise OSError("runner missing")

    monkeypatch.setattr(subject.subprocess, "run", fail_spawn)
    result = subject.execute_repair_verification_plan(
        worktree=str(tmp_path),
        verification_plan=[{"kind": "spawn", "command": "git diff --check"}],
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["reason"] == "spawn_failed:runner missing"
    assert result["results"] == [
        {
            "kind": "spawn",
            "command": "git diff --check",
            "required": True,
            "status": "failed",
            "reason": "spawn_failed:runner missing",
            "returncode": 127,
        }
    ]


def test_tail_empty_and_custom_limit_contract() -> None:
    assert subject._tail(None) == ""
    assert subject._tail("") == ""
    assert subject._tail("abcdef", limit=3) == "def"
    assert subject._tail(b"abcdef", limit=4) == "cdef"


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-049: malformed quoted command escapes fail-closed result",
)
def test_malformed_quoted_command_is_rejected_without_crashing(tmp_path: Path) -> None:
    result = subject.execute_repair_verification_plan(
        worktree=str(tmp_path),
        verification_plan=[{"kind": "malformed", "command": "uv run pytest '"}],
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["reason"] == "command_not_allowlisted"
    assert result["results"][0]["returncode"] == 126


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-050: empty repair worktree resolves to the current checkout",
)
def test_empty_worktree_is_rejected_instead_of_using_process_cwd() -> None:
    result = subject.execute_repair_verification_plan(
        worktree="",
        verification_plan=[{"kind": "review", "command": "review docs"}],
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["reason"] == "worktree_missing"
    assert result["results"] == []
