"""Aggregation contracts use generated reports, never Provider or user state."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from tests.testkit.native_acceptance import (
    MAINLINE,
    NATIVE_CASES,
    NATIVE_CLASS,
    PREMERGE_CLASSES,
    ROLES,
    NativeAcceptanceError,
    build_native_acceptance,
)


def _xml(role: str) -> ET.Element:
    suite = ET.Element("testsuite", tests="1", failures="0", errors="0", skipped="0")
    if role == "premerge":
        suite.set("tests", str(len(PREMERGE_CLASSES)))
        for classname in sorted(PREMERGE_CLASSES):
            ET.SubElement(suite, "testcase", classname=classname, name="test_contract")
        return suite
    case = ET.SubElement(
        suite, "testcase", classname=NATIVE_CLASS, name=NATIVE_CASES[role]
    )
    stage = role.split("_", 1)[0]
    stages = (
        ["impl", "verify"]
        if stage == "normal"
        else ["impl", "impl", "verify"]
        if stage == "impl"
        else ["impl", "verify", "verify"]
    )
    props = {
        "native_outcome": {
            "reason": "candidate_ready",
            "budget_expired": False,
            "blocker_types": [],
            "candidate_count": 1,
            "dispatched_stages": stages,
        },
        # PID 1 deliberately exists: historical cleanup must not consult /proc.
        "native_processes": {"session": "test-session", "observed_pids": [1]},
        "native_source": {
            "stable_observed": True,
            "git_head": "a" * 40,
            "files_sha256": "b" * 64,
            "scope": ["src"],
        },
    }
    for item in ("impl", "verify"):
        props[f"native_{item}"] = {
            "session_id": item,
            "attempt_id": f"new-{item}",
            "tool_names": ["Read", "Bash"],
            "binding": {"status": "archived", "stage": item, "session_id": item},
        }
    if role != "normal_native":
        props["native_recovery"] = {
            "stage": stage,
            "fault": "owned-provider-SIGKILL",
            "assistant_count": 2,
            "old_attempt_status": "superseded",
            "new_attempt_status": "succeeded",
            "attempt_id": "old",
            "new_attempt_id": f"new-{stage}",
            "request_id": "request",
            "completed_id": "completed",
            "redrive_id": "redrive",
        }
    properties = ET.SubElement(case, "properties")
    for name, value in props.items():
        ET.SubElement(properties, "property", name=name, value=json.dumps(value))
    return suite


def _mainline() -> dict:
    return {
        "stage": "MAINLINE-DETERMINISTIC",
        "verdict": "pass",
        "exit_code": 0,
        "timed_out": False,
        "session_finished": True,
        "collection_errors": [],
        "deselected": [],
        "evidence_errors": [],
        "git_head": "test-head",
        "git_dirty": True,
        "cases": [
            {
                "nodeid": node if "::" in node else node + "::test_contract",
                "status": "passed",
                "phases": {
                    phase: {"outcome": "passed"}
                    for phase in ("setup", "call", "teardown")
                },
            }
            for node in MAINLINE
        ],
    }


@pytest.fixture
def inputs(tmp_path):
    for role in ROLES:
        path = tmp_path / role
        path.write_bytes(
            json.dumps(_mainline()).encode()
            if role == "deterministic_mainline"
            else ET.tostring(_xml(role))
        )
    return _pin(tmp_path)


def _pin(directory: Path) -> Path:
    manifest = directory / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "zf-native-acceptance-input.v1",
                "evidence": [
                    {
                        "role": role,
                        "ref": role,
                        "sha256": hashlib.sha256(
                            (directory / role).read_bytes()
                        ).hexdigest(),
                    }
                    for role in ROLES
                ],
            }
        )
    )
    return manifest


def _run(inputs: Path) -> dict:
    return build_native_acceptance(manifest=inputs, output=inputs.parent / "out")


def _property(path: Path, name: str, mutate) -> None:
    tree = ET.parse(path)
    prop = tree.find(f".//property[@name='{name}']")
    prop.set("value", json.dumps(mutate(json.loads(prop.get("value")))))
    tree.write(path)


def test_native_acceptance_preserves_snapshots_and_scope(inputs):
    result = _run(inputs)
    assert result["verdict"] == "pass" and result["gate_exit"] == 0
    assert (
        result["same_revision_verified"] is False
        and result["release_authorized"] is False
    )
    for row in result["results"]:
        assert row["verdict"] == "pass"
        copied = (inputs.parent / "out" / row["snapshot"]).read_bytes()
        assert copied == Path(row["ref"]).read_bytes()
        assert hashlib.sha256(copied).hexdigest() == row["expected_sha256"]


@pytest.mark.parametrize(
    "classname", ["tests.test_structure_discipline", "tests.test_event_contracts"]
)
@pytest.mark.parametrize("tag", ["failure", "error", "skipped"])
def test_premerge_any_red_is_preserved_never_waived(inputs, classname, tag):
    path = inputs.parent / "premerge"
    tree = ET.parse(path)
    ET.SubElement(tree.find(f"./testcase[@classname='{classname}']"), tag)
    tree.getroot().set(
        {"failure": "failures", "error": "errors", "skipped": "skipped"}[tag], "1"
    )
    tree.write(path)
    _pin(inputs.parent)
    result = _run(inputs)
    assert result["verdict"] == "fail" and result["gate_exit"] == 1
    [premerge] = [r for r in result["results"] if r["role"] == "premerge"]
    assert len(premerge["nonpassing"]) == 1
    assert bool(premerge["structure_failures"]) == (
        classname == "tests.test_structure_discipline"
    )


@pytest.mark.parametrize(
    "change",
    [
        "wrong_stage",
        "missing_property",
        "bad_outcome",
        "old_attempt",
        "live_binding",
        "same_session",
        "duplicate_property",
        "wrong_case",
    ],
)
def test_native_claims_fail_closed(inputs, change):
    path = inputs.parent / "verify_recovery"
    if change in {"wrong_stage", "old_attempt"}:
        field, value = (
            ("stage", "impl")
            if change == "wrong_stage"
            else ("old_attempt_status", "sent")
        )
        _property(path, "native_recovery", lambda p: p | {field: value})
    elif change == "bad_outcome":
        _property(path, "native_outcome", lambda p: p | {"candidate_count": 0})
    elif change == "live_binding":
        _property(
            path,
            "native_verify",
            lambda p: p | {"binding": p["binding"] | {"status": "active"}},
        )
    elif change == "same_session":
        _property(
            path,
            "native_verify",
            lambda p: (
                p
                | {
                    "session_id": "impl",
                    "binding": p["binding"] | {"session_id": "impl"},
                }
            ),
        )
    else:
        tree = ET.parse(path)
        props = tree.find("./testcase/properties")
        if change == "missing_property":
            props.remove(props[0])
        elif change == "duplicate_property":
            ET.SubElement(props, "property", **props[0].attrib)
        else:
            tree.find("./testcase").set("name", NATIVE_CASES["impl_recovery"])
        tree.write(path)
    _pin(inputs.parent)
    result = _run(inputs)
    assert result["verdict"] == "harness_failure"
    assert [r["role"] for r in result["results"] if r["verdict"] != "pass"] == [
        "verify_recovery"
    ]


@pytest.mark.parametrize(
    "change", ["digest", "missing", "malformed", "count_mismatch", "extra_suite"]
)
def test_invalid_or_missing_junit_is_not_green(inputs, change):
    path = inputs.parent / "normal_native"
    if change == "missing":
        path.unlink()
    elif change in {"digest", "malformed"}:
        path.write_text("invalid")
        if change == "malformed":
            _pin(inputs.parent)
    else:
        tree = ET.parse(path)
        if change == "count_mismatch":
            tree.getroot().set("tests", "2")
        else:
            root = ET.Element("testsuites")
            root.extend([tree.getroot(), _xml("impl_recovery")])
            tree = ET.ElementTree(root)
        tree.write(path)
        _pin(inputs.parent)
    assert _run(inputs)["verdict"] == "harness_failure"


@pytest.mark.parametrize(
    "change",
    ["xfail", "missing_phase", "incomplete", "omitted_selection", "wrong_profile"],
)
def test_mainline_report_cannot_hide_missing_or_nonpassed_cases(inputs, change):
    path = inputs.parent / "deterministic_mainline"
    report = json.loads(path.read_text())
    if change == "xfail":
        report["cases"][0]["status"] = "xfail"
    elif change == "missing_phase":
        report["cases"][0]["phases"].pop("teardown")
    elif change == "omitted_selection":
        report["cases"].pop()
    elif change == "wrong_profile":
        report["stage"] = "S0"
    else:
        report["session_finished"] = False
    path.write_text(json.dumps(report))
    _pin(inputs.parent)
    result = _run(inputs)
    assert result["verdict"] == ("fail" if change == "xfail" else "harness_failure")


def test_manifest_requires_all_roles_and_rejects_overwrite(inputs):
    _run(inputs)
    before = (inputs.parent / "out/native-acceptance.json").read_bytes()
    with pytest.raises(FileExistsError):
        _run(inputs)
    assert (inputs.parent / "out/native-acceptance.json").read_bytes() == before
    plan = json.loads(inputs.read_text())
    plan["evidence"].pop()
    inputs.write_text(json.dumps(plan))
    with pytest.raises(NativeAcceptanceError, match="five evidence"):
        _run(inputs)


@pytest.mark.parametrize("broken", [False, True])
def test_native_acceptance_cli_real_entrypoint(inputs, broken):
    if broken:
        (inputs.parent / "verify_recovery").write_text("changed bytes")
    command = [
        sys.executable,
        "-m",
        "tests.testkit",
        "--profile",
        "native-acceptance",
        "--manifest",
        str(inputs),
        "--output",
        str(inputs.parent / "out"),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=20,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert completed.returncode == int(broken), completed.stderr
    result = json.loads(completed.stdout)
    assert result["verdict"] == ("harness_failure" if broken else "pass")
    assert (
        json.loads((inputs.parent / "out/native-acceptance.json").read_text()) == result
    )


def test_native_acceptance_cli_never_runs_provider_or_pytest(inputs, monkeypatch):
    from tests.testkit import __main__ as cli

    monkeypatch.setattr(
        cli, "run_pytest", lambda **kw: pytest.fail("aggregation started pytest")
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "testkit",
            "--profile",
            "native-acceptance",
            "--manifest",
            str(inputs),
            "--output",
            str(inputs.parent / "out"),
        ],
    )
    assert cli.main() == 0


def _bind_native_inputs(inputs: Path) -> dict:
    """Generated source/phase fixtures; these are not real Provider evidence."""
    from tests.testkit.evidence import evidence_ref, write_json_atomic
    from tests.testkit.native_run import SOURCE_SCOPE

    plan = json.loads(inputs.read_text())
    for entry in plan["evidence"]:
        role = entry["role"]
        if role not in NATIVE_CASES:
            continue
        directory = inputs.parent / (role + "-bound")
        directory.mkdir()
        files = [{"path": "src/example.py", "sha256": "a" * 64, "mode": 420}]
        source = {
            "schema_version": "zf-testkit-source-observation.v1",
            "git_head": "a" * 40,
            "scope": list(SOURCE_SCOPE),
            "files": files,
            "files_sha256": hashlib.sha256(
                json.dumps(files, sort_keys=True, ensure_ascii=True).encode()
            ).hexdigest(),
        }
        observation = {
            key: source[key] for key in ("git_head", "files_sha256", "scope")
        }
        observation["stable_observed"] = True
        junit = _xml(role)
        props = junit.find("./testcase/properties")
        props.find("./property[@name='native_source']").set(
            "value", json.dumps(observation)
        )
        ET.SubElement(
            props,
            "property",
            name="native_cleanup",
            value=json.dumps(
                {
                    "simulation_done": True,
                    "tmux_stopped": True,
                    "lock_released": True,
                    "owned_pids_gone": True,
                }
            ),
        )
        (directory / "junit.xml").write_bytes(ET.tostring(junit))
        node = NATIVE_CLASS.replace(".", "/") + ".py::" + NATIVE_CASES[role]
        case = {
            "nodeid": node,
            "status": "passed",
            "phases": {
                phase: {"outcome": "passed"} for phase in ("setup", "call", "teardown")
            },
        }
        runner = _mainline() | {
            "stage": "NATIVE-PIPELINE",
            "git_head": source["git_head"],
            "cases": [case],
        }
        phases = {
            "schema_version": "zf-testkit-pytest.v1",
            "session_finished": True,
            "session_exitstatus": 0,
            "items": [node],
            "results": {node: case},
            "collection_errors": [],
            "deselected": [],
        }
        run = {
            "schema_version": "zf-native-run.v1",
            "nodeid": node,
            "case": "normal" if role == "normal_native" else role.split("_")[0],
            "source_stable_observed": True,
            "verdict": "pass",
            "pytest_exit_code": 0,
            "gate_exit": 0,
            "cleanup_complete": True,
            "exact_selection": True,
            "session_finished": True,
            "timed_out": False,
        }
        for key, body in (
            ("source_before", source),
            ("source_after", source),
            ("runner", runner),
            ("phases", phases),
        ):
            write_json_atomic(directory / (key + ".json"), body)
            run[key] = evidence_ref(directory / (key + ".json"), root=directory)
        run["junit"] = evidence_ref(directory / "junit.xml", root=directory)
        write_json_atomic(directory / "native-run.json", run)
        entry.update(
            format="native-run",
            ref=str(directory / "native-run.json"),
            sha256=hashlib.sha256(
                (directory / "native-run.json").read_bytes()
            ).hexdigest(),
        )
    inputs.write_text(json.dumps(plan))
    return plan


def _mutate_bound(inputs, key, mutate):
    from tests.testkit.evidence import evidence_ref, write_json_atomic

    plan = json.loads(inputs.read_text())
    entry = next(e for e in plan["evidence"] if e["role"] == "impl_recovery")
    path = Path(entry["ref"])
    run = json.loads(path.read_text())
    if key == "run":
        mutate(run)
    else:
        target = path.parent / run[key]["ref"]
        body = json.loads(target.read_text())
        mutate(body)
        write_json_atomic(target, body)
        run[key] = evidence_ref(target, root=path.parent)
    write_json_atomic(path, run)
    entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    inputs.write_text(json.dumps(plan))


def test_bound_native_strict_source_and_snapshots(inputs):
    _bind_native_inputs(inputs)
    result = build_native_acceptance(
        manifest=inputs, output=inputs.parent / "out", require_source=True
    )
    assert result["verdict"] == "pass", result
    assert result["native_same_source_verified"] is True
    assert result["same_revision_verified"] is False  # mainline/premerge remain unbound
    for row in result["results"][:3]:
        assert row["source_verified"] is True
        for ref in row["bound_evidence"]:
            assert (inputs.parent / "out" / ref["snapshot"]).read_bytes() == Path(
                ref["ref"]
            ).read_bytes()


@pytest.mark.parametrize(
    "key,field,value",
    [
        ("source_after", "git_head", "b" * 40),
        ("source_before", "files_sha256", "b" * 64),
        ("runner", "git_head", "b" * 40),
        ("runner", "exit_code", False),
        ("phases", "session_exitstatus", 1),
        ("phases", "deselected", ["not-run"]),
        ("run", "session_finished", False),
        ("run", "timed_out", True),
        ("run", "cleanup_complete", False),
    ],
)
def test_bound_native_rejects_inconsistent_source_and_execution(
    inputs, key, field, value
):
    _bind_native_inputs(inputs)
    _mutate_bound(inputs, key, lambda body: body.update({field: value}))
    result = build_native_acceptance(
        manifest=inputs, output=inputs.parent / "out", require_source=True
    )
    assert result["verdict"] == "harness_failure", result


@pytest.mark.parametrize("verdict", ["fail", "harness_failure"])
def test_bound_native_preserves_failure_category_in_source_mode(inputs, verdict):
    _bind_native_inputs(inputs)
    _mutate_bound(
        inputs, "runner", lambda body: body.update(verdict=verdict, exit_code=1)
    )
    _mutate_bound(inputs, "phases", lambda body: body.update(session_exitstatus=1))
    _mutate_bound(
        inputs,
        "run",
        lambda body: body.update(verdict=verdict, gate_exit=1, pytest_exit_code=1),
    )
    result = build_native_acceptance(
        manifest=inputs, output=inputs.parent / "out", require_source=True
    )
    assert result["verdict"] == verdict, result
    row = next(r for r in result["results"] if r["role"] == "impl_recovery")
    assert row["verdict"] == verdict
    assert row["source_verified"] is True


def test_strict_source_rejects_legacy_junit_without_backfilling(inputs):
    result = build_native_acceptance(
        manifest=inputs, output=inputs.parent / "out", require_source=True
    )
    assert result["verdict"] == "harness_failure"
    assert result["native_same_source_verified"] is False
