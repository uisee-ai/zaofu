from pathlib import Path

from .failure_bundle import build_failure_bundle, write_failure_bundle


def test_failure_bundle_keeps_failure_identity_and_domains(tmp_path: Path):
    result = {"run_id": "r1", "target_commit": "a" * 40, "verdict": "fail",
              "domains": [{"capability": "core", "domains": ["P-01"], "status": "selected"}],
              "commands": [{"command_id": "bad", "passed": False, "exit_code": 2},
                           {"command_id": "ok", "passed": True}], "missing_commands": ["api"]}
    bundle = build_failure_bundle(result)
    assert bundle["failures"][0]["command_id"] == "bad"
    assert bundle["domains"][0]["domains"] == ["P-01"]
    path = tmp_path / "failure-bundle.json"
    write_failure_bundle(result, path)
    assert '"schema_version": "product-test-failure-bundle.v1"' in path.read_text()


def test_failure_bundle_keeps_structured_openapi_findings():
    result = {
        "run_id": "r1",
        "target_commit": "a" * 40,
        "verdict": "fail",
        "commands": [],
        "api_contracts": [{
            "contract_id": "openapi",
            "verdict": "fail",
            "reason": "product_finding",
            "failures": [{"type": "ServerError"}],
        }],
    }

    bundle = build_failure_bundle(result)

    assert bundle["api_contract_failures"][0]["failures"] == [{"type": "ServerError"}]
