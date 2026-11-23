"""Original 8×8 Candidate corpus, a newly authorized isolated CLI mount and real Codex exec."""

from collections import deque
from contextlib import contextmanager
import json
import os
from pathlib import Path
import selectors
import subprocess

import pytest
import yaml

from tests.e2e.test_workflow_input_replay_mock_e2e import copy_descriptor, copy_sources
from tests.integration.codex_file_tool_fixture import file_tool_endpoint
from tests.integration.test_codex_milestone_recovery import codex_command, run_turn
from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.artifact_read_capability import (
    bind_attempt_artifact_read_capability,
    provision_role_artifact_read_credential,
)
from zf.runtime.artifact_read_ledger import seal_read_ledger
from zf.runtime.artifact_read_ledger_validation import validate_required_reads
from zf.runtime.artifact_workdir_view import available_input_cgroup
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import WorkflowOperationService

pytestmark = pytest.mark.real_provider


@contextmanager
def mounted_workspace(root, tmp_path, cfg, state, attempt, env):
    zf = root / ".venv/bin/zf"
    assert zf.is_file()
    stderr = tmp_path / "mount-log.txt"
    with (
        stderr.open("w") as errors,
        subprocess.Popen(
            [
                str(zf),
                "artifact",
                "workspace",
                "--state-dir",
                str(state),
                "--attempt",
                attempt,
            ],
            cwd=tmp_path,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
        ) as mount,
    ):
        mountpoint = None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(mount.stdout, selectors.EVENT_READ)
                assert selector.select(25), "mount startup timeout"
                first = mount.stdout.readline().strip()
            assert first, stderr.read_text()
            index = Path(first)
            mountpoint = index.parent
            assert index.name == "INPUTS.md" and os.path.ismount(mountpoint)
            assert not mountpoint.is_relative_to(tmp_path)
            resume = Path(mount.stdout.readline().strip())
            yield index, resume
        finally:
            # A config revision explicitly invalidates the fixture's lease.
            cfg.touch()
            try:
                mount.wait(timeout=7)
            except subprocess.TimeoutExpired:
                mount.terminate()
                mount.wait(timeout=7)
            if mountpoint and os.path.ismount(mountpoint):
                subprocess.run(["fusermount3", "-u", str(mountpoint)], check=True)


def required_file_ranges(index):
    items = [
        json.loads(line)
        for line in index.read_text().splitlines()
        if line.startswith("{")
    ]
    required = [i for i in items if i["reason"] == "required"]
    assert len(required) == 40
    selections = []
    for row in required:
        length = row["characters"][1]
        assert isinstance(length, int), row["source_id"]
        file = index.parent / row["path"]
        for start in range(0, max(length, 1), 8192):
            selections.append(
                file
                if length <= 8192
                else Path(f"{file}.chars-{start}-{min(length, start + 8192)}")
            )
    return required, selections


def test_codex_reads_frozen_candidate_via_workspace_cli(tmp_path, monkeypatch):
    folder, attempt = (
        os.getenv("ZF_8X8_FIXTURE_STATE"),
        os.getenv("ZF_8X8_FIXTURE_ATTEMPT"),
    )
    if not folder or not attempt or os.getenv("ZF_TEST_INPUT_MOUNT") != "1":
        pytest.skip(
            "requires a frozen Candidate lane and an isolated Linux worker scope"
        )
    group = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    assert "zf-input-mock-" in group and group.endswith(".scope")
    source = Path(folder)
    with (source / "events.jsonl").open() as stream:
        original = next(
            row["payload"]
            for line in stream
            if (row := json.loads(line))["type"] == "workflow.operation.requested"
            and row["payload"].get("active_attempt_id") == attempt
        )
    envelope = hydrate_sidecar_ref(source, original["request_ref"]).payload
    frozen = envelope["request"]
    assert "native_input_cgroup" not in frozen
    role = frozen["role_instance"]
    # A separate control plane is the owner for this experiment. Preserve logical
    # run/operation/target IDs so the immutable original host receipts still bind.
    root = Path(__file__).resolve().parents[2]
    docs = list(yaml.safe_load_all((root / "zf-prd-v5.yaml").read_text()))
    docs[0]["spec"]["artifactWorkerCgroups"] = {role: group}
    config_spec = docs[1]["spec"]
    config_spec["profile_sources"] = [
        str(root / source) for source in config_spec["profile_sources"]
    ]
    config_spec["project"] = {
        "name": "isolated-input-corpus",
        "state_dir": "runtime-state",
    }
    cfg = tmp_path / "zf.yaml"
    cfg.write_text(yaml.safe_dump_all(docs, sort_keys=False))
    config = load_config(cfg)
    assert available_input_cgroup(config, role) == group
    state = tmp_path / config.project.state_dir
    copy_descriptor(source, state, frozen["input_consumption_policy_ref"])
    policy = hydrate_sidecar_ref(state, frozen["input_consumption_policy_ref"]).payload
    manifest_ref = policy["delivery_evidence"]["source_manifest_ref"]
    copy_descriptor(source, state, manifest_ref)
    manifest = hydrate_sidecar_ref(state, manifest_ref).payload
    copy_sources(source, state, manifest["sources"])
    assert len(policy["required_reads"]) == 40
    log = EventLog(state / "events.jsonl")
    service = WorkflowOperationService(
        state_dir=state, event_log=log, event_writer=EventWriter(log)
    )
    copied_fields = (
        "workflow_run_id",
        "operation_id",
        "operation_type",
        "parent_operation_id",
        "parent_stage_id",
        "parent_attempt_id",
        "task_id",
        "parent_task_id",
        "role_instance",
        "active_attempt_id",
        "lease_id",
        "child_task_ids",
    )
    issued = service.ensure_operation(
        **{k: envelope[k] for k in copied_fields},
        request={**frozen, "native_input_cgroup": group},
    )
    assert issued.request_hash != original["request_hash"]
    token = provision_role_artifact_read_credential(
        state, role, role_name=role, provider="codex"
    )
    bind_attempt_artifact_read_capability(
        state,
        operation_id=issued.operation_id,
        attempt_id=attempt,
        role_instance=role,
        manifest=manifest,
    )
    worker = {
        "ZF_PROJECT_ROOT": str(tmp_path),
        "ZF_STATE_DIR": str(state),
        "ZF_ROLE_INSTANCE": role,
        "ZF_ROLE_NAME": role,
        "ZF_ROLE_BACKEND": "codex",
        "ZF_ARTIFACT_READ_TOKEN_FILE": str(token),
        "ZF_ARTIFACT_PURPOSE": manifest["read_purpose"],
    }
    monkeypatch.setenv("ZF_REAL_CODEX_LOCAL_KEY", "isolated-test-only")
    targets = deque()
    with file_tool_endpoint(targets) as url:
        home, env, base = codex_command(
            tmp_path, url, "Read source files using the supplied tool calls."
        )
        env.update(worker)
        baseline, _ = run_turn(
            [*base, "Initialize this read-only Candidate Verify session."],
            env,
            tmp_path,
        )
        sid = next(r["thread_id"] for r in baseline if r["type"] == "thread.started")
        rollout = next((home / "sessions").rglob("*" + sid + "*.jsonl"))
        assert RoleSessionRegistry(
            state / "role_sessions.yaml", tmp_path
        ).bind_codex_session(role, sid, session_path=rollout)
        service.mark_started(
            operation_id=issued.operation_id,
            request_hash=issued.request_hash,
            workflow_run_id=envelope["workflow_run_id"],
            task_id=envelope["task_id"],
            role_instance=role,
            active_attempt_id=attempt,
            lease_id=envelope["lease_id"],
            dispatch_id=manifest["dispatch_id"],
            provider_session_id=sid,
        )
        with mounted_workspace(root, tmp_path, cfg, state, attempt, env) as (
            index,
            resume,
        ):
            required, selections = required_file_ranges(index)
            midpoint = len(selections) // 2
            for phase, part in enumerate(
                (selections[:midpoint], selections[midpoint:])
            ):
                if phase:
                    stopped, _ = run_turn(
                        [*base, "resume", sid, "Wait at the checkpoint."],
                        env,
                        tmp_path,
                        kill_on_start=True,
                    )
                    assert all(e["type"] != "turn.completed" for e in stopped)
                    targets.append(resume)
                else:
                    targets.append(index)
                targets.extend(part)
                events, _ = run_turn(
                    [
                        *base,
                        "resume",
                        sid,
                        "Continue reading the frozen selections.",
                    ],
                    env,
                    tmp_path,
                )
                completed = [
                    r
                    for r in events
                    if r["type"] == "item.completed"
                    and r.get("item", {}).get("type") == "command_execution"
                ]
                assert len(completed) == 1 + len(part) and all(
                    r["item"].get("exit_code") == 0 for r in completed
                )
                assert not targets
                checkpoint = json.loads(resume.read_bytes().split(b"\n", 2)[2])
                assert checkpoint["provider_delivery"]["status"] == (
                    "complete" if phase else "incomplete"
                ), checkpoint["provider_delivery"]
                host = checkpoint["host_evidence"]
                returned = completed[0]["item"]["aggregated_output"]
                if phase:
                    assert host["gaps"][0]["receipt_id"] in returned
                    assert host["gaps"][0]["declared_paths"][0] in returned
                else:
                    assert "INPUTS" in returned and required[0]["path"] in returned
                assert (host["total"], host["available"], len(host["gaps"])) == (
                    19,
                    18,
                    1,
                )
                assert not host["authorizes_retry"]
            ledger = seal_read_ledger(state, attempt)
            validation = validate_required_reads(
                state, policy=policy, ledger_descriptor=ledger
            )
            assert not validation, validation
            receipts = [
                json.loads(line)
                for line in hydrate_sidecar_ref(state, ledger).payload.splitlines()
            ]
            assert len(receipts) == len(selections) and all(
                r.get("native_proof_ref") for r in receipts
            )
            print(
                "ZF_FROZEN_CODEX_WORKSPACE",
                json.dumps(
                    {
                        "attempt": attempt,
                        "target": frozen["target_commit"],
                        "manifest_sha256": manifest_ref["sha256"],
                        "source_request": original["request_hash"],
                        "test_request": issued.request_hash,
                        "bound_sources": len(required),
                        "observed_tool_reads": len(receipts),
                        "host_receipts": host["total"],
                        "available_host_receipts": host["available"],
                        "host_gaps": len(host["gaps"]),
                        "same_session_turns": 2,
                        "killed_worker_processes": 1,
                        "navigation_reads": ["INPUTS.md", "RESUME.md"],
                    }
                ),
            )
