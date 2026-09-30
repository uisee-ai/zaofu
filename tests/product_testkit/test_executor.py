from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys

import pytest

from .executor import (
    ProductHarnessError,
    _allowed_manifest_paths,
    run_manifest_file,
    run_product_plan,
    verify_source_root,
    verify_target_commit,
)
from .containers import ContainerError
from .manifest import ProductManifest


def _manifest(command: str = "python -c 'print(1)'") -> ProductManifest:
    return ProductManifest({
        "schema_version": "product-test-manifest.v1",
        "project_type": "other",
        "source_root": ".",
        "target_commit": "a" * 40,
        "setup": [],
        "commands": [{"id": "smoke", "command": command, "tier": "unit",
                       "acceptance_ids": ["AC-1"], "mandatory": True,
                       "deterministic": True, "timeout_seconds": 30}],
        "acceptances": [{"id": "AC-1"}], "entrypoints": [],
        "capabilities": {}, "budgets": {}, "not_applicable": {}, "cleanup": [],
    })


def test_target_commit_mismatch_fails_closed(tmp_path: Path):
    with pytest.raises(ProductHarnessError):
        verify_target_commit(tmp_path, "a" * 40)


@pytest.mark.parametrize("staged", [False, True])
def test_target_commit_rejects_tracked_worktree_changes_but_allows_manifest_sidecar(
    tmp_path: Path,
    staged: bool,
):
    (tmp_path / "product.py").write_text("print('v1')\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "TestKit"],
        ["git", "add", "product.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=tmp_path, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    (tmp_path / "product-test-manifest.json").write_text("{}\n", encoding="utf-8")
    verify_target_commit(
        tmp_path,
        commit.upper(),
        allowed_untracked=(tmp_path / "product-test-manifest.json",),
    )

    (tmp_path / "product.py").write_text("print('changed')\n", encoding="utf-8")
    if staged:
        subprocess.run(["git", "add", "product.py"], cwd=tmp_path, check=True)
    with pytest.raises(ProductHarnessError, match="tracked modifications"):
        verify_target_commit(tmp_path, commit)


def test_target_commit_rejects_untracked_product_source(tmp_path: Path):
    """Only explicitly allowed manifest sidecars may precede execution."""

    (tmp_path / "product.py").write_text("print('v1')\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "TestKit"],
        ["git", "add", "product.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=tmp_path, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    manifest = tmp_path / "product-test-manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    (tmp_path / "untracked_product.py").write_text("print('untracked')\n", encoding="utf-8")
    with pytest.raises(ProductHarnessError, match="untracked files"):
        verify_target_commit(tmp_path, commit, allowed_untracked=(manifest,))


def test_external_manifest_path_does_not_escape_product_root_allowlist(tmp_path: Path):
    """Adapters may keep the manifest outside the product checkout."""

    project = tmp_path / "product"
    project.mkdir()
    (project / "product.py").write_text("print('v1')\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "TestKit"],
        ["git", "add", "product.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    external_manifest = tmp_path / "adapter" / "product-test-manifest.json"
    external_manifest.parent.mkdir()
    external_manifest.write_text("{}\n", encoding="utf-8")

    manifest = ProductManifest({"schema_version": "product-test-manifest.v1"}, path=external_manifest)
    assert _allowed_manifest_paths(manifest, project) == ()
    verify_target_commit(project, commit)


def test_target_commit_post_run_check_rejects_tracked_mutation(tmp_path: Path):
    """A green command cannot detach evidence from its declared commit."""

    project = tmp_path / "product"
    project.mkdir()
    (project / "mutate.py").write_text(
        "from pathlib import Path\n"
        "Path('tracked.py').write_text('changed\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    (project / "tracked.py").write_text("original\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "TestKit"],
        ["git", "add", "mutate.py", "tracked.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project, text=True
    ).strip()
    (project / "product-test-manifest.json").write_text(
        json.dumps({
            "schema_version": "product-test-manifest.v1",
            "project_type": "library",
            "source_root": ".",
            "target_commit": commit,
            "setup": [],
            "commands": [{
                "id": "mutate",
                "command": f"{sys.executable} mutate.py",
                "tier": "unit",
                "acceptance_ids": ["AC-1"],
                "domain_ids": [f"P-{index:02d}" for index in range(1, 19)],
                "mandatory": True,
                "deterministic": True,
                "timeout_seconds": 10,
            }],
            "acceptances": [{"id": "AC-1"}],
            "entrypoints": [],
            "capabilities": {},
            "budgets": {},
            "not_applicable": {},
            "cleanup": [],
        }),
        encoding="utf-8",
    )
    result = run_manifest_file(
        project / "product-test-manifest.json",
        project_root=project,
        output_dir=tmp_path / "evidence",
    )
    assert result.harness_errors
    assert "post-run identity check" in result.harness_errors[0]["message"]
    assert result.failed_command_id == ""


def test_source_root_must_exist_and_stay_inside_project(tmp_path: Path):
    (tmp_path / "src").mkdir()
    assert verify_source_root(tmp_path, "src") == (tmp_path / "src").resolve()
    with pytest.raises(ProductHarnessError, match="not a directory"):
        verify_source_root(tmp_path, "missing")
    with pytest.raises(ProductHarnessError, match="escapes"):
        verify_source_root(tmp_path, "../outside")


def test_plan_runs_independent_command_and_writes_receipt(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("tests.product_testkit.executor.verify_target_commit", lambda *a, **k: None)
    result = run_product_plan(_manifest(), project_root=tmp_path, output_dir=tmp_path / "out")
    assert result.passed
    assert result.receipts[0].command_id == "smoke"
    assert any(row["capability"] == "core" for row in result.domains)
    assert (tmp_path / "out" / "smoke.receipt.json").exists()


def test_plan_stops_on_failed_command(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("tests.product_testkit.executor.verify_target_commit", lambda *a, **k: None)
    result = run_product_plan(_manifest("python -c 'raise SystemExit(3)'"), project_root=tmp_path, output_dir=tmp_path / "out")
    assert not result.passed
    assert result.failed_command_id == "smoke"


def test_executor_revalidates_directly_constructed_manifest(tmp_path: Path, monkeypatch):
    called = False

    def unexpected(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("invalid manifest must not execute a product command")

    monkeypatch.setattr("tests.product_testkit.executor.run_command", unexpected)
    raw = _manifest().raw
    raw["commands"][0]["acceptance_ids"] = ["undeclared"]
    with pytest.raises(ValueError):
        run_product_plan(ProductManifest(raw), project_root=tmp_path, output_dir=tmp_path / "out", verify_commit=False)
    assert not called


def test_executor_can_enforce_explicit_domain_coverage(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("tests.product_testkit.executor.verify_target_commit", lambda *a, **k: None)
    with pytest.raises(ValueError, match="P-01"):
        run_product_plan(_manifest(), project_root=tmp_path, output_dir=tmp_path / "out",
                         verify_commit=False, enforce_domain_coverage=True)


def test_manifest_file_is_strict_canonical_entrypoint(tmp_path: Path, monkeypatch):
    manifest = _manifest().raw
    manifest["commands"][0]["domain_ids"] = [f"P-{index:02d}" for index in range(1, 19)]
    path = tmp_path / "product-test-manifest.json"
    import json
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr("tests.product_testkit.executor.verify_target_commit", lambda *a, **k: None)
    result = run_manifest_file(path, project_root=tmp_path, output_dir=tmp_path / "out", verify_commit=False)
    assert result.passed


def test_manifest_file_can_dispatch_only_selected_domains(tmp_path: Path, monkeypatch):
    manifest = _manifest().raw
    manifest["commands"].append({**manifest["commands"][0], "id": "api", "domain_ids": ["P-07"]})
    manifest["commands"][0]["domain_ids"] = ["P-01"]
    path = tmp_path / "product-test-manifest.json"
    import json
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr("tests.product_testkit.executor.verify_target_commit", lambda *a, **k: None)
    result = run_manifest_file(path, project_root=tmp_path, output_dir=tmp_path / "out",
                               verify_commit=False, selected_domains={"P-07"})
    assert [receipt.command_id for receipt in result.receipts] == ["api"]


def test_selected_command_ids_do_not_execute_unselected_api_contract(tmp_path: Path, monkeypatch):
    """A CI selection is an execution boundary for every test-unit kind."""

    manifest = _manifest().raw
    manifest["commands"][0]["domain_ids"] = ["P-01"]
    manifest["api_contracts"] = [{
        "id": "openapi",
        "entrypoint_id": "service",
        "schema": "missing-openapi.json",
        "base_url": "http://127.0.0.1:1",
        "tool_version": "4.26.1",
        "tier": "integration",
        "acceptance_ids": ["AC-1"],
        "domain_ids": ["P-07"],
        "mandatory": True,
        "deterministic": True,
        "timeout_seconds": 10,
        "phases": ["fuzzing"],
        "mode": "positive",
        "checks": ["not_a_server_error"],
        "workers": 1,
        "seed": 1,
        "max_examples": 1,
        "max_time_seconds": 1,
        "request_timeout_seconds": 1,
        "request_retries": 0,
    }]
    manifest["entrypoints"] = [{
        "entrypoint_id": "service",
        "start_command": "python -c 'pass'",
        "health_check": "python -c 'pass'",
        "owner": "test",
        "timeout_seconds": 1,
    }]
    monkeypatch.setattr("tests.product_testkit.executor.verify_target_commit", lambda *a, **k: None)
    monkeypatch.setattr(
        "tests.product_testkit.executor.start_entrypoint",
        lambda *a, **k: type("Handle", (), {"stop": lambda self: None})(),
    )

    def unexpected_api_execution(*args, **kwargs):
        raise AssertionError("an unselected API contract must not execute")

    monkeypatch.setattr("tests.product_testkit.executor.run_schemathesis_contract", unexpected_api_execution)
    result = run_product_plan(
        ProductManifest(manifest),
        project_root=tmp_path,
        output_dir=tmp_path / "out",
        verify_commit=False,
        selected_command_ids={"smoke"},
    )

    assert result.passed
    assert result.planned_command_ids == ("smoke",)
    assert result.api_contracts == ()


def test_selected_command_ids_execute_selected_browser_contract(tmp_path: Path, monkeypatch):
    manifest = _manifest().raw
    manifest["commands"][0]["domain_ids"] = ["P-01"]
    manifest["browser_tests"] = [{
        "id": "browser", "entrypoint_id": "service", "spec": "tests/smoke.spec.ts",
        "base_url": "http://127.0.0.1:8000", "image": "mcp/playwright:latest",
        "tier": "e2e", "acceptance_ids": ["AC-1"], "domain_ids": ["P-08"],
        "mandatory": True, "deterministic": True, "timeout_seconds": 10,
        "install_timeout_seconds": 1, "install_browser": False,
    }]
    manifest["entrypoints"] = [{
        "entrypoint_id": "service", "start_command": "python -c 'pass'",
        "health_check": "python -c 'pass'", "owner": "test", "timeout_seconds": 1,
    }]
    monkeypatch.setattr("tests.product_testkit.executor.verify_target_commit", lambda *a, **k: None)
    monkeypatch.setattr(
        "tests.product_testkit.executor.start_entrypoint",
        lambda *a, **k: type("Handle", (), {"stop": lambda self: None})(),
    )
    monkeypatch.setattr(
        "tests.product_testkit.executor.run_playwright_contract",
        lambda *a, **k: {"test_id": "browser", "verdict": "pass"},
    )
    result = run_product_plan(
        ProductManifest(manifest), project_root=tmp_path, output_dir=tmp_path / "out",
        verify_commit=False, selected_command_ids={"browser"},
    )
    assert result.passed
    assert result.browser_tests == ({"test_id": "browser", "verdict": "pass", "mandatory": True, "deterministic": True},)
    assert result.receipts == ()


def test_plan_starts_and_stops_declared_dependency_container(tmp_path: Path, monkeypatch):
    manifest = _manifest().raw
    manifest["dependency_containers"] = [{
        "id": "db",
        "image": "alpine:3.20",
        "command": "sleep 30",
        "health_command": "test -f /etc/alpine-release",
        "ports": [],
        "timeout_seconds": 5,
        "mandatory": True,
    }]
    seen: dict[str, object] = {}

    class FakeHandle:
        name = "zf-db-test"
        container_id = "container-id"
        env = {"ZF_CONTAINER_DB_NAME": name, "ZF_CONTAINER_DB_ID": container_id}

        def stop(self):
            seen["stopped"] = True
            return type("Receipt", (), {
                "container_id": self.container_id,
                "name": self.name,
                "image": "alpine:3.20",
                "healthy": True,
                "stopped": True,
                "passed": True,
            })()

    def fake_start(spec, *, output_dir, name=None):
        seen["started"] = spec["id"]
        return FakeHandle()

    monkeypatch.setattr("tests.product_testkit.executor.start_dependency_container", fake_start)
    monkeypatch.setattr("tests.product_testkit.executor.verify_target_commit", lambda *a, **k: None)
    result = run_product_plan(ProductManifest(manifest), project_root=tmp_path, output_dir=tmp_path / "out")

    assert result.passed
    assert seen == {"started": "db", "stopped": True}
    assert result.containers[0].container_id == "container-id"


def test_container_start_environment_error_is_not_reported_as_product_failure(tmp_path: Path, monkeypatch):
    manifest = _manifest().raw
    manifest["dependency_containers"] = [{
        "id": "db", "image": "missing:tag", "command": "sleep 1",
        "health_command": "true", "ports": [], "timeout_seconds": 5, "mandatory": True,
    }]
    monkeypatch.setattr("tests.product_testkit.executor.verify_target_commit", lambda *a, **k: None)
    def fail_start(*args, **kwargs):
        raise ContainerError("docker daemon unavailable", environment_error=True)

    monkeypatch.setattr("tests.product_testkit.executor.start_dependency_container", fail_start)

    result = run_product_plan(ProductManifest(manifest), project_root=tmp_path, output_dir=tmp_path / "out")

    assert not result.passed
    assert result.failed_container_id == "db"
    assert result.container_environment_error is True
    assert result.harness_errors == ()
