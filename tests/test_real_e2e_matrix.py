from __future__ import annotations

import json
from pathlib import Path

from zf.runtime.artifact_matrix_gate import evaluate_artifact_matrix_gate
from zf.runtime.delivery_contract_gate import evaluate_real_e2e_matrix
from zf.runtime.real_e2e_runner import run_real_e2e_matrix


def test_real_e2e_matrix_requires_surface_status_and_evidence(tmp_path: Path) -> None:
    matrix = tmp_path / "real-e2e.json"
    matrix.write_text(json.dumps({
        "schema_version": "real-e2e-matrix.v1",
        "rows": [
            {
                "id": "web-chat-doubao",
                "surface": "web",
                "status": "passed",
                "evidence_refs": ["reports/web-chat.json"],
            },
            {
                "id": "cli-doubao",
                "surface": "cli",
                "status": "failed",
            },
        ],
    }), encoding="utf-8")

    result = evaluate_real_e2e_matrix(tmp_path, {
        "real_e2e_matrix_paths": ["real-e2e.json"],
        "required_surfaces": ["web", "llm"],
    })

    assert result.passed is False
    codes = {item.code for item in result.findings}
    assert "real_e2e_status_not_allowed" in codes
    assert "real_e2e_evidence_missing" in codes
    assert "real_e2e_required_surface_missing" in codes


def test_artifact_matrix_gate_supports_real_e2e_matrix(tmp_path: Path) -> None:
    (tmp_path / "real-e2e.json").write_text(json.dumps({
        "schema_version": "real-e2e-matrix.v1",
        "rows": [{
            "id": "web-chat-doubao",
            "surface": "web",
            "status": "passed",
            "evidence_refs": ["reports/web-chat.json"],
        }],
    }), encoding="utf-8")

    result = evaluate_artifact_matrix_gate(tmp_path, {
        "real_e2e_matrix_paths": ["real-e2e.json"],
        "required_surfaces": ["web"],
    })

    assert result.passed is True
    assert "real-e2e.json" in result.checked_artifacts


def test_real_e2e_runner_executes_command_and_writes_evidence(tmp_path: Path) -> None:
    (tmp_path / "real-e2e.json").write_text(json.dumps({
        "schema_version": "real-e2e-matrix.v1",
        "rows": [{
            "id": "cli-smoke",
            "surface": "cli",
            "command": "printf ok",
        }],
    }), encoding="utf-8")

    result = run_real_e2e_matrix(tmp_path, {
        "real_e2e_matrix_paths": ["real-e2e.json"],
        "evidence_dir": "artifacts/e2e",
        "timeout_seconds": 5,
    })

    assert result.passed is True
    assert Path(result.result_matrix_ref).exists()
    assert result.evidence_refs
    evidence = json.loads(Path(result.evidence_refs[0]).read_text(encoding="utf-8"))
    assert evidence["exit_code"] == 0
    matrix = json.loads(Path(result.result_matrix_ref).read_text(encoding="utf-8"))
    assert matrix["rows"][0]["status"] == "passed"
    assert matrix["rows"][0]["evidence_refs"]


def test_real_e2e_gate_execute_mode_uses_runner_result(tmp_path: Path) -> None:
    (tmp_path / "real-e2e.json").write_text(json.dumps({
        "schema_version": "real-e2e-matrix.v1",
        "rows": [{
            "id": "cli-smoke",
            "surface": "cli",
            "command": "printf ok",
        }],
    }), encoding="utf-8")

    result = evaluate_real_e2e_matrix(tmp_path, {
        "real_e2e_matrix_paths": ["real-e2e.json"],
        "required_surfaces": ["cli"],
        "execute_real_e2e": True,
        "evidence_dir": "artifacts/e2e",
        "timeout_seconds": 5,
    })

    assert result.passed is True
    assert any(ref.endswith("real-e2e-results.json") for ref in result.checked_artifacts)
