"""Validation for the product-test-manifest.v1 input contract.

This module deliberately does not execute product commands.  It only admits a
closed, auditable test plan; command execution and evidence collection remain
separate concerns.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import shlex
from typing import Any, Mapping
from urllib.parse import urlsplit


SCHEMA_VERSION = "product-test-manifest.v1"
PROJECT_TYPES = {"cli", "library", "api", "web", "worker", "fullstack", "other"}
TIERS = {"unit", "integration", "e2e", "security", "perf", "release"}
SCHEMATHESIS_PHASES = {"examples", "coverage", "fuzzing"}
SCHEMATHESIS_MODES = {"positive", "negative", "all"}
SCHEMATHESIS_CHECKS = {
    "not_a_server_error",
    "status_code_conformance",
    "content_type_conformance",
    "response_headers_conformance",
    "response_schema_conformance",
    "negative_data_rejection",
    "positive_data_acceptance",
    "missing_required_header",
    "unsupported_method",
    "allow_header_conformance",
    "ignored_auth",
}
_TOP_LEVEL_KEYS = {
    "schema_version", "project_type", "source_root", "target_commit", "setup",
    "commands", "acceptances", "entrypoints", "api_contracts", "browser_tests",
    "dependency_containers", "mock_services", "performance_tests", "release_artifacts",
    "security_scans", "release_scenarios", "recovery_scenarios",
    "concurrency_scenarios", "persistence_scenarios", "capabilities", "budgets",
    "not_applicable", "runtime_paths", "cleanup",
}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_DOMAIN = re.compile(r"P-(?:0[1-9]|1[0-8])\Z")
_COMMIT = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")
_PORT = re.compile(r"[1-9][0-9]{0,4}:[1-9][0-9]{0,4}\Z")


class ManifestError(ValueError):
    """A product test manifest cannot be admitted."""


@dataclass(frozen=True)
class ProductManifest:
    """Normalized, immutable manifest accepted by the product TestKit."""

    raw: Mapping[str, Any]
    path: Path | None = None

    @property
    def project_type(self) -> str:
        return str(self.raw["project_type"])

    @property
    def commands(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.raw["commands"])

    @property
    def capabilities(self) -> Mapping[str, Any]:
        return self.raw.get("capabilities", {})

    @property
    def runtime_paths(self) -> tuple[str, ...]:
        """Product files intentionally created by a test run.

        These paths are the only additional untracked files permitted on a
        repeated run after target-commit validation.  They must be declared
        explicitly so a newly introduced source file cannot silently change
        the bytes under test.
        """

        return tuple(str(item) for item in self.raw.get("runtime_paths", []))

    @property
    def entrypoints(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.raw.get("entrypoints", []))

    @property
    def api_contracts(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.raw.get("api_contracts", []))

    @property
    def browser_tests(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.raw.get("browser_tests", []))

    @property
    def dependency_containers(self) -> tuple[Mapping[str, Any], ...]:
        """Ephemeral real dependencies used by integration/recovery tests."""

        return tuple(self.raw.get("dependency_containers", []))

    @property
    def mock_services(self) -> tuple[Mapping[str, Any], ...]:
        """Local deterministic HTTP fakes used by integration contracts."""

        return tuple(self.raw.get("mock_services", []))

    @property
    def performance_tests(self) -> tuple[Mapping[str, Any], ...]:
        """Explicit k6 performance/soak contracts."""

        return tuple(self.raw.get("performance_tests", []))

    @property
    def release_artifacts(self) -> tuple[Mapping[str, Any], ...]:
        """Immutable package/binary/container outputs required at release tier."""

        return tuple(self.raw.get("release_artifacts", []))

    @property
    def security_scans(self) -> tuple[Mapping[str, Any], ...]:
        """Opt-in dynamic security reports (currently ZAP JSON)."""

        return tuple(self.raw.get("security_scans", []))

    @property
    def release_scenarios(self) -> tuple[Mapping[str, Any], ...]:
        """Install/upgrade/rollback workflows executed at release tier."""

        return tuple(self.raw.get("release_scenarios", []))

    @property
    def recovery_scenarios(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.raw.get("recovery_scenarios", []))

    @property
    def concurrency_scenarios(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.raw.get("concurrency_scenarios", []))

    @property
    def persistence_scenarios(self) -> tuple[Mapping[str, Any], ...]:
        """Database/file migration and restore command sequences."""

        return tuple(self.raw.get("persistence_scenarios", []))

    @property
    def test_units(self) -> tuple[Mapping[str, Any], ...]:
        return (
            *self.commands,
            *self.api_contracts,
            *self.browser_tests,
            *self.performance_tests,
            *self.release_artifacts,
            *self.security_scans,
            *self.release_scenarios,
            *self.recovery_scenarios,
            *self.concurrency_scenarios,
            *self.persistence_scenarios,
        )


def load_manifest(path: Path) -> ProductManifest:
    """Load and validate a JSON manifest without changing the product tree."""

    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_number,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read product manifest {path}: {exc}") from exc
    validate_manifest(raw)
    return ProductManifest(raw=raw, path=path.resolve())


def validate_manifest(raw: Mapping[str, Any]) -> None:
    """Fail closed on missing, malformed, duplicated or dangling declarations."""

    if not isinstance(raw, Mapping):
        raise ManifestError("manifest must be a JSON object")
    unknown = sorted(set(raw) - _TOP_LEVEL_KEYS)
    if unknown:
        raise ManifestError("unknown manifest fields: " + ", ".join(str(key) for key in unknown))
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError("unsupported product test manifest schema")
    _required_string(raw, "project_type")
    if raw["project_type"] not in PROJECT_TYPES:
        raise ManifestError("project_type is invalid")
    for key in ("source_root", "target_commit"):
        _required_string(raw, key)
    if not _COMMIT.fullmatch(str(raw["target_commit"])):
        raise ManifestError("target_commit must be a 40- or 64-character hexadecimal Git SHA")
    setup = raw.get("setup", [])
    _validate_setup(setup)
    commands = raw.get("commands")
    if not isinstance(commands, list) or not commands:
        raise ManifestError("commands must be a non-empty list")
    command_ids: set[str] = set()
    acceptance_ids: set[str] = set()
    for index, command in enumerate(commands):
        if not isinstance(command, Mapping):
            raise ManifestError(f"commands[{index}] must be an object")
        cid = _required_id(command, "id", f"commands[{index}]")
        if cid in command_ids:
            raise ManifestError(f"duplicate command id: {cid}")
        command_ids.add(cid)
        _required_string(command, "command", f"commands[{index}]")
        tier = command.get("tier")
        if tier not in TIERS:
            raise ManifestError(f"commands[{index}].tier is invalid")
        if not isinstance(command.get("mandatory"), bool):
            raise ManifestError(f"commands[{index}].mandatory must be boolean")
        if not isinstance(command.get("deterministic"), bool):
            raise ManifestError(f"commands[{index}].deterministic must be boolean")
        timeout = command.get("timeout_seconds")
        if not _valid_timeout(timeout):
            raise ManifestError(f"commands[{index}].timeout_seconds must be positive")
        refs = command.get("acceptance_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(x, str) or not x for x in refs):
            raise ManifestError(f"commands[{index}].acceptance_ids must be non-empty")
        acceptance_ids.update(refs)
        domain_ids = command.get("domain_ids")
        if domain_ids is not None:
            if (not isinstance(domain_ids, list) or not domain_ids
                    or any(not isinstance(item, str) or not _DOMAIN.fullmatch(item) for item in domain_ids)
                    or len(set(domain_ids)) != len(domain_ids)):
                raise ManifestError(f"commands[{index}].domain_ids is invalid")
    setup_ids = {str(item["id"]) for item in setup}
    collision = setup_ids & command_ids
    if collision:
        raise ManifestError(f"setup and command ids collide: {sorted(collision)}")
    acceptances = raw.get("acceptances")
    if not isinstance(acceptances, list) or not acceptances:
        raise ManifestError("acceptances must be a non-empty list")
    _validate_ids(acceptances, "acceptances")
    declared_acceptances = {str(item["id"]) for item in acceptances}
    if acceptance_ids - declared_acceptances:
        missing = sorted(acceptance_ids - declared_acceptances)
        raise ManifestError(f"commands reference unknown acceptance ids: {missing}")
    entrypoint_ids = _validate_entrypoints(raw.get("entrypoints", []))
    _validate_api_contracts(
        raw.get("api_contracts", []),
        entrypoint_ids=entrypoint_ids,
        acceptance_ids=declared_acceptances,
        reserved_ids=setup_ids | command_ids,
    )
    api_ids = {str(item["id"]) for item in raw.get("api_contracts", [])}
    _validate_browser_tests(
        raw.get("browser_tests", []),
        entrypoint_ids=entrypoint_ids,
        acceptance_ids=declared_acceptances,
        reserved_ids=setup_ids | command_ids | api_ids,
    )
    browser_ids = {str(item["id"]) for item in raw.get("browser_tests", [])}
    _validate_dependency_containers(
        raw.get("dependency_containers", []),
        reserved_ids=setup_ids | command_ids | api_ids | browser_ids,
    )
    container_ids = {str(item["id"]) for item in raw.get("dependency_containers", [])}
    _validate_mock_services(
        raw.get("mock_services", []),
        reserved_ids=setup_ids | command_ids | api_ids | browser_ids | container_ids,
    )
    mock_ids = {str(item["id"]) for item in raw.get("mock_services", [])}
    _validate_performance_tests(
        raw.get("performance_tests", []),
        reserved_ids=setup_ids | command_ids | api_ids | browser_ids | container_ids | mock_ids,
        acceptance_ids=declared_acceptances,
    )
    performance_ids = {str(item["id"]) for item in raw.get("performance_tests", [])}
    _validate_release_artifacts(
        raw.get("release_artifacts", []),
        reserved_ids=setup_ids | command_ids | api_ids | browser_ids | container_ids | mock_ids | performance_ids,
        acceptance_ids=declared_acceptances,
    )
    release_ids = {str(item["id"]) for item in raw.get("release_artifacts", [])}
    _validate_security_scans(
        raw.get("security_scans", []),
        reserved_ids=setup_ids | command_ids | api_ids | browser_ids | container_ids | mock_ids | performance_ids | release_ids,
        acceptance_ids=declared_acceptances,
    )
    security_ids = {str(item["id"]) for item in raw.get("security_scans", [])}
    _validate_release_scenarios(
        raw.get("release_scenarios", []),
        reserved_ids=setup_ids | command_ids | api_ids | browser_ids | container_ids | mock_ids | performance_ids | release_ids | security_ids,
        acceptance_ids=declared_acceptances,
    )
    release_scenario_ids = {str(item["id"]) for item in raw.get("release_scenarios", [])}
    _validate_recovery_scenarios(
        raw.get("recovery_scenarios", []),
        reserved_ids=setup_ids | command_ids | api_ids | browser_ids | container_ids | mock_ids | performance_ids | release_ids | security_ids | release_scenario_ids,
        acceptance_ids=declared_acceptances,
    )
    recovery_ids = {str(item["id"]) for item in raw.get("recovery_scenarios", [])}
    _validate_concurrency_scenarios(
        raw.get("concurrency_scenarios", []),
        reserved_ids=setup_ids | command_ids | api_ids | browser_ids | container_ids | mock_ids | performance_ids | release_ids | security_ids | release_scenario_ids | recovery_ids,
        acceptance_ids=declared_acceptances,
    )
    concurrency_ids = {str(item["id"]) for item in raw.get("concurrency_scenarios", [])}
    _validate_persistence_scenarios(
        raw.get("persistence_scenarios", []),
        reserved_ids=setup_ids | command_ids | api_ids | browser_ids | container_ids | mock_ids
        | performance_ids | release_ids | security_ids | release_scenario_ids | recovery_ids | concurrency_ids,
        acceptance_ids=declared_acceptances,
    )
    _validate_not_applicable(raw.get("not_applicable", {}))
    if not isinstance(raw.get("capabilities", {}), Mapping):
        raise ManifestError("capabilities must be an object")
    if not isinstance(raw.get("budgets", {}), Mapping):
        raise ManifestError("budgets must be an object")
    cleanup = raw.get("cleanup", [])
    if not isinstance(cleanup, list) or any(not isinstance(x, str) or not x.strip() for x in cleanup):
        raise ManifestError("cleanup must be a list of non-empty commands")
    runtime_paths = raw.get("runtime_paths", [])
    _validate_runtime_paths(runtime_paths)


def _required_string(raw: Mapping[str, Any], key: str, subject: str = "manifest") -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{subject}.{key} must be a non-empty string")
    return value


def _required_id(raw: Mapping[str, Any], key: str, subject: str) -> str:
    value = _required_string(raw, key, subject)
    if not _ID.fullmatch(value):
        raise ManifestError(f"{subject}.{key} is not a stable identifier")
    return value


def _validate_setup(setup: Any) -> None:
    if not isinstance(setup, list):
        raise ManifestError("setup must be a list")
    seen: set[str] = set()
    for index, item in enumerate(setup):
        if not isinstance(item, Mapping):
            raise ManifestError(f"setup[{index}] must be an object")
        item_id = _required_id(item, "id", f"setup[{index}]")
        if item_id in seen:
            raise ManifestError(f"duplicate setup id: {item_id}")
        seen.add(item_id)
        _required_string(item, "command", f"setup[{index}]")
        timeout = item.get("timeout_seconds")
        if not _valid_timeout(timeout):
            raise ManifestError(f"setup[{index}].timeout_seconds must be positive")


def _validate_entrypoints(entrypoints: Any) -> set[str]:
    """Admit the same executable lifecycle contract as Product Acceptance."""

    if not isinstance(entrypoints, list):
        raise ManifestError("entrypoints must be a list")
    seen: set[str] = set()
    for index, item in enumerate(entrypoints):
        subject = f"entrypoints[{index}]"
        if not isinstance(item, Mapping):
            raise ManifestError(f"{subject} must be an object")
        entrypoint_id = _required_id(item, "entrypoint_id", subject)
        if entrypoint_id in seen:
            raise ManifestError(f"duplicate entrypoint id: {entrypoint_id}")
        seen.add(entrypoint_id)
        for field in ("start_command", "health_check", "owner"):
            _required_string(item, field, subject)
        if not _valid_timeout(item.get("timeout_seconds")):
            raise ManifestError(f"{subject}.timeout_seconds must be positive")
    return seen


def _validate_api_contracts(
    api_contracts: Any,
    *,
    entrypoint_ids: set[str],
    acceptance_ids: set[str],
    reserved_ids: set[str],
) -> None:
    if not isinstance(api_contracts, list):
        raise ManifestError("api_contracts must be a list")
    seen: set[str] = set()
    for index, item in enumerate(api_contracts):
        subject = f"api_contracts[{index}]"
        if not isinstance(item, Mapping):
            raise ManifestError(f"{subject} must be an object")
        contract_id = _required_id(item, "id", subject)
        if contract_id in seen:
            raise ManifestError(f"duplicate API contract id: {contract_id}")
        if contract_id in reserved_ids:
            raise ManifestError(f"API contract id collides with another test unit: {contract_id}")
        seen.add(contract_id)
        entrypoint_id = _required_id(item, "entrypoint_id", subject)
        if entrypoint_id not in entrypoint_ids:
            raise ManifestError(f"{subject}.entrypoint_id references unknown entrypoint: {entrypoint_id}")
        schema = _required_string(item, "schema", subject)
        schema_path = Path(schema)
        if schema_path.is_absolute() or ".." in schema_path.parts:
            raise ManifestError(f"{subject}.schema must stay inside the product root")
        base_url = _required_string(item, "base_url", subject)
        try:
            parsed_url = urlsplit(base_url)
        except ValueError as exc:
            raise ManifestError(f"{subject}.base_url is invalid") from exc
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            raise ManifestError(f"{subject}.base_url must use http or https")
        if parsed_url.username is not None or parsed_url.password is not None:
            raise ManifestError(f"{subject}.base_url must not contain credentials")
        if parsed_url.query or parsed_url.fragment:
            raise ManifestError(f"{subject}.base_url must not contain query or fragment")
        version = _required_string(item, "tool_version", subject)
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ManifestError(f"{subject}.tool_version must be an exact semantic version")
        if item.get("tier") not in TIERS:
            raise ManifestError(f"{subject}.tier is invalid")
        for field in ("mandatory", "deterministic"):
            if not isinstance(item.get(field), bool):
                raise ManifestError(f"{subject}.{field} must be boolean")
        if not _valid_timeout(item.get("timeout_seconds")):
            raise ManifestError(f"{subject}.timeout_seconds must be positive")
        refs = item.get("acceptance_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(value, str) or not value for value in refs):
            raise ManifestError(f"{subject}.acceptance_ids must be non-empty")
        missing_acceptances = sorted(set(refs) - acceptance_ids)
        if missing_acceptances:
            raise ManifestError(f"{subject} references unknown acceptance ids: {missing_acceptances}")
        domains = item.get("domain_ids")
        if (
            not isinstance(domains, list)
            or not domains
            or any(not isinstance(value, str) or not _DOMAIN.fullmatch(value) for value in domains)
            or len(set(domains)) != len(domains)
            or "P-07" not in domains
        ):
            raise ManifestError(f"{subject}.domain_ids must include valid P-07 coverage")
        phases = item.get("phases")
        if (
            not isinstance(phases, list)
            or not phases
            or len(set(phases)) != len(phases)
            or any(value not in SCHEMATHESIS_PHASES for value in phases)
        ):
            raise ManifestError(f"{subject}.phases must declare supported stateless phases")
        if item.get("mode") not in SCHEMATHESIS_MODES:
            raise ManifestError(f"{subject}.mode is invalid")
        checks = item.get("checks")
        if (
            not isinstance(checks, list)
            or not checks
            or len(set(checks)) != len(checks)
            or any(value not in SCHEMATHESIS_CHECKS for value in checks)
        ):
            raise ManifestError(f"{subject}.checks contains unsupported checks")
        if item.get("workers") != 1:
            raise ManifestError(f"{subject}.workers must be 1 for the deterministic gate")
        seed = item.get("seed")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise ManifestError(f"{subject}.seed must be a non-negative integer")
        for field in ("max_examples",):
            value = item.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ManifestError(f"{subject}.{field} must be a positive integer")
        max_time = item.get("max_time_seconds")
        if not isinstance(max_time, int) or isinstance(max_time, bool) or max_time <= 0:
            raise ManifestError(f"{subject}.max_time_seconds must be a positive integer")
        if not _valid_timeout(item.get("request_timeout_seconds")):
            raise ManifestError(f"{subject}.request_timeout_seconds must be positive")
        retries = item.get("request_retries")
        if not isinstance(retries, int) or isinstance(retries, bool) or retries < 0:
            raise ManifestError(f"{subject}.request_retries must be a non-negative integer")
        if float(item["timeout_seconds"]) <= float(item["max_time_seconds"]):
            raise ManifestError(f"{subject}.timeout_seconds must exceed max_time_seconds")


def _validate_browser_tests(
    browser_tests: Any,
    *,
    entrypoint_ids: set[str],
    acceptance_ids: set[str],
    reserved_ids: set[str],
) -> None:
    """Admit a deterministic Docker Playwright browser test declaration."""

    if not isinstance(browser_tests, list):
        raise ManifestError("browser_tests must be a list")
    seen: set[str] = set()
    for index, item in enumerate(browser_tests):
        subject = f"browser_tests[{index}]"
        if not isinstance(item, Mapping):
            raise ManifestError(f"{subject} must be an object")
        test_id = _required_id(item, "id", subject)
        if test_id in seen:
            raise ManifestError(f"duplicate browser test id: {test_id}")
        if test_id in reserved_ids:
            raise ManifestError(f"browser test id collides with another test unit: {test_id}")
        seen.add(test_id)
        entrypoint_id = _required_id(item, "entrypoint_id", subject)
        if entrypoint_id not in entrypoint_ids:
            raise ManifestError(f"{subject}.entrypoint_id references unknown entrypoint: {entrypoint_id}")
        spec = _required_string(item, "spec", subject)
        spec_path = Path(spec)
        if spec_path.is_absolute() or ".." in spec_path.parts:
            raise ManifestError(f"{subject}.spec must stay inside the product root")
        base_url = _required_string(item, "base_url", subject)
        try:
            parsed_url = urlsplit(base_url)
        except ValueError as exc:
            raise ManifestError(f"{subject}.base_url is invalid") from exc
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            raise ManifestError(f"{subject}.base_url must use http or https")
        if parsed_url.username is not None or parsed_url.password is not None:
            raise ManifestError(f"{subject}.base_url must not contain credentials")
        if parsed_url.query or parsed_url.fragment:
            raise ManifestError(f"{subject}.base_url must not contain query or fragment")
        image = _required_string(item, "image", subject)
        if any(char.isspace() for char in image) or any(char in image for char in "\n\r"):
            raise ManifestError(f"{subject}.image must be one Docker image token")
        if item.get("tier") != "e2e":
            raise ManifestError(f"{subject}.tier must be e2e")
        for field in ("mandatory", "deterministic"):
            if not isinstance(item.get(field), bool):
                raise ManifestError(f"{subject}.{field} must be boolean")
        if "install_browser" in item and not isinstance(item["install_browser"], bool):
            raise ManifestError(f"{subject}.install_browser must be boolean")
        for field in ("timeout_seconds", "install_timeout_seconds"):
            if not _valid_timeout(item.get(field)):
                raise ManifestError(f"{subject}.{field} must be positive")
        refs = item.get("acceptance_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(value, str) or not value for value in refs):
            raise ManifestError(f"{subject}.acceptance_ids must be non-empty")
        missing_acceptances = sorted(set(refs) - acceptance_ids)
        if missing_acceptances:
            raise ManifestError(f"{subject} references unknown acceptance ids: {missing_acceptances}")
        domains = item.get("domain_ids")
        if (
            not isinstance(domains, list)
            or not domains
            or any(not isinstance(value, str) or not _DOMAIN.fullmatch(value) for value in domains)
            or len(set(domains)) != len(domains)
            or "P-08" not in domains
        ):
            raise ManifestError(f"{subject}.domain_ids must include valid P-08 coverage")


def _validate_dependency_containers(
    containers: Any,
    *,
    reserved_ids: set[str],
) -> None:
    """Admit only bounded, ephemeral Docker dependency declarations."""

    if not isinstance(containers, list):
        raise ManifestError("dependency_containers must be a list")
    seen: set[str] = set()
    for index, item in enumerate(containers):
        subject = f"dependency_containers[{index}]"
        if not isinstance(item, Mapping):
            raise ManifestError(f"{subject} must be an object")
        container_id = _required_id(item, "id", subject)
        if container_id in seen or container_id in reserved_ids:
            raise ManifestError(f"duplicate or colliding dependency container id: {container_id}")
        seen.add(container_id)
        image = _required_string(item, "image", subject)
        if any(char.isspace() for char in image) or any(char in image for char in "\x00\n\r"):
            raise ManifestError(f"{subject}.image must be one Docker image token")
        for field in ("command", "health_command"):
            value = _required_string(item, field, subject)
            if any(char in value for char in "\x00\n\r"):
                raise ManifestError(f"{subject}.{field} must be one non-empty line")
        ports = item.get("ports", [])
        if not isinstance(ports, list) or any(not isinstance(port, str) or not _PORT.fullmatch(port) for port in ports):
            raise ManifestError(f"{subject}.ports must contain host:container port mappings")
        if len(set(ports)) != len(ports):
            raise ManifestError(f"{subject}.ports must not contain duplicates")
        if not _valid_timeout(item.get("timeout_seconds")):
            raise ManifestError(f"{subject}.timeout_seconds must be positive")
        if not isinstance(item.get("mandatory"), bool):
            raise ManifestError(f"{subject}.mandatory must be boolean")


def _validate_mock_services(services: Any, *, reserved_ids: set[str]) -> None:
    """Admit fixed localhost HTTP routes without allowing ambiguous input."""

    if not isinstance(services, list):
        raise ManifestError("mock_services must be a list")
    seen: set[str] = set()
    for index, item in enumerate(services):
        subject = f"mock_services[{index}]"
        if not isinstance(item, Mapping):
            raise ManifestError(f"{subject} must be an object")
        service_id = _required_id(item, "id", subject)
        if service_id in seen or service_id in reserved_ids:
            raise ManifestError(f"duplicate or colliding mock service id: {service_id}")
        seen.add(service_id)
        routes = item.get("routes")
        if not isinstance(routes, list) or not routes:
            raise ManifestError(f"{subject}.routes must be a non-empty list")
        route_keys: set[tuple[str, str]] = set()
        for route_index, route in enumerate(routes):
            route_subject = f"{subject}.routes[{route_index}]"
            if not isinstance(route, Mapping):
                raise ManifestError(f"{route_subject} must be an object")
            method = route.get("method")
            path = route.get("path")
            if not isinstance(method, str) or not re.fullmatch(r"[A-Z]+", method):
                raise ManifestError(f"{route_subject}.method must be uppercase")
            if not isinstance(path, str) or not path.startswith("/") or "?" in path or "#" in path:
                raise ManifestError(f"{route_subject}.path must be an absolute path")
            key = (method, path)
            if key in route_keys:
                raise ManifestError(f"duplicate mock route: {method} {path}")
            route_keys.add(key)
            status = route.get("status")
            if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
                raise ManifestError(f"{route_subject}.status must be an HTTP status")
            failures = route.get("failures_before_success", 0)
            if not isinstance(failures, int) or isinstance(failures, bool) or failures < 0:
                raise ManifestError(f"{route_subject}.failures_before_success must be non-negative")
            failure_status = route.get("failure_status", 503)
            if not isinstance(failure_status, int) or isinstance(failure_status, bool) or not 400 <= failure_status <= 599:
                raise ManifestError(f"{route_subject}.failure_status must be a 4xx/5xx status")


def _validate_performance_tests(
    tests: Any, *, reserved_ids: set[str], acceptance_ids: set[str]
) -> None:
    if not isinstance(tests, list):
        raise ManifestError("performance_tests must be a list")
    seen: set[str] = set()
    for index, item in enumerate(tests):
        subject = f"performance_tests[{index}]"
        if not isinstance(item, Mapping):
            raise ManifestError(f"{subject} must be an object")
        test_id = _required_id(item, "id", subject)
        if test_id in seen or test_id in reserved_ids:
            raise ManifestError(f"duplicate or colliding performance test id: {test_id}")
        seen.add(test_id)
        script = _required_string(item, "script", subject)
        script_path = Path(script)
        if script_path.is_absolute() or ".." in script_path.parts:
            raise ManifestError(f"{subject}.script must stay inside the product root")
        image = _required_string(item, "image", subject)
        if any(char.isspace() for char in image) or any(char in image for char in "\x00\n\r"):
            raise ManifestError(f"{subject}.image must be one Docker image token")
        if item.get("tier") != "perf":
            raise ManifestError(f"{subject}.tier must be perf")
        for field in ("mandatory", "deterministic"):
            if not isinstance(item.get(field), bool):
                raise ManifestError(f"{subject}.{field} must be boolean")
        if not _valid_timeout(item.get("timeout_seconds")):
            raise ManifestError(f"{subject}.timeout_seconds must be positive")
        refs = item.get("acceptance_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(value, str) or not value for value in refs):
            raise ManifestError(f"{subject}.acceptance_ids must be non-empty")
        if set(refs) - acceptance_ids:
            raise ManifestError(f"{subject} references unknown acceptance ids")
        domains = item.get("domain_ids")
        if (
            not isinstance(domains, list) or not domains
            or any(not isinstance(value, str) or not _DOMAIN.fullmatch(value) for value in domains)
            or len(set(domains)) != len(domains) or "P-14" not in domains
        ):
            raise ManifestError(f"{subject}.domain_ids must include valid P-14 coverage")
        thresholds = item.get("thresholds")
        if not isinstance(thresholds, list) or not thresholds:
            raise ManifestError(f"{subject}.thresholds must be non-empty")
        for threshold_index, threshold in enumerate(thresholds):
            target = f"{subject}.thresholds[{threshold_index}]"
            if not isinstance(threshold, Mapping):
                raise ManifestError(f"{target} must be an object")
            _required_string(threshold, "metric", target)
            _required_string(threshold, "statistic", target)
            if threshold.get("operator") not in {"<", "<=", ">", ">=", "=="}:
                raise ManifestError(f"{target}.operator is invalid")
            limit = threshold.get("limit")
            if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not math.isfinite(float(limit)):
                raise ManifestError(f"{target}.limit must be finite numeric")


def _valid_timeout(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and value > 0
    )


def _validate_release_artifacts(
    artifacts: Any,
    *,
    reserved_ids: set[str],
    acceptance_ids: set[str],
) -> None:
    """Admit release outputs without executing or trusting their contents yet."""

    if not isinstance(artifacts, list):
        raise ManifestError("release_artifacts must be a list")
    allowed_kinds = {"wheel", "sdist", "container", "npm", "binary"}
    seen: set[str] = set()
    for index, item in enumerate(artifacts):
        subject = f"release_artifacts[{index}]"
        if not isinstance(item, Mapping):
            raise ManifestError(f"{subject} must be an object")
        artifact_id = _required_id(item, "id", subject)
        if artifact_id in reserved_ids or artifact_id in seen:
            raise ManifestError(f"release artifact id collides or duplicates: {artifact_id}")
        seen.add(artifact_id)
        path = _required_string(item, "path", subject)
        path_obj = Path(path)
        if path_obj.is_absolute() or ".." in path_obj.parts:
            raise ManifestError(f"{subject}.path must stay inside the product root")
        if item.get("kind") not in allowed_kinds:
            raise ManifestError(f"{subject}.kind is invalid")
        if item.get("tier") != "release":
            raise ManifestError(f"{subject}.tier must be release")
        for field in ("mandatory", "deterministic"):
            if not isinstance(item.get(field), bool):
                raise ManifestError(f"{subject}.{field} must be boolean")
        refs = item.get("acceptance_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(value, str) or not value for value in refs):
            raise ManifestError(f"{subject}.acceptance_ids must be non-empty")
        missing = sorted(set(refs) - acceptance_ids)
        if missing:
            raise ManifestError(f"{subject} references unknown acceptance ids: {missing}")
        domains = item.get("domain_ids")
        if (
            not isinstance(domains, list)
            or not domains
            or any(not isinstance(value, str) or not _DOMAIN.fullmatch(value) for value in domains)
            or len(set(domains)) != len(domains)
            or "P-15" not in domains
        ):
            raise ManifestError(f"{subject}.domain_ids must include valid P-15 coverage")


def _validate_security_scans(
    scans: Any,
    *,
    reserved_ids: set[str],
    acceptance_ids: set[str],
) -> None:
    """Admit an owned, bounded ZAP report contract."""

    if not isinstance(scans, list):
        raise ManifestError("security_scans must be a list")
    seen: set[str] = set()
    for index, item in enumerate(scans):
        subject = f"security_scans[{index}]"
        if not isinstance(item, Mapping):
            raise ManifestError(f"{subject} must be an object")
        scan_id = _required_id(item, "id", subject)
        if scan_id in reserved_ids or scan_id in seen:
            raise ManifestError(f"security scan id collides or duplicates: {scan_id}")
        seen.add(scan_id)
        if item.get("tool", "zap") != "zap":
            raise ManifestError(f"{subject}.tool must be zap")
        report = _required_string(item, "report", subject)
        report_path = Path(report)
        if report_path.is_absolute() or ".." in report_path.parts:
            raise ManifestError(f"{subject}.report must stay inside the product root")
        _required_string(item, "command", subject)
        if item.get("tier") != "security":
            raise ManifestError(f"{subject}.tier must be security")
        for field in ("mandatory", "deterministic"):
            if not isinstance(item.get(field), bool):
                raise ManifestError(f"{subject}.{field} must be boolean")
        if not _valid_timeout(item.get("timeout_seconds")):
            raise ManifestError(f"{subject}.timeout_seconds must be positive")
        max_risk = item.get("max_risk")
        if isinstance(max_risk, bool) or not isinstance(max_risk, int) or max_risk not in range(4):
            raise ManifestError(f"{subject}.max_risk must be 0, 1, 2 or 3")
        refs = item.get("acceptance_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(value, str) or not value for value in refs):
            raise ManifestError(f"{subject}.acceptance_ids must be non-empty")
        missing = sorted(set(refs) - acceptance_ids)
        if missing:
            raise ManifestError(f"{subject} references unknown acceptance ids: {missing}")
        domains = item.get("domain_ids")
        if (
            not isinstance(domains, list)
            or not domains
            or any(not isinstance(value, str) or not _DOMAIN.fullmatch(value) for value in domains)
            or len(set(domains)) != len(domains)
            or "P-11" not in domains
        ):
            raise ManifestError(f"{subject}.domain_ids must include valid P-11 coverage")


def _validate_release_scenarios(
    scenarios: Any,
    *,
    reserved_ids: set[str],
    acceptance_ids: set[str],
) -> None:
    """Admit a bounded install → upgrade → rollback release lifecycle."""

    if not isinstance(scenarios, list):
        raise ManifestError("release_scenarios must be a list")
    seen: set[str] = set()
    for index, item in enumerate(scenarios):
        subject = f"release_scenarios[{index}]"
        if not isinstance(item, Mapping):
            raise ManifestError(f"{subject} must be an object")
        scenario_id = _required_id(item, "id", subject)
        if scenario_id in reserved_ids or scenario_id in seen:
            raise ManifestError(f"release scenario id collides or duplicates: {scenario_id}")
        seen.add(scenario_id)
        for phase in ("install_command", "upgrade_command", "rollback_command"):
            _required_string(item, phase, subject)
        if item.get("tier") != "release":
            raise ManifestError(f"{subject}.tier must be release")
        for field in ("mandatory", "deterministic"):
            if not isinstance(item.get(field), bool):
                raise ManifestError(f"{subject}.{field} must be boolean")
        if not _valid_timeout(item.get("timeout_seconds")):
            raise ManifestError(f"{subject}.timeout_seconds must be positive")
        refs = item.get("acceptance_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(value, str) or not value for value in refs):
            raise ManifestError(f"{subject}.acceptance_ids must be non-empty")
        missing = sorted(set(refs) - acceptance_ids)
        if missing:
            raise ManifestError(f"{subject} references unknown acceptance ids: {missing}")
        domains = item.get("domain_ids")
        if (
            not isinstance(domains, list)
            or not domains
            or any(not isinstance(value, str) or not _DOMAIN.fullmatch(value) for value in domains)
            or len(set(domains)) != len(domains)
            or "P-15" not in domains
        ):
            raise ManifestError(f"{subject}.domain_ids must include valid P-15 coverage")


def _validate_command_tokens(value: Any, subject: str) -> None:
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise ManifestError(f"{subject} must be a one-line command")
    try:
        if not shlex.split(value):
            raise ManifestError(f"{subject} must not be empty")
    except ValueError as exc:
        raise ManifestError(f"{subject} has invalid shell quoting") from exc


def _validate_relative_state_path(value: Any, subject: str) -> None:
    path = _required_string({"value": value}, "value", subject)
    path_obj = Path(path)
    if path_obj.is_absolute() or ".." in path_obj.parts:
        raise ManifestError(f"{subject} must stay inside the product root")


def _validate_recovery_scenarios(
    scenarios: Any,
    *,
    reserved_ids: set[str],
    acceptance_ids: set[str],
) -> None:
    if not isinstance(scenarios, list):
        raise ManifestError("recovery_scenarios must be a list")
    seen: set[str] = set()
    for index, item in enumerate(scenarios):
        subject = f"recovery_scenarios[{index}]"
        if not isinstance(item, Mapping):
            raise ManifestError(f"{subject} must be an object")
        scenario_id = _required_id(item, "id", subject)
        if scenario_id in reserved_ids or scenario_id in seen:
            raise ManifestError(f"recovery scenario id collides or duplicates: {scenario_id}")
        seen.add(scenario_id)
        _validate_command_tokens(item.get("command"), f"{subject}.command")
        _validate_relative_state_path(item.get("state_path"), f"{subject}.state_path")
        _validate_relative_state_path(item.get("ready_path"), f"{subject}.ready_path")
        if item.get("tier") not in {"integration", "e2e", "release"}:
            raise ManifestError(f"{subject}.tier is invalid")
        for field in ("mandatory", "deterministic"):
            if not isinstance(item.get(field), bool):
                raise ManifestError(f"{subject}.{field} must be boolean")
        if not _valid_timeout(item.get("timeout_seconds")):
            raise ManifestError(f"{subject}.timeout_seconds must be positive")
        refs = item.get("acceptance_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(value, str) or not value for value in refs):
            raise ManifestError(f"{subject}.acceptance_ids must be non-empty")
        if set(refs) - acceptance_ids:
            raise ManifestError(f"{subject} references unknown acceptance ids")
        domains = item.get("domain_ids")
        if (
            not isinstance(domains, list) or not domains
            or any(not isinstance(value, str) or not _DOMAIN.fullmatch(value) for value in domains)
            or len(set(domains)) != len(domains) or "P-12" not in domains
        ):
            raise ManifestError(f"{subject}.domain_ids must include valid P-12 coverage")


def _validate_concurrency_scenarios(
    scenarios: Any,
    *,
    reserved_ids: set[str],
    acceptance_ids: set[str],
) -> None:
    if not isinstance(scenarios, list):
        raise ManifestError("concurrency_scenarios must be a list")
    seen: set[str] = set()
    for index, item in enumerate(scenarios):
        subject = f"concurrency_scenarios[{index}]"
        if not isinstance(item, Mapping):
            raise ManifestError(f"{subject} must be an object")
        scenario_id = _required_id(item, "id", subject)
        if scenario_id in reserved_ids or scenario_id in seen:
            raise ManifestError(f"concurrency scenario id collides or duplicates: {scenario_id}")
        seen.add(scenario_id)
        commands = item.get("commands")
        if not isinstance(commands, list) or not commands:
            raise ManifestError(f"{subject}.commands must be non-empty")
        for command_index, command in enumerate(commands):
            _validate_command_tokens(command, f"{subject}.commands[{command_index}]")
        if item.get("tier") not in {"integration", "e2e", "release"}:
            raise ManifestError(f"{subject}.tier is invalid")
        for field in ("mandatory", "deterministic"):
            if not isinstance(item.get(field), bool):
                raise ManifestError(f"{subject}.{field} must be boolean")
        if not _valid_timeout(item.get("timeout_seconds")):
            raise ManifestError(f"{subject}.timeout_seconds must be positive")
        refs = item.get("acceptance_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(value, str) or not value for value in refs):
            raise ManifestError(f"{subject}.acceptance_ids must be non-empty")
        if set(refs) - acceptance_ids:
            raise ManifestError(f"{subject} references unknown acceptance ids")
        domains = item.get("domain_ids")
        if (
            not isinstance(domains, list) or not domains
            or any(not isinstance(value, str) or not _DOMAIN.fullmatch(value) for value in domains)
            or len(set(domains)) != len(domains) or "P-13" not in domains
        ):
            raise ManifestError(f"{subject}.domain_ids must include valid P-13 coverage")


def _validate_persistence_scenarios(
    scenarios: Any,
    *,
    reserved_ids: set[str],
    acceptance_ids: set[str],
) -> None:
    if not isinstance(scenarios, list):
        raise ManifestError("persistence_scenarios must be a list")
    seen: set[str] = set()
    for index, item in enumerate(scenarios):
        subject = f"persistence_scenarios[{index}]"
        if not isinstance(item, Mapping):
            raise ManifestError(f"{subject} must be an object")
        scenario_id = _required_id(item, "id", subject)
        if scenario_id in reserved_ids or scenario_id in seen:
            raise ManifestError(f"persistence scenario id collides or duplicates: {scenario_id}")
        seen.add(scenario_id)
        commands = item.get("commands")
        if not isinstance(commands, list) or not commands:
            raise ManifestError(f"{subject}.commands must be non-empty")
        for command_index, command in enumerate(commands):
            _validate_command_tokens(command, f"{subject}.commands[{command_index}]")
        if item.get("tier") not in {"integration", "release"}:
            raise ManifestError(f"{subject}.tier is invalid")
        for field in ("mandatory", "deterministic"):
            if not isinstance(item.get(field), bool):
                raise ManifestError(f"{subject}.{field} must be boolean")
        if not _valid_timeout(item.get("timeout_seconds")):
            raise ManifestError(f"{subject}.timeout_seconds must be positive")
        refs = item.get("acceptance_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(value, str) or not value for value in refs):
            raise ManifestError(f"{subject}.acceptance_ids must be non-empty")
        if set(refs) - acceptance_ids:
            raise ManifestError(f"{subject} references unknown acceptance ids")
        domains = item.get("domain_ids")
        if (
            not isinstance(domains, list) or not domains
            or any(not isinstance(value, str) or not _DOMAIN.fullmatch(value) for value in domains)
            or len(set(domains)) != len(domains) or "P-09" not in domains
        ):
            raise ManifestError(f"{subject}.domain_ids must include valid P-09 coverage")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_number(value: str) -> Any:
    raise ManifestError(f"non-standard JSON number: {value}")


def _validate_ids(rows: Any, subject: str) -> None:
    if not isinstance(rows, list):
        raise ManifestError(f"{subject} must be a list")
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ManifestError(f"{subject}[{index}] must be an object")
        value = _required_id(row, "id", f"{subject}[{index}]")
        if value in seen:
            raise ManifestError(f"duplicate {subject} id: {value}")
        seen.add(value)


def _validate_not_applicable(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ManifestError("not_applicable must be an object")
    for capability, reason in value.items():
        if not isinstance(capability, str) or not capability.strip():
            raise ManifestError("not_applicable keys must be non-empty strings")
        if not isinstance(reason, Mapping) or not isinstance(reason.get("reason"), str) or not reason["reason"].strip():
            raise ManifestError(f"not_applicable[{capability}] requires a reason")
        source = reason.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ManifestError(f"not_applicable[{capability}] requires a source")


def _validate_runtime_paths(value: Any) -> None:
    """Validate explicit product-generated files allowed across reruns."""

    if not isinstance(value, list):
        raise ManifestError("runtime_paths must be a list")
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ManifestError(f"runtime_paths[{index}] must be a non-empty relative path")
        path = Path(item)
        if path.is_absolute() or ".." in path.parts or "." == item:
            raise ManifestError(f"runtime_paths[{index}] must stay inside the product root")
        normalized = path.as_posix()
        if normalized in seen:
            raise ManifestError(f"duplicate runtime path: {normalized}")
        seen.add(normalized)

