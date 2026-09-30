from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from .manifest import ManifestError, ProductManifest, SCHEMA_VERSION, load_manifest, validate_manifest
from .planner import require_capability_decisions, require_domain_coverage, select_test_domains


def _manifest() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "project_type": "api",
        "source_root": ".",
        "target_commit": "a" * 40,
        "setup": [{"id": "install", "command": "uv sync", "timeout_seconds": 60}],
        "commands": [{
            "id": "unit",
            "command": "pytest -q",
            "tier": "unit",
            "acceptance_ids": ["AC-1"],
            "mandatory": True,
            "deterministic": True,
            "timeout_seconds": 120,
        }],
        "acceptances": [{"id": "AC-1"}],
        "entrypoints": [{
            "entrypoint_id": "api",
            "start_command": "python app.py",
            "health_check": "python health.py",
            "owner": "product-test",
            "timeout_seconds": 15,
        }],
        "capabilities": {"api": True},
        "budgets": {},
        "not_applicable": {"web": {"reason": "no browser surface", "source": "project profile"}},
        "cleanup": ["stop-test-service"],
    }


def _api_contract() -> dict:
    return {
        "id": "openapi-contract",
        "entrypoint_id": "api",
        "schema": "openapi.json",
        "base_url": "http://127.0.0.1:8000",
        "tool_version": "4.26.1",
        "tier": "integration",
        "acceptance_ids": ["AC-1"],
        "domain_ids": ["P-07"],
        "mandatory": True,
        "deterministic": True,
        "timeout_seconds": 20,
        "phases": ["fuzzing"],
        "mode": "positive",
        "checks": [
            "not_a_server_error",
            "status_code_conformance",
            "response_schema_conformance",
        ],
        "workers": 1,
        "seed": 1729,
        "max_examples": 1,
        "max_time_seconds": 5,
        "request_timeout_seconds": 1,
        "request_retries": 0,
    }


def test_valid_manifest_loads(tmp_path: Path):
    path = tmp_path / "product-test-manifest.json"
    path.write_text(json.dumps(_manifest()), encoding="utf-8")
    loaded = load_manifest(path)
    assert loaded.project_type == "api"
    assert loaded.commands[0]["id"] == "unit"


def test_unknown_top_level_manifest_fields_fail_closed():
    raw = _manifest()
    raw["comand_timeout_seconds"] = 30
    with pytest.raises(ManifestError, match="unknown manifest fields"):
        validate_manifest(raw)


@pytest.mark.parametrize(
    "runtime_paths",
    [
        ["../outside"],
        ["/absolute/path"],
        ["."],
        ["state.json", "state.json"],
        [""],
    ],
)
def test_runtime_paths_are_explicit_relative_unique_files(runtime_paths):
    raw = _manifest()
    raw["runtime_paths"] = runtime_paths
    with pytest.raises(ManifestError, match="runtime_paths|duplicate runtime path"):
        validate_manifest(raw)


def test_runtime_paths_are_exposed_as_normalized_strings():
    raw = _manifest()
    raw["runtime_paths"] = ["state.json", "tmp/result.json"]
    loaded = ProductManifest(raw)
    validate_manifest(raw)
    assert loaded.runtime_paths == ("state.json", "tmp/result.json")


def test_manifest_admits_versioned_stateless_openapi_contract(tmp_path: Path):
    raw = _manifest()
    raw["api_contracts"] = [_api_contract()]
    path = tmp_path / "product-test-manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_manifest(path)

    assert hasattr(loaded, "api_contracts"), "ProductManifest must expose admitted API contracts"
    assert loaded.api_contracts[0]["tool_version"] == "4.26.1"


def test_manifest_admits_versioned_docker_browser_contract(tmp_path: Path):
    raw = _manifest()
    raw["browser_tests"] = [{
        "id": "browser-smoke",
        "entrypoint_id": "api",
        "spec": "tests/e2e/smoke.spec.ts",
        "base_url": "http://127.0.0.1:8000",
        "image": "mcp/playwright:latest",
        "tier": "e2e",
        "acceptance_ids": ["AC-1"],
        "domain_ids": ["P-08"],
        "mandatory": True,
        "deterministic": True,
        "timeout_seconds": 180,
        "install_timeout_seconds": 60,
    }]
    validate_manifest(raw)
    loaded = ProductManifest(raw)
    assert loaded.browser_tests[0]["id"] == "browser-smoke"
    assert loaded.test_units[-1]["id"] == "browser-smoke"


def test_manifest_admits_dependency_container_contract():
    raw = _manifest()
    raw["dependency_containers"] = [{
        "id": "redis",
        "image": "redis:7-alpine",
        "command": "redis-server --save ''",
        "health_command": "redis-cli ping",
        "ports": ["6379:6379"],
        "timeout_seconds": 30,
        "mandatory": True,
    }]

    validate_manifest(raw)
    loaded = ProductManifest(raw)

    assert loaded.dependency_containers[0]["id"] == "redis"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda item: item.update(id="bad id"),
        lambda item: item.update(image="redis:7-alpine\nunsafe"),
        lambda item: item.update(command="echo x\nunsafe"),
        lambda item: item.update(health_command="echo x\runsafe"),
        lambda item: item.update(ports=["6379"]),
        lambda item: item.update(timeout_seconds=0),
        lambda item: item.update(mandatory="yes"),
    ],
)
def test_dependency_container_contract_fails_closed(mutator):
    raw = _manifest()
    container = {
        "id": "redis",
        "image": "redis:7-alpine",
        "command": "redis-server",
        "health_command": "redis-cli ping",
        "ports": ["6379:6379"],
        "timeout_seconds": 30,
        "mandatory": True,
    }
    mutator(container)
    raw["dependency_containers"] = [container]

    with pytest.raises(ManifestError):
        validate_manifest(raw)


def test_manifest_admits_local_mock_service_contract():
    raw = _manifest()
    raw["mock_services"] = [{
        "id": "provider",
        "routes": [{"method": "GET", "path": "/health", "status": 200, "body": {"ok": True}}],
    }]
    validate_manifest(raw)
    assert ProductManifest(raw).mock_services[0]["id"] == "provider"


@pytest.mark.parametrize("mutator", [
    lambda service: service.update(id="bad id"),
    lambda service: service.update(routes=[]),
    lambda service: service.update(routes=[{"method": "get", "path": "health", "status": 200, "body": {}}]),
    lambda service: service.update(routes=[{"method": "GET", "path": "/health", "status": 99, "body": {}}]),
    lambda service: service.update(routes=[{"method": "GET", "path": "/health", "status": 200, "body": {}, "failures_before_success": -1}]),
])
def test_mock_service_contract_fails_closed(mutator):
    raw = _manifest()
    service = {
        "id": "provider",
        "routes": [{"method": "GET", "path": "/health", "status": 200, "body": {"ok": True}}],
    }
    mutator(service)
    raw["mock_services"] = [service]
    with pytest.raises(ManifestError):
        validate_manifest(raw)


def test_manifest_admits_performance_contract():
    raw = _manifest()
    raw["performance_tests"] = [{
        "id": "load-smoke", "script": "tests/load.js", "image": "grafana/k6:1.5.0",
        "tier": "perf", "acceptance_ids": ["AC-1"], "domain_ids": ["P-14"],
        "mandatory": True, "deterministic": True, "timeout_seconds": 60,
        "thresholds": [{"metric": "http_req_duration", "statistic": "p(95)", "operator": "<", "limit": 500}],
    }]
    validate_manifest(raw)
    assert ProductManifest(raw).performance_tests[0]["id"] == "load-smoke"


def test_manifest_admits_release_artifact_contract():
    raw = _manifest()
    raw["release_artifacts"] = [{
        "id": "python-wheel", "path": "dist/product-1.0-py3-none-any.whl", "kind": "wheel",
        "tier": "release", "acceptance_ids": ["AC-1"], "domain_ids": ["P-15"],
        "mandatory": True, "deterministic": True,
    }]

    validate_manifest(raw)
    assert ProductManifest(raw).release_artifacts[0]["id"] == "python-wheel"
    assert ProductManifest(raw).test_units[-1]["id"] == "python-wheel"


def test_manifest_admits_zap_security_contract():
    raw = _manifest()
    raw["security_scans"] = [{
        "id": "zap-baseline", "tool": "zap", "command": "zap-baseline.py",
        "report": "evidence/zap.json", "max_risk": 0, "tier": "security",
        "acceptance_ids": ["AC-1"], "domain_ids": ["P-11"],
        "mandatory": True, "deterministic": True, "timeout_seconds": 180,
    }]
    validate_manifest(raw)
    assert ProductManifest(raw).security_scans[0]["id"] == "zap-baseline"
    assert ProductManifest(raw).test_units[-1]["id"] == "zap-baseline"


def test_manifest_admits_release_install_upgrade_rollback_scenario():
    raw = _manifest()
    raw["release_scenarios"] = [{
        "id": "upgrade-cycle", "install_command": "python install.py",
        "upgrade_command": "python upgrade.py", "rollback_command": "python rollback.py",
        "tier": "release", "acceptance_ids": ["AC-1"], "domain_ids": ["P-15"],
        "mandatory": True, "deterministic": True, "timeout_seconds": 120,
    }]
    validate_manifest(raw)
    assert ProductManifest(raw).release_scenarios[0]["id"] == "upgrade-cycle"


@pytest.mark.parametrize("mutator", [
    lambda item: item.update(install_command=""),
    lambda item: item.update(upgrade_command=""),
    lambda item: item.update(rollback_command=""),
    lambda item: item.update(tier="unit"),
    lambda item: item.update(domain_ids=["P-14"]),
    lambda item: item.update(timeout_seconds=0),
])
def test_release_scenario_contract_fails_closed(mutator):
    raw = _manifest()
    item = {
        "id": "upgrade-cycle", "install_command": "python install.py",
        "upgrade_command": "python upgrade.py", "rollback_command": "python rollback.py",
        "tier": "release", "acceptance_ids": ["AC-1"], "domain_ids": ["P-15"],
        "mandatory": True, "deterministic": True, "timeout_seconds": 120,
    }
    mutator(item)
    raw["release_scenarios"] = [item]
    with pytest.raises(ManifestError):
        validate_manifest(raw)


def test_manifest_admits_recovery_and_concurrency_scenarios():
    raw = _manifest()
    raw["recovery_scenarios"] = [{
        "id": "restart", "command": "python worker.py state.json ready", "state_path": "state.json",
        "ready_path": "ready", "tier": "integration", "acceptance_ids": ["AC-1"],
        "domain_ids": ["P-12"], "mandatory": True, "deterministic": True, "timeout_seconds": 30,
    }]
    raw["concurrency_scenarios"] = [{
        "id": "workers", "commands": ["python worker.py a", "python worker.py b"],
        "tier": "integration", "acceptance_ids": ["AC-1"], "domain_ids": ["P-13"],
        "mandatory": True, "deterministic": True, "timeout_seconds": 30,
    }]
    validate_manifest(raw)
    loaded = ProductManifest(raw)
    assert loaded.recovery_scenarios[0]["id"] == "restart"
    assert loaded.concurrency_scenarios[0]["id"] == "workers"


def test_manifest_admits_persistence_scenario():
    raw = _manifest()
    raw["persistence_scenarios"] = [{
        "id": "migration", "commands": ["python migrate.py", "python verify.py"],
        "tier": "integration", "acceptance_ids": ["AC-1"], "domain_ids": ["P-09"],
        "mandatory": True, "deterministic": True, "timeout_seconds": 60,
    }]
    validate_manifest(raw)
    assert ProductManifest(raw).persistence_scenarios[0]["id"] == "migration"


@pytest.mark.parametrize("field, value", [
    ("commands", []), ("commands", ["bad\ncommand"]), ("tier", "unit"),
    ("domain_ids", ["P-10"]), ("timeout_seconds", 0), ("acceptance_ids", ["missing"]),
])
def test_persistence_scenario_contract_fails_closed(field, value):
    raw = _manifest()
    raw["persistence_scenarios"] = [{
        "id": "migration", "commands": ["python migrate.py"],
        "tier": "integration", "acceptance_ids": ["AC-1"], "domain_ids": ["P-09"],
        "mandatory": True, "deterministic": True, "timeout_seconds": 60,
    }]
    raw["persistence_scenarios"][0][field] = value
    with pytest.raises(ManifestError):
        validate_manifest(raw)


@pytest.mark.parametrize("field, value", [
    ("command", ""), ("state_path", "../state.json"), ("ready_path", "/tmp/ready"),
    ("domain_ids", ["P-13"]), ("tier", "unit"),
])
def test_recovery_scenario_contract_fails_closed(field, value):
    raw = _manifest()
    item = {
        "id": "restart", "command": "python worker.py state.json ready", "state_path": "state.json",
        "ready_path": "ready", "tier": "integration", "acceptance_ids": ["AC-1"],
        "domain_ids": ["P-12"], "mandatory": True, "deterministic": True, "timeout_seconds": 30,
    }
    item[field] = value
    raw["recovery_scenarios"] = [item]
    with pytest.raises(ManifestError):
        validate_manifest(raw)


@pytest.mark.parametrize("mutator", [
    lambda item: item.update(report="../zap.json"),
    lambda item: item.update(tool="nmap"),
    lambda item: item.update(max_risk=4),
    lambda item: item.update(tier="release"),
    lambda item: item.update(domain_ids=["P-10"]),
    lambda item: item.update(command=""),
])
def test_security_scan_contract_fails_closed(mutator):
    raw = _manifest()
    item = {
        "id": "zap-baseline", "tool": "zap", "command": "zap-baseline.py",
        "report": "evidence/zap.json", "max_risk": 0, "tier": "security",
        "acceptance_ids": ["AC-1"], "domain_ids": ["P-11"],
        "mandatory": True, "deterministic": True, "timeout_seconds": 180,
    }
    mutator(item)
    raw["security_scans"] = [item]
    with pytest.raises(ManifestError):
        validate_manifest(raw)


@pytest.mark.parametrize("mutator", [
    lambda item: item.update(path="../outside.whl"),
    lambda item: item.update(path=""),
    lambda item: item.update(kind="unknown"),
    lambda item: item.update(tier="unit"),
    lambda item: item.update(domain_ids=["P-14"]),
    lambda item: item.update(acceptance_ids=["missing"]),
    lambda item: item.update(mandatory="yes"),
])
def test_release_artifact_contract_fails_closed(mutator):
    raw = _manifest()
    item = {
        "id": "python-wheel", "path": "dist/product-1.0-py3-none-any.whl", "kind": "wheel",
        "tier": "release", "acceptance_ids": ["AC-1"], "domain_ids": ["P-15"],
        "mandatory": True, "deterministic": True,
    }
    mutator(item)
    raw["release_artifacts"] = [item]
    with pytest.raises(ManifestError):
        validate_manifest(raw)


@pytest.mark.parametrize("mutator", [
    lambda item: item.update(script="../load.js"),
    lambda item: item.update(tier="integration"),
    lambda item: item.update(domain_ids=["P-07"]),
    lambda item: item.update(thresholds=[]),
    lambda item: item.update(thresholds=[{"metric": "x", "statistic": "p(95)", "operator": "?", "limit": 1}]),
])
def test_performance_contract_fails_closed(mutator):
    raw = _manifest()
    item = {
        "id": "load-smoke", "script": "tests/load.js", "image": "grafana/k6:1.5.0",
        "tier": "perf", "acceptance_ids": ["AC-1"], "domain_ids": ["P-14"],
        "mandatory": True, "deterministic": True, "timeout_seconds": 60,
        "thresholds": [{"metric": "http_req_duration", "statistic": "p(95)", "operator": "<", "limit": 500}],
    }
    mutator(item)
    raw["performance_tests"] = [item]
    with pytest.raises(ManifestError):
        validate_manifest(raw)


def test_openapi_contract_rejects_unknown_entrypoint():
    raw = _manifest()
    contract = _api_contract()
    contract["entrypoint_id"] = "missing-service"
    raw["api_contracts"] = [contract]

    with pytest.raises(ManifestError, match="unknown entrypoint"):
        validate_manifest(raw)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda c: c.update(schema=""), "schema"),
        (lambda c: c.update(base_url="ftp://127.0.0.1"), "base_url"),
        (lambda c: c.update(base_url="http://"), "base_url"),
        (lambda c: c.update(base_url="http://user:secret@127.0.0.1"), "credentials"),
        (lambda c: c.update(tool_version=""), "tool_version"),
        (lambda c: c.update(tier="unknown"), "tier"),
        (lambda c: c.update(acceptance_ids=["missing"]), "acceptance"),
        (lambda c: c.update(domain_ids=["P-99"]), "domain_ids"),
        (lambda c: c.update(timeout_seconds=0), "timeout_seconds"),
        (lambda c: c.update(phases=["stateful"]), "stateless"),
        (lambda c: c.update(mode="random"), "mode"),
        (lambda c: c.update(checks=["unknown_check"]), "checks"),
        (lambda c: c.update(workers=2), "workers"),
        (lambda c: c.update(seed=True), "seed"),
        (lambda c: c.update(max_examples=0), "max_examples"),
        (lambda c: c.update(max_time_seconds=0), "max_time_seconds"),
        (lambda c: c.update(max_time_seconds=1.5), "max_time_seconds"),
        (lambda c: c.update(request_timeout_seconds=0), "request_timeout_seconds"),
        (lambda c: c.update(request_retries=-1), "request_retries"),
    ],
)
def test_openapi_contract_fails_closed_on_unreproducible_or_unsafe_declaration(mutator, message):
    raw = _manifest()
    contract = _api_contract()
    mutator(contract)
    raw["api_contracts"] = [contract]

    with pytest.raises(ManifestError, match=message):
        validate_manifest(raw)


def test_openapi_contract_id_cannot_collide_with_other_test_units():
    raw = _manifest()
    contract = _api_contract()
    contract["id"] = "unit"
    raw["api_contracts"] = [contract]

    with pytest.raises(ManifestError, match="collide"):
        validate_manifest(raw)


@pytest.mark.parametrize("mutator", [
    lambda b: b.update(spec="../outside.spec.ts"),
    lambda b: b.update(base_url="ftp://127.0.0.1"),
    lambda b: b.update(image="mcp/playwright:latest\nunsafe"),
    lambda b: b.update(tier="integration"),
    lambda b: b.update(domain_ids=["P-07"]),
    lambda b: b.update(timeout_seconds=0),
    lambda b: b.update(install_timeout_seconds=0),
])
def test_browser_contract_fails_closed_on_unsafe_declaration(mutator):
    raw = _manifest()
    browser = {
        "id": "browser-smoke", "entrypoint_id": "api", "spec": "tests/e2e/smoke.spec.ts",
        "base_url": "http://127.0.0.1:8000", "image": "mcp/playwright:latest", "tier": "e2e",
        "acceptance_ids": ["AC-1"], "domain_ids": ["P-08"], "mandatory": True,
        "deterministic": True, "timeout_seconds": 180, "install_timeout_seconds": 60,
    }
    mutator(browser)
    raw["browser_tests"] = [browser]
    with pytest.raises(ManifestError):
        validate_manifest(raw)


@pytest.mark.parametrize("mutator", [
    lambda m: m.update(schema_version="wrong"),
    lambda m: m["commands"].append(dict(m["commands"][0])),
    lambda m: m["commands"][0].update(acceptance_ids=["missing"]),
    lambda m: m["commands"][0].update(timeout_seconds=0),
    lambda m: m["not_applicable"].update(web={"reason": ""}),
])
def test_invalid_manifest_fails_closed(mutator):
    manifest = _manifest()
    mutator(manifest)
    with pytest.raises(ManifestError):
        validate_manifest(manifest)


def test_not_applicable_requires_auditable_source():
    manifest = _manifest()
    manifest["not_applicable"]["web"].pop("source")
    with pytest.raises(ManifestError, match="requires a source"):
        validate_manifest(manifest)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_timeout_fails_closed(value):
    manifest = _manifest()
    manifest["commands"][0]["timeout_seconds"] = value
    with pytest.raises(ManifestError):
        validate_manifest(manifest)


def test_missing_acceptance_declaration_fails_closed():
    manifest = _manifest()
    manifest.pop("acceptances")
    with pytest.raises(ManifestError):
        validate_manifest(manifest)


@pytest.mark.parametrize("commit", ["main", "a" * 39, "g" * 40, "a" * 41, "g" * 64])
def test_target_commit_must_be_immutable_git_sha(commit):
    manifest = _manifest()
    manifest["target_commit"] = commit
    with pytest.raises(ManifestError, match="target_commit"):
        validate_manifest(manifest)


def test_target_commit_accepts_sha256_git_repository_identifier():
    manifest = _manifest()
    manifest["target_commit"] = "a" * 64
    validate_manifest(manifest)


def test_duplicate_setup_id_and_cross_namespace_collision_fail_closed():
    manifest = _manifest()
    manifest["setup"].append(dict(manifest["setup"][0]))
    with pytest.raises(ManifestError):
        validate_manifest(manifest)

    manifest = _manifest()
    manifest["setup"][0]["id"] = "unit"
    with pytest.raises(ManifestError):
        validate_manifest(manifest)


def test_json_duplicate_keys_and_nonstandard_numbers_fail_closed(tmp_path: Path):
    path = tmp_path / "manifest.json"
    path.write_text('{"schema_version":"product-test-manifest.v1", "schema_version":"product-test-manifest.v1"}', encoding="utf-8")
    with pytest.raises(ManifestError):
        load_manifest(path)
    path.write_text('{"timeout":NaN}', encoding="utf-8")
    with pytest.raises(ManifestError):
        load_manifest(path)


@pytest.mark.parametrize("domain_ids", [["P-00"], ["P-19"], ["P-07", "P-07"], [], [7]])
def test_invalid_command_domain_ids_fail_closed(domain_ids):
    manifest = _manifest()
    manifest["commands"][0]["domain_ids"] = domain_ids
    with pytest.raises(ManifestError):
        validate_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("entrypoint_id", "entrypoints[0].entrypoint_id"),
        ("start_command", "entrypoints[0].start_command"),
        ("health_check", "entrypoints[0].health_check"),
        ("owner", "entrypoints[0].owner"),
        ("timeout_seconds", "entrypoints[0].timeout_seconds"),
    ],
)
def test_entrypoint_requires_executable_lifecycle_contract(field, message):
    manifest = _manifest()
    manifest["entrypoints"][0].pop(field)

    with pytest.raises(ManifestError, match=message.replace("[", r"\[").replace("]", r"\]")):
        validate_manifest(manifest)


def test_entrypoint_ids_are_unique_and_stable():
    manifest = _manifest()
    manifest["entrypoints"].append(dict(manifest["entrypoints"][0]))
    with pytest.raises(ManifestError, match="duplicate entrypoint id: api"):
        validate_manifest(manifest)

    manifest = _manifest()
    manifest["entrypoints"][0]["entrypoint_id"] = "bad id"
    with pytest.raises(ManifestError, match="stable identifier"):
        validate_manifest(manifest)


@pytest.mark.parametrize("timeout", [0, -1, math.nan, math.inf])
def test_entrypoint_timeout_must_be_positive_and_finite(timeout):
    manifest = _manifest()
    manifest["entrypoints"][0]["timeout_seconds"] = timeout
    with pytest.raises(ManifestError, match="timeout_seconds"):
        validate_manifest(manifest)


def test_documented_manifest_template_is_admissible_after_placeholder_substitution():
    example_path = (
        Path(__file__).parents[2]
        / "awesome-zaofu"
        / "New_function"
        / "Test ZaoFu_Project"
        / "product-test-manifest.example.json"
    )
    raw = json.loads(example_path.read_text(encoding="utf-8"))
    raw["target_commit"] = "a" * 40
    raw["commands"][0]["command"] = "python -m pytest -q"
    raw["acceptances"][0]["description"] = "The declared product behavior passes"

    validate_manifest(raw)
    manifest = ProductManifest(raw)
    require_capability_decisions(manifest)
    selected = {
        domain
        for row in select_test_domains(manifest)
        if row["status"] == "selected"
        for domain in row["domains"]
    }
    require_domain_coverage(manifest, selected)
