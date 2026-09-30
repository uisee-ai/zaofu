"""Source-bound deterministic runner, exercised through real child pytest."""

import json
from pathlib import Path

import pytest

from tests.testkit import native_acceptance, runner
from tests.testkit.evidence import evidence_ref, write_json_atomic
from tests.testkit.test_native_run import repo as repo
from tests.testkit.test_native_acceptance import inputs as inputs, _bind_native_inputs


def _run(repo, tmp_path, monkeypatch, *, change=False, fail=False, source_profile="backend"):
    case = repo / "tests/test_observation.py"
    body = "from pathlib import Path\ndef test_observation():\n"
    if change:
        body += "    (Path(__file__).parents[1] / 'src/module.py').write_text('value = 2\\n')\n"
    body += "    assert " + ("False" if fail else "True") + "\n"
    case.write_text(body)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    node = "tests/test_observation.py::test_observation"
    monkeypatch.setattr(native_acceptance, "MAINLINE", (node,))
    output = tmp_path / "out"
    result = runner.run_pytest(
        project_root=repo,
        stage="MAINLINE-DETERMINISTIC",
        nodeids=(node,),
        output_dir=output,
        timeout_seconds=30,
        bind_source=True,
        source_profile=source_profile,
    )
    return output, result


@pytest.mark.parametrize(
    "change,fail,verdict",
    [(False, False, "pass"), (False, True, "fail"), (True, False, "harness_failure")],
)
def test_source_bound_runner_preserves_result_and_detects_edits(
    repo, tmp_path, monkeypatch, change, fail, verdict
):
    output, result = _run(repo, tmp_path, monkeypatch, change=change, fail=fail)
    assert result.verdict == verdict
    source = result.source_evidence
    assert source["stable_observed"] is not change
    for key in ("before", "after", "phases", "junit"):
        path = output / source[key]["ref"]
        assert evidence_ref(path, root=output) == source[key]
    if change:
        assert "source changed" in " ".join(result.evidence_errors)
        return
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    path = output / "testkit-run.json"
    observed = native_acceptance._bound_mainline(path.read_bytes(), path, snapshots)
    assert observed["verdict"] == verdict
    assert observed["source_verified"] is True


@pytest.mark.parametrize("damage", ["digest", "phase", "head", "junit", "escape"])
def test_mainline_binding_rejects_tampering(repo, tmp_path, monkeypatch, damage):
    output, _ = _run(repo, tmp_path, monkeypatch)
    path = output / "testkit-run.json"
    report = json.loads(path.read_text())
    refs = report["source_evidence"]
    if damage == "digest":
        refs["before"]["digest"] = "0" * 64
    elif damage == "head":
        report["git_head"] = "0" * 40
    elif damage == "escape":
        refs["before"]["ref"] = "../unrelated.json"
    elif damage == "phase":
        phases_path = output / refs["phases"]["ref"]
        phases = json.loads(phases_path.read_text())
        phases["session_exitstatus"] = 1
        write_json_atomic(phases_path, phases)
        refs["phases"] = evidence_ref(phases_path, root=output)
    else:
        junit_path = output / refs["junit"]["ref"]
        junit_path.write_text(
            junit_path.read_text().replace(
                'name="test_observation"', 'name="other_test"'
            )
        )
        refs["junit"] = evidence_ref(junit_path, root=output)
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    with pytest.raises(native_acceptance.NativeAcceptanceError):
        native_acceptance._bound_mainline(json.dumps(report).encode(), path, snapshots)


def test_bound_runner_rejects_output_inside_source_before_spawn(repo, monkeypatch):
    monkeypatch.setattr(
        runner, "capture_source", lambda root: pytest.fail("unexpected observation")
    )
    with pytest.raises(ValueError, match="outside the source"):
        runner.run_pytest(
            project_root=repo,
            output_dir=repo / "out",
            bind_source=True,
            nodeids=("tests/test_none.py",),
        )
    assert not (repo / "out").exists()


@pytest.mark.parametrize("source_profile", ["backend", "acceptance"])
def test_aggregate_reads_new_mainline_without_claiming_historical_native_source_match(
    repo,
    tmp_path,
    monkeypatch,
    inputs,
    source_profile,
):
    _bind_native_inputs(inputs)
    output, _ = _run(repo, tmp_path, monkeypatch, source_profile=source_profile)
    path = output / "testkit-run.json"
    plan = json.loads(inputs.read_text())
    entry = next(e for e in plan["evidence"] if e["role"] == "deterministic_mainline")
    entry.update(ref=str(path), sha256=evidence_ref(path, root=output)["digest"],
                 source_profile=source_profile)
    write_json_atomic(inputs, plan)
    observed = native_acceptance.build_native_acceptance(
        manifest=inputs,
        output=tmp_path / "aggregate",
        require_source=True,
    )
    assert observed["verdict"] == "pass"  # valid per-run observations, not release
    assert observed["native_same_source_verified"] is True
    assert observed["native_mainline_same_source_verified"] is False
    assert observed["same_revision_verified"] is False
    [mainline] = [
        r for r in observed["results"] if r["role"] == "deterministic_mainline"
    ]
    assert mainline["source_verified"] is True


@pytest.mark.parametrize("profile,target", [
    ("acceptance", "awesome-zaofu/New_function/Test ZaoFu_Project/ZaoFu_issue.md"),
    ("browser", "web/src/probe.ts"),
])
@pytest.mark.parametrize("change", [False, True])
def test_explicit_profile_binds_real_web_and_ledger_changes(
    repo, tmp_path, monkeypatch, profile, target, change
):
    from tests.testkit import source_observation

    target_path = repo / target
    target_path.parent.mkdir(parents=True)
    target_path.write_text("before\n")
    body = "from pathlib import Path\ndef test_observation():\n    assert True\n"
    if change:
        body += f"    (Path(__file__).parents[1] / {target!r}).write_text('after\\n')\n"
    (repo / "tests/test_observation.py").write_text(body)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    node = "tests/test_observation.py::test_observation"
    out = tmp_path / "bound"
    result = runner.run_pytest(project_root=repo, output_dir=out, nodeids=(node,),
                               bind_source=True, source_profile=profile, timeout_seconds=30)
    assert result.verdict == ("harness_failure" if change else "pass")
    before = json.loads((out / "source-before.json").read_text())
    assert before["scope"] == list(source_observation.SOURCE_PROFILES[profile])
    assert target in {row["path"] for row in before["files"]}
    assert result.source_evidence["stable_observed"] is not change
    if not change:
        snapshots = tmp_path / "copies"
        snapshots.mkdir()
        path = out / "testkit-run.json"
        observed = native_acceptance._bound_mainline(
            path.read_bytes(), path, snapshots, expected_nodes=(node,), source_profile=profile,
        )
        assert observed["verdict"] == "pass"
        assert observed["source_observation"]["scope"] == before["scope"]


def test_narrow_source_cannot_satisfy_requested_browser_profile(repo, tmp_path, monkeypatch):
    out, _ = _run(repo, tmp_path, monkeypatch)
    path = out / "testkit-run.json"
    snapshots = tmp_path / "copies"
    snapshots.mkdir()
    with pytest.raises(native_acceptance.NativeAcceptanceError, match="source inventory"):
        native_acceptance._bound_mainline(
            path.read_bytes(), path, snapshots, source_profile="browser",
        )


@pytest.mark.parametrize("profile,bound", [("unknown", True), ("browser", False)])
def test_bad_profile_rejected_before_output_or_spawn(repo, tmp_path, monkeypatch, profile, bound):
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: pytest.fail("spawned"))
    with pytest.raises(ValueError, match="source profile"):
        runner.run_pytest(project_root=repo, output_dir=tmp_path / "out",
                          bind_source=bound, source_profile=profile)
    assert not (tmp_path / "out").exists()
