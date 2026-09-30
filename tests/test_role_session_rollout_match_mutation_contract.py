"""Mutation-resistant contracts for Codex rollout project ownership."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.state.role_sessions import RoleSessionRegistry


def _registry(tmp_path: Path) -> RoleSessionRegistry:
    project_root = tmp_path / "project"
    project_root.mkdir()
    return RoleSessionRegistry(
        tmp_path / "state" / "role_sessions.yaml",
        str(project_root),
    )


def _rollout(tmp_path: Path, first_line: str) -> Path:
    path = tmp_path / "rollout.jsonl"
    path.write_text(first_line + "\nignored-second-line\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("first_line", ["", "   ", "not-json"])
def test_rollout_match_accepts_empty_and_unparseable_legacy_first_lines(
    tmp_path: Path,
    first_line: str,
) -> None:
    registry = _registry(tmp_path)

    assert registry._rollout_matches_project(_rollout(tmp_path, first_line)) is True


def test_rollout_match_accepts_unreadable_path(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    unreadable = tmp_path / "rollout-directory"
    unreadable.mkdir()

    assert registry._rollout_matches_project(unreadable) is True


@pytest.mark.parametrize(
    "body",
    [
        [],
        "scalar",
        {"other": "field"},
        {"payload": "not-a-mapping"},
        {"payload": {}},
        {"payload": {"cwd": ""}},
    ],
)
def test_rollout_match_accepts_legacy_shapes_without_concrete_cwd(
    tmp_path: Path,
    body: object,
) -> None:
    registry = _registry(tmp_path)
    path = _rollout(tmp_path, json.dumps(body))

    assert registry._rollout_matches_project(path) is True


@pytest.mark.parametrize(
    ("cwd_kind", "expected"),
    [
        ("project", True),
        ("state-workdir", True),
        ("legacy-workdir", True),
        ("external", False),
    ],
)
def test_rollout_match_resolves_project_and_sanctioned_workdirs(
    tmp_path: Path,
    cwd_kind: str,
    expected: bool,
) -> None:
    registry = _registry(tmp_path)
    project_root = Path(registry.project_root)
    choices = {
        "project": project_root,
        "state-workdir": registry.path.parent / "workdirs" / "run-1" / "task-1",
        "legacy-workdir": project_root / ".zf" / "workdirs" / "run-1" / "task-1",
        "external": tmp_path / "other-project",
    }
    cwd = choices[cwd_kind]
    cwd.mkdir(parents=True, exist_ok=True)
    path = _rollout(tmp_path, json.dumps({"payload": {"cwd": str(cwd)}}))

    assert registry._rollout_matches_project(path) is expected


@pytest.mark.parametrize(
    ("rollout_cwd", "project_root", "expected"),
    [
        ("same-raw-path", "same-raw-path", True),
        ("different-raw-path", "project-raw-path", False),
    ],
)
def test_rollout_match_oserror_fallback_compares_raw_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rollout_cwd: str,
    project_root: str,
    expected: bool,
) -> None:
    registry = RoleSessionRegistry(
        tmp_path / "state" / "role_sessions.yaml",
        project_root,
    )
    path = _rollout(
        tmp_path,
        json.dumps({"payload": {"cwd": rollout_cwd}}),
    )

    def fail_resolve(self: Path, strict: bool = False) -> Path:
        del self, strict
        raise OSError("synthetic resolve failure")

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    assert registry._rollout_matches_project(path) is expected


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-055: invalid UTF-8 rollout metadata escapes fail-open policy",
)
def test_rollout_match_accepts_invalid_utf8_as_unparseable_legacy_data(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    path = tmp_path / "invalid-utf8-rollout.jsonl"
    path.write_bytes(b"\xff\xfe\n")

    assert registry._rollout_matches_project(path) is True
