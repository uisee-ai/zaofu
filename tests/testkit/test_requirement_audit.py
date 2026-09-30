"""Real runner reports and adversarial bindings for the requirement audit CLI."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.testkit.evidence import evidence_ref, write_json_atomic
from tests.testkit.native_acceptance import NativeAcceptanceError
from tests.testkit.requirement_audit import build_requirement_audit
from tests.testkit.test_native_run import repo as repo
from tests.testkit.test_source_observation import _run
from tests.testkit.test_native_acceptance import _bind_native_inputs, inputs as inputs
from tests.testkit.native_acceptance import NATIVE_CASES, NATIVE_CLASS
from tests.testkit.test_premerge_run import _script
from tests.testkit.premerge_run import run_premerge


NODE = "tests/test_observation.py::test_observation"


@pytest.mark.parametrize("in_class", [False, True])
@pytest.mark.parametrize("damage", [None, "parameter", "class"])
def test_audit_binds_real_junit_with_node_separators_inside_parameters(
    repo, tmp_path, monkeypatch, in_class, damage
):
    from tests.testkit.runner import run_pytest
    import xml.etree.ElementTree as ET

    body = (
        '@pytest.mark.parametrize("value", [0, 1], ids=["plain", "Type::probe[x::y]"])\n'
        + f'def test_parameter({"self, " if in_class else ""}value):\n'
        + '    assert value in (0, 1)\n'
    )
    if in_class:
        body = 'class TestContainer:\n' + ''.join('    ' + line for line in body.splitlines(True))
    (repo / "tests/test_parameter.py").write_text('import pytest\n' + body)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    out = tmp_path / "run"
    result = run_pytest(project_root=repo, nodeids=("tests/test_parameter.py",),
                        output_dir=out, timeout_seconds=30, bind_source=True)
    assert result.verdict == "pass"
    path = out / "testkit-run.json"
    report = json.loads(path.read_text())
    if damage:
        junit = out / report["source_evidence"]["junit"]["ref"]
        tree = ET.parse(junit)
        case = next(c for c in tree.iter("testcase") if "Type::" in c.get("name", ""))
        if damage == "parameter":
            case.set("name", case.get("name").replace("Type::probe", "Other::probe"))
        else:
            case.set("classname", case.get("classname") + ".OtherClass")
        tree.write(junit, encoding="utf-8")
        report["source_evidence"]["junit"] = evidence_ref(junit, root=out)
        write_json_atomic(path, report)
    manifest = _plan(tmp_path, path)
    plan = json.loads(manifest.read_text())
    plan["requirements"][0]["nodeids"] = [c["nodeid"] for c in report["cases"]]
    write_json_atomic(manifest, plan)
    audit = build_requirement_audit(manifest=manifest, output=tmp_path / "audit")
    assert audit["verdict"] == ("harness_failure" if damage else "pass")
    if damage:
        assert "JUnit selection disagrees" in audit["requirements"][0]["reason"]


def _plan(tmp_path, report):
    path = tmp_path / "manifest.json"
    write_json_atomic(path, {
        "schema_version": "zf-requirement-audit-input.v1",
        "requirements": [{"id": "test_observation", "nodeids": [NODE], "report": {
            "ref": str(report), "sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
        }}],
    })
    return path


@pytest.mark.parametrize("fail", [False, True])
def test_audit_cli_consumes_real_source_bound_reports(repo, tmp_path, monkeypatch, fail):
    out, _ = _run(repo, tmp_path, monkeypatch, fail=fail)
    report = out / "testkit-run.json"
    manifest = _plan(tmp_path, report)
    audit_dir = tmp_path / "audit"
    completed = subprocess.run([
        sys.executable, "-m", "tests.testkit", "--profile", "requirement-audit",
        "--manifest", str(manifest), "--output", str(audit_dir),
    ], cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True, timeout=20)
    assert completed.returncode == int(fail), completed.stderr
    result = json.loads(completed.stdout)
    assert result["verdict"] == ("fail" if fail else "pass")
    assert result["same_revision_verified"] is True
    assert result["release_authorized"] is False
    assert (audit_dir / "test_observation/testkit-run.json").read_bytes() == report.read_bytes()
    for ref in result["requirements"][0]["bound_evidence"]:
        assert hashlib.sha256((audit_dir / ref["snapshot"]).read_bytes()).hexdigest() == ref["digest"]
    with pytest.raises(FileExistsError):
        build_requirement_audit(manifest=manifest, output=audit_dir)


@pytest.mark.parametrize("damage", ["digest", "wrong_node", "phase", "junit", "source", "escape"])
def test_audit_rejects_tampered_or_wrong_evidence(repo, tmp_path, monkeypatch, damage):
    out, _ = _run(repo, tmp_path, monkeypatch)
    report_path = out / "testkit-run.json"
    report = json.loads(report_path.read_text())
    refs = report["source_evidence"]
    if damage in {"phase", "junit", "source"}:
        key = {"phase": "phases", "junit": "junit", "source": "before"}[damage]
        attachment = out / refs[key]["ref"]
        if damage == "junit":
            attachment.write_text(attachment.read_text().replace("test_observation", "test_wrong"))
        else:
            payload = json.loads(attachment.read_text())
            if damage == "phase":
                payload["results"][NODE]["phases"]["call"]["outcome"] = "failed"
            else:
                payload["files_sha256"] = "0" * 64
            write_json_atomic(attachment, payload)
        refs[key] = evidence_ref(attachment, root=out)
    elif damage == "escape":
        refs["junit"]["ref"] = "../other.xml"
    write_json_atomic(report_path, report)
    manifest = _plan(tmp_path, report_path)
    plan = json.loads(manifest.read_text())
    if damage == "digest":
        plan["requirements"][0]["report"]["sha256"] = "0" * 64
    elif damage == "wrong_node":
        plan["requirements"][0]["nodeids"] = ["tests/other.py::test_other"]
    write_json_atomic(manifest, plan)
    result = build_requirement_audit(manifest=manifest, output=tmp_path / "audit")
    assert result["verdict"] == "harness_failure" and result["gate_exit"] == 1


def test_audit_missing_evidence_never_passes(tmp_path):
    manifest = tmp_path / "manifest.json"
    write_json_atomic(manifest, {"schema_version": "zf-requirement-audit-input.v1", "requirements": [
        {"id": "os_crash", "nodeids": [], "report": None, "reason": "isolated VM unavailable"},
    ]})
    result = build_requirement_audit(manifest=manifest, output=tmp_path / "audit")
    assert result["verdict"] == "incomplete" and result["gate_exit"] == 1
    assert result["same_revision_verified"] is False


def test_audit_same_head_different_sources_never_pass(repo, tmp_path, monkeypatch):
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    first, _ = _run(repo, first_dir, monkeypatch)
    (repo / "src/module.py").write_text("value = 2\n")
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second, _ = _run(repo, second_dir, monkeypatch)
    manifest = _plan(tmp_path, first / "testkit-run.json")
    plan = json.loads(manifest.read_text())
    path = second / "testkit-run.json"
    plan["requirements"].append({"id": "second", "nodeids": [NODE], "report": {
        "ref": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }})
    write_json_atomic(manifest, plan)
    result = build_requirement_audit(manifest=manifest, output=tmp_path / "audit")
    assert all(row["status"] == "pass" for row in result["requirements"])
    assert result["source_variant_count"] == 2
    assert result["verdict"] == "source_mismatch" and result["gate_exit"] == 1
    assert (tmp_path / "audit/input-manifest.json").read_bytes() == manifest.read_bytes()


@pytest.mark.parametrize("damage", ["duplicate", "empty", "escape_id", "source_bypass"])
def test_audit_rejects_invalid_manifest_before_creating_output(tmp_path, damage):
    row = {"id": "pending", "nodeids": [], "report": None, "reason": "not run"}
    rows = [row, row] if damage == "duplicate" else [] if damage == "empty" else [row]
    if damage == "escape_id":
        row["id"] = "../escape"
    elif damage == "source_bypass":
        row["require_source"] = False
    manifest = tmp_path / "manifest.json"
    write_json_atomic(manifest, {"schema_version": "zf-requirement-audit-input.v1", "requirements": rows})
    with pytest.raises(NativeAcceptanceError):
        build_requirement_audit(manifest=manifest, output=tmp_path / "audit")
    assert not (tmp_path / "audit").exists()


@pytest.mark.parametrize("role", list(NATIVE_CASES))
def test_audit_accepts_native_run_via_existing_role_validator(inputs, tmp_path, role):
    native = _bind_native_inputs(inputs)
    entry = next(row for row in native["evidence"] if row["role"] == role)
    manifest = tmp_path / "audit-input.json"
    write_json_atomic(manifest, {"schema_version": "zf-requirement-audit-input.v1", "requirements": [{
        "id": role, "format": "native-run", "role": role,
        "nodeids": [NATIVE_CLASS.replace(".", "/") + ".py::" + NATIVE_CASES[role]],
        "report": {key: entry[key] for key in ("ref", "sha256")},
    }]})
    result = build_requirement_audit(manifest=manifest, output=tmp_path / "audit")
    assert result["verdict"] == "pass" and result["same_revision_verified"]
    for ref in result["requirements"][0]["bound_evidence"]:
        assert hashlib.sha256((tmp_path / "audit" / ref["snapshot"]).read_bytes()).hexdigest() == ref["digest"]


def test_native_format_cannot_ignore_declared_wrong_node(inputs, tmp_path):
    native = _bind_native_inputs(inputs)
    entry = next(row for row in native["evidence"] if row["role"] == "normal_native")
    manifest = tmp_path / "audit-input.json"
    write_json_atomic(manifest, {"schema_version": "zf-requirement-audit-input.v1", "requirements": [{
        "id": "native", "format": "native-run", "role": "normal_native", "nodeids": [NODE],
        "report": {key: entry[key] for key in ("ref", "sha256")},
    }]})
    with pytest.raises(NativeAcceptanceError, match="node"):
        build_requirement_audit(manifest=manifest, output=tmp_path / "audit")


@pytest.mark.parametrize("failure", [False, True])
def test_premerge_shares_source_scope_and_preserves_full_script_red(repo, tmp_path, monkeypatch, failure):
    _script(repo, failure=failure)
    (repo / "docs").mkdir()
    (repo / "docs/probe.md").write_text("extra premerge scope\n")
    out, _ = _run(repo, tmp_path, monkeypatch)
    premerge = tmp_path / "premerge"
    run_premerge(project_root=repo, output=premerge, timeout_seconds=10)
    manifest = _plan(tmp_path, out / "testkit-run.json")
    plan = json.loads(manifest.read_text())
    path = premerge / "premerge-run.json"
    plan["requirements"].append({"id": "premerge", "format": "premerge-run", "role": "premerge",
        "nodeids": [], "report": {"ref": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}})
    write_json_atomic(manifest, plan)
    result = build_requirement_audit(manifest=manifest, output=tmp_path / "audit")
    assert result["same_revision_verified"] is True
    assert result["verdict"] == ("fail" if failure else "pass")
    assert result["requirements"][-1]["status"] == ("fail" if failure else "pass")


def test_archived_premerge_report_remains_bound_after_relocation(repo, tmp_path, monkeypatch):
    _script(repo, failure=True)
    source = tmp_path / "run"
    run_premerge(project_root=repo, output=source, timeout_seconds=10)
    archive = tmp_path / "archive"
    archive.mkdir()
    for name in ("premerge-run.json", "premerge-junit.xml", "source-before.json",
                 "source-after.json", "premerge.stdout.log", "premerge.stderr.log"):
        (archive / name).write_bytes((source / name).read_bytes())
    plan = tmp_path / "manifest.json"
    write_json_atomic(plan, {
        "schema_version": "zf-requirement-audit-input.v1",
        "requirements": [{
            "id": "archived-premerge", "format": "premerge-run", "role": "premerge",
            "nodeids": [], "report": {
                "ref": str(archive / "premerge-run.json"),
                "sha256": hashlib.sha256((archive / "premerge-run.json").read_bytes()).hexdigest(),
            },
        }],
    })
    result = build_requirement_audit(manifest=plan, output=tmp_path / "audit")
    assert result["verdict"] == "fail"
    assert result["requirements"][0]["status"] == "fail"


def test_mainline_format_does_not_shrink_current_registry(repo, tmp_path, monkeypatch):
    from tests.testkit import native_acceptance

    out, _ = _run(repo, tmp_path, monkeypatch)
    manifest = _plan(tmp_path, out / "testkit-run.json")
    plan = json.loads(manifest.read_text())
    plan["requirements"][0].update(format="mainline-run", nodeids=[])
    write_json_atomic(manifest, plan)
    monkeypatch.setattr(native_acceptance, "MAINLINE", (NODE, "tests/new.py::test_required"))
    result = build_requirement_audit(manifest=manifest, output=tmp_path / "audit")
    assert result["verdict"] == "harness_failure"
    assert "selection missing" in result["requirements"][0]["reason"]


@pytest.mark.parametrize("fail", [False, True])
def test_historical_mainline_is_source_bound_but_not_current_green(
    repo, tmp_path, monkeypatch, fail
):
    out, _ = _run(repo, tmp_path, monkeypatch, fail=fail)
    manifest = _plan(tmp_path, out / "testkit-run.json")
    plan = json.loads(manifest.read_text())
    plan["requirements"][0].update(
        format="mainline-run", selection="historical", nodeids=[NODE],
    )
    monkeypatch.setattr("tests.testkit.native_acceptance.MAINLINE", ("tests/new.py::test_required",))
    write_json_atomic(manifest, plan)
    result = build_requirement_audit(manifest=manifest, output=tmp_path / "audit")
    row = result["requirements"][0]
    assert row["status"] == "historical"
    assert row["observed_verdict"] == ("fail" if fail else "pass")
    assert result["verdict"] == ("fail" if fail else "incomplete")
    assert result["gate_exit"] == 1
    assert result["release_authorized"] is False
    assert result["same_revision_verified"] is True


def test_mainline_selection_field_cannot_be_used_by_other_formats(repo, tmp_path, monkeypatch):
    out, _ = _run(repo, tmp_path, monkeypatch)
    manifest = _plan(tmp_path, out / "testkit-run.json")
    plan = json.loads(manifest.read_text())
    plan["requirements"][0]["selection"] = "historical"
    write_json_atomic(manifest, plan)
    with pytest.raises(NativeAcceptanceError, match="selection"):
        build_requirement_audit(manifest=manifest, output=tmp_path / "audit")


@pytest.mark.parametrize("damage", ["wrong_node", "wrong_stage", "phase", "junit"])
def test_historical_mainline_rejects_selection_and_evidence_tamper(
    repo, tmp_path, monkeypatch, damage
):
    out, _ = _run(repo, tmp_path, monkeypatch)
    path = out / "testkit-run.json"
    report = json.loads(path.read_text())
    if damage == "wrong_stage":
        report["stage"] = "S0"
    elif damage in {"phase", "junit"}:
        key = "phases" if damage == "phase" else "junit"
        attachment = out / report["source_evidence"][key]["ref"]
        attachment.write_text(attachment.read_text().replace("test_observation", "test_other"))
        report["source_evidence"][key] = evidence_ref(attachment, root=out)
    write_json_atomic(path, report)
    manifest = _plan(tmp_path, path)
    plan = json.loads(manifest.read_text())
    plan["requirements"][0].update(
        format="mainline-run", selection="historical",
        nodeids=["tests/other.py::test_other"] if damage == "wrong_node" else [NODE],
    )
    write_json_atomic(manifest, plan)
    result = build_requirement_audit(manifest=manifest, output=tmp_path / "audit")
    assert result["verdict"] == "harness_failure"
    assert result["gate_exit"] == 1


@pytest.mark.parametrize("fail", [False, True])
def test_current_mainline_route_preserves_bound_verdict(repo, tmp_path, monkeypatch, fail):
    out, _ = _run(repo, tmp_path, monkeypatch, fail=fail)
    manifest = _plan(tmp_path, out / "testkit-run.json")
    plan = json.loads(manifest.read_text())
    plan["requirements"][0].update(format="mainline-run", nodeids=[])
    write_json_atomic(manifest, plan)
    result = build_requirement_audit(manifest=manifest, output=tmp_path / "audit")
    assert result["verdict"] == ("fail" if fail else "pass")
    assert result["requirements"][0]["selection"] == "current"


def test_historical_mainline_requires_explicit_nonempty_selection(tmp_path):
    manifest = tmp_path / "manifest.json"
    write_json_atomic(manifest, {
        "schema_version": "zf-requirement-audit-input.v1",
        "requirements": [{"id": "old", "format": "mainline-run",
                          "selection": "historical", "nodeids": [],
                          "reason": "not yet inventoried"}],
    })
    with pytest.raises(NativeAcceptanceError, match="selection snapshot"):
        build_requirement_audit(manifest=manifest, output=tmp_path / "audit")
    assert not (tmp_path / "audit").exists()


@pytest.mark.parametrize("other_format", ["browser", "premerge"])
@pytest.mark.parametrize("change_web", [False, True])
def test_common_backend_cannot_hide_conflicting_web_sources(
    repo, tmp_path, monkeypatch, other_format, change_web
):
    from tests.testkit import runner

    _script(repo)
    (repo / "web").mkdir()
    (repo / "web/probe.ts").write_text("before\n")
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    first, _ = _run(repo, first_dir, monkeypatch, source_profile="browser")
    if change_web:
        (repo / "web/probe.ts").write_text("after\n")
    second = tmp_path / "second"
    if other_format == "premerge":
        run_premerge(project_root=repo, output=second)
        path = second / "premerge-run.json"
        extra = {"format": "premerge-run", "role": "premerge", "nodeids": []}
    else:
        runner.run_pytest(project_root=repo, output_dir=second, nodeids=(NODE,),
                          bind_source=True, source_profile="browser")
        path = second / "testkit-run.json"
        extra = {"source_profile": "browser", "nodeids": [NODE]}
    manifest = _plan(tmp_path, first / "testkit-run.json")
    plan = json.loads(manifest.read_text())
    plan["requirements"][0]["source_profile"] = "browser"
    plan["requirements"].append({"id": "second", **extra, "report": {
        "ref": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }})
    write_json_atomic(manifest, plan)
    result = build_requirement_audit(manifest=manifest, output=tmp_path / "audit")
    assert all(row["status"] == "pass" for row in result["requirements"])
    assert result["source_variant_count"] == 1  # common backend remains identical
    assert result["source_scope_conflicts"] == (["web"] if change_web else [])
    assert result["same_revision_verified"] is not change_web
    assert result["verdict"] == ("source_mismatch" if change_web else "pass")
