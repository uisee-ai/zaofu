"""Real CLI process kill and session resume, finishing at the Candidate submission boundary."""

import json
import os
import re
import signal
import subprocess
import sys
import time
from uuid import uuid4
from urllib.parse import urlparse

import pytest

from tests.test_candidate_verification_authority import _runtime, _seed_frozen_candidate
from zf.core.events import EventLog, EventWriter
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.artifact_read_ledger import (
    active_read_ledger_path,
    read_attempt_artifact,
)
from zf.runtime.artifact_input_recovery import build_input_recovery
from zf.runtime.artifact_workdir_view import AttemptFiles
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.call_result_runtime import (
    mark_call_operation_started,
    prepare_call_operation,
)
from zf.runtime.codex_session_ownership import codex_session_exists_for_role
from zf.runtime.result_submit import (
    SemanticResultSubmitService,
    provision_role_submit_credential,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_pipeline_briefing import verification_result_template

pytestmark = pytest.mark.real_provider


def run_turn(cmd, env, cwd, *, kill_on_start=False):
    import selectors

    started = time.monotonic()
    rows = []
    pending = b""
    with (
        subprocess.Popen(
            cmd,
            env=env,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ) as process,
        selectors.DefaultSelector() as selector,
    ):
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while time.monotonic() - started < 210:
                if not selector.select(1):
                    continue
                block = os.read(process.stdout.fileno(), 65536)
                if not block:
                    break
                pending += block
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    event = json.loads(line)
                    rows.append(event)
                    if kill_on_start and event.get("type") == "turn.started":
                        os.killpg(process.pid, signal.SIGKILL)
                        assert process.wait(timeout=10) < 0
                        return rows, time.monotonic() - started
            assert not kill_on_start, (
                "provider never opened the turn selected for interruption"
            )
            assert process.wait(timeout=4) == 0
            assert any(r["type"] == "turn.completed" for r in rows), [
                r["type"] for r in rows
            ]
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    return rows, time.monotonic() - started


def codex_command(tmp_path, url, instructions):
    home = tmp_path / "codex-home"
    home.mkdir()
    env = {
        **os.environ,
        "CODEX_HOME": str(home),
        "ZF_HOST_COUNTER": str(tmp_path / "host-execution-counter"),
    }
    rules = tmp_path / "reader-instructions.txt"
    rules.write_text(instructions)
    base = [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "-s",
        "read-only",
        "-c",
        "model_instructions_file=" + json.dumps(str(rules)),
        "-c",
        'model_reasoning_effort="low"',
        "-c",
        'model_provider="validation"',
        "-c",
        'model_providers.validation.name="validation"',
        "-c",
        "model_providers.validation.base_url=" + json.dumps(url),
        "-c",
        'model_providers.validation.wire_api="responses"',
        "-c",
        'model_providers.validation.env_key="ZF_REAL_CODEX_LOCAL_KEY"',
        "-m",
        os.environ.get("ZF_REAL_CODEX_LOCAL_MODEL", "zf-local"),
    ]
    return home, env, base


def test_real_candidate_process_loss_then_admission(tmp_path):
    url = os.getenv("ZF_REAL_CODEX_LOCAL_URL", "")
    if not url:
        pytest.skip("set a loopback Responses API serving an actual installed model")
    parsed = urlparse(url)
    assert (
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and not parsed.username
    )
    runtime = _runtime(tmp_path)
    state = runtime.state_dir
    actor = "verify-lane-0"
    cwd = tmp_path / "worker"
    cwd.mkdir()
    (cwd / "tests").mkdir()
    counter = tmp_path / "host-execution-counter"
    (cwd / "tests/test_pulse.py").write_text(
        "import os\nfrom pathlib import Path\ndef test_pulse():\n"
        "    with Path(os.environ['ZF_HOST_COUNTER']).open('a') as output:\n"
        "        output.write('executed\\n')\n    assert 13 + 29 == 42\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=cwd, check=True)
    subprocess.run(["git", "add", "--", "tests/test_pulse.py"], cwd=cwd, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=cwd,
        check=True,
    )
    target = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=cwd, text=True
    ).strip()
    seeded = _seed_frozen_candidate(runtime, candidate_head=target)
    case = uuid4().hex[:8]
    audit_ref = write_immutable_json_sidecar(
        state,
        {"audit_tag": case},
        root="test-audit-facts",
        kind="audit_tag",
        schema_version="audit-tag.v1",
        created_by="test-owner",
    )
    payload = dict(
        workflow_run_id=seeded["run_id"],
        task_id="FLOW-ANCHOR",
        fanout_id="fanout-candidate-verify",
        child_id=actor,
        role_instance=actor,
        stage_id="issue-lanes-verify",
        output_profile_id="candidate-verify",
        artifact_package_mode="shadow",
        input_refs=[
            {
                **audit_ref,
                "source_id": "audit-tag",
                "artifact_id": "audit-tag",
                "allowed_paths": ["$"],
            }
        ],
        target_commit=target,
        canonical_success_event="test.passed",
        canonical_failure_event="test.failed",
    )
    credential = provision_role_submit_credential(state, actor).read_text().strip()
    op = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key=actor,
        stage_id=payload["stage_id"],
        task_id=payload["task_id"],
        dispatch_id="dispatch-candidate-verify",
        causation_id="evt-candidate-ready",
    )
    manifest = hydrate_sidecar_ref(state, payload["attempt_source_manifest"]).payload
    required = payload["required_reads"]
    for row in required:
        read_attempt_artifact(
            state,
            manifest=manifest,
            source_id=row["source_id"],
            artifact_id=row["artifact_id"],
            json_path=row["json_path"],
        )
    mark_call_operation_started(
        runtime,
        op,
        task_id=payload["task_id"],
        dispatch_id=manifest["dispatch_id"],
        causation_id="evt-candidate-ready",
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_pulse.py"],
        cwd=cwd,
        text=True,
        capture_output=True,
        env={**os.environ, "ZF_HOST_COUNTER": str(counter)},
        check=True,
    )
    receipt = write_immutable_json_sidecar(
        state,
        {
            "target": target,
            "command": seeded["command"],
            "exit_code": result.returncode,
            "stdout": result.stdout,
        },
        root="test-host-facts",
        kind="execution",
        schema_version="test-execution.v1",
        created_by="test-host",
    )
    assert counter.read_text().splitlines() == ["executed"]
    home, env, base = codex_command(
        tmp_path,
        url,
        "Read-only evidence reviewer. Answer the current user in plain text. Do not call tools.",
    )
    initial, duration = run_turn(
        [
            *base,
            f"Remember the audit tag {case} for your next turn. Reply with exactly ACK, no tools.",
        ],
        env,
        cwd,
    )
    sid = next(r["thread_id"] for r in initial if r["type"] == "thread.started")
    transcript = next((home / "sessions").rglob("*" + sid + "*.jsonl"))
    registry_path = state / "role_sessions.yaml"
    registry = RoleSessionRegistry(registry_path, cwd)
    assert registry.bind_codex_session(actor, sid, session_path=transcript)
    interrupted_turns = 0
    for _ in range(2):
        interrupted, _ = run_turn(
            [*base, "resume", sid, "Wait for the remaining checkpoint material."],
            env,
            cwd,
            kill_on_start=True,
        )
        assert all(r["type"] != "turn.completed" for r in interrupted)
        # Reopen registry and event store after the process has actually terminated.
        registry = RoleSessionRegistry(registry_path, cwd)
        assert codex_session_exists_for_role(
            registry,
            role_sessions_root=home / "sessions",
            instance_id=actor,
            session_id=sid,
        )
        interrupted_turns += 1
    log = EventLog(state / "events.jsonl")
    ident = dict(
        actor=actor,
        role="verify",
        provider="codex",
        purpose=manifest.get("read_purpose", "candidate-verify"),
        operation_id=op.operation_id,
        attempt_id=manifest["attempt_id"],
    )
    reads = [
        json.loads(l)
        for l in active_read_ledger_path(state, manifest["attempt_id"])
        .read_text()
        .splitlines()
    ]
    recovery = build_input_recovery(
        state_dir=state,
        manifest=manifest,
        identity=ident,
        events=log.read_all(),
        required=required,
        reads=reads,
    )
    view = AttemptFiles(state, manifest, ident, required, lambda: recovery)
    recovered = json.loads(view.read("RESUME.md")[0].split(b"\n", 2)[2])
    assert recovered["identity"]["target_commit"] == target
    ref = receipt["ref"]
    host = hydrate_sidecar_ref(state, receipt).payload
    final, elapsed = run_turn(
        [
            *base,
            "resume",
            sid,
            "Use exactly two words on one line: your saved audit tag, then PASSED or FAILED. "
            f"Current host receipt for the frozen target: exit {host['exit_code']}. Do not run commands.",
        ],
        env,
        cwd,
    )
    response = " ".join(
        r["item"]["text"]
        for r in final
        if r.get("item", {}).get("type") == "agent_message"
    )
    # The historical receipt does not prove model memory. Correct a mismatch
    # from the exact frozen source through the ordinary reader, then retry once.
    rereads = 0
    verified = re.compile(re.escape(case) + r"\s+PASSED", re.I)
    if not verified.fullmatch(response.strip()):
        rereads = 1
        fact = read_attempt_artifact(
            state,
            manifest=manifest,
            source_id="audit-tag",
            artifact_id="audit-tag",
            json_path="$",
            actor=actor,
            role="verify",
            provider="codex",
            purpose=ident["purpose"],
        )
        correction, more = run_turn(
            [
                *base,
                "resume",
                sid,
                "Return exactly two words, its audit_tag then PASSED or FAILED. Verified source: "
                + str(fact["content"])
                + f" Host receipt exit {host['exit_code']}.",
            ],
            env,
            cwd,
        )
        final += correction
        elapsed += more
        response = " ".join(
            r["item"]["text"]
            for r in correction
            if r.get("item", {}).get("type") == "agent_message"
        )
    assert verified.fullmatch(response.strip()), response[:300]
    contract = hydrate_sidecar_ref(
        state,
        {
            "ref": payload["contract_snapshot_ref"],
            "sha256": payload["contract_snapshot_digest"],
        },
    ).payload
    semantic = verification_result_template(contract)
    semantic.update(summary=response, evidence_refs=[ref], verdict="passed")
    for row in semantic["probe_receipts"]:
        row.update(target_commit=target, evidence_refs=[ref])
    for row in semantic["requirement_results"]:
        row["evidence_refs"] = [ref]
    result = SemanticResultSubmitService(
        state_dir=state, event_log=log, event_writer=EventWriter(log)
    ).submit(
        operation_id=op.operation_id,
        semantic_result=semantic,
        role_instance=actor,
        credential=credential,
    )
    assert result.admitted_event_id
    assert (
        len(
            [
                e
                for e in log.read_all()
                if e.type == "workflow.call.result.admitted"
                and e.payload.get("operation_id") == op.operation_id
            ]
        )
        == 1
    )
    assert counter.read_text().splitlines() == ["executed"]
    print(
        "ZF_REAL_RECOVERY",
        json.dumps(
            {
                "backend": "codex-local-responses",
                "target_commit": target,
                "killed_processes": interrupted_turns,
                "targeted_rereads": rereads,
                "session_reused": True,
                "admitted": result.admitted_event_id,
                "host_commands_after_resume": 0,
                "first_seconds": round(duration, 2),
                "resumed_seconds": round(elapsed, 2),
                "usage": [
                    r["usage"] for r in initial + final if r["type"] == "turn.completed"
                ],
            }
        ),
    )


def test_real_codex_mounted_read(tmp_path, monkeypatch):
    """Check the public tool output against the actual kernel read in a scoped worker."""
    import multiprocessing
    from pathlib import Path

    from tests.test_artifact_read_snapshot import IDENTITY, published
    from zf.runtime.artifact_mount import MountAccess, serve_mount
    from zf.runtime.artifact_native_delivery import observe_native, verified_native

    url = os.getenv("ZF_REAL_CODEX_NATIVE_URL", "")
    if os.getenv("ZF_TEST_INPUT_MOUNT") != "1":
        pytest.skip(
            "requires a loopback tool-capable Responses model and a dedicated worker cgroup"
        )
    if url:
        assert urlparse(url).hostname == "127.0.0.1" and urlparse(url).scheme == "http"
    group = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    assert "zf-input-mock-" in group and group.endswith(".scope")
    version = subprocess.check_output(["codex", "--version"], text=True).split()[-1]
    assert version == "0.171.0"
    state = tmp_path / "state"
    nonce = uuid4().hex[:12]
    manifest, required, _ = published(
        state, body={"shared": nonce, "tasks": list(range(8))}
    )
    view = AttemptFiles(
        state, manifest, {**IDENTITY, "operation_id": "op"}, required, lambda: {}
    )
    path = tmp_path / "inputs-view"
    path.mkdir()
    name = next(
        name for name, (row, _) in view.files.items() if row["json_path"] == "$.shared"
    )
    target = path / name
    access = MountAccess(state, view, path, group, lambda: 1, version)
    daemon = multiprocessing.get_context("fork").Process(
        target=serve_mount, args=(access,)
    )
    from contextlib import ExitStack
    from tests.integration.codex_file_tool_fixture import file_tool_endpoint

    daemon.start()
    providers = ExitStack()
    try:
        if not url:
            monkeypatch.setenv("ZF_REAL_CODEX_LOCAL_KEY", "test-only-loopback")
            url = providers.enter_context(file_tool_endpoint(target))
        deadline = time.monotonic() + 10
        while (
            not os.path.ismount(path)
            and time.monotonic() < deadline
            and daemon.is_alive()
        ):
            time.sleep(0.02)
        assert os.path.ismount(path)
        home, env, base = codex_command(
            tmp_path,
            url,
            "You run shell commands when explicitly asked. Use exec_command for shell commands. Return the result. Otherwise just acknowledge.",
        )
        first, _ = run_turn(
            [*base, "Reply only ACK. Do not use a tool on this turn."], env, tmp_path
        )
        sid = next(r["thread_id"] for r in first if r["type"] == "thread.started")
        transcript = next((home / "sessions").rglob("*" + sid + "*.jsonl"))
        registry = RoleSessionRegistry(state / "role_sessions.yaml", tmp_path)
        assert registry.bind_codex_session(
            IDENTITY["actor"], sid, session_path=transcript
        )
        rows, _ = run_turn(
            [
                *base,
                "resume",
                sid,
                f"Call the exec_command tool with exactly this cmd: /usr/bin/cat {target}\nReturn the contents.",
            ],
            env,
            tmp_path,
        )
        commands = [
            r["item"]
            for r in rows
            if r.get("item", {}).get("type") == "command_execution"
            and r["type"] == "item.completed"
        ]
        assert any(nonce in c.get("aggregated_output", "") for c in commands), commands
        ledger = active_read_ledger_path(state, manifest["attempt_id"])
        assert ledger.exists(), "an actual Codex read did not produce host evidence"
        (receipt,) = [
            json.loads(l)
            for l in ledger.read_text().splitlines()
            if json.loads(l).get("native_host_ref")
        ]
        bound = observe_native(state, receipt, True, {})
        selected = view.read(name)[0].decode()
        intervals = verified_native(state, bound, selected)
        assert [(r["page_start"], r["page_end"]) for r in intervals] == [
            (0, len(selected))
        ]
    finally:
        providers.close()
        daemon.terminate()
        daemon.join(5)
        if os.path.ismount(path):
            subprocess.run(["fusermount3", "-u", str(path)], check=True)
