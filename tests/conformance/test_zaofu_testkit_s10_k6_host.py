"""Optional fixed-image k6 smoke; never part of the ordinary S10 gate."""

from __future__ import annotations

import os
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

import pytest

from tests.testkit.performance import PerformanceThreshold, evaluate_k6_summary
from tests.testkit.command_runner import run_command
from tests.testkit.evidence import evidence_ref, sha256_file, write_json_atomic


pytestmark = pytest.mark.host

_DEFAULT_IMAGE = (
    "grafana/k6:1.5.0@sha256:2072ea9eafa596532d9aee0cc0e0a50cfb0e7fb703981a46179af5f4f22c5ea4"
)


@pytest.mark.parametrize("force_failure", [False, True])
def test_fixed_k6_docker_smoke_produces_threshold_evidence(
    tmp_path: Path, force_failure: bool
) -> None:
    """Run one local iteration with read-only inputs and parse its summary."""

    repo_root = Path(__file__).resolve().parents[2]
    output = tmp_path / "out"
    output.mkdir()
    output.chmod(0o777)
    script_dir = Path(tempfile.mkdtemp(prefix="zf-k6-script-"))
    script_dir.chmod(0o755)
    script_path = script_dir / "smoke.js"
    shutil.copy2(repo_root / "tests/testkit/k6_smoke.js", script_path)
    script_path.chmod(0o644)
    image = os.environ.get("ZF_TESTKIT_K6_IMAGE", _DEFAULT_IMAGE)
    assert "@sha256:" in image, "k6 host smoke requires a digest-pinned image"
    container_name = f"zf-testkit-k6-{uuid.uuid4().hex[:12]}"
    command_id = "k6-host-smoke-fail" if force_failure else "k6-host-smoke"
    command = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        container_name,
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--network=none",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--mount",
        f"type=bind,src={script_path},dst=/scripts/smoke.js,readonly",
        "--mount",
        f"type=bind,src={output},dst=/out",
        "-e",
        "TESTKIT_FORCE_FAILURE=1" if force_failure else "TESTKIT_FORCE_FAILURE=0",
        image,
        "run",
        "--summary-mode=full",
        "--summary-trend-stats=avg,min,med,max,p(90),p(95),count",
        "/scripts/smoke.js",
    ]
    try:
        receipt = run_command(
            command,
            command_id=command_id,
            cwd=repo_root,
            output_dir=output,
            timeout_seconds=90,
        )
        if force_failure:
            assert receipt.exit_code == 99, receipt
        else:
            assert receipt.passed, receipt
        summary_path = output / "summary.json"
        assert summary_path.is_file() and summary_path.stat().st_size > 0
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        verdict = evaluate_k6_summary(
            summary,
            (
                PerformanceThreshold("checks", "rate", ">", 0.99),
                PerformanceThreshold("testkit_iterations", "count", ">", 0),
                PerformanceThreshold("testkit_latency", "p(95)", "<", 100),
            ),
            exit_code=receipt.exit_code,
        )
        assert verdict.status == ("fail" if force_failure else "pass"), verdict
        verdict_path = output / f"{command_id}.verdict.json"
        write_json_atomic(
            verdict_path,
            {
                "status": verdict.status,
                "checked": list(verdict.checked),
                "command_receipt": evidence_ref(output / f"{command_id}.receipt.json", root=output),
                "summary": evidence_ref(summary_path, root=output),
                "script": {"ref": str(script_path), "digest": sha256_file(script_path)},
                "image": image,
            },
        )
        assert verdict_path.is_file()
    finally:
        # A timeout can kill the Docker client before the daemon observes
        # --rm.  The explicit name makes cleanup bounded and auditable.
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        shutil.rmtree(script_dir, ignore_errors=True)
        assert not script_dir.exists()
