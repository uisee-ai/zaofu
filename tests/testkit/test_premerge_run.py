import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import pytest

from tests.testkit import premerge_run
from tests.testkit import native_acceptance
from tests.testkit.evidence import evidence_ref
from tests.testkit.test_native_run import repo as repo
from tests.testkit.test_native_acceptance import _xml, inputs as inputs


def test_premerge_run_binds_source_and_preserves_script_exit(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "scripts").mkdir()
    xml = _xml("premerge")
    ET.SubElement(xml.find("./testcase"), "failure")
    xml.set("failures", "1")
    (root / "scripts/dev-premerge-gate.sh").write_text(
        "#!/usr/bin/env bash\nset -e\necho docs-ok\ncat >\"${1#--junitxml=}\" <<'EOF'\n"
        + ET.tostring(xml).decode()
        + "\nEOF\nexit 1\n"
    )
    (root / "scripts/dev-premerge-gate.sh").chmod(0o755)
    monkeypatch.setattr(
        premerge_run,
        "capture_source",
        lambda _, **kwargs: {
            "schema_version": "zf-testkit-source-observation.v1",
            "git_head": "a" * 40,
            "files_sha256": "b" * 64,
            "scope": [],
            "files": [{"path": "scripts/dev-premerge-gate.sh"}],
        },
    )
    result = premerge_run.run_premerge(project_root=root, output=tmp_path / "out")
    assert result["verdict"] == "fail" and result["gate_exit"] == 1
    assert result["exit_code"] == 1 and result["source_stable_observed"] is True
    assert json.loads((tmp_path / "out/premerge-run.json").read_text()) == result


def test_premerge_run_rejects_checkout_output_without_starting(monkeypatch, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("started"))
    with pytest.raises(ValueError, match="outside the source"):
        premerge_run.run_premerge(project_root=root, output=root / "out")


def _script(repo, *, failure=False, early=False, change=False, timeout=False):
    (repo / "scripts").mkdir()
    xml = _xml("premerge")
    if failure:
        ET.SubElement(xml.find("./testcase"), "failure")
        xml.set("failures", "1")
    body = "#!/usr/bin/env bash\nset -eu\necho docs-check\n"
    if early:
        body += "echo docs-failed >&2\nexit 1\n"
    elif timeout:
        body += "exec sleep 10\n"
    else:
        body += (
            "cat >\"${1#--junitxml=}\" <<'EOF'\n"
            + ET.tostring(xml).decode()
            + "\nEOF\n"
        )
        if change:
            body += "echo changed > src/module.py\n"
        body += f"exit {int(failure)}\n"
    (repo / "scripts/dev-premerge-gate.sh").write_text(body)


@pytest.mark.parametrize(
    "mode,verdict",
    [
        ("pass", "pass"),
        ("failure", "fail"),
        ("early", "fail"),
        ("change", "harness_failure"),
        ("timeout", "harness_failure"),
    ],
)
def test_premerge_real_script_and_aggregation(repo, tmp_path, inputs, mode, verdict):
    _script(repo, **({mode: True} if mode != "pass" else {}))
    out = tmp_path / "gate"
    result = premerge_run.run_premerge(
        project_root=repo, output=out, timeout_seconds=0.1 if mode == "timeout" else 10
    )
    assert result["verdict"] == verdict
    assert result["gate_exit"] == int(verdict != "pass")
    assert result["timed_out"] is (mode == "timeout")
    assert result["source_stable_observed"] is (mode != "change")
    assert (result["junit"] is None) is (mode in {"early", "timeout"})
    plan = json.loads(inputs.read_text())
    path = out / "premerge-run.json"
    entry = next(e for e in plan["evidence"] if e["role"] == "premerge")
    entry.update(
        format="premerge-run",
        ref=str(path),
        sha256=evidence_ref(path, root=out)["digest"],
    )
    inputs.write_text(json.dumps(plan))
    aggregated = native_acceptance.build_native_acceptance(
        manifest=inputs, output=tmp_path / "aggregate"
    )
    assert aggregated["verdict"] == verdict
    gate = next(r for r in aggregated["results"] if r["role"] == "premerge")
    assert gate.get("source_verified", False) is (mode not in {"change", "timeout"})
    assert aggregated["same_revision_verified"] is False
    if mode == "early":
        assert gate["pytest_observed"] is False
    with pytest.raises(FileExistsError):
        premerge_run.run_premerge(project_root=repo, output=out)


def test_premerge_cli_calls_registered_runner(monkeypatch, tmp_path, capsys):
    import sys
    from tests.testkit.__main__ import main

    calls = []

    def run(**kwargs):
        calls.append(kwargs)
        return {"gate_exit": 1, "verdict": "fail"}

    monkeypatch.setattr(premerge_run, "run_premerge", run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "testkit",
            "--profile",
            "premerge-pipeline",
            "--output",
            str(tmp_path / "out"),
        ],
    )
    assert main() == 1
    assert calls == [
        {
            "project_root": Path(__file__).resolve().parents[2],
            "output": tmp_path / "out",
            "timeout_seconds": 120,
        }
    ]
    assert json.loads(capsys.readouterr().out)["verdict"] == "fail"


@pytest.mark.parametrize("damage", ["skip", "malformed", "missing"])
def test_zero_exit_never_hides_incomplete_pytest_evidence(repo, tmp_path, damage):
    _script(repo)
    script = repo / "scripts/dev-premerge-gate.sh"
    body = script.read_text()
    if damage == "skip":
        body = body.replace('skipped="0"', 'skipped="1"', 1)
        body = body.replace(" />", "><skipped/></testcase>", 1)
    elif damage == "malformed":
        body = body.replace("<testsuite", "<broken", 1)
    else:
        body = "#!/usr/bin/env bash\nexit 0\n"
    script.write_text(body)
    result = premerge_run.run_premerge(project_root=repo, output=tmp_path / "gate")
    assert result["exit_code"] == 0
    assert result["verdict"] == ("fail" if damage == "skip" else "harness_failure")
    assert result["gate_exit"] == 1


@pytest.mark.parametrize("damage", ["digest", "escape", "command", "verdict"])
def test_bound_premerge_rejects_tampered_evidence(repo, tmp_path, damage):
    _script(repo)
    out = tmp_path / "gate"
    report = premerge_run.run_premerge(project_root=repo, output=out)
    if damage == "digest":
        report["stdout"]["digest"] = "0" * 64
    elif damage == "escape":
        report["stdout"]["ref"] = "../unrelated.log"
    elif damage == "command":
        report["command"][1] = "other-script.sh"
    else:
        report["gate_exit"] = 1
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    with pytest.raises(native_acceptance.NativeAcceptanceError):
        native_acceptance._bound_premerge(
            json.dumps(report).encode(), out / "premerge-run.json", snapshots
        )
