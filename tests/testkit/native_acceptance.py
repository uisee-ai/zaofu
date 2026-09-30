"""Digest-pinned observations from separate runs, not release eligibility.

Legacy JUnit cannot prove source identity or distinguish non-strict XPASS.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from typing import Any

from .evidence import write_json_atomic
from .suites import MAINLINE


NATIVE_CLASS = "tests.conformance.test_zaofu_testkit_s5_native_claude_pipeline"
NATIVE_CASES = {
    "normal_native": "test_native_claude_start_dispatch_submit_verify_candidate",
    "impl_recovery": "test_native_claude_interrupted_stage_recovers_to_candidate[impl]",
    "verify_recovery": "test_native_claude_interrupted_stage_recovers_to_candidate[verify]",
}
ROLES = (*NATIVE_CASES, "deterministic_mainline", "premerge")
PREMERGE_CLASSES = {
    "tests.test_event_contracts",
    "tests.test_manual_docs",
    "tests.test_registry_forcing_closure",
    "tests.test_structure_discipline",
    "tests.test_workflow_spine_projection",
}


class NativeAcceptanceError(ValueError):
    """Evidence cannot support a reported observation."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise NativeAcceptanceError(message)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json(data: bytes | str) -> Any:
    return json.loads(data, object_pairs_hook=_unique_object)


def _junit(data: bytes) -> tuple[list[ET.Element], list[str]]:
    _require(b"<!DOCTYPE" not in data.upper(), "JUnit DTD is unsupported")
    root = ET.fromstring(data)
    suites = [root] if root.tag == "testsuite" else root.findall("./testsuite")
    _require(len(suites) == 1, "expected exactly one JUnit testsuite")
    suite = suites[0]
    cases = suite.findall("./testcase")
    _require(bool(cases), "empty JUnit suite")
    _require(int(suite.attrib["tests"]) == len(cases), "JUnit test count mismatch")
    for count, tag in (
        ("failures", "failure"),
        ("errors", "error"),
        ("skipped", "skipped"),
    ):
        _require(
            int(suite.attrib[count]) == len(suite.findall(f"./testcase/{tag}")),
            f"JUnit {count} count mismatch",
        )
    identities = [(c.get("classname"), c.get("name")) for c in cases]
    _require(len(set(identities)) == len(cases), "duplicate JUnit testcase")
    nonpassing = [
        f"{c.get('classname')}::{c.get('name')}"
        for c in cases
        if any(c.find(tag) is not None for tag in ("failure", "error", "skipped"))
    ]
    return cases, nonpassing


def _native(data: bytes, role: str) -> dict[str, Any]:
    cases, nonpassing = _junit(data)
    _require(len(cases) == 1, "native input must contain exactly one testcase")
    case = cases[0]
    _require(
        case.get("classname") == NATIVE_CLASS
        and case.get("name") == NATIVE_CASES[role],
        "wrong native testcase identity/stage",
    )
    if nonpassing:
        return {"verdict": "fail", "nonpassing": nonpassing}
    properties = _unique_object(
        [
            (p.attrib["name"], p.attrib["value"])
            for p in case.findall("./properties/property")
        ]
    )
    props = {
        key: _json(properties[key])
        for key in (
            "native_outcome",
            "native_impl",
            "native_verify",
            "native_processes",
        )
    }
    _require(
        all(isinstance(value, dict) for value in props.values()),
        "native properties must be objects",
    )
    source = (
        _json(properties["native_source"]) if "native_source" in properties else None
    )
    outcome = props["native_outcome"]
    stages = {
        "normal_native": ["impl", "verify"],
        "impl_recovery": ["impl", "impl", "verify"],
        "verify_recovery": ["impl", "verify", "verify"],
    }
    _require(
        outcome.get("reason") == "candidate_ready"
        and outcome.get("budget_expired") is False
        and outcome.get("blocker_types") == []
        and type(outcome.get("candidate_count")) is int
        and outcome["candidate_count"] == 1
        and outcome.get("dispatched_stages") == stages[role],
        "native outcome is not terminal-green",
    )
    sessions = []
    for stage in ("impl", "verify"):
        result = props[f"native_{stage}"]
        binding = result["binding"]
        _require(
            binding["status"] == "archived"
            and binding["stage"] == stage
            and result["session_id"] == binding["session_id"]
            and bool(result["session_id"])
            and {"Read", "Bash"} <= set(result["tool_names"]),
            "stage session proof incomplete",
        )
        sessions.append(result["session_id"])
    _require(len(set(sessions)) == 2, "stage sessions are not independent")
    processes = props["native_processes"]
    _require(
        bool(processes["session"]) and bool(processes["observed_pids"]),
        "process proof missing",
    )
    # Historical PIDs can be reused. Cleanup is the original testcase's oracle,
    # not a new /proc inspection or mutation of retained Provider transcripts.
    if role != "normal_native":
        stage = role.split("_", 1)[0]
        recovery = _json(properties["native_recovery"])
        _require(
            recovery["stage"] == stage
            and recovery["fault"] == "owned-provider-SIGKILL"
            and recovery["old_attempt_status"] == "superseded"
            and recovery["new_attempt_status"] == "succeeded"
            and recovery["attempt_id"] != recovery["new_attempt_id"]
            and recovery["new_attempt_id"] == props[f"native_{stage}"]["attempt_id"]
            and type(recovery["assistant_count"]) is int
            and recovery["assistant_count"] > 0
            and len(
                {
                    recovery["request_id"],
                    recovery["completed_id"],
                    recovery["redrive_id"],
                }
            )
            == 3,
            "recovery stage/attempt proof incomplete",
        )
    return {
        "verdict": "pass",
        "testcase": NATIVE_CASES[role],
        "native_outcome": outcome,
        "cleanup_basis": "original complete testcase, not current PID lookup",
        "source_observation": source,
    }


def _mainline(
    data: bytes, *, expected_nodes: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    report = _json(data)
    if expected_nodes is None:
        _require(report["stage"] == "MAINLINE-DETERMINISTIC", "wrong deterministic profile")
    _require(report["session_finished"] is True, "incomplete mainline session")
    _require(
        report["timed_out"] is False
        and not report["collection_errors"]
        and not report["evidence_errors"]
        and not report["deselected"],
        "mainline evidence incomplete",
    )
    cases = report["cases"]
    _require(isinstance(cases, list) and bool(cases), "empty mainline cases")
    nodes = [c["nodeid"] for c in cases]
    _require(len(nodes) == len(set(nodes)), "duplicate mainline case")
    if expected_nodes is not None:
        _require(set(nodes) == set(expected_nodes), "exact requirement selection disagrees")
    missing = [
        entry
        for entry in (MAINLINE if expected_nodes is None else expected_nodes)
        if not any(
            node == entry
            or node.startswith(entry + "::")
            or node.startswith(entry + "[")
            for node in nodes
        )
    ]
    _require(not missing, f"mainline selection missing: {missing}")
    nonpassing = [c["nodeid"] for c in cases if c["status"] != "passed"]
    for case in cases:
        if case["status"] == "passed":
            phases = case["phases"]
            _require(
                set(phases) == {"setup", "call", "teardown"}
                and all(
                    p["outcome"] == "passed" and not p.get("wasxfail")
                    for p in phases.values()
                ),
                "passed mainline case has incomplete/nonpassed phases",
            )
    verdict = (
        "pass"
        if not nonpassing
        and type(report["exit_code"]) is int
        and report["exit_code"] == 0
        and report["verdict"] == "pass"
        else "fail"
    )
    return {
        "verdict": verdict,
        "nonpassing": nonpassing,
        "case_count": len(cases),
        "git_head": report["git_head"],
        "git_dirty": report["git_dirty"],
    }


def _premerge(data: bytes) -> dict[str, Any]:
    cases, nonpassing = _junit(data)
    _require(
        PREMERGE_CLASSES <= {c.get("classname") for c in cases},
        "premerge selection incomplete",
    )
    # Structure debt is classified for diagnosis only: it never changes fail to pass.
    return {
        "verdict": "fail" if nonpassing else "pass",
        "nonpassing": nonpassing,
        "structure_failures": [
            node
            for node in nonpassing
            if node.startswith("tests.test_structure_discipline::")
        ],
        "case_count": len(cases),
    }


def _read_bound(
    refs_by_key: dict, path: Path, role: str, output: Path
) -> tuple[dict, list]:
    bodies, refs = {}, []
    for key, ref in refs_by_key.items():
        target = (path.parent / ref["ref"]).resolve()
        _require(
            not Path(ref["ref"]).is_absolute() and target.is_relative_to(path.parent),
            "bound evidence escapes run directory",
        )
        content = target.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        _require(digest == ref["digest"], "bound evidence digest mismatch")
        suffix = (
            ".xml"
            if key == "junit"
            else ".log"
            if key in {"stdout", "stderr"}
            else ".json"
        )
        snapshot = output / f"{role}-{key}{suffix}"
        snapshot.write_bytes(content)
        refs.append({"ref": str(target), "digest": digest, "snapshot": snapshot.name})
        bodies[key] = (
            content if key in {"junit", "stdout", "stderr"} else _json(content)
        )
    return bodies, refs


def _validate_source_pair(
    before: dict, after: dict, *, scope: tuple | None = None
) -> None:
    from .source_observation import SOURCE_SCOPE

    for source in (before, after):
        _require(
            source["schema_version"] == "zf-testkit-source-observation.v1"
            and source["scope"] == list(SOURCE_SCOPE if scope is None else scope)
            and bool(source["files"]),
            "native source inventory incomplete",
        )
        digest = hashlib.sha256(
            json.dumps(source["files"], sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()
        _require(
            digest == source["files_sha256"], "native source inventory hash mismatch"
        )
    _require(
        before["git_head"] == after["git_head"]
        and before["files_sha256"] == after["files_sha256"],
        "native source changed during execution",
    )


def _bound_mainline(
    data: bytes, path: Path, output: Path, *, expected_nodes: tuple[str, ...] | None = None,
    source_profile: str = "backend",
) -> dict[str, Any]:
    from .source_observation import SOURCE_PROFILES, SOURCE_SCOPE, scope_digests, scope_observation

    result = _mainline(data, expected_nodes=expected_nodes)
    run = _json(data)
    source = run["source_evidence"]
    bodies, refs = _read_bound(
        {key: source[key] for key in ("before", "after", "phases", "junit")},
        path,
        "deterministic_mainline",
        output,
    )
    before, after = bodies["before"], bodies["after"]
    _validate_source_pair(before, after, scope=SOURCE_PROFILES[source_profile])
    _require(
        source["stable_observed"] is True and run["git_head"] == before["git_head"],
        "mainline source observation disagrees with runner",
    )
    phases = bodies["phases"]
    cases = run["cases"]
    _require(
        phases["schema_version"] == "zf-testkit-pytest.v1"
        and phases["session_finished"] is True
        and not phases["collection_errors"]
        and not phases["deselected"]
        and sorted(phases["results"].values(), key=lambda c: c["nodeid"])
        == sorted(cases, key=lambda c: c["nodeid"])
        and sorted(phases["items"]) == sorted(c["nodeid"] for c in cases)
        and type(phases["session_exitstatus"]) is int
        and type(run["exit_code"]) is int
        and phases["session_exitstatus"] == run["exit_code"],
        "mainline phase/selection/exit evidence disagrees",
    )
    junit, nonpassing = _junit(bodies["junit"])
    identities = set()
    for case in cases:
        # Match pytest's JUnit mapping: parameter text may contain ``::``.
        head, separator, parameters = case["nodeid"].partition("[")
        parts = head.split("::")
        name = parts[-1] + separator + parameters
        identities.add(
            (".".join([parts[0][:-3].replace("/", "."), *parts[1:-1]]), name)
        )
    _require(
        identities == {(c.get("classname"), c.get("name")) for c in junit},
        "mainline JUnit selection disagrees",
    )
    _require(
        result["verdict"] != "pass" or not nonpassing, "mainline JUnit contradicts pass"
    )
    return result | {
        "source_verified": True,
        "bound_evidence": refs,
        "scope_digests": scope_digests(before),
        "shared_source_observation": scope_observation(before, SOURCE_SCOPE),
        "source_observation": {
            key: before[key] for key in ("git_head", "files_sha256", "scope")
        },
    }


def _bound_premerge(data: bytes, path: Path, output: Path) -> dict[str, Any]:
    from .premerge_run import PREMERGE_SCOPE
    from .source_observation import SOURCE_SCOPE, scope_digests

    run = _json(data)
    _require(run["schema_version"] == "zf-premerge-run.v1", "unknown premerge schema")
    command = run["command"]
    # The report is often archived and read back from a different directory.
    # Bind the complete script and the JUnit filename, not the transient
    # absolute output directory embedded in the original command.
    _require(
        isinstance(command, list)
        and len(command) == 3
        and command[:2] == ["bash", "scripts/dev-premerge-gate.sh"]
        and isinstance(command[2], str)
        and command[2].startswith("--junitxml=")
        and Path(command[2].split("=", 1)[1]).name == "premerge-junit.xml",
        "premerge is not the complete registered script",
    )
    keys = ["source_before", "source_after", "stdout", "stderr"]
    if run.get("junit") is not None:
        keys.append("junit")
    bodies, refs = _read_bound(
        {key: run[key] for key in keys}, path, "premerge", output
    )
    before, after = bodies["source_before"], bodies["source_after"]
    _validate_source_pair(before, after, scope=PREMERGE_SCOPE)
    _require(
        run["source_stable_observed"] is True
        and run["timed_out"] is False
        and not run.get("error")
        and type(run["exit_code"]) is int,
        "premerge execution/source incomplete",
    )
    if "junit" in bodies:
        result = _premerge(bodies["junit"])
        expected = (
            "fail" if run["exit_code"] != 0 or result["verdict"] != "pass" else "pass"
        )
    else:
        _require(run["exit_code"] != 0, "premerge success has no pytest evidence")
        result = {
            "case_count": 0,
            "nonpassing": [],
            "pytest_observed": False,
            "reason": "script failed before producing pytest evidence",
        }
        expected = "fail"
    _require(
        run["verdict"] == expected
        and type(run["gate_exit"]) is int
        and run["gate_exit"] == int(expected != "pass"),
        "premerge verdict/exit disagree",
    )
    common_files = [
        row
        for row in before["files"]
        if any(
            row["path"] == prefix or row["path"].startswith(prefix + "/")
            for prefix in SOURCE_SCOPE
        )
    ]
    common_digest = hashlib.sha256(
        json.dumps(common_files, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()
    return result | {
        "verdict": expected,
        "source_verified": True,
        "bound_evidence": refs,
        "scope_digests": scope_digests(before),
        "source_observation": {
            key: before[key] for key in ("git_head", "files_sha256", "scope")
        },
        "shared_source_observation": {
            "git_head": before["git_head"],
            "files_sha256": common_digest,
            "scope": list(SOURCE_SCOPE),
        },
    }


def _bound_native(data: bytes, path: Path, role: str, output: Path) -> dict[str, Any]:
    run = _json(data)
    _require(run["schema_version"] == "zf-native-run.v1", "unknown native run schema")
    nodeid = NATIVE_CLASS.replace(".", "/") + ".py::" + NATIVE_CASES[role]
    case = "normal" if role == "normal_native" else role.split("_", 1)[0]
    _require(
        run["nodeid"] == nodeid and run["case"] == case, "native run selection mismatch"
    )
    bodies, refs = _read_bound(
        {
            key: run[key]
            for key in ("source_before", "source_after", "runner", "phases", "junit")
        },
        path,
        role,
        output,
    )
    before, after = bodies["source_before"], bodies["source_after"]
    _validate_source_pair(before, after)
    _require(
        run["source_stable_observed"] is True, "native source changed during execution"
    )
    # Source provenance is independent of the test verdict: a sourced red run is
    # still a test failure, not missing evidence. Do not require terminal-green
    # native properties before extracting the finally-recorded observation.
    result = _native(bodies["junit"], role)
    source_props = ET.fromstring(bodies["junit"]).findall(
        ".//testcase/properties/property[@name='native_source']"
    )
    _require(len(source_props) == 1, "testcase source proof missing")
    observation = _json(source_props[0].attrib["value"])
    _require(
        isinstance(observation, dict)
        and observation.get("stable_observed") is True
        and all(
            observation.get(k) == before[k]
            for k in ("git_head", "files_sha256", "scope")
        ),
        "testcase source differs from runner source",
    )
    provenance = {
        "source_verified": True,
        "bound_evidence": refs,
        "source_observation": observation,
    }
    runner, phases = bodies["runner"], bodies["phases"]
    _require(
        runner["stage"] == "NATIVE-PIPELINE"
        and runner["session_finished"] is True
        and runner["git_head"] == before["git_head"]
        and run["session_finished"] is True
        and run["timed_out"] is False
        and runner["timed_out"] is False
        and not runner["deselected"]
        and not runner["collection_errors"]
        and not runner["evidence_errors"]
        and phases["schema_version"] == "zf-testkit-pytest.v1"
        and not phases["deselected"]
        and not phases["collection_errors"]
        and phases["session_finished"] is True
        and phases["items"] == [nodeid]
        and set(phases["results"]) == {nodeid}
        and runner["cases"] == [phases["results"][nodeid]],
        "native phases/selection incomplete",
    )
    _require(
        all(
            type(code) is int
            for code in (
                runner["exit_code"],
                phases["session_exitstatus"],
                run["pytest_exit_code"],
                run["gate_exit"],
            )
        ),
        "native exit codes must be integers",
    )
    _require(
        runner["exit_code"] == phases["session_exitstatus"] == run["pytest_exit_code"],
        "native exit codes disagree",
    )
    if runner["verdict"] != "pass" or run["verdict"] != "pass":
        _require(
            runner["verdict"] in {"pass", "fail", "harness_failure"}
            and run["verdict"] in {"fail", "harness_failure"}
            and run["gate_exit"] == 1,
            "native failure verdict inconsistent",
        )
        verdict = (
            "harness_failure"
            if "harness_failure" in {runner["verdict"], run["verdict"]}
            else "fail"
        )
        return {
            "verdict": verdict,
            "runner_verdict": runner["verdict"],
            "pytest_exit_code": runner["exit_code"],
            **provenance,
        }
    phase_values = phases["results"][nodeid]["phases"]
    _require(
        runner["exit_code"] == 0
        and phases["session_exitstatus"] == 0
        and run["pytest_exit_code"] == 0
        and run["gate_exit"] == 0
        and run["cleanup_complete"] is True
        and run["exact_selection"] is True
        and phases["results"][nodeid]["status"] == "passed"
        and set(phase_values) == {"setup", "call", "teardown"}
        and all(
            p["outcome"] == "passed" and not p.get("wasxfail")
            for p in phase_values.values()
        ),
        "native run cannot claim phase-complete pass",
    )
    cleanup = ET.fromstring(bodies["junit"]).findall(
        ".//testcase/properties/property[@name='native_cleanup']"
    )
    _require(
        len(cleanup) == 1
        and _json(cleanup[0].attrib["value"])
        == {
            "simulation_done": True,
            "tmux_stopped": True,
            "lock_released": True,
            "owned_pids_gone": True,
        },
        "native cleanup proof missing",
    )
    return result | provenance


def build_native_acceptance(
    *, manifest: Path, output: Path, require_source: bool = False
) -> dict[str, Any]:
    """Check five pinned inputs; preserve all red/missing results in a new report."""
    manifest_bytes = manifest.read_bytes()
    plan = _json(manifest_bytes)
    _require(
        plan["schema_version"] == "zf-native-acceptance-input.v1",
        "unknown manifest schema",
    )
    entries = plan["evidence"]
    _require(
        isinstance(entries, list) and len(entries) == len(ROLES),
        "five evidence roles required",
    )
    _require(
        {entry["role"] for entry in entries} == set(ROLES),
        "missing/duplicate evidence roles",
    )
    # Never overwrite evidence, a prior verdict, or an existing directory.
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for entry in entries:
        role = entry["role"]
        result = {
            "role": role,
            "ref": entry.get("ref"),
            "expected_sha256": entry.get("sha256"),
        }
        try:
            _require(
                isinstance(entry["ref"], str) and bool(entry["ref"]),
                "empty evidence ref",
            )
            path = (manifest.parent / entry["ref"]).resolve()
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            result.update(ref=str(path), sha256=digest)
            _require(digest == entry["sha256"], "evidence digest mismatch")
            bound = entry.get("format") == "native-run"
            premerge_bound = entry.get("format") == "premerge-run"
            _require(
                not bound or role in NATIVE_CASES,
                "native-run format requires a native role",
            )
            _require(
                entry.get("format") in (None, "native-run", "premerge-run"),
                "unknown evidence format",
            )
            _require(
                not premerge_bound or role == "premerge",
                "premerge-run requires premerge role",
            )
            _require(
                "source_profile" not in entry or (
                    role == "deterministic_mainline"
                    and entry["source_profile"] in {"backend", "acceptance"}
                    and bool(_json(data).get("source_evidence"))
                ),
                "source profile requires a bound deterministic mainline",
            )
            suffix = (
                ".json"
                if role == "deterministic_mainline" or bound or premerge_bound
                else ".xml"
            )
            snapshot = output / (role + suffix)
            snapshot.write_bytes(data)
            result["snapshot"] = snapshot.name
            result.update(
                _bound_premerge(data, path, output)
                if premerge_bound
                else _bound_native(data, path, role, output)
                if bound
                else _native(data, role)
                if role in NATIVE_CASES
                else _bound_mainline(data, path, output,
                                    source_profile=entry.get("source_profile", "backend"))
                if role == "deterministic_mainline"
                and _json(data).get("source_evidence")
                else _mainline(data)
                if role == "deterministic_mainline"
                else _premerge(data)
            )
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            ET.ParseError,
        ) as exc:
            result.update(
                verdict="harness_failure", error=f"{type(exc).__name__}: {exc}"
            )
        results.append(result)
    statuses = {r["verdict"] for r in results}
    native_results = [
        row
        for row in results
        if row["role"] in NATIVE_CASES and row.get("source_verified")
    ]
    observations = [row.get("source_observation") for row in native_results]
    same_source = (
        len(observations) == 3
        and len({(item["git_head"], item["files_sha256"]) for item in observations})
        == 1
    )
    if require_source and not same_source:
        statuses.add("harness_failure")
    mainline = next(row for row in results if row["role"] == "deterministic_mainline")
    mainline_source = mainline.get("shared_source_observation", mainline.get("source_observation", {}))
    mainline_same_source = bool(
        same_source
        and mainline.get("source_verified")
        and all(
            mainline_source.get(key) == observations[0][key]
            for key in ("git_head", "files_sha256")
        )
    )
    premerge = next(row for row in results if row["role"] == "premerge")
    premerge_common = premerge.get("shared_source_observation", {})
    all_same_source = bool(
        mainline_same_source
        and premerge.get("source_verified")
        and all(
            premerge_common.get(key) == mainline_source[key]
            for key in ("git_head", "files_sha256")
        )
    )
    verdict = (
        "harness_failure"
        if "harness_failure" in statuses
        else "fail"
        if "fail" in statuses
        else "pass"
    )
    payload = {
        "schema_version": "zf-native-acceptance.v1",
        "verdict": verdict,
        "gate_exit": 0 if verdict == "pass" else 1,
        "scope": "reported observations from separate native runs; not release acceptance",
        "native_same_source_verified": same_source,
        "native_mainline_same_source_verified": mainline_same_source,
        "require_source": require_source,
        "same_revision_verified": all_same_source,
        "release_authorized": False,
        "limitations": [
            "native source observation covers configured source paths, not installed dependency bytes",
            "legacy native JUnit without bound source and phase proof is insufficient in require-source mode",
            "legacy mainline/premerge lack source binding; same revision compares shared source scope, premerge also observes web/docs",
            "Plan/Run admission is Given; only lease clock is accelerated",
            "no OS crash, network fault, remote delivery or overall completion proof",
        ],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "results": results,
    }
    write_json_atomic(output / "native-acceptance.json", payload)
    return payload
