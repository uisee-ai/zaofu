from pathlib import Path
import shlex
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

import pytest

from . import schemathesis as schemathesis_module
from .schemathesis import build_schemathesis_command, classify_schemathesis_result, run_schemathesis_contract


_NO_PROXY_ENV = {
    "HTTP_PROXY": "",
    "HTTPS_PROXY": "",
    "ALL_PROXY": "",
    "http_proxy": "",
    "https_proxy": "",
    "all_proxy": "",
    "NO_PROXY": "127.0.0.1,localhost",
    "no_proxy": "127.0.0.1,localhost",
}


def test_schemathesis_helpers_are_public_testkit_api():
    from tests.product_testkit import (
        build_schemathesis_command as exported_builder,
        classify_schemathesis_result as exported_classifier,
        run_schemathesis_contract as exported_runner,
    )

    assert exported_builder is build_schemathesis_command
    assert exported_classifier is classify_schemathesis_result
    assert exported_runner is run_schemathesis_contract


def _report(*, failures=(), errors=(), tested=1, complete=True, exit_code=0):
    return {
        "schemathesis_version": "4.26.1",
        "complete": complete,
        "stop_reason": "completed",
        "exit_code": exit_code,
        "seed": 1729,
        "operations": {
            "total": 1,
            "selected": 1,
            "tested": tested,
            "errored": len(errors),
            "skipped": 0,
            "skip_reasons": [],
        },
        "phases": {
            "examples": {"status": "skip", "skip_reason": "disabled"},
            "coverage": {"status": "skip", "skip_reason": "disabled"},
            "fuzzing": {"status": "success", "skip_reason": None},
            "stateful": {"status": "skip", "skip_reason": "disabled"},
        },
        "test_cases": {
            "generated": 1,
            "with_failures": len(failures),
            "unique_failures": len(failures),
            "without_checks": 0,
        },
        "failures": list(failures),
        "errors": list(errors),
        "warnings": {
            "missing_auth": [],
            "base_url_mismatch": [],
            "missing_test_data": [],
            "validation_mismatch": [],
            "missing_deserializer": [],
            "unused_openapi_auth": [],
            "unsupported_regex": [],
            "method_not_allowed": [],
            "constants_extraction": [],
            "unmatched_filter": [],
            "unresolvable_reference": [],
        },
    }


def test_schemathesis_command_is_reproducible(tmp_path: Path):
    schema = tmp_path / "openapi.json"
    schema.write_text("{}", encoding="utf-8")
    command = build_schemathesis_command(
        schema, base_url="http://127.0.0.1:8000", checks=("not_a_server_error",)
    )
    assert "schemathesis run" in command
    assert "--checks not_a_server_error" in command


def test_schemathesis_command_rejects_invalid_inputs(tmp_path: Path):
    with pytest.raises(ValueError):
        build_schemathesis_command(tmp_path / "missing.json", base_url="ftp://host")
    schema = tmp_path / "openapi.json"
    schema.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        build_schemathesis_command(schema, base_url="http://")
    with pytest.raises(ValueError):
        build_schemathesis_command(schema, base_url="http://user:secret@host")
    with pytest.raises(ValueError):
        build_schemathesis_command(schema, base_url="http://host", checks=("unknown",))


def test_structured_schemathesis_result_passes_only_after_real_operation_coverage():
    classify = getattr(schemathesis_module, "classify_schemathesis_result", None)
    assert callable(classify), "Schemathesis JSON needs a dedicated semantic classifier"

    result = classify(
        _report(),
        outer_exit_code=0,
        outer_timed_out=False,
        outer_environment_error=False,
        expected_version="4.26.1",
        expected_phases=("fuzzing",),
    )

    assert result == {
        "verdict": "pass",
        "reason": "checks_passed",
        "complete": True,
        "tool_version": "4.26.1",
        "operations_selected": 1,
        "operations_tested": 1,
        "seed": 1729,
    }


def test_schemathesis_command_pins_deterministic_profile_and_namespaced_reports(tmp_path: Path):
    schema = tmp_path / "openapi.json"
    schema.write_text("{}", encoding="utf-8")
    report_dir = tmp_path / "evidence" / "openapi-contract"

    command = build_schemathesis_command(
        schema,
        base_url="http://127.0.0.1:8000",
        checks=(
            "not_a_server_error",
            "status_code_conformance",
            "response_schema_conformance",
        ),
        phases=("fuzzing",),
        mode="positive",
        workers=1,
        seed=1729,
        max_examples=1,
        max_time_seconds=5,
        request_timeout_seconds=1,
        request_retries=0,
        report_json=report_dir / "result.json",
        report_junit=report_dir / "junit.xml",
        deterministic=True,
    )

    assert shlex.split(command) == [
        "schemathesis",
        "run",
        str(schema),
        "--url",
        "http://127.0.0.1:8000",
        "--phases",
        "fuzzing",
        "--mode",
        "positive",
        "--checks",
        "not_a_server_error,status_code_conformance,response_schema_conformance",
        "--workers",
        "1",
        "--seed",
        "1729",
        "--max-examples",
        "1",
        "--max-time",
        "5",
        "--request-timeout",
        "1",
        "--request-retries",
        "0",
        "--generation-deterministic",
        "--report-json-path",
        str(report_dir / "result.json"),
        "--report-junit-path",
        str(report_dir / "junit.xml"),
        "--no-color",
        "--output-truncate",
        "false",
    ]


@pytest.mark.parametrize(
    ("report", "outer_exit", "timed_out", "environment_error", "verdict", "reason"),
    [
        (
            _report(
                failures=({"type": "ServerError", "title": "Server error"},),
                exit_code=1,
            ),
            1,
            False,
            False,
            "fail",
            "product_finding",
        ),
        (
            _report(errors=({"title": "Network Error", "count": 1},), exit_code=1),
            1,
            False,
            False,
            "environment_failure",
            "target_transport_error",
        ),
        (_report(complete=False), 1, False, False, "harness_failure", "incomplete_report"),
        (_report(tested=0, exit_code=1), 1, False, False, "not_run", "no_operations_tested"),
        (_report(), None, True, False, "environment_failure", "outer_execution_failed"),
    ],
)
def test_schemathesis_classifier_separates_product_environment_harness_and_not_run(
    report, outer_exit, timed_out, environment_error, verdict, reason
):
    result = schemathesis_module.classify_schemathesis_result(
        report,
        outer_exit_code=outer_exit,
        outer_timed_out=timed_out,
        outer_environment_error=environment_error,
        expected_version="4.26.1",
        expected_phases=("fuzzing",),
    )

    assert result["verdict"] == verdict
    assert result["reason"] == reason


def test_schemathesis_classifier_rejects_configuration_warning_and_version_drift():
    warning_report = _report()
    warning_report["warnings"]["missing_auth"] = [{"operation": "GET /private"}]
    warning_result = schemathesis_module.classify_schemathesis_result(
        warning_report,
        outer_exit_code=0,
        outer_timed_out=False,
        outer_environment_error=False,
        expected_version="4.26.1",
        expected_phases=("fuzzing",),
    )
    version_result = schemathesis_module.classify_schemathesis_result(
        _report(),
        outer_exit_code=0,
        outer_timed_out=False,
        outer_environment_error=False,
        expected_version="4.27.1",
        expected_phases=("fuzzing",),
    )

    assert warning_result["verdict"] == "harness_failure"
    assert warning_result["reason"] == "configuration_warning"
    assert version_result["verdict"] == "harness_failure"
    assert version_result["reason"] == "version_mismatch"


class _OpenApiFixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps([{"id": 1}]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def test_real_schemathesis_runner_executes_http_operation_and_preserves_raw_reports(tmp_path: Path):
    run_contract = getattr(schemathesis_module, "run_schemathesis_contract", None)
    assert callable(run_contract), "P-07 needs a real Schemathesis process adapter"
    schema = tmp_path / "openapi.json"
    schema.write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": "generic fixture", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "responses": {
                                "200": {
                                    "description": "ok",
                                    "content": {
                                        "application/json": {
                                            "schema": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "required": ["id"],
                                                    "properties": {"id": {"type": "integer"}},
                                                },
                                            }
                                        }
                                    },
                                }
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenApiFixtureHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output = tmp_path / "evidence"
    contract = {
        "id": "openapi-contract",
        "entrypoint_id": "api",
        "schema": "openapi.json",
        "base_url": f"http://127.0.0.1:{server.server_port}",
        "tool_version": "4.26.1",
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
        "max_time_seconds": 1,
        "request_timeout_seconds": 1,
        "request_retries": 0,
        "deterministic": True,
        "timeout_seconds": 10,
    }
    try:
        result = run_contract(contract, project_root=tmp_path, output_dir=output, env=_NO_PROXY_ENV)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result["verdict"] == "pass"
    assert result["reason"] == "checks_passed"
    assert result["operations_tested"] == 1
    assert result["tool_version"] == "4.26.1"
    assert result["version_probe"]["passed"] is True
    assert result["command_receipt"]["exit_code"] == 0
    assert Path(result["report_json_ref"]).is_file()
    assert Path(result["report_junit_ref"]).is_file()


def test_real_schemathesis_runner_classifies_accepted_invalid_input_as_product_finding(tmp_path: Path):
    schema = tmp_path / "openapi.json"
    schema.write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": "negative fixture", "version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "parameters": [
                                {
                                    "name": "limit",
                                    "in": "query",
                                    "required": True,
                                    "schema": {"type": "integer", "minimum": 1, "maximum": 3},
                                }
                            ],
                            "responses": {
                                "200": {
                                    "description": "ok",
                                    "content": {
                                        "application/json": {"schema": {"type": "array"}}
                                    },
                                },
                                "400": {"description": "invalid input"},
                            },
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenApiFixtureHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    contract = {
        "id": "negative-contract",
        "entrypoint_id": "api",
        "schema": "openapi.json",
        "base_url": f"http://127.0.0.1:{server.server_port}",
        "tool_version": "4.26.1",
        "phases": ["coverage"],
        "mode": "all",
        "checks": ["negative_data_rejection"],
        "workers": 1,
        "seed": 1729,
        "max_examples": 1,
        "max_time_seconds": 5,
        "request_timeout_seconds": 1,
        "request_retries": 0,
        "deterministic": True,
        "timeout_seconds": 30,
    }
    try:
        result = schemathesis_module.run_schemathesis_contract(
            contract,
            project_root=tmp_path,
            output_dir=tmp_path / "evidence",
            env=_NO_PROXY_ENV,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result["verdict"] == "fail"
    assert result["reason"] == "product_finding"
    assert result["operations_tested"] == 1
    assert result["failures"]
