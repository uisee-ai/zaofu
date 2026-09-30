from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys
import pytest
from xml.etree.ElementTree import parse

from .runner import run_manifest_and_collect
from . import runner as runner_module
from .containers import ContainerReceipt
from .test_acceptance import _journey, _spec


def test_unified_runner_collects_all_machine_reports(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    import subprocess
    (project / "x.py").write_text("print('ok')\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project, check=True)
    subprocess.run(["git", "add", "x.py"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {"schema_version": "product-test-manifest.v1", "project_type": "library",
                "source_root": ".", "target_commit": commit, "setup": [],
                "commands": [{"id": "smoke", "command": "python x.py", "tier": "unit",
                               "acceptance_ids": ["AC-1"], "domain_ids": [f"P-{i:02d}" for i in range(1, 19)],
                               "mandatory": True, "deterministic": True, "timeout_seconds": 20}],
                "acceptances": [{"id": "AC-1"}], "entrypoints": [], "capabilities": {},
                "budgets": {}, "not_applicable": {}, "cleanup": []}
    path = project / "product-test-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = run_manifest_and_collect(path, project_root=project, output_dir=tmp_path / "out", run_id="r1")
    assert result["verdict"] == "pass"
    assert (tmp_path / "out" / "junit.xml").exists()
    assert (tmp_path / "out" / "failure-bundle.json").exists()
    index = json.loads((tmp_path / "out" / "artifact-index.json").read_text())
    assert len(index) == 3
    assert all(set(item) == {"ref", "digest"} for item in index)
    persisted = json.loads((tmp_path / "out" / "verification-result.json").read_text())
    assert "ci_plan" in persisted and "domains" in persisted
    assert persisted["artifact_index_ref"] == "artifact-index.json"


def test_unified_runner_requires_declared_release_artifact_and_indexes_copy(tmp_path: Path):
    import subprocess

    project = tmp_path / "project"
    project.mkdir()
    (project / "build.py").write_text(
        "from pathlib import Path\n"
        "Path('dist').mkdir(exist_ok=True)\n"
        "Path('dist/product-1.0-py3-none-any.whl').write_bytes(b'wheel')\n",
        encoding="utf-8",
    )
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "build.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1", "project_type": "library",
        "source_root": ".", "target_commit": commit,
        "setup": [],
        "commands": [{
            "id": "build", "command": f"{sys.executable} build.py", "tier": "release",
            "acceptance_ids": ["AC-1"], "domain_ids": [f"P-{i:02d}" for i in range(1, 19)],
            "mandatory": True, "deterministic": True, "timeout_seconds": 20,
        }],
        "release_artifacts": [{
            "id": "wheel", "path": "dist/product-1.0-py3-none-any.whl", "kind": "wheel",
            "tier": "release", "acceptance_ids": ["AC-1"], "domain_ids": ["P-15"],
            "mandatory": True, "deterministic": True,
        }],
        "release_scenarios": [{
            "id": "upgrade-cycle", "install_command": "printf install",
            "upgrade_command": "printf upgrade", "rollback_command": "printf rollback",
            "tier": "release", "acceptance_ids": ["AC-1"], "domain_ids": ["P-15"],
            "mandatory": True, "deterministic": True, "timeout_seconds": 20,
        }],
        "acceptances": [{"id": "AC-1"}], "entrypoints": [], "capabilities": {},
        "budgets": {}, "not_applicable": {}, "cleanup": [],
    }
    manifest_path = project / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out"

    result = run_manifest_and_collect(
        manifest_path, project_root=project, output_dir=output, run_id="release-run",
    )

    assert result["verdict"] == "pass"
    assert result["release_artifacts"][0]["passed"] is True
    refs = {row["ref"] for row in result["artifact_index"]}
    assert "release/wheel/product-1.0-py3-none-any.whl" in refs
    assert "release.wheel.receipt.json" in refs
    assert result["release_scenarios"][0]["passed"] is True
    assert "release-scenario.upgrade-cycle" in {case.attrib["name"] for case in parse(output / "junit.xml").getroot().findall("testcase")}
    assert "wheel" in {row["command_id"] for row in result["command_plan"]}


def test_unified_runner_executes_persistence_scenario_and_indexes_receipt(tmp_path: Path):
    import subprocess

    project = tmp_path / "project"
    project.mkdir()
    (project / "noop.py").write_text("print('ok')\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "noop.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1", "project_type": "library",
        "source_root": ".", "target_commit": commit, "setup": [],
        "commands": [{"id": "smoke", "command": f"{sys.executable} noop.py", "tier": "unit",
                       "acceptance_ids": ["AC-1"], "domain_ids": [f"P-{i:02d}" for i in range(1, 19)],
                       "mandatory": True, "deterministic": True, "timeout_seconds": 20}],
        "persistence_scenarios": [{"id": "migration", "commands": [f"{sys.executable} -c print(1)"],
                                     "tier": "integration", "acceptance_ids": ["AC-1"],
                                     "domain_ids": ["P-09"], "mandatory": True,
                                     "deterministic": True, "timeout_seconds": 20}],
        "acceptances": [{"id": "AC-1"}], "entrypoints": [], "capabilities": {},
        "budgets": {}, "not_applicable": {}, "cleanup": [],
    }
    path = project / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out"
    result = run_manifest_and_collect(path, project_root=project, output_dir=output, run_id="persistence-run")
    assert result["verdict"] == "pass"
    assert result["persistence_scenarios"][0]["passed"] is True
    refs = {row["ref"] for row in result["artifact_index"]}
    assert "persistence.migration.receipt.json" in refs
    assert "persistence.migration" in {case.attrib["name"] for case in parse(output / "junit.xml").getroot().findall("testcase")}


def test_unified_runner_reports_missing_release_artifact_as_product_failure(tmp_path: Path):
    import subprocess

    project = tmp_path / "project"
    project.mkdir()
    (project / "noop.py").write_text("pass\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "noop.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1", "project_type": "library",
        "source_root": ".", "target_commit": commit, "setup": [],
        "commands": [{"id": "smoke", "command": f"{sys.executable} noop.py", "tier": "release",
                       "acceptance_ids": ["AC-1"], "domain_ids": [f"P-{i:02d}" for i in range(1, 19)], "mandatory": True,
                       "deterministic": True, "timeout_seconds": 20}],
        "release_artifacts": [{"id": "wheel", "path": "dist/missing.whl", "kind": "wheel",
                                "tier": "release", "acceptance_ids": ["AC-1"], "domain_ids": ["P-15"],
                                "mandatory": True, "deterministic": True}],
        "acceptances": [{"id": "AC-1"}], "entrypoints": [], "capabilities": {},
        "budgets": {}, "not_applicable": {}, "cleanup": [],
    }
    manifest_path = project / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = run_manifest_and_collect(
        manifest_path, project_root=project, output_dir=tmp_path / "out", run_id="release-fail",
    )
    assert result["verdict"] == "fail"
    assert result["failure_phase"] == "release_artifact"
    assert result["release_artifacts"][0]["passed"] is False


def test_unified_runner_executes_zap_contract_and_indexes_report(tmp_path: Path):
    import subprocess

    project = tmp_path / "project"
    project.mkdir()
    (project / "noop.py").write_text("pass\n", encoding="utf-8")
    (project / "scanner.py").write_text(
        "from pathlib import Path\n"
        "import json\n"
        "Path('evidence').mkdir(exist_ok=True)\n"
        "Path('evidence/zap.json').write_text(json.dumps({'@programName':'ZAP','site':[{'alerts':[]}]}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "noop.py", "scanner.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1", "project_type": "api",
        "source_root": ".", "target_commit": commit, "setup": [],
        "commands": [{"id": "smoke", "command": f"{sys.executable} noop.py", "tier": "unit",
                       "acceptance_ids": ["AC-1"], "domain_ids": [f"P-{i:02d}" for i in range(1, 19)],
                       "mandatory": True, "deterministic": True, "timeout_seconds": 20}],
        "security_scans": [{"id": "zap", "tool": "zap", "command": f"{sys.executable} scanner.py",
                             "report": "evidence/zap.json", "max_risk": 0, "tier": "security",
                             "acceptance_ids": ["AC-1"], "domain_ids": ["P-11"], "mandatory": True,
                             "deterministic": True, "timeout_seconds": 20}],
        "acceptances": [{"id": "AC-1"}], "entrypoints": [], "capabilities": {},
        "budgets": {}, "not_applicable": {}, "cleanup": [],
    }
    manifest_path = project / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out"
    result = run_manifest_and_collect(manifest_path, project_root=project, output_dir=output, run_id="zap-run")

    assert result["verdict"] == "pass"
    assert result["security_scans"][0]["verdict"] == "pass"
    refs = {row["ref"] for row in result["artifact_index"]}
    assert "security/zap/zap-report.json" in refs
    assert "security/zap/result.json" in refs


def test_unified_runner_reports_dependency_container_and_indexes_its_evidence(tmp_path: Path, monkeypatch):
    import subprocess

    project = tmp_path / "project"
    project.mkdir()
    (project / "product.txt").write_text("fixture\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "product.txt"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    domains = [f"P-{index:02d}" for index in range(1, 19)]
    manifest = {
        "schema_version": "product-test-manifest.v1", "project_type": "library",
        "source_root": ".", "target_commit": commit, "setup": [],
        "commands": [{"id": "smoke", "command": f"{sys.executable} -c 'print(1)'", "tier": "integration",
                       "acceptance_ids": ["AC-1"], "domain_ids": domains, "mandatory": True,
                       "deterministic": True, "timeout_seconds": 20}],
        "acceptances": [{"id": "AC-1"}], "entrypoints": [], "capabilities": {}, "budgets": {},
        "not_applicable": {}, "cleanup": [],
        "dependency_containers": [{"id": "db", "image": "alpine:3.20", "command": "sleep 10",
                                    "health_command": "test -f /etc/alpine-release", "ports": [],
                                    "timeout_seconds": 5, "mandatory": True}],
    }
    manifest_path = project / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out"

    class FakeHandle:
        container_id = "cid"
        env = {"ZF_CONTAINER_DB_NAME": "zf-db", "ZF_CONTAINER_DB_ID": "cid"}

        def stop(self):
            refs = {
                "stdout_ref": output / "container.zf-db.stdout.log",
                "stderr_ref": output / "container.zf-db.stderr.log",
                "health_stdout_ref": output / "container.zf-db.health.stdout.log",
                "health_stderr_ref": output / "container.zf-db.health.stderr.log",
                "receipt_ref": output / "container.zf-db.receipt.json",
            }
            for path in refs.values():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            receipt = ContainerReceipt(
                container_id="cid", name="zf-db", image="alpine:3.20", start_command="sleep 10",
                start_command_digest="start", health_command="test -f /etc/alpine-release",
                health_command_digest="health", ports=(), healthy=True, health_attempts=1,
                started=True, stopped=True, exit_code=0, timed_out=False, environment_error=False,
                stdout_ref=str(refs["stdout_ref"]), stderr_ref=str(refs["stderr_ref"]),
                health_stdout_ref=str(refs["health_stdout_ref"]), health_stderr_ref=str(refs["health_stderr_ref"]),
                receipt_ref=str(refs["receipt_ref"]), started_at="now", ready_at="now",
            )
            refs["receipt_ref"].write_text(json.dumps({**receipt.__dict__, "passed": True}), encoding="utf-8")
            return receipt

    monkeypatch.setattr(runner_module, "run_manifest_file", runner_module.run_manifest_file)
    monkeypatch.setattr("tests.product_testkit.executor.start_dependency_container", lambda *a, **k: FakeHandle())
    result = run_manifest_and_collect(manifest_path, project_root=project, output_dir=output, run_id="container-run")

    assert result["verdict"] == "pass"
    assert result["containers"][0]["name"] == "zf-db"
    assert "container.zf-db.receipt.json" in {row["ref"] for row in result["artifact_index"]}


def test_unified_runner_drives_local_mock_service_and_records_contract_evidence(tmp_path: Path):
    import subprocess

    project = tmp_path / "project"
    project.mkdir()
    (project / "product.txt").write_text("fixture\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "product.txt"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    domains = [f"P-{index:02d}" for index in range(1, 19)]
    code = (
        "import os,urllib.request,json; "
        "o=urllib.request.build_opener(urllib.request.ProxyHandler({})); "
        "r=o.open(os.environ['ZF_MOCK_PROVIDER_BASE_URL']+'/health'); "
        "assert json.load(r)['ok'] is True"
    )
    manifest = {
        "schema_version": "product-test-manifest.v1", "project_type": "worker",
        "source_root": ".", "target_commit": commit, "setup": [],
        "commands": [{"id": "integration-smoke", "command": f'{sys.executable} -c "{code}"',
                       "tier": "integration", "acceptance_ids": ["AC-1"], "domain_ids": domains,
                       "mandatory": True, "deterministic": True, "timeout_seconds": 20}],
        "acceptances": [{"id": "AC-1"}], "entrypoints": [], "capabilities": {}, "budgets": {},
        "not_applicable": {}, "cleanup": [],
        "mock_services": [{"id": "provider", "routes": [{"method": "GET", "path": "/health",
                                                              "status": 200, "body": {"ok": True}}]}],
    }
    manifest_path = project / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out"

    result = run_manifest_and_collect(manifest_path, project_root=project, output_dir=output, run_id="mock-run")

    assert result["verdict"] == "pass"
    [mock] = result["mock_services"]
    assert mock["request_count"] == 1
    assert mock["passed"] is True
    indexed = {row["ref"] for row in result["artifact_index"]}
    assert "mock.provider.requests.json" in indexed
    assert "mock.provider.receipt.json" in indexed


def test_unified_runner_executes_setup_service_health_tests_stop_and_cleanup_in_order(tmp_path: Path):
    import subprocess

    project = tmp_path / "project"
    project.mkdir()
    (project / "setup.py").write_text(
        "from pathlib import Path\nPath('configured').write_text('yes', encoding='utf-8')\n",
        encoding="utf-8",
    )
    (project / "service.py").write_text(
        "from pathlib import Path\n"
        "import os, sys, time\n"
        "if not Path('configured').is_file(): raise SystemExit(11)\n"
        "Path('service.pid').write_text(str(os.getpid()), encoding='ascii')\n"
        "Path('ready').write_text('yes', encoding='utf-8')\n"
        "print('service-running', flush=True)\n"
        "while True: time.sleep(1)\n",
        encoding="utf-8",
    )
    (project / "health.py").write_text(
        "from pathlib import Path\n"
        "raise SystemExit(0 if Path('ready').is_file() else 9)\n",
        encoding="utf-8",
    )
    (project / "test_surface.py").write_text(
        "from pathlib import Path\n"
        "assert Path('ready').read_text(encoding='utf-8') == 'yes'\n",
        encoding="utf-8",
    )
    (project / "cleanup.py").write_text(
        "from pathlib import Path\n"
        "pid = int(Path('service.pid').read_text(encoding='ascii'))\n"
        "assert not Path(f'/proc/{pid}').exists(), 'service still running during cleanup'\n"
        "Path('cleanup-ok').write_text('yes', encoding='utf-8')\n",
        encoding="utf-8",
    )
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "setup.py", "service.py", "health.py", "test_surface.py", "cleanup.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1",
        "project_type": "api",
        "source_root": ".",
        "target_commit": commit,
        "setup": [{
            "id": "setup",
            "command": f"{sys.executable} setup.py",
            "timeout_seconds": 10,
        }],
        "commands": [{
            "id": "surface",
            "command": f"{sys.executable} test_surface.py",
            "tier": "integration",
            "acceptance_ids": ["AC-1"],
            "domain_ids": [f"P-{index:02d}" for index in range(1, 19)],
            "mandatory": True,
            "deterministic": True,
            "timeout_seconds": 10,
        }],
        "acceptances": [{"id": "AC-1"}],
        "entrypoints": [{
            "entrypoint_id": "api",
            "start_command": f"{sys.executable} service.py",
            "health_check": f"{sys.executable} health.py",
            "owner": "product-test",
            "timeout_seconds": 5,
        }],
        "capabilities": {"api": True},
        "budgets": {},
        "not_applicable": {},
        "cleanup": [f"{sys.executable} cleanup.py"],
    }
    manifest_path = project / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out"

    result = run_manifest_and_collect(
        manifest_path,
        project_root=project,
        output_dir=output,
        run_id="service-run",
    )

    assert result["verdict"] == "pass"
    [service_result] = result["services"]
    assert service_result["entrypoint_id"] == "api"
    assert service_result["healthy"] is True
    assert service_result["stopped"] is True
    assert service_result["passed"] is True
    assert result["cleanup"][0]["passed"] is True
    assert (project / "cleanup-ok").read_text(encoding="utf-8") == "yes"
    indexed_refs = {row["ref"] for row in result["artifact_index"]}
    assert {
        "entrypoint.api.stdout.log",
        "entrypoint.api.stderr.log",
        "entrypoint.api.health.stdout.log",
        "entrypoint.api.health.stderr.log",
        "entrypoint.api.receipt.json",
    } <= indexed_refs


def test_unified_runner_rechecks_target_after_cleanup_mutation(tmp_path: Path):
    import subprocess

    project = tmp_path / "project"
    project.mkdir()
    (project / "product.txt").write_text("baseline\n", encoding="utf-8")
    (project / "cleanup.py").write_text(
        "from pathlib import Path\n"
        "Path('product.txt').write_text('mutated by cleanup\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    for command in (
        ["git", "init", "-q"], ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Test"], ["git", "add", "product.txt", "cleanup.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1", "project_type": "library",
        "source_root": ".", "target_commit": commit, "setup": [],
        "commands": [{"id": "smoke", "command": "true", "tier": "unit",
                       "acceptance_ids": ["AC-1"], "domain_ids": [f"P-{i:02d}" for i in range(1, 19)],
                       "mandatory": True, "deterministic": True, "timeout_seconds": 10}],
        "acceptances": [{"id": "AC-1"}], "entrypoints": [], "capabilities": {},
        "budgets": {}, "not_applicable": {}, "cleanup": [f"{sys.executable} cleanup.py"],
    }
    manifest_path = project / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = run_manifest_and_collect(
        manifest_path, project_root=project, output_dir=tmp_path / "out", run_id="cleanup-identity",
    )

    assert result["verdict"] == "harness_failure"
    assert any("post-run identity check" in row["message"] for row in result["errors"])
    assert result["cleanup"][0]["passed"] is True


def test_unified_runner_returns_terminal_environment_failure_when_entrypoint_cannot_start(tmp_path: Path):
    import subprocess

    project = tmp_path / "project"
    project.mkdir()
    (project / "product.txt").write_text("fixture\n", encoding="utf-8")
    (project / "cleanup.py").write_text(
        "from pathlib import Path\nPath('cleanup-ran').write_text('yes', encoding='utf-8')\n",
        encoding="utf-8",
    )
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "product.txt", "cleanup.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1",
        "project_type": "worker",
        "source_root": ".",
        "target_commit": commit,
        "setup": [],
        "commands": [{
            "id": "worker-contract",
            "command": f"{sys.executable} -c 'raise SystemExit(0)'",
            "tier": "integration",
            "acceptance_ids": ["AC-1"],
            "domain_ids": [f"P-{index:02d}" for index in range(1, 19)],
            "mandatory": True,
            "deterministic": True,
            "timeout_seconds": 10,
        }],
        "acceptances": [{"id": "AC-1"}],
        "entrypoints": [{
            "entrypoint_id": "worker",
            "start_command": "definitely-missing-product-service",
            "health_check": f"{sys.executable} -c 'raise SystemExit(0)'",
            "owner": "product-test",
            "timeout_seconds": 1,
        }],
        "capabilities": {"long_running": True},
        "budgets": {},
        "not_applicable": {},
        "cleanup": [f"{sys.executable} cleanup.py"],
    }
    manifest_path = project / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out"

    result = run_manifest_and_collect(
        manifest_path,
        project_root=project,
        output_dir=output,
        run_id="entrypoint-failure",
    )

    assert result["verdict"] == "environment_failure"
    assert result["failure_phase"] == "entrypoint"
    assert result["missing_commands"] == ["worker-contract"]
    assert result["commands"] == []
    [service_result] = result["services"]
    assert service_result["entrypoint_id"] == "worker"
    assert service_result["environment_error"] is True
    assert service_result["passed"] is False
    assert result["cleanup"][0]["passed"] is True
    assert (project / "cleanup-ran").read_text(encoding="utf-8") == "yes"
    junit = parse(output / "junit.xml").getroot()
    assert junit.attrib["errors"] == "1"


def test_product_closure_classifies_entrypoint_evidence_collision_as_harness_failure(tmp_path: Path):
    import subprocess

    project = tmp_path / "project"
    project.mkdir()
    (project / "product.txt").write_text("fixture\n", encoding="utf-8")
    (project / "cleanup.py").write_text(
        "from pathlib import Path\nPath('cleanup-after-collision').write_text('yes', encoding='utf-8')\n",
        encoding="utf-8",
    )
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "product.txt", "cleanup.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1",
        "project_type": "worker",
        "source_root": ".",
        "target_commit": commit,
        "setup": [],
        "commands": [{
            "id": "worker-contract",
            "command": f"{sys.executable} -c 'raise SystemExit(0)'",
            "tier": "integration",
            "acceptance_ids": ["AC-1"],
            "domain_ids": [f"P-{index:02d}" for index in range(1, 19)],
            "mandatory": True,
            "deterministic": True,
            "timeout_seconds": 10,
        }],
        "acceptances": [{"id": "AC-1"}],
        "entrypoints": [{
            "entrypoint_id": "worker",
            "start_command": f"{sys.executable} -c 'import time; time.sleep(5)'",
            "health_check": f"{sys.executable} -c 'raise SystemExit(0)'",
            "owner": "product-test",
            "timeout_seconds": 1,
        }],
        "capabilities": {"long_running": True},
        "budgets": {},
        "not_applicable": {},
        "cleanup": [f"{sys.executable} cleanup.py"],
    }
    manifest_path = project / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    (output / "entrypoint.worker.stdout.log").write_text("previous run\n", encoding="utf-8")

    result = runner_module.run_product_closure(
        manifest_path,
        project_root=project,
        output_dir=output,
        run_id="evidence-collision",
    )

    assert result["verdict"] == "harness_failure"
    assert result["failure_phase"] == "execution"
    assert result["errors"][0]["type"] == "ProductHarnessError"
    assert "service evidence already exists" in result["errors"][0]["message"]
    assert result["cleanup"][0]["passed"] is True
    assert (project / "cleanup-after-collision").read_text(encoding="utf-8") == "yes"


def test_unified_runner_keeps_fail_then_pass_rerun_flaky_and_preserves_both_attempts(tmp_path: Path):
    import subprocess

    project = tmp_path / "project"
    project.mkdir()
    (project / "flaky.py").write_text(
        "from pathlib import Path\n"
        "marker = Path('.flaky-seen')\n"
        "if marker.exists(): raise SystemExit(0)\n"
        "marker.write_text('seen', encoding='utf-8')\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "flaky.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1",
        "project_type": "library",
        "source_root": ".",
        "target_commit": commit,
        "setup": [],
        "commands": [{
            "id": "flaky-case",
            "command": f"{sys.executable} flaky.py",
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
        "runtime_paths": [".flaky-seen"],
        "cleanup": [],
    }
    manifest_path = project / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    first = run_manifest_and_collect(
        manifest_path,
        project_root=project,
        output_dir=tmp_path / "attempt-1",
        run_id="stability-run",
        candidate_ref="candidate/main",
    )
    second = run_manifest_and_collect(
        manifest_path,
        project_root=project,
        output_dir=tmp_path / "attempt-2",
        run_id="stability-run",
        candidate_ref="candidate/main",
        prior_attempts=(first,),
    )

    assert first["verdict"] == "fail"
    assert second["verdict"] == "flaky"
    assert second["stability"]["flaky_commands"] == ["flaky-case"]
    assert json.loads((tmp_path / "attempt-2" / "stability-attempt.001.json").read_text())[
        "verdict"
    ] == "fail"
    indexed_refs = {row["ref"] for row in second["artifact_index"]}
    assert {"stability-attempt.001.json", "stability-report.json"} <= indexed_refs
    junit = parse(tmp_path / "attempt-2" / "junit.xml").getroot()
    assert junit.attrib["failures"] == "1"
    stability_case = next(case for case in junit.findall("testcase") if case.attrib["name"] == "testkit.stability")
    assert stability_case.find("failure").attrib["type"] == "flaky"
    bundle = json.loads((tmp_path / "attempt-2" / "failure-bundle.json").read_text())
    assert bundle["stability"]["verdict"] == "flaky"


def test_cli_reports_manifest_admission_error_as_structured_harness_failure(tmp_path: Path, capsys):
    import subprocess
    project = tmp_path / "project"
    project.mkdir()
    manifest = project / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    output = tmp_path / "out"
    completed = subprocess.run(
        ["python", "-m", "tests.product_testkit", "--manifest", str(manifest),
         "--project-root", str(project), "--output-dir", str(output), "--run-id", "r1"],
        cwd=Path(__file__).parents[2], capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["verdict"] == "harness_failure"
    assert "schema" in payload["error"]
    persisted = json.loads((output / "verification-result.json").read_text(encoding="utf-8"))
    assert persisted["run_id"] == "r1"
    assert persisted["verdict"] == "harness_failure"
    assert persisted["failure_phase"] == "admission"
    assert persisted["errors"] == [{
        "type": "ManifestError",
        "message": "unsupported product test manifest schema",
    }]
    assert persisted["commands"] == []
    junit = parse(output / "junit.xml").getroot()
    assert junit.attrib == {
        "name": "zaoFu-product",
        "tests": "1",
        "failures": "0",
        "errors": "1",
    }
    [case] = junit.findall("testcase")
    assert case.attrib["name"] == "testkit.admission"
    [error] = case.findall("error")
    assert error.attrib["type"] == "harness-failure"
    assert error.text == "unsupported product test manifest schema"
    bundle = json.loads((output / "failure-bundle.json").read_text(encoding="utf-8"))
    assert bundle["verdict"] == "harness_failure"
    assert bundle["errors"] == persisted["errors"]


def test_programmatic_closure_returns_terminal_result_for_invalid_manifest(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    manifest = project / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    output = tmp_path / "out"

    product_testkit = __import__("tests.product_testkit", fromlist=["run_product_closure"])
    result = product_testkit.run_product_closure(
        manifest,
        project_root=project,
        output_dir=output,
        run_id="programmatic-1",
    )

    assert result["verdict"] == "harness_failure"
    assert result["failure_phase"] == "admission"
    assert result["run_id"] == "programmatic-1"
    assert json.loads((output / "verification-result.json").read_text(encoding="utf-8")) == result


def test_programmatic_closure_classifies_artifact_collection_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Evidence-index failures must become a durable harness verdict."""
    import subprocess

    project = tmp_path / "project"
    project.mkdir()
    (project / "smoke.py").write_text("print('ok')\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "smoke.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project, text=True,
    ).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1",
        "project_type": "library",
        "source_root": ".",
        "target_commit": commit,
        "setup": [],
        "commands": [{
            "id": "smoke",
            "command": f"{sys.executable} smoke.py",
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
    }
    manifest_path = project / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out"

    def fail_index(*args, **kwargs):
        raise runner_module.ArtifactError("evidence index is unavailable")

    monkeypatch.setattr(runner_module, "build_artifact_index", fail_index)
    result = runner_module.run_product_closure(
        manifest_path,
        project_root=project,
        output_dir=output,
        run_id="artifact-failure",
    )

    assert result["verdict"] == "harness_failure"
    assert result["failure_phase"] == "evidence"
    assert result["errors"] == [{
        "type": "ArtifactError",
        "message": "evidence index is unavailable",
    }]
    assert (output / "verification-result.json").is_file()
    assert (output / "junit.xml").is_file()
    assert (output / "failure-bundle.json").is_file()


def test_cli_keeps_structured_terminal_output_when_evidence_directory_is_unwritable(tmp_path: Path):
    import subprocess
    project = tmp_path / "project"
    project.mkdir()
    manifest = project / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    output_file = tmp_path / "not-a-directory"
    output_file.write_text("occupied", encoding="utf-8")

    completed = subprocess.run(
        ["python", "-m", "tests.product_testkit", "--manifest", str(manifest),
         "--project-root", str(project), "--output-dir", str(output_file), "--run-id", "r1"],
        cwd=Path(__file__).parents[2], capture_output=True, text=True, check=False,
    )

    assert completed.returncode == 1
    assert "Traceback" not in completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["verdict"] == "harness_failure"
    assert payload["run_id"] == "r1"
    assert payload["error"] == "unsupported product test manifest schema"


def test_product_closure_keeps_missing_tool_as_environment_failure(tmp_path: Path):
    import subprocess
    project = tmp_path / "project"
    project.mkdir()
    (project / "product.txt").write_text("fixture\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "product.txt"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1",
        "project_type": "library",
        "source_root": ".",
        "target_commit": commit,
        "setup": [],
        "commands": [{
            "id": "missing-tool",
            "command": "zf-product-testkit-tool-that-does-not-exist --check",
            "tier": "unit",
            "acceptance_ids": ["AC-1"],
            "domain_ids": [f"P-{index:02d}" for index in range(1, 19)],
            "mandatory": True,
            "deterministic": True,
            "timeout_seconds": 20,
        }],
        "acceptances": [{"id": "AC-1"}],
        "entrypoints": [],
        "capabilities": {},
        "budgets": {},
        "not_applicable": {},
        "cleanup": [],
    }
    manifest_path = project / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out"

    result = runner_module.run_product_closure(
        manifest_path,
        project_root=project,
        output_dir=output,
        run_id="environment-1",
    )

    assert result["verdict"] == "environment_failure"
    assert result["commands"][0]["environment_error"] is True
    junit = parse(output / "junit.xml").getroot()
    assert junit.attrib["failures"] == "0"
    assert junit.attrib["errors"] == "1"
    [error] = junit.findall("testcase/error")
    assert error.attrib["type"] == "environment-failure"
    bundle = json.loads((output / "failure-bundle.json").read_text(encoding="utf-8"))
    assert bundle["verdict"] == "environment_failure"
    assert bundle["failures"][0]["command_id"] == "missing-tool"


def test_product_closure_reports_empty_requested_tier_as_not_run(tmp_path: Path):
    import subprocess
    project = tmp_path / "project"
    project.mkdir()
    (project / "release.py").write_text("raise SystemExit('must not execute in T0')\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "release.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1",
        "project_type": "library",
        "source_root": ".",
        "target_commit": commit,
        "setup": [],
        "commands": [{
            "id": "release-only",
            "command": "python release.py",
            "tier": "release",
            "acceptance_ids": ["AC-1"],
            "domain_ids": [f"P-{index:02d}" for index in range(1, 19)],
            "mandatory": True,
            "deterministic": True,
            "timeout_seconds": 20,
        }],
        "acceptances": [{"id": "AC-1"}],
        "entrypoints": [],
        "capabilities": {},
        "budgets": {},
        "not_applicable": {},
        "cleanup": [],
    }
    manifest_path = project / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out"

    result = runner_module.run_product_closure(
        manifest_path,
        project_root=project,
        output_dir=output,
        run_id="not-run-1",
        ci_tier="T0",
    )

    assert result["verdict"] == "not_run"
    assert result["commands"] == []
    assert result["missing_commands"] == []
    assert result["selection"] == {"ci_tier": "T0", "reason": "no_commands_for_requested_tier"}
    junit = parse(output / "junit.xml").getroot()
    assert junit.attrib["tests"] == "1"
    assert junit.attrib["skipped"] == "1"
    assert junit.attrib["failures"] == "0"
    assert junit.attrib["errors"] == "0"
    [skipped] = junit.findall("testcase/skipped")
    assert skipped.attrib["type"] == "not-run"


def test_unified_runner_writes_acceptance_report_when_spec_is_supplied(tmp_path: Path):
    # Use a tiny independent product and commit to exercise the same canonical path.
    project = tmp_path / "p"
    project.mkdir()
    import subprocess
    (project / "x.py").write_text("print('ok')\n", encoding="utf-8")
    for command in (["git", "init", "-q"], ["git", "config", "user.email", "t@example.invalid"],
                    ["git", "config", "user.name", "Test"], ["git", "add", "x.py"],
                    ["git", "commit", "-qm", "fixture"]):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {"schema_version": "product-test-manifest.v1", "project_type": "library", "source_root": ".",
                "target_commit": commit, "setup": [], "commands": [{"id": "smoke", "command": "python x.py",
                "tier": "unit", "acceptance_ids": ["AC-1"], "domain_ids": [f"P-{i:02d}" for i in range(1, 19)],
                "mandatory": True, "deterministic": True, "timeout_seconds": 20}], "acceptances": [{"id": "AC-1"}],
                "entrypoints": [], "capabilities": {}, "budgets": {}, "not_applicable": {}, "cleanup": []}
    path = project / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    evidence = output / "evidence.json"
    evidence.write_text('{"status":"passed"}\n', encoding="utf-8")
    evidence_digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    journey = _journey()
    journey["evidence_refs"][0]["sha256"] = evidence_digest
    journey["assertion_results"][0]["evidence_refs"][0]["sha256"] = evidence_digest
    result = run_manifest_and_collect(path, project_root=project, output_dir=output, run_id="run-1",
                                      candidate_ref="candidate", acceptance_spec=_spec(), acceptance_spec_ref="s",
                                      acceptance_spec_digest="a" * 64, journey_results=(journey,))
    assert result["product_acceptance_report"]["verdict"] == "passed"
    assert result["delivery_readiness"]["allowed"] is True
    assert result["delivery_readiness"]["merge_authorized"] is False
    persisted = json.loads((output / "verification-result.json").read_text())
    assert persisted["product_acceptance_report"]["verdict"] == "passed"
    assert json.loads((output / "delivery-readiness.json").read_text()) == result["delivery_readiness"]


def test_unified_runner_blocks_green_acceptance_with_unbound_evidence(tmp_path: Path):
    import subprocess
    project = tmp_path / "p"
    project.mkdir()
    (project / "x.py").write_text("print('ok')\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "x.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1",
        "project_type": "library",
        "source_root": ".",
        "target_commit": commit,
        "setup": [],
        "commands": [{
            "id": "smoke",
            "command": "python x.py",
            "tier": "unit",
            "acceptance_ids": ["AC-1"],
            "domain_ids": [f"P-{index:02d}" for index in range(1, 19)],
            "mandatory": True,
            "deterministic": True,
            "timeout_seconds": 20,
        }],
        "acceptances": [{"id": "AC-1"}],
        "entrypoints": [],
        "capabilities": {},
        "budgets": {},
        "not_applicable": {},
        "cleanup": [],
    }
    path = project / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out"

    result = run_manifest_and_collect(
        path,
        project_root=project,
        output_dir=output,
        run_id="run-1",
        candidate_ref="candidate",
        acceptance_spec=_spec(),
        acceptance_spec_ref="spec.json",
        acceptance_spec_digest="a" * 64,
        journey_results=(_journey(),),
    )

    assert result["verdict"] == "harness_failure"
    assert result["failure_phase"] == "delivery_readiness"
    assert result["delivery_readiness"]["reasons"] == [
        "acceptance_evidence_not_indexed:evidence.json"
    ]
    assert result["errors"] == [{
        "type": "DeliveryReadinessError",
        "message": "acceptance_evidence_not_indexed:evidence.json",
    }]
    junit = parse(output / "junit.xml").getroot()
    assert junit.attrib["tests"] == "2"
    assert junit.attrib["errors"] == "1"
    bundle = json.loads((output / "failure-bundle.json").read_text(encoding="utf-8"))
    assert bundle["errors"] == result["errors"]


def test_unified_runner_downgrades_green_commands_when_acceptance_is_rejected(tmp_path: Path):
    project = tmp_path / "p"
    project.mkdir()
    import subprocess
    (project / "x.py").write_text("print('ok')\n", encoding="utf-8")
    for command in (["git", "init", "-q"], ["git", "config", "user.email", "t@example.invalid"],
                    ["git", "config", "user.name", "Test"], ["git", "add", "x.py"],
                    ["git", "commit", "-qm", "fixture"]):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {"schema_version": "product-test-manifest.v1", "project_type": "library",
                "source_root": ".", "target_commit": commit, "setup": [],
                "commands": [{"id": "smoke", "command": "python x.py", "tier": "unit",
                               "acceptance_ids": ["AC-1"], "domain_ids": [f"P-{i:02d}" for i in range(1, 19)],
                               "mandatory": True, "deterministic": True, "timeout_seconds": 20}],
                "acceptances": [{"id": "AC-1"}], "entrypoints": [], "capabilities": {},
                "budgets": {}, "not_applicable": {}, "cleanup": []}
    path = project / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = run_manifest_and_collect(path, project_root=project, output_dir=tmp_path / "out", run_id="r1",
                                      candidate_ref="candidate", acceptance_spec=_spec(),
                                      acceptance_spec_ref="s", acceptance_spec_digest="a" * 64,
                                      journey_results=(_journey("failed"),))
    assert result["verdict"] == "fail"
    persisted = json.loads((tmp_path / "out" / "verification-result.json").read_text())
    assert persisted["verdict"] == "fail"


def test_unified_runner_failure_still_writes_reproducible_bundle(tmp_path: Path):
    project = tmp_path / "p"
    project.mkdir()
    import subprocess
    (project / "x.py").write_text("raise SystemExit(7)\n", encoding="utf-8")
    for command in (["git", "init", "-q"], ["git", "config", "user.email", "t@example.invalid"],
                    ["git", "config", "user.name", "Test"], ["git", "add", "x.py"],
                    ["git", "commit", "-qm", "fixture"]):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {"schema_version": "product-test-manifest.v1", "project_type": "library", "source_root": ".",
                "target_commit": commit, "setup": [], "commands": [{"id": "smoke", "command": "python x.py",
                "tier": "unit", "acceptance_ids": ["AC-1"], "domain_ids": [f"P-{i:02d}" for i in range(1, 19)],
                "mandatory": True, "deterministic": True, "timeout_seconds": 20}], "acceptances": [{"id": "AC-1"}],
                "entrypoints": [], "capabilities": {}, "budgets": {}, "not_applicable": {}, "cleanup": []}
    path = project / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = run_manifest_and_collect(path, project_root=project, output_dir=tmp_path / "out", run_id="r1")
    assert result["verdict"] == "fail"
    bundle = json.loads((tmp_path / "out" / "failure-bundle.json").read_text())
    assert bundle["failures"][0]["command_id"] == "smoke"


def test_early_command_failure_is_not_hidden_as_not_run(tmp_path: Path):
    project = tmp_path / "p"
    project.mkdir()
    import subprocess
    (project / "x.py").write_text("raise SystemExit(9)\n", encoding="utf-8")
    for command in (["git", "init", "-q"], ["git", "config", "user.email", "t@example.invalid"],
                    ["git", "config", "user.name", "Test"], ["git", "add", "x.py"],
                    ["git", "commit", "-qm", "fixture"]):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {"schema_version": "product-test-manifest.v1", "project_type": "library",
                "source_root": ".", "target_commit": commit, "setup": [],
                "commands": [{"id": "bad", "command": "python x.py", "tier": "unit",
                               "acceptance_ids": ["AC-1"], "domain_ids": [f"P-{i:02d}" for i in range(1, 19)],
                               "mandatory": True, "deterministic": True, "timeout_seconds": 20},
                              {"id": "never", "command": "python x.py", "tier": "unit",
                               "acceptance_ids": ["AC-1"], "domain_ids": ["P-01"],
                               "mandatory": True, "deterministic": True, "timeout_seconds": 20}],
                "acceptances": [{"id": "AC-1"}], "entrypoints": [], "capabilities": {},
                "budgets": {}, "not_applicable": {}, "cleanup": []}
    path = project / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = run_manifest_and_collect(path, project_root=project, output_dir=tmp_path / "out", run_id="r1")
    assert result["verdict"] == "fail"
    assert result["missing_commands"] == ["never"]


def test_unified_runner_ci_tier_selects_commands(tmp_path: Path):
    project = tmp_path / "p"; project.mkdir()
    import subprocess
    (project / "x.py").write_text("print('ok')\n", encoding="utf-8")
    for command in (["git", "init", "-q"], ["git", "config", "user.email", "t@example.invalid"],
                    ["git", "config", "user.name", "Test"], ["git", "add", "x.py"], ["git", "commit", "-qm", "fixture"]):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    common = {"schema_version": "product-test-manifest.v1", "project_type": "library", "source_root": ".",
              "target_commit": commit, "setup": [], "acceptances": [{"id": "a"}], "entrypoints": [],
              "capabilities": {}, "budgets": {}, "not_applicable": {}, "cleanup": []}
    common["commands"] = [{"id": "u", "command": "python x.py", "tier": "unit", "acceptance_ids": ["a"],
                           "domain_ids": [f"P-{i:02d}" for i in range(1, 19)], "mandatory": True, "deterministic": True, "timeout_seconds": 10},
                          {"id": "e", "command": "python x.py", "tier": "e2e", "acceptance_ids": ["a"],
                           "domain_ids": [f"P-{i:02d}" for i in range(1, 19)], "mandatory": False, "deterministic": True, "timeout_seconds": 10}]
    path = project / "manifest.json"; path.write_text(json.dumps(common), encoding="utf-8")
    result = run_manifest_and_collect(path, project_root=project, output_dir=tmp_path / "out", run_id="r1", ci_tier="T0")
    assert [row["command_id"] for row in result["commands"]] == ["u"]
    assert result["verdict"] == "pass"
    assert result["missing_commands"] == []


@pytest.mark.parametrize("cleanup_exit", [0, 7])
def test_unified_runner_executes_cleanup_and_records_receipt(tmp_path: Path, cleanup_exit: int):
    project = tmp_path / "p"; project.mkdir()
    import subprocess
    (project / "x.py").write_text("print('ok')\n", encoding="utf-8")
    for command in (["git", "init", "-q"], ["git", "config", "user.email", "t@example.invalid"],
                    ["git", "config", "user.name", "Test"], ["git", "add", "x.py"],
                    ["git", "commit", "-qm", "fixture"]):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {"schema_version": "product-test-manifest.v1", "project_type": "library", "source_root": ".",
                "target_commit": commit, "setup": [], "commands": [{"id": "smoke", "command": "python x.py",
                "tier": "unit", "acceptance_ids": ["AC-1"], "domain_ids": [f"P-{i:02d}" for i in range(1, 19)],
                "mandatory": True, "deterministic": True, "timeout_seconds": 20}], "acceptances": [{"id": "AC-1"}],
                "entrypoints": [], "capabilities": {}, "budgets": {}, "not_applicable": {},
                "cleanup": [f"python -c \"open('cleaned','w').write('ok');raise SystemExit({cleanup_exit})\""]}
    path = project / "manifest.json"; path.write_text(json.dumps(manifest), encoding="utf-8")
    result = run_manifest_and_collect(path, project_root=project, output_dir=tmp_path / "out", run_id="r1")
    assert result["verdict"] == ("pass" if cleanup_exit == 0 else "harness_failure")
    assert result["cleanup"][0]["command_id"] == "cleanup.0"
    assert (project / "cleaned").read_text() == "ok"
    bundle = json.loads((tmp_path / "out" / "failure-bundle.json").read_text())
    assert bundle["verdict"] == result["verdict"]
    assert len(bundle["cleanup_failures"]) == (0 if cleanup_exit == 0 else 1)
    junit = parse(tmp_path / "out" / "junit.xml").getroot()
    assert "cleanup.0" in (tmp_path / "out" / "junit.xml").read_text()
    if cleanup_exit:
        assert junit.attrib["failures"] == "0"
        assert junit.attrib["errors"] == "1"
        cleanup_case = next(case for case in junit.findall("testcase") if case.attrib["name"] == "cleanup.0")
        assert cleanup_case.find("error").attrib["type"] == "cleanup-failure"
