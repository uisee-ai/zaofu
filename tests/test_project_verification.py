"""Executable acceptance probes and distributable project-verification methods."""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess

import pytest

from tests.e2e.project_verification_probes import ContractProbeFailed, run_contract_probe
from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.skills.materialize import materialize_role_skills
from zf.core.skills.provenance import read_skill_metadata
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.skill_briefing import render_skill_briefing_section


ROOT = Path(__file__).resolve().parents[1]
BUNDLES = ("zf-verify-zaofu", "zf-harness-verification-checklist")


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def _check_links(root: Path) -> None:
    for doc in root.rglob("*.md"):
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", doc.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            resolved = (doc.parent / target.split("#", 1)[0]).resolve()
            assert resolved.is_relative_to(root.resolve())
            assert resolved.is_file(), (doc, target)


@pytest.mark.parametrize("name", BUNDLES)
def test_project_method_distribution_is_complete(name: str) -> None:
    canonical = _files(ROOT / "skills" / name)
    assert canonical
    for tree in ("skills", ".codex/skills", ".claude/skills"):
        bundle = ROOT / tree / name
        assert _files(bundle) == canonical
        _check_links(bundle)
        assert not read_skill_metadata(bundle / "SKILL.md", expected_name=name).warnings


@pytest.mark.parametrize("backend", ["codex", "claude"])
@pytest.mark.parametrize("mode", ["copy", "symlink"])
def test_project_methods_reach_runtime_briefing_with_resources(tmp_path, backend, mode):
    for name in BUNDLES:
        shutil.copytree(ROOT / "skills" / name, tmp_path / "skills" / name)
    role = RoleConfig(name="verify", backend=backend, skills=["zf-verify-zaofu"])
    config = ZfConfig(project=ProjectConfig(name="method-fixture", state_dir="runtime-data"),
                      roles=[role])
    config.runtime.skills.materialize = mode
    result = materialize_role_skills(
        config=config, project_root=tmp_path, state_dir=tmp_path / "runtime-data",
        role=role, task_id="method-fixture",
    )
    assert result is not None
    paths = result.materialized_paths_under(tmp_path)
    assert set(paths) == set(BUNDLES)
    for name in BUNDLES:
        assert _files(paths[name]) == _files(ROOT / "skills" / name)
        _check_links(paths[name])
    briefing = "\n".join(render_skill_briefing_section(role, list(result.skills)))
    for skill in result.skills:
        assert skill.name in briefing
        assert skill.description in briefing
    assert not (tmp_path / ".zf").exists()


@pytest.fixture
def probe_project(tmp_path):
    workdir = tmp_path / "project"
    workdir.mkdir()
    for args in (("init", "-q"), ("config", "user.email", "fixture@example.test"),
                 ("config", "user.name", "Fixture")):
        subprocess.run(["git", *args], cwd=workdir, check=True, capture_output=True)
    (workdir / "result.txt").write_text("expected\n")
    subprocess.run(["git", "add", "--", "result.txt"], cwd=workdir, check=True)
    subprocess.run(["git", "commit", "-qm", "test: seed probe"], cwd=workdir, check=True)
    target = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=workdir, text=True).strip()
    return {
        "state_dir": tmp_path / "runtime-data", "workdir": workdir,
        "target_commit": target, "receipt_id": "probe-result",
        "command": {"command_id": "result-content", "command_digest": "fixture-command",
                    "command": "test -f result.txt && grep -qx expected result.txt"},
    }


def test_probe_records_actual_command_output_and_candidate(probe_project):
    receipt = run_contract_probe(**probe_project)
    evidence = hydrate_sidecar_ref(probe_project["state_dir"], receipt["evidence_refs"][0]).payload
    assert receipt["status"] == "passed" and receipt["exit_code"] == 0
    assert evidence["command"] == probe_project["command"]["command"]
    assert evidence["target_commit"] == probe_project["target_commit"]
    assert evidence["method"] == "local-command/mock-provider"


@pytest.mark.parametrize("defect", ["wrong-content", "missing-output"])
def test_probe_rejects_the_planted_defect_with_command_evidence(probe_project, defect):
    output = probe_project["workdir"] / "result.txt"
    if defect == "missing-output":
        output.unlink()
    else:
        output.write_text("wrong\n")
    # Each mutant is its own candidate; a dirty workspace is not a new target.
    subprocess.run(["git", "add", "--", "result.txt"], cwd=probe_project["workdir"], check=True)
    subprocess.run(["git", "commit", "-qm", "test: plant output defect"],
                   cwd=probe_project["workdir"], check=True)
    probe_project["target_commit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=probe_project["workdir"], text=True,
    ).strip()
    with pytest.raises(ContractProbeFailed, match="result-content exited 1") as failed:
        run_contract_probe(**probe_project)
    evidence = hydrate_sidecar_ref(probe_project["state_dir"], failed.value.evidence).payload
    assert evidence["exit_code"] == 1  # Not command-not-found or infrastructure failure.
    assert evidence["stderr"] == ""


def test_wrong_target_is_rejected_before_command_execution(probe_project):
    probe_project["target_commit"] = "0" * 40
    probe_project["command"]["command"] = "touch should-not-run"
    with pytest.raises(AssertionError, match="probe target mismatch"):
        run_contract_probe(**probe_project)
    assert not (probe_project["workdir"] / "should-not-run").exists()
    assert not probe_project["state_dir"].exists()


def test_dirty_target_cannot_borrow_a_commits_identity(probe_project):
    (probe_project["workdir"] / "result.txt").write_text("uncommitted\n")
    probe_project["command"]["command"] = "touch should-not-run"
    with pytest.raises(AssertionError, match="uncommitted tracked changes"):
        run_contract_probe(**probe_project)
    assert not (probe_project["workdir"] / "should-not-run").exists()
