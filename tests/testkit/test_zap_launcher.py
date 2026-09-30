"""Launcher contracts with no Docker/Provider/remote network activity."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import pytest

from tests.testkit.browser_app import create_app
from tests.testkit.command_runner import run_command
from tests.testkit.evidence import sha256_file, write_json_atomic
from tests.testkit.fixtures.fake_zap_docker import FakeDocker, initial_state
from tests.testkit.zap_launcher import LABEL, _Docker, main, run_zap, seed_target
from tests.testkit.zap_protocol import PATHS, fixed_plan, owned_report_verdict, strict_json


ZAP_IMAGE = "ghcr.io/zaproxy/zaproxy:stable@sha256:" + "a" * 64
APP_IMAGE = "zf-test-app:local@sha256:" + "b" * 64
ORIGIN = "http://172.29.0.2:8002"
NONCE = "a" * 32


@pytest.fixture(autouse=True)
def clean_docker_environment(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)


def launch(tmp_path: Path, fake: FakeDocker, **kwargs) -> dict:
    return run_zap(output_dir=tmp_path / "output", zap_image=ZAP_IMAGE, app_image=APP_IMAGE, executor=fake, **kwargs)


def test_fixed_plan_requires_all_url_tests_and_waits_before_report() -> None:
    plan = fixed_plan(ORIGIN, NONCE)
    assert [job["type"] for job in plan["jobs"]] == ["requestor", "passiveScan-wait", "report"]
    assert plan["env"]["parameters"] == {
        "failOnError": True, "failOnWarning": True, "continueOnFailure": False, "progressToStdout": True,
    }
    requests = plan["jobs"][0]["requests"]
    assert {row["url"] for row in requests} == {ORIGIN + path for path in PATHS}
    assert all(row["method"] == "GET" and row["responseCode"] == 200 for row in requests)
    for test in plan["jobs"][0]["tests"]:
        assert test["operator"] == "and" and test["onFail"] == "error"
        assert all(test[key] for key in ("requestHeaderRegex", "requestBodyRegex", "responseHeaderRegex", "responseBodyRegex"))
    assert f"TASK-ZAP-{NONCE}" in plan["jobs"][0]["tests"][1]["responseBodyRegex"]
    assert all(job["alwaysRun"] is False for job in plan["jobs"])
    assert plan["jobs"][1]["parameters"]["maxDuration"] == 0
    assert plan["jobs"][-1]["parameters"]["reportFile"] == f"zap-{NONCE}.json"


@pytest.mark.parametrize("origin", [
    "http://127.0.0.1:8002", "http://169.254.169.254:8002", "https://172.29.0.2:8002",
    "http://172.29.0.2:8001", "http://172.29.0.2.evil:8002", "http://user@172.29.0.2:8002",
    "http://172.29.0.2:8002/", "http://172.29.0.2:8002#x", "http://8.8.8.8:8002",
])
def test_plan_rejects_free_or_ambiguous_origins(origin: str) -> None:
    with pytest.raises(ValueError):
        fixed_plan(origin, NONCE)


def test_seeded_target_real_api_exposes_only_this_nonce(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "fixture"
    seed_target(root, NONCE)
    monkeypatch.setenv("ZF_TESTKIT_BROWSER_ROOT", str(root))
    monkeypatch.setenv("ZF_TESTKIT_BROWSER_DIST", str(root / "empty-ui"))
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(root / "workspace"))
    with TestClient(create_app()) as client:
        health = client.get("/health")
        state = client.get("/api/state")
    assert health.status_code == state.status_code == 200
    assert {task["id"] for task in state.json()["tasks"]} == {f"TASK-ZAP-{NONCE}"}
    assert not (root / ".zf").exists()


@pytest.mark.parametrize("risk,expected", [(0, "pass"), (1, "pass"), (2, "fail"), (3, "fail")])
def test_owned_launch_risk_gate_and_cleanup(tmp_path: Path, risk: int, expected: str) -> None:
    fake = FakeDocker(risk=risk)
    result = launch(tmp_path, fake)
    assert result["status"] == expected, result.get("reason")
    assert result["execution_kind"] == "fake" and result["cleanup_ok"] is True
    assert result["coverage_proof"]["requests"] == {path: 1 for path in PATHS}
    assert result["report"]["digest"] == sha256_file(Path(result["report"]["ref"]))
    assert not fake.state["containers"] and not fake.state["networks"]
    assert json.loads((tmp_path / "output/zap-run.json").read_text())["status"] == expected
    assert json.loads((tmp_path / "output/scan-result.json").read_text())["status"] == expected
    ownership = json.loads((tmp_path / "output/ownership.json").read_text())
    assert ownership["run_id"] == result["run_id"] and ownership["resources"] == result["ownership"]
    assert all(result["harness_digests"].values())
    creates = [args for args in fake.state["calls"] if args[:2] == ["container", "create"]]
    assert len(creates) == 2
    for args in creates:
        assert "--pull=never" in args and "--read-only" in args and "--cap-drop=ALL" in args
        assert "--publish" not in args and "host" not in args and "--privileged" not in args
        mounts = [args[index + 1] for index, arg in enumerate(args) if arg == "--mount"]
        assert all("docker.sock" not in value and "/home/uisee/." not in value for value in mounts)
    assert not any(args[:2] == ["image", "pull"] for args in fake.state["calls"])


@pytest.mark.parametrize("fault", [
    "old_engine", "missing_image", "wrong_digest", "unsafe_network", "unsafe_container", "foreign_member",
    "wrong_nonce", "missing_report", "zero_requests", "unexpected_request", "wrong_report_target",
    "changed_plan", "mutate_canonical", "oom", "cleanup_rm_failure", "cleanup_daemon_down", "name_conflict",
])
def test_launch_faults_never_produce_green(tmp_path: Path, fault: str) -> None:
    fake = FakeDocker(**{fault: True}, risk=3)
    result = launch(tmp_path, fake)
    assert result["status"] == "harness_failure", result
    assert result.get("reason") or result["cleanup_errors"]
    assert (tmp_path / "output/zap-run.json").is_file()
    if fault not in {"cleanup_rm_failure", "cleanup_daemon_down", "foreign_member"}:
        assert not fake.state["containers"] and not fake.state["networks"]
    if fault == "foreign_member":
        assert result["cleanup_ok"] is False and fake.state["networks"]
        assert not any(args[:2] == ["network", "disconnect"] or args[-1] == "f" * 64
                       for args in fake.state["calls"])
    if fault in {"old_engine", "missing_image", "wrong_digest"}:
        assert not any(args[1:2] == ["create"] for args in fake.state["calls"])


@pytest.mark.parametrize("code", [1, 2, 3, 125, 126, 127, 137])
def test_af_execution_failure_dominates_high_risk_report(tmp_path: Path, code: int) -> None:
    result = launch(tmp_path, FakeDocker(scan_exit=code, risk=3))
    assert result["status"] == "harness_failure" and result["scan"]["exit_code"] == code
    assert result["cleanup_ok"] is True


@pytest.mark.parametrize("kind", ["container", "network"])
def test_lost_create_response_is_reconciled_by_owned_label_and_name(tmp_path: Path, kind: str) -> None:
    fake = FakeDocker(lost_create_reply=kind)
    result = launch(tmp_path, fake)
    assert result["status"] == "harness_failure" and result["cleanup_ok"] is True
    assert not fake.state["containers"] and not fake.state["networks"]


def test_resource_intent_is_durable_before_each_create(tmp_path: Path) -> None:
    fake = FakeDocker()
    observed = []

    def executor(argv, **kwargs):
        kind, action = argv[5:7]
        if action == "create":
            name = argv[argv.index("--name") + 1] if kind == "container" else argv[-1]
            ownership = strict_json((tmp_path / "output/ownership.json").read_text())
            assert ownership["resources"][kind][name] == ""
            observed.append((kind, name))
        return fake(argv, **kwargs)

    assert launch(tmp_path, executor)["status"] == "pass"
    assert [kind for kind, _ in observed] == ["network", "container", "container"]


def test_cleanup_never_removes_an_unrelated_resource(tmp_path: Path) -> None:
    fake = FakeDocker()
    foreign_id = "f" * 64
    foreign = {"Id": foreign_id, "Name": "/unrelated-session",
               "Config": {"Labels": {LABEL: "another-run"}}, "NetworkSettings": {"Networks": {}}}
    fake.state["containers"][foreign_id] = foreign
    result = launch(tmp_path, fake)
    assert result["status"] == "pass" and result["cleanup_ok"] is True
    assert fake.state["containers"] == {foreign_id: foreign}
    assert not any(args[0] == "container" and args[-1] == foreign_id for args in fake.state["calls"])


@pytest.mark.parametrize("mismatch", ["label", "name", "id"])
def test_cleanup_inspection_mismatch_never_authorizes_delete(tmp_path: Path, mismatch: str) -> None:
    fake = FakeDocker()
    identity, name = "a" * 64, "zf-zap-owned-net"
    fake.state["networks"][identity] = {"Id": identity, "Name": name, "Labels": {LABEL: NONCE}}
    docker = _Docker(tmp_path, NONCE, fake)
    docker.intents["network"][name] = identity
    original_inspect = docker.inspect

    def inspect(kind, resource):
        row = original_inspect(kind, resource)
        row[{"label": "Labels", "name": "Name", "id": "Id"}[mismatch]] = (
            {LABEL: "other-run"} if mismatch == "label" else "other-resource"
        )
        return row

    docker.inspect = inspect
    errors = docker.cleanup()
    assert any("ownership mismatch" in error for error in errors)
    assert identity in fake.state["networks"]
    assert not any(args[:2] == ["network", "rm"] for args in fake.state["calls"])


def test_cancellation_keeps_receipt_and_cleans_owned_resources(tmp_path: Path) -> None:
    fake = FakeDocker(interrupt=True)
    with pytest.raises(KeyboardInterrupt, match="synthetic cancellation"):
        launch(tmp_path, fake)
    result = json.loads((tmp_path / "output/zap-run.json").read_text())
    assert result["status"] == "harness_failure" and result["cleanup_ok"] is True
    assert not fake.state["containers"] and not fake.state["networks"]


@pytest.mark.parametrize("value", ["stable", "ghcr.io/zaproxy/zaproxy:stable", "x@sha256:abc"])
def test_unpinned_images_are_rejected_before_executor(tmp_path: Path, value: str) -> None:
    fake = FakeDocker()
    result = run_zap(output_dir=tmp_path / "output", zap_image=value, app_image=APP_IMAGE, executor=fake)
    assert result["status"] == "harness_failure" and fake.state["calls"] == []


def test_remote_daemon_environment_is_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DOCKER_HOST", "tcp://other.invalid:2375")
    fake = FakeDocker()
    assert launch(tmp_path, fake)["status"] == "harness_failure"
    assert fake.state["calls"] == []


def test_preexisting_output_is_not_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "output"
    write_json_atomic(output / "zap-run.json", {"original": True})
    with pytest.raises(ValueError, match="new non-symlink"):
        launch(tmp_path, FakeDocker())
    assert strict_json((output / "zap-run.json").read_text()) == {"original": True}


@pytest.mark.parametrize("contents", ['{"site": [], "site": []}', '{"number": NaN}'])
def test_report_json_rejects_ambiguous_values(contents: str) -> None:
    with pytest.raises(ValueError):
        strict_json(contents)


def test_report_cannot_borrow_another_origin_or_symlink(tmp_path: Path) -> None:
    target = tmp_path / "source.json"
    write_json_atomic(target, {"site": []})
    link = tmp_path / "link.json"
    link.symlink_to(target)
    result = owned_report_verdict(link, origin=ORIGIN, max_risk=1)
    assert result.status == "harness_failure" and "symlink" in result.reason


@pytest.mark.parametrize("mutation", ["uri", "method", "metadata", "empty_sites", "ssl_type", "empty_file"])
def test_report_identity_and_instance_scope_are_required(tmp_path: Path, mutation: str) -> None:
    result = launch(tmp_path, FakeDocker(risk=1))
    report_path = Path(result["report"]["ref"])
    payload = strict_json(report_path.read_text())
    site = payload["site"][0]
    if mutation == "uri":
        site["alerts"][0]["instances"][0]["uri"] = ORIGIN + "/api/action"
    elif mutation == "method":
        site["alerts"][0]["instances"][0]["method"] = "POST"
    elif mutation == "metadata":
        payload.pop("@version")
    elif mutation == "empty_sites":
        payload["site"] = []
    elif mutation == "ssl_type":
        site["@ssl"] = False
    if mutation == "empty_file":
        report_path.write_bytes(b"")
    else:
        write_json_atomic(report_path, payload)
    assert owned_report_verdict(report_path, origin=ORIGIN, max_risk=1).status == "harness_failure"


@pytest.mark.parametrize("block_scan", [False, True])
def test_actual_subprocess_receipts_and_timeout_cleanup(tmp_path: Path, block_scan: bool) -> None:
    fake_state = tmp_path / "fake-daemon.json"
    write_json_atomic(fake_state, initial_state(block_scan=block_scan))
    repo = Path(__file__).resolve().parents[2]

    def executor(argv, **kwargs):
        return run_command([sys.executable, "-m", "tests.testkit.fixtures.fake_zap_docker", str(fake_state), *argv],
                           **(kwargs | {"env": {"PYTHONPATH": str(repo)}}))

    result = run_zap(output_dir=tmp_path / "output", zap_image=ZAP_IMAGE, app_image=APP_IMAGE,
                     timeout_seconds=0.3 if block_scan else 10, executor=executor)
    assert result["execution_kind"] == "fake"
    assert result["status"] == ("harness_failure" if block_scan else "pass"), result.get("reason")
    assert result["cleanup_ok"] is True
    if block_scan:
        assert result["scan"]["timed_out"] is True
    state = json.loads(fake_state.read_text())
    assert not state["containers"] and not state["networks"]
    assert result["commands"] and all(Path(row["stdout_ref"]).is_file() for row in result["commands"])


def test_cli_calls_the_launcher_without_a_free_target_surface(tmp_path: Path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr("tests.testkit.zap_launcher.run_zap", lambda **kwargs: calls.append(kwargs) or {"status": "pass"})
    monkeypatch.setattr(sys, "argv", ["zap_launcher", "--output", str(tmp_path / "out"), "--zap-image", ZAP_IMAGE, "--app-image", APP_IMAGE])
    assert main() == 0
    assert calls[0]["zap_image"] == ZAP_IMAGE and "executor" not in calls[0]


def test_cli_subprocess_bad_image_reports_harness_failure_before_docker(tmp_path: Path) -> None:
    output = tmp_path / "scan"
    receipt = run_command(
        [sys.executable, "-m", "tests.testkit.zap_launcher", "--output", str(output),
         "--zap-image", "not-pinned", "--app-image", APP_IMAGE],
        command_id="zap-cli", cwd=Path(__file__).resolve().parents[2], output_dir=tmp_path / "commands",
        timeout_seconds=15,
    )
    assert receipt.exit_code == 1 and not receipt.timed_out
    result = json.loads((output / "zap-run.json").read_text())
    assert result["status"] == "harness_failure" and result["commands"] == []
    assert "digest-pinned" in result["reason"]
