"""Subprocess runner and fail-closed verdict for the ZaoFu TestKit."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import hashlib
import shutil
from collections.abc import Mapping
from typing import Any, Iterable

from .cleanup import terminate_process_group
from .case_evidence import validate_case_evidence
from .ordinary_recovery import recover_unbound_snapshots
from .evidence import evidence_ref, sha256_file, sha256_text, write_json_atomic
from .failure_bundle import write_failure_bundle
from .parallel import merge_worker_reports
from .suites import nodeids_for_stage
from .source_observation import SOURCE_PROFILES, capture_source
from .memray_guard import CaptureBudget, preflight_budget, wait_for_capture


@dataclass(frozen=True)
class RunnerResult:
    stage: str
    command: tuple[str, ...]
    command_digest: str
    project_root: str
    git_head: str
    git_dirty: bool
    output_dir: str
    exit_code: int | None
    timed_out: bool
    duration_seconds: float
    report_path: str
    junit_path: str
    stdout_path: str
    stderr_path: str
    config_path: str
    config_digest: str
    lock_path: str
    lock_digest: str
    failure_bundle_path: str = ""
    failure_bundle_error: str = ""
    cases: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    collection_errors: tuple[dict[str, str], ...] = field(default_factory=tuple)
    deselected: tuple[str, ...] = field(default_factory=tuple)
    verdict: str = "harness_failure"
    startup_error: str = ""
    session_finished: bool = False
    evidence_errors: tuple[str, ...] = field(default_factory=tuple)
    ordinary_diagnostics: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    source_evidence: dict[str, Any] = field(default_factory=dict)
    capture_guard: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "command": list(self.command),
            "cases": list(self.cases),
            "collection_errors": list(self.collection_errors),
            "deselected": list(self.deselected),
        }


def _terminate(process: subprocess.Popen[str]) -> None:
    terminate_process_group(process, timeout=3.0)


def _load_report(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _result_records(report: dict[str, Any] | None) -> Mapping[str, Mapping[str, Any]] | None:
    """Return validated case records, or ``None`` for malformed evidence."""

    if report is None:
        return None
    results = report.get("results")
    if not isinstance(results, Mapping):
        return None
    if any(not isinstance(item, Mapping) for item in results.values()):
        return None
    return results


def _relocate_worker_evidence(report: dict[str, Any], output: Path) -> None:
    """Move worker-owned ordinary snapshots under the parent case namespace."""
    for case in (report.get("results") or {}).values():
        if not isinstance(case, dict):
            continue
        ordinary = case.get("ordinary_evidence")
        if not isinstance(ordinary, dict):
            continue
        nodeid = case.get("nodeid")
        snapshots = ordinary.get("snapshots")
        if not isinstance(nodeid, str) or not isinstance(snapshots, dict):
            continue
        directory = output / "case-evidence" / hashlib.sha256(nodeid.encode()).hexdigest()
        for point, snapshot in snapshots.items():
            source = Path(snapshot.get("path", "")) if isinstance(snapshot, dict) else Path()
            if not source.is_file():
                raise ValueError(f"worker ordinary snapshot missing: {source}")
            destination = directory / f"{point}.json"
            directory.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ValueError(f"duplicate ordinary snapshot: {destination}")
            shutil.copyfile(source, destination)
            if sha256_file(destination) != snapshot.get("digest"):
                raise ValueError(f"worker ordinary snapshot digest mismatch: {source}")
            snapshot["path"] = str(destination)


def _git_identity(root: Path) -> tuple[str, bool]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    return head, dirty


def _file_identity(root: Path, name: str) -> tuple[str, str]:
    """Return an optional project file path and digest for run evidence."""
    path = root / name
    if not path.is_file():
        return "", ""
    resolved = path.resolve()
    return str(resolved), sha256_file(resolved)


def _verdict(
    *,
    report: dict[str, Any] | None,
    exit_code: int | None,
    timed_out: bool,
    git_head: str,
) -> str:
    records = _result_records(report)
    if (
        timed_out
        or not git_head
        or report is None
        or not report.get("session_finished")
        or records is None
    ):
        return "harness_failure"
    if report.get("collection_errors") or report.get("deselected"):
        return "fail"
    for item in records.values():
        if "ordinary_evidence" in item:
            evidence = item["ordinary_evidence"]
            if not isinstance(evidence, Mapping) or evidence.get("errors"):
                return "harness_failure"
    statuses = {str(item.get("status")) for item in records.values()}
    bad = {"failed", "error", "xfailed", "xpassed", "flaky_pass", "skipped", "not_run"}
    if exit_code != 0 or statuses & bad:
        return "fail"
    return "pass" if statuses and statuses <= {"passed"} else "harness_failure"


def run_pytest(
    *,
    project_root: Path,
    stage: str = "all",
    output_dir: Path,
    timeout_seconds: float = 900,
    nodeids: Iterable[str] | None = None,
    xdist_workers: int | None = None,
    bind_source: bool = False,
    source_profile: str = "backend",
    retain_case_artifacts: bool = False,
    capture_budget: CaptureBudget | None = None,
) -> RunnerResult:
    """Run an explicit suite in a fresh process and return a closed verdict."""
    if source_profile not in SOURCE_PROFILES or (source_profile != "backend" and not bind_source):
        raise ValueError("source profile requires a known scope and source binding")
    if capture_budget is not None:
        preflight_budget(capture_budget)
    root = project_root.resolve()
    try:
        git_head, git_dirty = _git_identity(root)
    except (OSError, subprocess.CalledProcessError):
        git_head, git_dirty = "", False
    output = output_dir.resolve()
    if bind_source and output.is_relative_to(root):
        raise ValueError("source-bound evidence must be outside the source checkout")
    config_path, config_digest = _file_identity(root, "zf.yaml")
    lock_path, lock_digest = _file_identity(root, "uv.lock")
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "pytest-testkit.json"
    junit_path = output / "pytest-junit.xml"
    stdout_path = output / "pytest.stdout.log"
    stderr_path = output / "pytest.stderr.log"
    selected = tuple(nodeids) if nodeids is not None else nodeids_for_stage(stage)
    if not selected:
        raise ValueError("TestKit suite must contain at least one test path")
    claim_path = output / ".testkit-run.claim"
    try:
        claim_fd = os.open(
            claim_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError as exc:
        raise FileExistsError(f"TestKit output directory is already claimed: {output}") from exc
    try:
        os.write(claim_fd, f"stage={stage.upper()}\n".encode("ascii"))
    finally:
        os.close(claim_fd)
    for path in (
        report_path,
        junit_path,
        stdout_path,
        stderr_path,
        output / "testkit-run.json",
        output / "model-evidence",
        output / "case-evidence",
        *((output / "case-work",) if retain_case_artifacts else ()),
        *(
            (output / "source-before.json", output / "source-after.json")
            if bind_source
            else ()
        ),
        *((output / "capture-budget.json", output / "capture-guard.json")
          if capture_budget is not None else ()),
    ):
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"TestKit evidence already exists: {path}")
    command = (
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "tests.testkit.pytest_plugin",
        "-o",
        "addopts=",
        "-q",
        f"--junitxml={junit_path}",
        f"--testkit-report={report_path}",
        *((f"--basetemp={output / 'case-work'}",) if retain_case_artifacts else ()),
        *(("-n", str(xdist_workers)) if xdist_workers is not None else ()),
        *selected,
    )
    env = os.environ.copy()
    # Do not leak an enclosing xdist worker's identity into a nested runner.
    # An explicit xdist invocation creates fresh worker identities itself;
    # inherited values would redirect a single-process child report and make
    # the parent read the wrong path.
    for name in ("PYTEST_XDIST_WORKER", "PYTEST_XDIST_TESTRUNUID", "PYTEST_XDIST_WORKER_COUNT"):
        env.pop(name, None)
    env["ZF_TESTKIT_MODEL_EVIDENCE"] = str(output / "model-evidence")
    current_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(root), current_pythonpath) if item
    )
    source_evidence: dict[str, Any] = {}
    guard_result: dict[str, Any] = {}
    guard_evidence: dict[str, Any] = {}
    before = None
    if bind_source:
        # Fail before starting pytest if the initial provenance is unavailable.
        before = capture_source(root, scope=SOURCE_PROFILES[source_profile])
        write_json_atomic(output / "source-before.json", before)
        source_evidence["before"] = evidence_ref(
            output / "source-before.json", root=output
        )
    started = time.monotonic()
    if capture_budget is not None:
        write_json_atomic(output / "capture-budget.json", capture_budget.policy())
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        process: subprocess.Popen[str] | None = None
        startup_error = ""
        try:
            process = subprocess.Popen(
                command,
                cwd=root,
                env=env,
                stdout=stdout,
                stderr=stderr,
                start_new_session=(os.name == "posix"),
                text=True,
            )
        except OSError as exc:
            # Failure to start pytest is a harness/environment failure, not a
            # product-test failure. Keep it in the normal evidence files so
            # callers receive a closed result rather than an unstructured
            # exception.
            startup_error = f"{type(exc).__name__}: {exc}"
            stderr.write(startup_error + "\n")
        timed_out = False
        if process is not None:
            try:
                if capture_budget is None:
                    process.wait(timeout=timeout_seconds)
                else:
                    guard_result = wait_for_capture(
                        process, budget=capture_budget, timeout_seconds=timeout_seconds,
                    )
                    timed_out = (guard_result.get("violation") or {}).get("kind") == "timeout"
                    write_json_atomic(output / "capture-guard.json", guard_result)
                    guard_evidence = {
                        "policy": evidence_ref(output / "capture-budget.json", root=output),
                        "result": evidence_ref(output / "capture-guard.json", root=output),
                    }
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                # A crashed or normally exited pytest leader may leave owned
                # descendants alive. Timeout is not the only cleanup boundary.
                _terminate(process)
    duration = round(time.monotonic() - started, 6)
    if xdist_workers is not None:
        worker_reports = sorted((output / "workers").glob("*/*/pytest-testkit.json"))
        try:
            payloads = [_load_report(path) for path in worker_reports]
            if not payloads or any(payload is None for payload in payloads):
                raise ValueError("xdist worker report missing or malformed")
            for payload in payloads:
                _relocate_worker_evidence(payload, output)
            write_json_atomic(report_path, merge_worker_reports(payloads))
        except (OSError, ValueError) as exc:
            write_json_atomic(report_path, {
                "results": {}, "session_finished": False,
                "collection_errors": [{"nodeid": "xdist", "longrepr": str(exc)}],
            })
    report = _load_report(report_path)
    verdict = _verdict(
        report=report,
        exit_code=process.returncode if process is not None else None,
        timed_out=timed_out,
        git_head=git_head,
    )
    session_finished = bool(report and report.get("session_finished"))
    records = _result_records(report) or {}
    cases = tuple(
        sorted(
            (dict(case) for case in records.values()),
            key=lambda item: str(item.get("nodeid", "")),
        )
    )
    ordinary_paths, evidence_errors = validate_case_evidence(output, cases)
    if guard_result.get("violation"):
        evidence_errors.append(f"capture guard rejected run: {guard_result['violation']}")
    diagnostics, recovery_errors = recover_unbound_snapshots(output, cases, ordinary_paths)
    evidence_errors.extend(recovery_errors)
    if bind_source:
        try:
            after = capture_source(root, scope=SOURCE_PROFILES[source_profile])
            write_json_atomic(output / "source-after.json", after)
            source_evidence["after"] = evidence_ref(
                output / "source-after.json", root=output
            )
            source_evidence["stable_observed"] = (
                before["git_head"] == after["git_head"] == git_head
                and before["files_sha256"] == after["files_sha256"]
            )
            if not source_evidence["stable_observed"]:
                evidence_errors.append("source changed during pytest execution")
            for name, path in (("phases", report_path), ("junit", junit_path)):
                if path.is_file():
                    source_evidence[name] = evidence_ref(path, root=output)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            evidence_errors.append(
                f"source observation failed: {type(exc).__name__}: {exc}"
            )
    if evidence_errors or diagnostics:
        verdict = "harness_failure"
    result = RunnerResult(
        stage=stage.upper(),
        command=command,
        command_digest=sha256_text("\0".join(command)),
        project_root=str(root),
        git_head=git_head,
        git_dirty=git_dirty,
        output_dir=str(output),
        exit_code=process.returncode if process is not None else None,
        timed_out=timed_out,
        duration_seconds=duration,
        report_path=str(report_path),
        junit_path=str(junit_path),
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        config_path=config_path,
        config_digest=config_digest,
        lock_path=lock_path,
        lock_digest=lock_digest,
        cases=cases,
        collection_errors=tuple((report or {}).get("collection_errors", ())),
        deselected=tuple((report or {}).get("deselected", ())),
        verdict=verdict,
        startup_error=startup_error,
        session_finished=session_finished,
        evidence_errors=tuple(evidence_errors),
        ordinary_diagnostics=tuple(diagnostics),
        source_evidence=source_evidence,
        capture_guard=guard_evidence,
    )
    if result.verdict != "pass":
        bundle_dir = output / "failure-bundle"
        bundle_metadata = {
            "stage": result.stage,
            "command": list(result.command),
            "command_digest": result.command_digest,
            "project_root": result.project_root,
            "git_head": result.git_head,
            "git_dirty": result.git_dirty,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "session_finished": result.session_finished,
            "verdict_before_bundle": result.verdict,
            "collection_errors": list(result.collection_errors),
            "deselected": list(result.deselected),
            "startup_error": result.startup_error,
            "config_path": result.config_path,
            "config_digest": result.config_digest,
            "lock_path": result.lock_path,
            "lock_digest": result.lock_digest,
            "evidence_errors": list(result.evidence_errors),
            "ordinary_diagnostics": list(result.ordinary_diagnostics),
        }
        evidence_paths = tuple(
            path
            for path in (
                report_path, junit_path, stdout_path, stderr_path,
                *sorted((output / "model-evidence").glob("*/*.json")),
                *ordinary_paths,
                *((output / "capture-budget.json", output / "capture-guard.json")
                  if capture_budget is not None else ()),
                *(Path(row["path"]) for row in diagnostics),
            )
            if path.is_file()
        )
        try:
            write_failure_bundle(
                bundle_dir,
                metadata=bundle_metadata,
                evidence_paths=evidence_paths,
            )
            result = replace(result, failure_bundle_path=str(bundle_dir))
        except Exception as exc:  # noqa: BLE001
            result = replace(
                result,
                verdict="harness_failure",
                failure_bundle_error=f"{type(exc).__name__}: {exc}",
            )
    write_json_atomic(output / "testkit-run.json", result.to_dict())
    return result
