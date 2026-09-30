"""Manifest-driven command execution for generated product projects."""

from __future__ import annotations

from dataclasses import dataclass
import copy
from pathlib import Path
import subprocess
from typing import Iterable, Mapping

from tests.testkit.command_runner import CommandReceipt, run_command

from .manifest import ProductManifest, load_manifest, validate_manifest
from .planner import commands_for_domains, require_capability_decisions, require_domain_coverage, select_test_domains
from .schemathesis import run_schemathesis_contract
from .browser import run_playwright_contract
from .service import ServiceError, ServiceHandle, ServiceReceipt, start_entrypoint
from .containers import ContainerError, ContainerHandle, ContainerReceipt, start_dependency_container
from .mocks import MockServiceHandle, MockServiceReceipt, start_mock_service
from .k6 import run_k6_contract
from .release import run_release_artifact_checks, run_release_scenarios
from .security_scan import run_zap_contract
from .recovery import run_recovery_scenarios
from .concurrency import run_concurrency_scenarios
from .persistence import run_persistence_scenarios


class ProductHarnessError(RuntimeError):
    """The product test harness cannot safely execute a plan."""


@dataclass(frozen=True)
class ProductRun:
    receipts: tuple[CommandReceipt, ...]
    failed_command_id: str = ""
    domains: tuple[dict[str, object], ...] = ()
    planned_command_ids: tuple[str, ...] | None = None
    services: tuple[ServiceReceipt, ...] = ()
    failed_entrypoint_id: str = ""
    harness_errors: tuple[dict[str, str], ...] = ()
    api_contracts: tuple[dict[str, object], ...] = ()
    failed_api_contract_id: str = ""
    browser_tests: tuple[dict[str, object], ...] = ()
    failed_browser_test_id: str = ""
    containers: tuple[ContainerReceipt, ...] = ()
    failed_container_id: str = ""
    container_environment_error: bool = False
    container_error_message: str = ""
    mock_services: tuple[MockServiceReceipt, ...] = ()
    failed_mock_service_id: str = ""
    performance_tests: tuple[dict[str, object], ...] = ()
    failed_performance_test_id: str = ""
    release_artifacts: tuple[dict[str, object], ...] = ()
    failed_release_artifact_id: str = ""
    security_scans: tuple[dict[str, object], ...] = ()
    failed_security_scan_id: str = ""
    release_scenarios: tuple[dict[str, object], ...] = ()
    failed_release_scenario_id: str = ""
    recovery_scenarios: tuple[dict[str, object], ...] = ()
    failed_recovery_scenario_id: str = ""
    concurrency_scenarios: tuple[dict[str, object], ...] = ()
    failed_concurrency_scenario_id: str = ""
    persistence_scenarios: tuple[dict[str, object], ...] = ()
    failed_persistence_scenario_id: str = ""

    @property
    def passed(self) -> bool:
        return (
            not self.failed_command_id
            and not self.failed_entrypoint_id
            and not self.failed_api_contract_id
            and not self.failed_browser_test_id
            and not self.failed_container_id
            and not self.failed_mock_service_id
            and not self.failed_performance_test_id
            and not self.failed_release_artifact_id
            and not self.failed_security_scan_id
            and not self.failed_release_scenario_id
            and not self.failed_recovery_scenario_id
            and not self.failed_concurrency_scenario_id
            and not self.harness_errors
            and all(item.passed for item in self.receipts)
            and all(item.passed for item in self.services)
            and all(item.passed for item in self.containers)
            and all(item.passed for item in self.mock_services)
            and all(item.get("verdict") == "pass" or not item.get("mandatory", True) for item in self.performance_tests)
            and all(bool(item.get("passed")) or not item.get("mandatory", True) for item in self.release_artifacts)
            and all(item.get("verdict") == "pass" or not item.get("mandatory", True) for item in self.security_scans)
            and all(bool(item.get("passed")) or not item.get("mandatory", True) for item in self.release_scenarios)
            and all(bool(item.get("passed")) or not item.get("mandatory", True) for item in self.recovery_scenarios)
            and all(bool(item.get("passed")) or not item.get("mandatory", True) for item in self.concurrency_scenarios)
            and all(bool(item.get("passed")) or not item.get("mandatory", True) for item in self.persistence_scenarios)
            and all(item.get("verdict") == "pass" or not item.get("mandatory", True) for item in self.api_contracts)
            and all(item.get("verdict") == "pass" or not item.get("mandatory", True) for item in self.browser_tests)
        )


def verify_target_commit(
    project_root: Path,
    target_commit: str,
    *,
    allowed_untracked: Iterable[Path] = (),
    check_untracked: bool = True,
) -> None:
    """Reject a run if the worktree is not the manifest's immutable target.

    ``HEAD`` alone is insufficient as an identity check: a tracked file can
    be modified (or staged) after the commit was resolved while retaining the
    same ``HEAD``.  Product evidence would then describe bytes that are not
    represented by ``target_commit``.  A manifest may be supplied as an
    untracked sidecar, but arbitrary untracked product files are rejected
    before setup; otherwise a command could execute bytes not represented by
    ``target_commit``.
    """

    def resolve_commit(revision: str) -> str:
        try:
            resolved = subprocess.run(
                ["git", "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
                cwd=project_root,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise ProductHarnessError(f"cannot resolve product commit: {exc}") from exc
        if resolved.returncode != 0:
            detail = (resolved.stderr or resolved.stdout).strip()
            raise ProductHarnessError(
                f"cannot resolve product commit {revision}: {detail or resolved.returncode}"
            )
        return resolved.stdout.strip()

    expected = resolve_commit(target_commit)
    actual = resolve_commit("HEAD")
    if actual != expected:
        raise ProductHarnessError(
            f"product target commit mismatch: expected {expected}, got {actual}"
        )
    try:
        clean = subprocess.run(
            ["git", "diff", "--quiet", "--no-ext-diff", expected, "--"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ProductHarnessError(f"cannot inspect product worktree: {exc}") from exc
    if clean.returncode == 1:
        raise ProductHarnessError(
            "product worktree has tracked modifications; target_commit does not "
            "identify the bytes that would be tested"
        )
    if clean.returncode != 0:
        detail = (clean.stderr or clean.stdout).strip()
        raise ProductHarnessError(
            f"cannot inspect product worktree cleanliness: {detail or clean.returncode}"
        )
    base = project_root.resolve()
    allowed: set[str] = set()
    for candidate in allowed_untracked:
        path = Path(candidate)
        if not path.is_absolute():
            path = base / path
        try:
            allowed.add(path.resolve().relative_to(base).as_posix())
        except ValueError as exc:
            raise ProductHarnessError("allowed untracked path escapes product root") from exc
    if check_untracked:
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                cwd=project_root,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise ProductHarnessError(f"cannot inspect product untracked files: {exc}") from exc
        if status.returncode != 0:
            detail = (status.stderr or status.stdout).decode("utf-8", "replace").strip()
            raise ProductHarnessError(
                f"cannot inspect product untracked files: {detail or status.returncode}"
            )
        unexpected: list[str] = []
        for entry in status.stdout.split(b"\0"):
            if not entry or not entry.startswith(b"?? "):
                continue
            raw_path = entry[3:].decode("utf-8", "surrogateescape")
            try:
                relative = (base / raw_path).resolve().relative_to(base).as_posix()
            except ValueError:
                relative = raw_path
            if relative not in allowed:
                unexpected.append(relative)
        if unexpected:
            raise ProductHarnessError(
                "product worktree has untracked files outside the allowlist: "
                + ", ".join(sorted(unexpected))
            )


def _post_run_identity_error(
    project_root: Path,
    target_commit: str,
    *,
    verify_commit: bool,
) -> dict[str, str] | None:
    """Return a harness error if execution changed tracked target bytes.

    Product commands routinely create untracked reports, caches, or runtime
    markers.  Those are checked before execution by the manifest allowlist;
    the post-run check intentionally focuses on tracked bytes and commit
    identity so normal evidence outputs do not turn a valid run into a false
    failure.
    """

    if not verify_commit:
        return None
    try:
        verify_target_commit(
            project_root,
            target_commit,
            check_untracked=False,
        )
    except ProductHarnessError as exc:
        return {"type": type(exc).__name__, "message": f"post-run identity check: {exc}"}
    return None


def verify_source_root(project_root: Path, source_root: str) -> Path:
    """Resolve the declared product source root without permitting escape."""

    base = project_root.resolve()
    candidate = (base / source_root).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ProductHarnessError("product source_root escapes project root") from exc
    if not candidate.is_dir():
        raise ProductHarnessError(f"product source_root is not a directory: {source_root}")
    return candidate


def _allowed_manifest_paths(manifest: ProductManifest, project_root: Path) -> tuple[Path, ...]:
    """Return the manifest sidecar and explicitly declared runtime paths."""

    base = project_root.resolve()
    paths: list[Path] = []
    # A manifest may be supplied from an external adapter directory.  It is
    # not part of the product worktree's ``git status`` and therefore must not
    # be treated as an escaping allowlist entry.  A sidecar inside the product
    # root remains explicitly allowlisted as before; symlinks resolving
    # outside the root are deliberately not allowlisted and will fail closed.
    if manifest.path:
        manifest_path = Path(manifest.path)
        if not manifest_path.is_absolute():
            manifest_path = base / manifest_path
        try:
            manifest_path.resolve().relative_to(base)
        except ValueError:
            pass
        else:
            paths.append(manifest_path)
    paths.extend(base / relative for relative in manifest.runtime_paths)
    return tuple(paths)


def run_product_plan(
    manifest: ProductManifest,
    *,
    project_root: Path,
    output_dir: Path,
    env: Mapping[str, str] | None = None,
    verify_commit: bool = True,
    enforce_domain_coverage: bool = False,
    selected_domains: set[str] | None = None,
    selected_command_ids: set[str] | None = None,
) -> ProductRun:
    """Run setup then mandatory commands independently and stop on first failure."""

    # Re-admit and snapshot the object: callers must not bypass validation by
    # constructing ProductManifest directly or mutating its raw mapping while
    # a run is in progress.
    raw = copy.deepcopy(dict(manifest.raw))
    validate_manifest(raw)
    admitted = ProductManifest(raw=raw, path=manifest.path)
    verify_source_root(project_root, str(raw["source_root"]))
    domains = select_test_domains(admitted)
    if enforce_domain_coverage:
        require_capability_decisions(admitted)
        # A tier/domain selection is an execution boundary.  A T0 run must
        # not be marked ``not_run`` merely because a product also declares a
        # T1/T3 capability whose tests are intentionally deferred.  Compute
        # coverage from the units that survive the selection, while retaining
        # the full capability plan in the report for auditability.
        if selected_domains is not None:
            applicable = set(selected_domains)
        elif selected_command_ids is not None:
            selected_ids = {str(item) for item in selected_command_ids}
            selected_units = tuple(
                item for item in admitted.test_units
                if str(item.get("id")) in selected_ids
            )
            applicable = {
                str(domain)
                for item in selected_units
                for domain in (item.get("domain_ids") or [])
            }
        else:
            applicable = {
                domain
                for row in domains
                if row["status"] == "selected"
                for domain in row["domains"]
            }
        if applicable:
            require_domain_coverage(admitted, applicable)
    if selected_domains is not None:
        selected = commands_for_domains(admitted, selected_domains)
        if not selected:
            raise ProductHarnessError("selected domains have no mapped commands")
        selected_ids = {str(item["id"]) for item in selected}
        raw["commands"] = [item for item in raw["commands"] if str(item["id"]) in selected_ids]
        raw["api_contracts"] = [
            item for item in raw.get("api_contracts", []) if str(item["id"]) in selected_ids
        ]
        raw["browser_tests"] = [
            item for item in raw.get("browser_tests", []) if str(item["id"]) in selected_ids
        ]
        raw["performance_tests"] = [
            item for item in raw.get("performance_tests", []) if str(item["id"]) in selected_ids
        ]
        raw["release_artifacts"] = [
            item for item in raw.get("release_artifacts", []) if str(item["id"]) in selected_ids
        ]
        raw["security_scans"] = [
            item for item in raw.get("security_scans", []) if str(item["id"]) in selected_ids
        ]
        raw["release_scenarios"] = [
            item for item in raw.get("release_scenarios", []) if str(item["id"]) in selected_ids
        ]
        raw["recovery_scenarios"] = [
            item for item in raw.get("recovery_scenarios", []) if str(item["id"]) in selected_ids
        ]
        raw["concurrency_scenarios"] = [
            item for item in raw.get("concurrency_scenarios", []) if str(item["id"]) in selected_ids
        ]
        raw["persistence_scenarios"] = [
            item for item in raw.get("persistence_scenarios", []) if str(item["id"]) in selected_ids
        ]
    if selected_command_ids is not None:
        requested_command_ids = set(selected_command_ids)
        raw["commands"] = [item for item in raw["commands"] if str(item["id"]) in selected_command_ids]
        raw["api_contracts"] = [
            item for item in raw.get("api_contracts", []) if str(item["id"]) in selected_command_ids
        ]
        raw["browser_tests"] = [
            item for item in raw.get("browser_tests", []) if str(item["id"]) in selected_command_ids
        ]
        raw["performance_tests"] = [
            item for item in raw.get("performance_tests", []) if str(item["id"]) in selected_command_ids
        ]
        raw["release_artifacts"] = [
            item for item in raw.get("release_artifacts", []) if str(item["id"]) in selected_command_ids
        ]
        raw["security_scans"] = [
            item for item in raw.get("security_scans", []) if str(item["id"]) in selected_command_ids
        ]
        raw["release_scenarios"] = [
            item for item in raw.get("release_scenarios", []) if str(item["id"]) in selected_command_ids
        ]
        raw["recovery_scenarios"] = [
            item for item in raw.get("recovery_scenarios", []) if str(item["id"]) in selected_command_ids
        ]
        raw["concurrency_scenarios"] = [
            item for item in raw.get("concurrency_scenarios", []) if str(item["id"]) in selected_command_ids
        ]
        raw["persistence_scenarios"] = [
            item for item in raw.get("persistence_scenarios", []) if str(item["id"]) in selected_command_ids
        ]
        if not raw["commands"] and not raw["api_contracts"] and not raw["browser_tests"] and not raw["performance_tests"] and not raw["release_artifacts"] and not raw["security_scans"] and not raw["release_scenarios"] and not raw["recovery_scenarios"] and not raw["concurrency_scenarios"] and not raw["persistence_scenarios"]:
            if requested_command_ids:
                raise ProductHarnessError("selected command IDs have no mapped commands")
            if verify_commit:
                verify_target_commit(
                    project_root,
                    str(raw["target_commit"]),
                    allowed_untracked=_allowed_manifest_paths(manifest, project_root),
                )
            return ProductRun((), domains=domains, planned_command_ids=())
    if verify_commit:
        verify_target_commit(
            project_root,
            str(raw["target_commit"]),
            allowed_untracked=_allowed_manifest_paths(manifest, project_root),
        )
    planned = tuple(
        str(item["id"])
        for item in (
            *raw.get("setup", []), *raw["commands"], *raw.get("api_contracts", []),
            *raw.get("browser_tests", []), *raw.get("performance_tests", []),
            *raw.get("release_artifacts", []),
            *raw.get("security_scans", []),
            *raw.get("release_scenarios", []),
            *raw.get("recovery_scenarios", []), *raw.get("concurrency_scenarios", []),
            *raw.get("persistence_scenarios", []),
        )
    )
    receipts: list[CommandReceipt] = []
    for item in raw.get("setup", []):
        receipt = run_command(
            item["command"],
            command_id=str(item["id"]),
            cwd=project_root,
            output_dir=output_dir,
            env=env,
            timeout_seconds=float(item["timeout_seconds"]),
        )
        receipts.append(receipt)
        if not receipt.passed and (item.get("mandatory", True) or receipt.environment_error or receipt.timed_out):
            identity_error = _post_run_identity_error(
                project_root, str(raw["target_commit"]), verify_commit=verify_commit
            )
            return ProductRun(tuple(receipts), failed_command_id=receipt.command_id, domains=domains,
                              planned_command_ids=planned,
                              harness_errors=(identity_error,) if identity_error else ())

    handles: list[ServiceHandle] = []
    service_receipts: dict[str, ServiceReceipt] = {}
    harness_errors: list[dict[str, str]] = []
    failed_entrypoint_id = ""
    failed_command_id = ""
    failed_api_contract_id = ""
    api_contract_results: list[dict[str, object]] = []
    failed_browser_test_id = ""
    browser_test_results: list[dict[str, object]] = []
    container_handles: list[tuple[str, ContainerHandle]] = []
    container_receipts: dict[str, ContainerReceipt] = {}
    failed_container_id = ""
    container_environment_error = False
    container_error_message = ""
    mock_handles: list[tuple[str, MockServiceHandle]] = []
    mock_receipts: dict[str, MockServiceReceipt] = {}
    failed_mock_service_id = ""
    performance_results: list[dict[str, object]] = []
    failed_performance_test_id = ""
    release_results: list[dict[str, object]] = []
    failed_release_artifact_id = ""
    security_results: list[dict[str, object]] = []
    failed_security_scan_id = ""
    release_scenario_results: list[dict[str, object]] = []
    failed_release_scenario_id = ""
    recovery_results: list[dict[str, object]] = []
    failed_recovery_scenario_id = ""
    concurrency_results: list[dict[str, object]] = []
    failed_concurrency_scenario_id = ""
    persistence_results: list[dict[str, object]] = []
    failed_persistence_scenario_id = ""
    process_env = {**dict(env or {})}
    try:
        for spec in admitted.dependency_containers:
            container_id = str(spec["id"])
            try:
                handle = start_dependency_container(
                    spec,
                    output_dir=output_dir,
                    name=None,
                )
            except ContainerError as exc:
                failed_container_id = container_id
                if exc.receipt is not None:
                    container_receipts[container_id] = exc.receipt
                if exc.environment_error:
                    container_environment_error = True
                else:
                    harness_errors.append({
                        "type": type(exc).__name__,
                        "message": str(exc),
                    })
                container_error_message = str(exc)
                break
            container_handles.append((container_id, handle))
            process_env.update(handle.env)

        for spec in admitted.mock_services if not failed_container_id else ():
            service_id = str(spec["id"])
            try:
                mock_handle = start_mock_service(spec, output_dir=output_dir)
            except (OSError, ValueError) as exc:
                failed_mock_service_id = service_id
                harness_errors.append({"type": type(exc).__name__, "message": str(exc)})
                break
            mock_handles.append((service_id, mock_handle))
            process_env.update(mock_handle.env)

        for entrypoint in admitted.entrypoints if not failed_container_id and not failed_mock_service_id else ():
            entrypoint_id = str(entrypoint["entrypoint_id"])
            try:
                handles.append(start_entrypoint(
                    entrypoint,
                    cwd=project_root,
                    output_dir=output_dir,
                    env=process_env,
                ))
            except ServiceError as exc:
                failed_entrypoint_id = entrypoint_id
                if exc.receipt is None:
                    harness_errors.append({
                        "type": "ProductHarnessError",
                        "message": f"entrypoint {entrypoint_id} harness failure: {exc}",
                    })
                    break
                service_receipts[entrypoint_id] = exc.receipt
                break

        if not failed_entrypoint_id:
            for item in raw["commands"] if not failed_container_id and not failed_mock_service_id else ():
                receipt = run_command(
                    item["command"],
                    command_id=str(item["id"]),
                    cwd=project_root,
                    output_dir=output_dir,
                    env=process_env,
                    timeout_seconds=float(item["timeout_seconds"]),
                )
                receipts.append(receipt)
                if not receipt.passed and (item.get("mandatory", True) or receipt.environment_error or receipt.timed_out):
                    failed_command_id = receipt.command_id
                    break
            if not failed_command_id:
                for contract in admitted.api_contracts if not failed_container_id and not failed_mock_service_id else ():
                    try:
                        result = run_schemathesis_contract(
                            contract,
                            project_root=project_root,
                            output_dir=output_dir,
                            env=process_env,
                        )
                    except (OSError, ValueError) as exc:
                        failed_api_contract_id = str(contract["id"])
                        harness_errors.append({
                            "type": type(exc).__name__,
                            "message": str(exc),
                        })
                        break
                    result = {**result, "mandatory": bool(contract.get("mandatory", True)), "deterministic": bool(contract.get("deterministic", True))}
                    api_contract_results.append(result)
                    if result.get("verdict") != "pass" and (result.get("mandatory") or result.get("verdict") in {"harness_failure", "environment_failure"}):
                        failed_api_contract_id = str(contract["id"])
                        break
            if not failed_command_id and not failed_api_contract_id:
                for contract in admitted.browser_tests if not failed_container_id and not failed_mock_service_id else ():
                    try:
                        result = run_playwright_contract(
                            contract,
                            project_root=project_root,
                            output_dir=output_dir,
                            env=process_env,
                        )
                    except (OSError, ValueError) as exc:
                        failed_browser_test_id = str(contract["id"])
                        harness_errors.append({
                            "type": type(exc).__name__,
                            "message": str(exc),
                        })
                        break
                    result = {**result, "mandatory": bool(contract.get("mandatory", True)), "deterministic": bool(contract.get("deterministic", True))}
                    browser_test_results.append(result)
                    if result.get("verdict") != "pass" and (result.get("mandatory") or result.get("verdict") in {"harness_failure", "environment_failure"}):
                        failed_browser_test_id = str(contract["id"])
                        break
            if not failed_command_id and not failed_api_contract_id and not failed_browser_test_id:
                for contract in admitted.performance_tests:
                    try:
                        result = run_k6_contract(
                            contract, project_root=project_root, output_dir=output_dir, env=process_env,
                        )
                    except (OSError, ValueError) as exc:
                        failed_performance_test_id = str(contract["id"])
                        harness_errors.append({"type": type(exc).__name__, "message": str(exc)})
                        break
                    result = {**result, "mandatory": bool(contract.get("mandatory", True)), "deterministic": bool(contract.get("deterministic", True))}
                    performance_results.append(result)
                    if result.get("verdict") != "pass" and (result.get("mandatory") or result.get("verdict") in {"harness_failure", "environment_failure"}):
                        failed_performance_test_id = str(contract["id"])
                        break
            if (
                not failed_command_id
                and not failed_api_contract_id
                and not failed_browser_test_id
                and not failed_performance_test_id
            ):
                release_results = list(run_release_artifact_checks(
                    admitted.release_artifacts,
                    project_root=project_root,
                    output_dir=output_dir,
                ))
                for result in release_results:
                    if result.get("mandatory", True) and not result.get("passed"):
                        failed_release_artifact_id = str(result["artifact_id"])
                        break
            if (
                not failed_command_id
                and not failed_api_contract_id
                and not failed_browser_test_id
                and not failed_performance_test_id
                and not failed_release_artifact_id
            ):
                for scan in admitted.security_scans:
                    try:
                        result = run_zap_contract(
                            scan, project_root=project_root, output_dir=output_dir, env=process_env,
                        )
                    except (OSError, ValueError) as exc:
                        failed_security_scan_id = str(scan["id"])
                        harness_errors.append({"type": type(exc).__name__, "message": str(exc)})
                        break
                    result = {**result, "mandatory": bool(scan.get("mandatory", True)), "deterministic": bool(scan.get("deterministic", True))}
                    security_results.append(result)
                    if result.get("verdict") != "pass" and (result.get("mandatory") or result.get("verdict") in {"harness_failure", "environment_failure"}):
                        failed_security_scan_id = str(scan["id"])
                        break
            if (
                not failed_command_id
                and not failed_api_contract_id
                and not failed_browser_test_id
                and not failed_performance_test_id
                and not failed_release_artifact_id
                and not failed_security_scan_id
            ):
                release_scenario_results = list(run_release_scenarios(
                    admitted.release_scenarios,
                    project_root=project_root,
                    output_dir=output_dir,
                    env=process_env,
                ))
                for result in release_scenario_results:
                    if result.get("mandatory", True) and not result.get("passed"):
                        failed_release_scenario_id = str(result["scenario_id"])
                        break
            if (
                not failed_command_id and not failed_api_contract_id and not failed_browser_test_id
                and not failed_performance_test_id and not failed_release_artifact_id
                and not failed_security_scan_id and not failed_release_scenario_id
            ):
                recovery_results = list(run_recovery_scenarios(
                    admitted.recovery_scenarios,
                    project_root=project_root,
                    output_dir=output_dir,
                    env=process_env,
                ))
                for result in recovery_results:
                    if result.get("mandatory", True) and not result.get("passed", False):
                        failed_recovery_scenario_id = str(result["scenario_id"])
                        break
            if (
                not failed_command_id and not failed_api_contract_id and not failed_browser_test_id
                and not failed_performance_test_id and not failed_release_artifact_id
                and not failed_security_scan_id and not failed_release_scenario_id
                and not failed_recovery_scenario_id
            ):
                concurrency_results = list(run_concurrency_scenarios(
                    admitted.concurrency_scenarios,
                    project_root=project_root,
                    output_dir=output_dir,
                    env=process_env,
                ))
                for result in concurrency_results:
                    if result.get("mandatory", True) and not result.get("passed", False):
                        failed_concurrency_scenario_id = str(result["scenario_id"])
                        break
            if (
                not failed_command_id and not failed_api_contract_id and not failed_browser_test_id
                and not failed_performance_test_id and not failed_release_artifact_id
                and not failed_security_scan_id and not failed_release_scenario_id
                and not failed_recovery_scenario_id and not failed_concurrency_scenario_id
            ):
                persistence_results = list(run_persistence_scenarios(
                    admitted.persistence_scenarios,
                    project_root=project_root,
                    output_dir=output_dir,
                    env=process_env,
                ))
                for result in persistence_results:
                    if result.get("mandatory", True) and not result.get("passed", False):
                        failed_persistence_scenario_id = str(result["scenario_id"])
                        break
    finally:
        for handle in reversed(handles):
            receipt = handle.stop()
            if receipt is not None:
                service_receipts[receipt.entrypoint_id] = receipt
        for service_id, handle in reversed(mock_handles):
            mock_receipts[service_id] = handle.stop()
        for container_id, handle in reversed(container_handles):
            receipt = handle.stop()
            container_receipts[container_id] = receipt

    ordered_services = tuple(
        service_receipts[str(entrypoint["entrypoint_id"])]
        for entrypoint in admitted.entrypoints
        if str(entrypoint["entrypoint_id"]) in service_receipts
    )
    identity_error = _post_run_identity_error(
        project_root, str(raw["target_commit"]), verify_commit=verify_commit
    )
    if identity_error:
        harness_errors.append(identity_error)
    return ProductRun(
        tuple(receipts),
        failed_command_id=failed_command_id,
        domains=domains,
        planned_command_ids=planned,
        services=ordered_services,
        failed_entrypoint_id=failed_entrypoint_id,
        harness_errors=tuple(harness_errors),
        api_contracts=tuple(api_contract_results),
        failed_api_contract_id=failed_api_contract_id,
        browser_tests=tuple(browser_test_results),
        failed_browser_test_id=failed_browser_test_id,
        containers=tuple(
            container_receipts[str(spec["id"])]
            for spec in admitted.dependency_containers
            if str(spec["id"]) in container_receipts
        ),
        failed_container_id=failed_container_id,
        container_environment_error=container_environment_error,
        container_error_message=container_error_message,
        mock_services=tuple(
            mock_receipts[str(spec["id"])]
            for spec in admitted.mock_services
            if str(spec["id"]) in mock_receipts
        ),
        failed_mock_service_id=failed_mock_service_id,
        performance_tests=tuple(performance_results),
        failed_performance_test_id=failed_performance_test_id,
        release_artifacts=tuple(release_results),
        failed_release_artifact_id=failed_release_artifact_id,
        security_scans=tuple(security_results),
        failed_security_scan_id=failed_security_scan_id,
        release_scenarios=tuple(release_scenario_results),
        failed_release_scenario_id=failed_release_scenario_id,
        recovery_scenarios=tuple(recovery_results),
        failed_recovery_scenario_id=failed_recovery_scenario_id,
        concurrency_scenarios=tuple(concurrency_results),
        failed_concurrency_scenario_id=failed_concurrency_scenario_id,
        persistence_scenarios=tuple(persistence_results),
        failed_persistence_scenario_id=failed_persistence_scenario_id,
    )


def run_manifest_file(
    manifest_path: Path,
    *,
    project_root: Path,
    output_dir: Path,
    env: Mapping[str, str] | None = None,
    verify_commit: bool = True,
    selected_domains: set[str] | None = None,
    selected_command_ids: set[str] | None = None,
) -> ProductRun:
    """Canonical file-based entrypoint for CI and generated products."""
    manifest = load_manifest(manifest_path)
    return run_product_plan(
        manifest,
        project_root=project_root,
        output_dir=output_dir,
        env=env,
        verify_commit=verify_commit,
        enforce_domain_coverage=True,
        selected_domains=selected_domains,
        selected_command_ids=selected_command_ids,
    )

