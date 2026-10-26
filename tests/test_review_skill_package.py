"""Review skill resources must survive distribution and actual runtime loading."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import runpy
import shutil

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.config.loader import ConfigError
from zf.core.skills.materialize import materialize_role_skills
from zf.core.skills.provenance import read_skill_metadata
from zf.runtime.skill_briefing import render_skill_briefing_section

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills/zf-cr"


def _files(bundle: Path) -> dict[str, bytes]:
    return {p.relative_to(bundle).as_posix(): p.read_bytes()
            for p in bundle.rglob("*") if p.is_file()}


def _check_links(bundle: Path) -> None:
    """Resolve local Markdown resource links inside the distributed package."""
    for doc in bundle.rglob("*.md"):
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", doc.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            path = (doc.parent / target.split("#", 1)[0]).resolve()
            assert path.is_relative_to(bundle.resolve()), target
            assert path.is_file(), (doc, target)


def test_complete_review_package_is_identical_and_references_resolve() -> None:
    canonical = _files(SOURCE)
    assert canonical
    for directory in (SOURCE, ROOT / ".codex/skills/zf-cr", ROOT / ".claude/skills/zf-cr"):
        assert _files(directory) == canonical
        _check_links(directory)
        metadata = read_skill_metadata(directory / "SKILL.md", expected_name="zf-cr")
        assert not metadata.warnings


def test_missing_reference_is_detected(tmp_path: Path) -> None:
    bundle = tmp_path / "zf-cr"
    shutil.copytree(SOURCE, bundle)
    (bundle / "references/full-review.md").unlink()
    with pytest.raises(AssertionError):
        _check_links(bundle)


def test_export_fallback_documents_exist() -> None:
    # These are actual fallback entrypoints, not a test of an unavailable doc's name.
    for relative in ("docs/manual/00-index.md", "docs/manual/architecture.md"):
        assert (ROOT / relative).is_file()


@pytest.mark.parametrize("backend", ["codex", "claude"])
@pytest.mark.parametrize("mode", ["copy", "symlink"])
def test_runtime_materialization_preserves_review_resources(
    tmp_path: Path, backend: str, mode: str,
) -> None:
    shutil.copytree(SOURCE, tmp_path / "skills/zf-cr")
    before = _files(tmp_path / "skills/zf-cr")
    role = RoleConfig(name="reviewer", backend=backend, skills=["zf-cr"])
    config = ZfConfig(project=ProjectConfig(name="review", state_dir="runtime-data"),
                      roles=[role])
    config.runtime.skills.materialize = mode
    result = materialize_role_skills(
        config=config, project_root=tmp_path, state_dir=tmp_path / "runtime-data",
        role=role, task_id="review-fixture",
    )
    assert result is not None
    materialized = result.materialized_paths_under(tmp_path)["zf-cr"]
    assert _files(materialized) == before
    _check_links(materialized)
    briefing = "\n".join(render_skill_briefing_section(role, list(result.skills)))
    assert "/zf-cr" in briefing
    assert result.skills[0].description in briefing
    assert not result.skills[0].auto_inject
    assert result.skills[0].load_on_demand
    assert "validation_paths.py" not in briefing  # no inline body expansion
    assert _files(tmp_path / "skills/zf-cr") == before
    assert not (tmp_path / ".zf").exists()


@pytest.mark.parametrize("configured", [None, "runtime-data", "nested/run", "/tmp/zf-explicit-state"])
def test_validation_paths_respect_explicit_project_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: str | None,
) -> None:
    helper = runpy.run_path(str(SOURCE / "scripts/validation_paths.py"))["validation_paths"]
    config = "project:\n  name: review\n"
    if configured is not None:
        config += f"  state_dir: {configured}\n"
    (tmp_path / "zf.yaml").write_text(config)
    # Parent worker state must not redirect a different explicit validation project.
    monkeypatch.setenv("ZF_PROJECT_ROOT", "/tmp/unrelated-project")
    monkeypatch.setenv("ZF_STATE_DIR", "/tmp/unrelated-state")
    before = _files(tmp_path)
    paths = helper(tmp_path)
    expected = Path(configured or ".zf")
    if not expected.is_absolute():
        expected = tmp_path / expected
    assert Path(paths["state_dir"]) == expected.resolve()
    assert Path(paths["events"]) == expected.resolve() / "events.jsonl"
    assert _files(tmp_path) == before
    assert list(tmp_path.iterdir()) == [tmp_path / "zf.yaml"]


def test_validation_paths_require_config_and_never_fall_back(tmp_path: Path) -> None:
    helper = runpy.run_path(str(SOURCE / "scripts/validation_paths.py"))["validation_paths"]
    with pytest.raises(ValueError, match="non-empty"):
        helper("")
    with pytest.raises(FileNotFoundError, match="explicit project config"):
        helper(tmp_path)
    assert not list(tmp_path.iterdir())


def test_invalid_validation_config_does_not_create_default_state(tmp_path: Path) -> None:
    helper = runpy.run_path(str(SOURCE / "scripts/validation_paths.py"))["validation_paths"]
    (tmp_path / "zf.yaml").write_text("project: [unclosed\n")
    before = _files(tmp_path)
    with pytest.raises(ConfigError):
        helper(tmp_path)
    assert _files(tmp_path) == before
    assert not (tmp_path / ".zf").exists()


def _eval_module():
    spec = importlib.util.spec_from_file_location("review_eval", ROOT / "scripts/eval-review-skill.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_eval_trace_preserves_failure_and_does_not_invent_usage(tmp_path: Path) -> None:
    module = _eval_module()
    trace = tmp_path / "trace.jsonl"
    trace.write_text(json.dumps({"type": "turn.failed", "error": "timeout"}) + "\n")
    result = module.collect_trace(trace)
    assert not result["turn_completed"]
    assert result["usage"] == {}
    assert result["errors"] == ["turn.failed"]


def test_eval_fixture_does_not_disclose_expected_answer(tmp_path: Path) -> None:
    module = _eval_module()
    spec = json.loads(module.CASES.read_text())
    case = spec["cases"][0]
    project = tmp_path / "project"
    module.prepare(project, {**spec["common_files"], **case["files"]}, SOURCE)
    assert not any(case["expected"].encode() in value for value in _files(project).values())
    before = module.snapshot(project)
    (project / "src/zf/runtime/retry.py").write_text("changed")
    assert module.digest(module.snapshot(project)) != module.digest(before)


@pytest.mark.parametrize("relative", ["../outside", "/tmp/outside", ".git/config", ".agents/skills/override"])
def test_eval_fixture_cannot_escape_or_override_instructions(
    tmp_path: Path, relative: str,
) -> None:
    with pytest.raises(ValueError, match="fixture path"):
        _eval_module().prepare(tmp_path / "project", {relative: "bad"}, SOURCE)
    assert not list(tmp_path.iterdir())
