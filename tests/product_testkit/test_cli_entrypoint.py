from __future__ import annotations

import json
import subprocess
import hashlib
from pathlib import Path
import sys


def test_checkout_launcher_imports_testkit_from_external_working_directory(tmp_path: Path):
    """The documented external-product entrypoint must not depend on cwd."""
    launcher = Path(__file__).parents[2] / "scripts" / "product-testkit.py"
    completed = subprocess.run(
        [sys.executable, str(launcher), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--manifest" in completed.stdout


def test_checkout_launcher_runs_product_from_external_working_directory(tmp_path: Path):
    """The launcher must execute a real manifest, not only print help."""
    product = tmp_path / "product"
    caller = tmp_path / "caller"
    product.mkdir()
    caller.mkdir()
    (product / "main.py").write_text("print('external-product-ok')\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "main.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=product, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=product, text=True,
    ).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1",
        "project_type": "library",
        "source_root": ".",
        "target_commit": commit,
        "setup": [],
        "commands": [{
            "id": "smoke",
            "command": f"{sys.executable} main.py",
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
    manifest_path = product / "product-test-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    launcher = Path(__file__).parents[2] / "scripts" / "product-testkit.py"
    output = tmp_path / "evidence"
    completed = subprocess.run(
        [sys.executable, str(launcher), "--manifest", str(manifest_path),
         "--project-root", str(product), "--output-dir", str(output),
         "--run-id", "external-run"],
        cwd=caller,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["verdict"] == "pass"
    assert json.loads((output / "verification-result.json").read_text())["run_id"] == "external-run"


def test_module_cli_runs_independent_product(tmp_path: Path):
    project = tmp_path / "product"
    project.mkdir()
    (project / "main.py").write_text("print('product-ok')\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "main.py"],
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
            "id": "smoke", "command": "python main.py", "tier": "unit",
            "acceptance_ids": ["AC-1"], "domain_ids": [f"P-{i:02d}" for i in range(1, 19)],
            "mandatory": True, "deterministic": True, "timeout_seconds": 20,
        }],
        "acceptances": [{"id": "AC-1"}], "entrypoints": [], "capabilities": {},
        "budgets": {}, "not_applicable": {}, "cleanup": [],
    }
    manifest_path = project / "product-test-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out"
    completed = subprocess.run(
        ["python", "-m", "tests.product_testkit", "--manifest", str(manifest_path),
         "--project-root", str(project), "--output-dir", str(output), "--run-id", "cli-1"],
        cwd=Path(__file__).parents[2], text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["verdict"] == "pass"
    assert (output / "verification-result.json").exists()


def test_module_cli_accepts_prior_result_and_never_greenwashes_flaky_rerun(tmp_path: Path):
    project = tmp_path / "product"
    project.mkdir()
    (project / "flaky.py").write_text(
        "from pathlib import Path\n"
        "marker = Path('.seen')\n"
        "if marker.exists(): raise SystemExit(0)\n"
        "marker.write_text('seen', encoding='utf-8')\n"
        "raise SystemExit(3)\n",
        encoding="utf-8",
    )
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.invalid"],
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
            "id": "flaky", "command": "python flaky.py", "tier": "unit",
            "acceptance_ids": ["AC-1"],
            "domain_ids": [f"P-{index:02d}" for index in range(1, 19)],
            "mandatory": True, "deterministic": True, "timeout_seconds": 20,
        }],
        "acceptances": [{"id": "AC-1"}],
        "entrypoints": [],
        "capabilities": {},
        "budgets": {},
        "not_applicable": {},
        "runtime_paths": [".seen"],
        "cleanup": [],
    }
    manifest_path = project / "product-test-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    root = Path(__file__).parents[2]
    first_output = tmp_path / "attempt-1"
    first = subprocess.run(
        ["python", "-m", "tests.product_testkit", "--manifest", str(manifest_path),
         "--project-root", str(project), "--output-dir", str(first_output),
         "--run-id", "cli-stability", "--candidate-ref", "candidate/main"],
        cwd=root, text=True, capture_output=True, check=False,
    )
    assert first.returncode == 1
    assert json.loads(first.stdout)["verdict"] == "fail"

    second_output = tmp_path / "attempt-2"
    second = subprocess.run(
        ["python", "-m", "tests.product_testkit", "--manifest", str(manifest_path),
         "--project-root", str(project), "--output-dir", str(second_output),
         "--run-id", "cli-stability", "--candidate-ref", "candidate/main",
         "--prior-result", str(first_output / "verification-result.json")],
        cwd=root, text=True, capture_output=True, check=False,
    )

    assert second.returncode == 1
    assert json.loads(second.stdout)["verdict"] == "flaky"
    persisted = json.loads((second_output / "verification-result.json").read_text())
    assert persisted["stability"]["flaky_commands"] == ["flaky"]


def test_module_cli_runs_cumulative_ci_matrix(tmp_path: Path):
    project = tmp_path / "product"
    project.mkdir()
    (project / "main.py").write_text("print('ok')\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"], ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Test"], ["git", "add", "main.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1", "project_type": "library",
        "source_root": ".", "target_commit": commit, "setup": [],
        "commands": [{"id": "smoke", "command": "python main.py", "tier": "unit",
                       "acceptance_ids": ["AC-1"], "domain_ids": [f"P-{i:02d}" for i in range(1, 19)],
                       "mandatory": True, "deterministic": True, "timeout_seconds": 20}],
        "acceptances": [{"id": "AC-1"}], "entrypoints": [], "capabilities": {},
        "budgets": {}, "not_applicable": {}, "cleanup": [],
    }
    manifest_path = project / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "matrix"
    completed = subprocess.run(
        ["python", "-m", "tests.product_testkit", "--manifest", str(manifest_path),
         "--project-root", str(project), "--output-dir", str(output), "--run-id", "matrix-1",
         "--ci-matrix"], cwd=Path(__file__).parents[2], text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["verdict"] == "pass"
    assert len(json.loads((output / "ci-matrix.json").read_text())["tiers"]) == 4


def test_module_cli_acceptance_inputs_produce_bound_report(tmp_path: Path):
    project = tmp_path / "product"
    project.mkdir()
    (project / "main.py").write_text("print('ok')\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"], ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Test"], ["git", "add", "main.py"],
        ["git", "commit", "-qm", "fixture"],
    ):
        subprocess.run(command, cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    manifest = {
        "schema_version": "product-test-manifest.v1", "project_type": "library",
        "source_root": ".", "target_commit": commit, "setup": [],
        "commands": [{"id": "smoke", "command": "python main.py", "tier": "unit",
                       "acceptance_ids": ["AC-1"], "domain_ids": [f"P-{i:02d}" for i in range(1, 19)],
                       "mandatory": True, "deterministic": True, "timeout_seconds": 20}],
        "acceptances": [{"id": "AC-1"}], "entrypoints": [], "capabilities": {},
        "budgets": {}, "not_applicable": {}, "runtime_paths": [
            "product-acceptance-spec.json", "journey-results.json",
        ], "cleanup": [],
    }
    manifest_path = project / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    evidence = output / "journey.json"
    evidence.write_text('{"ok": true}\n', encoding="utf-8")
    # Acceptance refs are rooted at the TestKit output directory.
    evidence_ref = "journey.json"
    evidence_digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    spec = {
        "schema_version": "product_acceptance_spec.v1", "workflow_run_id": "run-1",
        "flow_kind": "prd", "plan_revision": "p1", "task_map_generation": "g1",
        "assembly_owner": "testkit", "entrypoints": [{"entrypoint_id": "main",
        "start_command": "python main.py", "health_check": "python main.py", "owner": "testkit"}],
        "user_journeys": [{"journey_id": "j1", "title": "smoke", "verification_tier": "runtime",
                            "mandatory": True, "assertions": [{"assertion_id": "a1",
                            "statement": "works", "observation_kind": "cli", "mandatory": True}]}],
        "provider_qualification": {},
    }
    spec_path = project / "product-acceptance-spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    journeys_path = project / "journey-results.json"
    journeys_path.write_text(json.dumps({"journey_results": [{
        "journey_id": "j1", "status": "passed", "evidence_refs": [{"ref": evidence_ref, "sha256": evidence_digest}],
        "assertion_results": [{"assertion_id": "a1", "status": "passed",
                               "evidence_refs": [{"ref": evidence_ref, "sha256": evidence_digest}]}],
    }]}), encoding="utf-8")
    completed = subprocess.run(
        ["python", "-m", "tests.product_testkit", "--manifest", str(manifest_path),
         "--project-root", str(project), "--output-dir", str(output), "--run-id", "run-1",
         "--candidate-ref", "candidate/main", "--acceptance-spec", str(spec_path),
         "--journey-results", str(journeys_path)],
        cwd=Path(__file__).parents[2], text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["verdict"] == "pass"
    report = json.loads((output / "product-acceptance-report.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "passed"
    assert report["candidate_ref"] == "candidate/main"
    assert report["product_acceptance_spec_digest"] == hashlib.sha256(spec_path.read_bytes()).hexdigest()


def test_module_cli_malformed_acceptance_input_writes_terminal_evidence(tmp_path: Path):
    project = tmp_path / "product"
    project.mkdir()
    manifest = project / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    spec = project / "spec.json"
    spec.write_text("not-json", encoding="utf-8")
    output = tmp_path / "out"
    completed = subprocess.run(
        ["python", "-m", "tests.product_testkit", "--manifest", str(manifest),
         "--project-root", str(project), "--output-dir", str(output), "--run-id", "bad-acceptance",
         "--acceptance-spec", str(spec), "--journey-results", str(spec)],
        cwd=Path(__file__).parents[2], text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["verdict"] == "harness_failure"
    assert "invalid Product Acceptance input" in payload["error"]
    assert (output / "verification-result.json").is_file()
    assert (output / "junit.xml").is_file()
    assert (output / "failure-bundle.json").is_file()
