"""Opt-in qualification of a scoped host mount and the worker sandbox that consumes it."""

from collections import deque
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.integration.codex_file_tool_fixture import file_tool_endpoint
from tests.integration.test_codex_milestone_recovery import codex_command, run_turn
from tests.integration.test_frozen_codex_workspace import mounted_workspace
from tests.test_artifact_read_snapshot import published
from zf.core.events import EventLog, EventWriter
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.artifact_native_delivery import observe_native, verified_native
from zf.runtime.artifact_read_capability import (
    bind_attempt_artifact_read_capability,
    provision_role_artifact_read_credential,
)
from zf.runtime.artifact_read_ledger import active_read_ledger_path
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.workflow_operation import WorkflowOperationService

pytestmark = pytest.mark.real_provider


@pytest.mark.skipif(
    os.getenv("ZF_TEST_INPUT_MOUNT") != "1", reason="requires an isolated Linux worker scope"
)
def test_scoped_workspace_and_sandbox_native_witness(tmp_path, monkeypatch):
    group = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    assert "zf-input-mock-" in group and group.endswith(".scope")
    role = "planner"
    state = tmp_path / "qualified-state"
    cfg = tmp_path / "zf.yaml"
    cfg.write_text(f"""apiVersion: zaofu.dev/v1
kind: PrdFlow
spec:
  lanes: 1
  artifactWorkerCgroups:
    {role}: {group}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
spec:
  project: {{name: native-qualification, state_dir: qualified-state}}
""")
    marker = "bounded-native-qualification"
    manifest, rows, manifest_ref = published(state, body={"shared": marker, "tasks": list(range(8))})
    wanted = [row for row in rows if row["json_path"] == "$.shared"]
    policy = write_immutable_json_sidecar(
        state,
        {
            "schema_version": "qualification-policy.v1",
            "attempt_id": "a",
            "required_reads": wanted,
            "delivery_evidence": {"source_manifest_ref": manifest_ref},
        },
        root="qualification",
        kind="input_consumption_policy",
        schema_version="qualification-policy.v1",
        created_by="qualification",
    )
    log = EventLog(state / "events.jsonl")
    service = WorkflowOperationService(state_dir=state, event_log=log, event_writer=EventWriter(log))
    issued = service.ensure_operation(
        workflow_run_id="r",
        task_id="T7",
        role_instance=role,
        active_attempt_id="a",
        operation_id="native-qualification",
        operation_type="fanout_reader_child",
        lease_id="lease-qualification",
        request={
            "workflow_run_id": "r",
            "task_id": "T7",
            "role_instance": role,
            "active_attempt_id": "a",
            "native_input_cgroup": group,
            "input_consumption_policy_ref": policy,
        },
    )
    token = provision_role_artifact_read_credential(state, role, role_name=role, provider="codex")
    bind_attempt_artifact_read_capability(
        state, operation_id=issued.operation_id, attempt_id="a", role_instance=role, manifest=manifest
    )
    monkeypatch.setenv("ZF_REAL_CODEX_LOCAL_KEY", "local-qualification-only")
    targets = deque()
    evidence = {
        "worker_cgroup": group,
        "operation_id": issued.operation_id,
        "attempt_id": "a",
        "provider": "Codex CLI with deterministic loopback Responses fixture",
    }
    with file_tool_endpoint(targets) as url:
        home, env, base = codex_command(
            tmp_path, url, "Read only the requested frozen input using exec_command."
        )
        base[base.index("read-only")] = "workspace-write"
        env.update(
            ZF_PROJECT_ROOT=str(tmp_path),
            ZF_STATE_DIR=str(state),
            ZF_ROLE_INSTANCE=role,
            ZF_ROLE_NAME=role,
            ZF_ROLE_BACKEND="codex",
            ZF_ARTIFACT_READ_TOKEN_FILE=str(token),
            ZF_ARTIFACT_PURPOSE="implementation",
        )
        initial, _ = run_turn([*base, "Initialize the qualification session."], env, tmp_path)
        sid = next(row["thread_id"] for row in initial if row["type"] == "thread.started")
        rollout = next((home / "sessions").rglob("*" + sid + "*.jsonl"))
        assert RoleSessionRegistry(state / "role_sessions.yaml", tmp_path).bind_codex_session(
            role, sid, session_path=rollout
        )
        service.mark_started(
            operation_id=issued.operation_id,
            request_hash=issued.request_hash,
            workflow_run_id="r",
            task_id="T7",
            role_instance=role,
            active_attempt_id="a",
            dispatch_id="a",
            lease_id="lease-qualification",
            provider_session_id=sid,
        )
        # A dependency visible to the controller can still be hidden in the tool namespace.
        sandbox = ["codex", "sandbox", "-c", 'sandbox_mode="workspace-write"', "--"]
        inspect = (
            "import importlib.util,json,os,pathlib; "
            "name='fusepy' if importlib.util.find_spec('fusepy') else 'fuse'; __import__(name); "
            "print(json.dumps({'adapter':name,'device_present':pathlib.Path('/dev/fuse').exists(),"
            "'cgroup':pathlib.Path('/proc/self/cgroup').read_text().strip()}))"
        )
        observed = subprocess.run(
            [*sandbox, sys.executable, "-Bc", inspect],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        evidence["workspace_write"] = json.loads(observed.stdout)
        assert evidence["workspace_write"]["cgroup"] == "0::" + group
        if not evidence["workspace_write"]["device_present"]:
            attempt = subprocess.run(
                [
                    *sandbox,
                    sys.executable,
                    "-Bm",
                    "zf.cli.main",
                    "artifact",
                    "workspace",
                    "--state-dir",
                    str(state),
                    "--attempt",
                    "a",
                ],
                env=env,
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert attempt.returncode == 1 and "Use zf artifact index/read/recover" in attempt.stderr, (
                attempt.stderr
            )
            assert "FUSE mount failed" in attempt.stderr
            fallback = subprocess.run(
                [
                    *sandbox,
                    sys.executable,
                    "-Bm",
                    "zf.cli.main",
                    "artifact",
                    "read",
                    "--state-dir",
                    str(state),
                    "--attempt",
                    "a",
                    "--source",
                    wanted[0]["source_id"],
                    "--artifact",
                    wanted[0]["artifact_id"],
                    "--json-path",
                    "$.shared",
                ],
                env=env,
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
            assert marker in fallback.stdout
            evidence["workspace_write"].update(
                workspace_exit=attempt.returncode,
                authorized_cli_read_exit=0,
                fallback_reason=next(
                    line for line in attempt.stderr.splitlines() if "Input workspace unavailable" in line
                ),
            )
        root = Path(__file__).resolve().parents[2]
        with mounted_workspace(
            root, tmp_path, cfg, state, "a", env, cli=[sys.executable, "-Bm", "zf.cli.main"]
        ) as (index, _):
            assert os.path.ismount(index.parent)
            selected = next(
                row
                for line in index.read_text().splitlines()
                if line.startswith("{") and (row := json.loads(line))["reason"] == "required"
            )
            targets.append(index.parent / selected["path"])
            finished, _ = run_turn([*base, "resume", sid, "Read the qualified input."], env, tmp_path)
            assert any(marker in row.get("item", {}).get("aggregated_output", "") for row in finished)
            (receipt,) = [
                row
                for line in active_read_ledger_path(state, "a").read_text().splitlines()
                if (row := json.loads(line)).get("native_host_ref")
            ]
            result = observe_native(state, receipt, True, {})
            text = (index.parent / selected["path"]).read_text()
            ranges = verified_native(state, result, text, ephemeral=False)
            assert [(part["page_start"], part["page_end"]) for part in ranges] == [(0, len(text))]
            evidence["host_prepared_readonly_view"] = {
                "mounted": True,
                "verified_range": [0, len(text)],
                "codex_version": subprocess.check_output(["codex", "--version"], text=True).strip(),
                "readonly": bool(os.statvfs(index).f_flag & os.ST_RDONLY),
                "source_id": result["source_id"],
                "production_status": result["production_status"],
                "delivery_status": result["provider_delivery_status"],
                "native_host_sha256": result["native_host_ref"]["sha256"],
                "provider_call_id": result["provider_call_id"],
                "provider_session_id": sid,
            }
    evidence["host_prepared_readonly_view"]["released"] = not os.path.ismount(index.parent)
    assert (
        evidence["host_prepared_readonly_view"]["released"]
        and evidence["host_prepared_readonly_view"]["readonly"]
    )
    (tmp_path / "native-workspace-qualification.json").write_text(json.dumps(evidence, indent=2) + "\n")
