"""Native source-bound runner tested with offline child pytest processes."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.testkit import native_run


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src/module.py").write_text("value = 1\n")
    (root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "add", "--", "src/module.py", ".gitignore"], cwd=root, check=True
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=TestKit",
            "-c",
            "user.email=testkit@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=root,
        check=True,
    )
    return root


def test_source_observation_includes_untracked_edits_modes_and_deletions(repo):
    before = native_run.capture_source(repo)
    (repo / "src/new.py").write_text("candidate = True\n")
    added = native_run.capture_source(repo)
    assert added["git_head"] == before["git_head"]
    assert added["files_sha256"] != before["files_sha256"]
    assert "src/new.py" in {f["path"] for f in added["files"]}
    (repo / "src/module.py").chmod(0o755)
    mode = native_run.capture_source(repo)
    assert mode["files_sha256"] != added["files_sha256"]
    (repo / "src/module.py").unlink()
    deleted = native_run.capture_source(repo)
    assert {"path": "src/module.py", "kind": "missing"} in deleted["files"]
    assert native_run.capture_source(repo)["files_sha256"] == deleted["files_sha256"]


def test_source_observation_rejects_external_symlink_without_reading_body(
    repo, tmp_path
):
    other = tmp_path / "unrelated.txt"
    other.write_text("not source")
    (repo / "src/escape.py").symlink_to(other)
    with pytest.raises(ValueError, match="escapes repository"):
        native_run.capture_source(repo)


def _offline_case(
    repo: Path, monkeypatch, *, outcome="passed", change=False, cleanup=True
):
    body = """import json
from pathlib import Path
import pytest

def test_native(record_property):
    record_property("native_cleanup", json.dumps({
        "simulation_done": True, "tmux_stopped": True,
        "lock_released": True, "owned_pids_gone": True,
    }))
"""
    if not cleanup:
        body = body.replace('"native_cleanup"', '"not_cleanup"')
    if change:
        body += '    (Path(__file__).parents[1] / "src/module.py").write_text("value = 2\\n")\n'
    if outcome == "failed":
        body += '    assert False, "offline failure"\n'
    if outcome == "skipped":
        body += '    pytest.skip("offline skip")\n'
    if outcome == "xpassed":
        body = body.replace(
            "def test_native",
            '@pytest.mark.xfail(strict=False, reason="offline")\ndef test_native',
        )
    if outcome == "timeout":
        body += "    import time\n    time.sleep(10)\n"
    (repo / "tests/test_offline.py").write_text(body)
    monkeypatch.setitem(
        native_run.NATIVE_NODES, "verify", "tests/test_offline.py::test_native"
    )
    monkeypatch.setenv("ZF_RUN_NATIVE_CLAUDE_PIPELINE", "1")
    monkeypatch.setenv("ZF_NATIVE_CLAUDE_PIPELINE_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)


@pytest.mark.parametrize(
    "outcome,changed,cleanup,verdict",
    [
        ("passed", False, True, "pass"),
        ("passed", True, True, "harness_failure"),
        ("passed", False, False, "harness_failure"),
        ("failed", False, True, "fail"),
        ("skipped", False, True, "fail"),
        ("xpassed", False, True, "fail"),
    ],
)
def test_native_run_uses_real_phase_runner_and_source_binding(
    repo, tmp_path, monkeypatch, outcome, changed, cleanup, verdict
):
    _offline_case(repo, monkeypatch, outcome=outcome, change=changed, cleanup=cleanup)
    output = tmp_path / "evidence"
    result = native_run.run_native_pipeline(
        project_root=repo, case="verify", output=output, timeout_seconds=70
    )
    assert result["verdict"] == verdict, result
    assert result["gate_exit"] == (verdict != "pass")
    assert result["source_stable_observed"] is not changed
    assert result["cleanup_complete"] is cleanup
    assert result["exact_selection"] is True
    report = json.loads((output / result["runner"]["ref"]).read_text())
    assert report["cases"][0]["status"] == outcome
    assert set(report["cases"][0]["phases"]) == {"setup", "call", "teardown"}
    for key in ("source_before", "source_after", "runner", "phases", "junit"):
        ref = result[key]
        assert (
            hashlib.sha256((output / ref["ref"]).read_bytes()).hexdigest()
            == ref["digest"]
        )
    assert json.loads((output / "native-run.json").read_text()) == result


@pytest.mark.parametrize(
    "damage", ["no_optin", "parallel_addopts", "short_budget", "nan", "in_source"]
)
def test_native_run_preconditions_never_start_pytest(
    repo, tmp_path, monkeypatch, damage
):
    _offline_case(repo, monkeypatch)
    monkeypatch.setattr(
        native_run, "run_pytest", lambda **kw: pytest.fail("unexpected pytest")
    )
    output, timeout = tmp_path / "out", 70
    if damage == "no_optin":
        monkeypatch.delenv("ZF_RUN_NATIVE_CLAUDE_PIPELINE")
    elif damage == "parallel_addopts":
        monkeypatch.setenv("PYTEST_ADDOPTS", "-n 2")
    elif damage == "short_budget":
        timeout = 60
    elif damage == "nan":
        timeout = float("nan")
    else:
        output = repo / "out"
    with pytest.raises(ValueError):
        native_run.run_native_pipeline(
            project_root=repo, case="verify", output=output, timeout_seconds=timeout
        )
    assert not output.exists()


def test_native_run_does_not_overwrite_evidence(repo, tmp_path, monkeypatch):
    _offline_case(repo, monkeypatch)
    output = tmp_path / "out"
    output.mkdir()
    sentinel = output / "native-run.json"
    sentinel.write_text("prior")
    with pytest.raises(FileExistsError):
        native_run.run_native_pipeline(
            project_root=repo, case="verify", output=output, timeout_seconds=70
        )
    assert sentinel.read_text() == "prior"


def test_native_cli_routes_one_case_and_preserves_failure(tmp_path, monkeypatch):
    from tests.testkit import __main__ as cli

    calls = []

    def run(**kwargs):
        calls.append(kwargs)
        return {"verdict": "fail", "gate_exit": 1}

    monkeypatch.setattr(native_run, "run_native_pipeline", run)
    monkeypatch.setattr(
        cli, "run_pytest", lambda **kw: pytest.fail("ordinary tier ran")
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "testkit",
            "--profile",
            "native-pipeline",
            "--native-case",
            "verify",
            "--output",
            str(tmp_path / "out"),
            "--timeout",
            "1500",
        ],
    )
    assert cli.main() == 1
    assert len(calls) == 1 and calls[0]["case"] == "verify"
    assert calls[0]["timeout_seconds"] == 1500


def test_native_cli_real_entry_rejects_implicit_provider_run(tmp_path):
    env = dict(os.environ)
    env.pop("ZF_RUN_NATIVE_CLAUDE_PIPELINE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.testkit",
            "--profile",
            "native-pipeline",
            "--native-case",
            "verify",
            "--output",
            str(tmp_path / "out"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1
    assert "requires ZF_RUN_NATIVE_CLAUDE_PIPELINE=1" in result.stderr
    assert not (tmp_path / "out").exists()


def test_real_offline_runner_report_is_consumed_by_source_bound_acceptance(
    repo, tmp_path, monkeypatch
):
    """Exercise real pytest/JUnit/phase serialization, with no Provider call."""
    from tests.testkit.native_acceptance import _bound_native
    from tests.testkit.test_native_acceptance import _xml

    props = {
        p.get("name"): p.get("value")
        for p in _xml("normal_native").findall(".//property")
    }
    props.pop("native_source")
    props["native_cleanup"] = json.dumps(
        {
            "simulation_done": True,
            "tmux_stopped": True,
            "lock_released": True,
            "owned_pids_gone": True,
        }
    )
    directory = repo / "tests/conformance"
    directory.mkdir()
    module = directory / "test_zaofu_testkit_s5_native_claude_pipeline.py"
    module.write_text(
        "import json\nfrom pathlib import Path\n"
        "from tests.testkit.native_run import capture_source\n"
        "def test_native_claude_start_dispatch_submit_verify_candidate(record_property):\n"
        f"    for name, value in {props!r}.items():\n"
        "        record_property(name, value)\n"
        "    source = capture_source(Path(__file__).resolve().parents[2])\n"
        "    record_property('native_source', json.dumps({\n"
        "        **{key: source[key] for key in ('git_head', 'files_sha256', 'scope')},\n"
        "        'stable_observed': True,\n"
        "    }))\n"
    )
    monkeypatch.setenv("ZF_RUN_NATIVE_CLAUDE_PIPELINE", "1")
    monkeypatch.setenv("ZF_NATIVE_CLAUDE_PIPELINE_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    output = tmp_path / "run"
    result = native_run.run_native_pipeline(
        project_root=repo, case="normal", output=output, timeout_seconds=70
    )
    assert result["verdict"] == "pass", result
    snapshots = tmp_path / "acceptance"
    snapshots.mkdir()
    path = output / "native-run.json"
    observed = _bound_native(path.read_bytes(), path, "normal_native", snapshots)
    assert observed["verdict"] == "pass" and observed["source_verified"] is True
