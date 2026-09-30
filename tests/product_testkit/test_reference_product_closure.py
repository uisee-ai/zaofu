"""End-to-end proof that the generic product closure is self-hosted.

The fixture is deliberately not HighwayPilot Lab (or any other real product).
It is a tiny, disposable Git project whose only purpose is to prove that the
TestKit can reach terminal product/issue outcomes without a real product being
available or correct.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from .issue_loop import IssueMergeLoop
from .runner import run_manifest_and_collect


_ALL_DOMAINS = [f"P-{index:02d}" for index in range(1, 19)]


def _git_fixture(tmp_path: Path, *, broken: bool = False) -> tuple[Path, Path, str]:
    project = tmp_path / ("broken-product" if broken else "reference-product")
    project.mkdir()
    check_body = "raise SystemExit(17)\n" if broken else "print('product smoke passed')\n"
    (project / "check.py").write_text(check_body, encoding="utf-8")
    (project / "build.py").write_text(
        "from pathlib import Path\n"
        "Path('dist').mkdir(exist_ok=True)\n"
        "Path('dist/reference-1.0-py3-none-any.whl').write_bytes(b'reference-wheel')\n",
        encoding="utf-8",
    )
    (project / "scanner.py").write_text(
        "from pathlib import Path\n"
        "import json\n"
        "Path('evidence').mkdir(exist_ok=True)\n"
        "Path('evidence/zap.json').write_text(json.dumps({'@programName':'ZAP', 'site':[{'alerts':[]}]}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    (project / "worker.py").write_text(
        "import pathlib, sys, time\n"
        "state = pathlib.Path(sys.argv[1]); ready = pathlib.Path(sys.argv[2])\n"
        "if not state.exists(): state.write_text('{\"status\":\"ready\",\"version\":1}', encoding='utf-8')\n"
        "ready.write_text('ready', encoding='ascii')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    (project / "worker_once.py").write_text(
        "import pathlib, sys\n"
        "pathlib.Path(f'worker-{sys.argv[1]}.done').write_text('done', encoding='ascii')\n",
        encoding="utf-8",
    )
    (project / "step.py").write_text(
        "import pathlib, sys\n"
        "pathlib.Path(f'persistence-{sys.argv[1]}').write_text('ok', encoding='ascii')\n",
        encoding="utf-8",
    )
    (project / "release.py").write_text(
        "import pathlib, sys\n"
        "pathlib.Path(f'release-{sys.argv[1]}').write_text('ok', encoding='ascii')\n",
        encoding="utf-8",
    )
    (project / "cleanup.py").write_text(
        "import pathlib\npathlib.Path('cleanup.done').write_text('yes', encoding='ascii')\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "TestKit"], cwd=project, check=True)
    subprocess.run(
        [
            "git", "add", "check.py", "build.py", "scanner.py", "worker.py",
            "worker_once.py", "step.py", "release.py", "cleanup.py",
        ],
        cwd=project,
        check=True,
    )
    subprocess.run(["git", "commit", "-qm", "reference fixture"], cwd=project, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()

    py = shlex_quote(sys.executable)
    manifest = {
        "schema_version": "product-test-manifest.v1",
        "project_type": "fullstack",
        "source_root": ".",
        "target_commit": commit,
        "setup": [{"id": "build", "command": f"{py} build.py", "timeout_seconds": 20}],
        "commands": [{
            "id": "product-smoke",
            "command": f"{py} check.py",
            "tier": "unit",
            "acceptance_ids": ["AC-reference"],
            "domain_ids": _ALL_DOMAINS,
            "mandatory": True,
            "deterministic": True,
            "timeout_seconds": 20,
        }],
        "acceptances": [{"id": "AC-reference"}],
        "entrypoints": [],
        "capabilities": {
            "database": True,
            "long_running": True,
            "release": True,
            "api": False,
            "cli": False,
            "web": False,
            "external_service": False,
        },
        "budgets": {},
        "not_applicable": {
            "api": {"reason": "fixture has no HTTP surface", "source": "reference fixture"},
            "cli": {"reason": "fixture has no user CLI", "source": "reference fixture"},
            "web": {"reason": "fixture has no browser surface", "source": "reference fixture"},
            "external_service": {"reason": "fixture has no remote dependency", "source": "reference fixture"},
        },
        "cleanup": [f"{py} cleanup.py"],
        "release_artifacts": [{
            "id": "wheel", "path": "dist/reference-1.0-py3-none-any.whl", "kind": "wheel",
            "tier": "release", "acceptance_ids": ["AC-reference"], "domain_ids": ["P-15"],
            "mandatory": True, "deterministic": True,
        }],
        "security_scans": [{
            "id": "zap", "tool": "zap", "command": f"{py} scanner.py", "report": "evidence/zap.json",
            "max_risk": 0, "tier": "security", "acceptance_ids": ["AC-reference"], "domain_ids": ["P-11"],
            "mandatory": True, "deterministic": True, "timeout_seconds": 20,
        }],
        "release_scenarios": [{
            "id": "upgrade-cycle", "install_command": f"{py} release.py install",
            "upgrade_command": f"{py} release.py upgrade", "rollback_command": f"{py} release.py rollback",
            "tier": "release", "acceptance_ids": ["AC-reference"], "domain_ids": ["P-15"],
            "mandatory": True, "deterministic": True, "timeout_seconds": 20,
        }],
        "recovery_scenarios": [{
            "id": "restart", "command": f"{py} worker.py state.json ready",
            "state_path": "state.json", "ready_path": "ready", "tier": "integration",
            "acceptance_ids": ["AC-reference"], "domain_ids": ["P-12"],
            "mandatory": True, "deterministic": True, "timeout_seconds": 5,
        }],
        "concurrency_scenarios": [{
            "id": "workers", "commands": [f"{py} worker_once.py one", f"{py} worker_once.py two"],
            "tier": "integration", "acceptance_ids": ["AC-reference"], "domain_ids": ["P-13"],
            "mandatory": True, "deterministic": True, "timeout_seconds": 10,
        }],
        "persistence_scenarios": [{
            "id": "migration", "commands": [f"{py} step.py before", f"{py} step.py after"],
            "tier": "integration", "acceptance_ids": ["AC-reference"], "domain_ids": ["P-09"],
            "mandatory": True, "deterministic": True, "timeout_seconds": 20,
        }],
    }
    manifest_path = project / "product-test-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # The manifest is intentionally untracked: target_commit identifies the
    # product revision and the manifest is the TestKit input for that revision.
    return project, manifest_path, commit


def shlex_quote(value: str) -> str:
    """Quote the interpreter path without introducing a shell command."""

    import shlex

    return shlex.quote(value)


def _acceptance_inputs(output: Path) -> tuple[dict, dict, str]:
    output.mkdir(parents=True, exist_ok=True)
    evidence = output / "acceptance.json"
    evidence.write_text('{"journey":"reference","status":"passed"}\n', encoding="utf-8")
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    spec = {
        "schema_version": "product_acceptance_spec.v1", "workflow_run_id": "reference-run",
        "flow_kind": "prd", "plan_revision": "p1", "task_map_generation": "g1",
        "assembly_owner": "testkit", "entrypoints": [{
            "entrypoint_id": "main", "start_command": "true", "health_check": "true", "owner": "testkit",
        }],
        "user_journeys": [{
            "journey_id": "j1", "title": "reference smoke", "verification_tier": "runtime",
            "mandatory": True, "assertions": [{
                "assertion_id": "a1", "statement": "reference product is usable",
                "observation_kind": "cli", "mandatory": True,
            }],
        }],
        "provider_qualification": {},
    }
    refs = [{"ref": "acceptance.json", "sha256": digest}]
    journey = {
        "journey_id": "j1", "status": "passed", "evidence_refs": refs,
        "assertion_results": [{"assertion_id": "a1", "status": "passed", "evidence_refs": refs}],
    }
    return spec, journey, digest


def test_reference_fixture_reaches_product_acceptance_without_real_product(tmp_path: Path):
    project, manifest, commit = _git_fixture(tmp_path)
    output = tmp_path / "out"
    spec, journey, digest = _acceptance_inputs(output)
    result = run_manifest_and_collect(
        manifest, project_root=project, output_dir=output, run_id="reference-run",
        candidate_ref="candidate/reference", target_commit=commit,
        acceptance_spec=spec, acceptance_spec_ref="product-acceptance-spec.json",
        acceptance_spec_digest="b" * 64, journey_results=(journey,),
    )

    assert result["verdict"] == "pass"
    assert result["product_acceptance_report"]["verdict"] == "passed"
    assert result["delivery_readiness"]["allowed"] is True
    assert result["delivery_readiness"]["merge_authorized"] is False
    assert result["domain_coverage"]["missing"] == []
    assert result["release_artifacts"][0]["passed"] is True
    assert result["security_scans"][0]["verdict"] == "pass"
    assert result["recovery_scenarios"][0]["passed"] is True
    assert result["concurrency_scenarios"][0]["passed"] is True
    assert result["persistence_scenarios"][0]["passed"] is True
    assert result["product_acceptance_report"]["journey_results"][0]["evidence_refs"][0]["sha256"] == digest
    assert (project / "cleanup.done").read_text(encoding="ascii") == "yes"
    assert (output / "verification-result.json").is_file()
    assert (output / "artifact-index.json").is_file()

    merge_calls: list[str] = []
    loop_result = IssueMergeLoop().run(
        {"id": "ISSUE-reference-pass", "revision": "r1", "body": "verified product change"},
        triage=lambda issue: {"status": "ready"},
        approve=lambda triage: True,
        run_tests=lambda _: result,
        merge=lambda _: (merge_calls.append("merge") or {"status": "merged"}),
        post_merge=lambda _: {"verdict": "pass"},
    )
    assert loop_result.passed is True
    assert merge_calls == ["merge"]


def test_reference_product_failure_is_product_verdict_and_blocks_issue_merge(tmp_path: Path):
    project, manifest, commit = _git_fixture(tmp_path, broken=True)
    result = run_manifest_and_collect(
        manifest, project_root=project, output_dir=tmp_path / "out", run_id="broken-run",
        candidate_ref="candidate/broken", target_commit=commit,
    )
    assert result["verdict"] == "fail"
    smoke = next(row for row in result["commands"] if row["command_id"] == "product-smoke")
    assert smoke["environment_error"] is False
    assert smoke["exit_code"] == 17

    merge_calls: list[str] = []
    loop_result = IssueMergeLoop().run(
        {"id": "ISSUE-reference", "revision": "r1", "body": "product assertion failed"},
        triage=lambda issue: {"status": "ready"},
        approve=lambda triage: True,
        run_tests=lambda _: result,
        merge=lambda _: (merge_calls.append("merge") or {"status": "merged"}),
        post_merge=lambda _: {"verdict": "pass"},
    )
    assert loop_result.status == "blocked"
    assert loop_result.reason == "product_tests_not_passed"
    assert merge_calls == []
