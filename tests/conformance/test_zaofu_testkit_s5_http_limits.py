"""Installed Claude CLI against a local HTTP fixture, never real quota exhaustion."""

import json
from dataclasses import replace
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from hashlib import sha256
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest

from tests.testkit.provider_http_fault import ProviderHttpFault, isolated_provider_env
from tests.test_role_lifecycle_runtime import _runtime
from zf.core.task.schema import Task
from zf.runtime.provider_health import project_provider_health
from zf.runtime.runtime_logs import read_runtime_logs
from zf.runtime.session_mutex import SessionLock
from zf.runtime.session_tailer import claude_session_path
from zf.runtime.transport import DispatchContext
from zf.runtime.transport_stream_json import StreamJsonTransport


pytestmark = [pytest.mark.host, pytest.mark.serial]


class BillingFailureRequeued(AssertionError):
    """A proven account failure was incorrectly treated as transient transport."""


@pytest.mark.parametrize("scenario", ["retry", "quota"])
def test_installed_claude_consumes_real_http_limit_response(scenario, record_property):
    if os.environ.get("ZF_RUN_LOCAL_PROVIDER_HTTP") != "1":
        pytest.skip("set ZF_RUN_LOCAL_PROVIDER_HTTP=1 for installed CLI/local HTTP")
    executable = shutil.which("claude")
    assert executable, "installed Claude CLI required"
    responses, fallback = (["rate_limit"], "success") if scenario == "retry" else ([], "quota")
    with (TemporaryDirectory(prefix="zf-k549-http-20260924-") as directory,
          ProviderHttpFault(responses, fallback=fallback) as server):
        root = Path(directory)
        env = {"PATH": os.defpath, "LANG": "C.UTF-8", **isolated_provider_env(root, server.url)}
        session_id = str(uuid4())
        command = [executable, "--setting-sources", "", "--tools", "", "--model", "claude-sonnet-4-6",
                   "--session-id", session_id, "--max-turns", "1", "--effort", "low",
                   "--output-format", "json", "-p", "Return TESTKIT_LOCAL_OK without tools."]
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=90)
        payload = json.loads(result.stdout)
        record_property("http_limit", json.dumps({"scenario": scenario, "records": server.records,
                        "exit_code": result.returncode, "result": payload, "session_id": session_id}))
        assert payload["type"] == "result" and payload["session_id"] == session_id
        if scenario == "retry":
            assert result.returncode == 0 and not payload["is_error"]
            assert payload["result"].strip() == "TESTKIT_LOCAL_OK"
            assert [r["status"] for r in server.records][:2] == [429, 200]
        else:
            assert result.returncode != 0 and payload["is_error"]
            assert "credit balance" in json.dumps(payload).lower()
            assert server.records and all(r["status"] == 400 for r in server.records)


@pytest.mark.parametrize("scenario", [
    "retry",
    pytest.param("quota", marks=pytest.mark.xfail(
        strict=True,
        raises=BillingFailureRequeued,
        reason="ZF-TEST-104: billing_error is requeued as transport_error",
    )),
    "quota_restore", "persistent_rate",
])
def test_real_http_response_through_sdk_transport_and_kernel(monkeypatch, record_property, scenario):
    if os.environ.get("ZF_RUN_LOCAL_PROVIDER_HTTP") != "1":
        pytest.skip("set ZF_RUN_LOCAL_PROVIDER_HTTP=1 for installed CLI/local HTTP")
    from claude_code_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
    from zf.runtime.stream_owned_process import OwnedSubprocessCLITransport

    executable = shutil.which("claude")
    assert executable
    responses = ["rate_limit"] if scenario == "retry" else []
    fallback = {"retry": "success", "quota": "quota", "quota_restore": "quota",
                "persistent_rate": "rate_limit"}[scenario]
    with (TemporaryDirectory(prefix="zf-k549-sdk-http-20260924-") as directory,
          ProviderHttpFault(responses, fallback=fallback) as server):
        root = Path(directory)
        home = root / "provider-home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        env = {"PATH": os.defpath, "LANG": "C.UTF-8", **isolated_provider_env(root, server.url)}
        env["CLAUDE_CONFIG_DIR"] = str(home / ".claude")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", env["CLAUDE_CONFIG_DIR"])
        wrapper = root / "isolated-claude"
        wrapper.write_text(f"#!{sys.executable}\nimport os,sys\n"
                           f"os.execve({executable!r}, [{executable!r}, '--setting-sources', '', "
                           f"'--tools', '', *sys.argv[1:]], {env!r})\n")
        wrapper.chmod(0o700)
        # Discovery-only wrapper; real installed binary, SDK query/parser and
        # ZaoFu owned process spawn/close/drain/health remain untouched.
        monkeypatch.setattr(SubprocessCLITransport, "_find_cli", lambda self: str(wrapper))
        processes = []
        connect = OwnedSubprocessCLITransport.connect

        async def observe(transport):
            await connect(transport)
            processes.append(transport._process)

        monkeypatch.setattr(OwnedSubprocessCLITransport, "connect", observe)
        runtime, role, _ = _runtime(root)
        role.permission_mode, role.model = "allowlist", "claude-sonnet-4-6"
        transport = StreamJsonTransport(runtime.state_dir, runtime._role_lifecycle_registry(),
                                       cwd=root, timeout_s=4 if scenario == "persistent_rate" else 30,
                                       max_turns=1, background_dispatch=True)
        runtime.transport = transport
        runtime.task_store.add(Task(id="TASK-HTTP", title="HTTP limits", status="in_progress",
                                   assigned_to=role.instance_id))
        transport.spawn(role, [], cwd=root)
        context = DispatchContext(trace_id="TRACE-HTTP", run_id="RUN-HTTP", task_id="TASK-HTTP",
                                  role_name=role.name, instance_id=role.instance_id, backend="claude-code",
                                  briefing_path=root / "briefing.md", dispatch_id="dispatch-http-1")
        observed = {"scenario": scenario}
        try:
            started = time.monotonic()
            transport.send_task(role.instance_id, context.briefing_path, "Return TESTKIT_LOCAL_OK.", context=context)
            with transport._dispatch_lock:
                worker = transport._dispatch_threads.get(role.instance_id)
            if worker:
                worker.join(40)
                assert not worker.is_alive(), "owned HTTP turn did not finish"
            assert processes and all(p.returncode is not None for p in processes)
            assert all(not Path(f"/proc/{p.pid}").exists() for p in processes)
            sid = str(transport.registry.get(role.instance_id))
            with SessionLock(transport.lock_dir, sid):
                pass
            runtime._drain_transport_events()
            events = runtime.event_log.read_all()
            blocked = [e for e in events if e.type == "agent.api_blocked"]
            assert runtime.task_store.get("TASK-HTTP").status == "in_progress"
            assert not any(e.type in {"task.done", "candidate.ready", "run.goal.completed"} for e in events)
            path = claude_session_path(str(root), sid)
            transcript = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
            observed.update(records=list(server.records), seconds=time.monotonic() - started,
                            session_id=sid, pids=[p.pid for p in processes],
                            exit_codes=[p.returncode for p in processes],
                            events=[{"type": e.type, "payload": e.payload} for e in events if e.type.startswith("agent.")],
                            logs=read_runtime_logs(runtime.state_dir),
                            transcript_sha256=sha256(path.read_bytes()).hexdigest() if path.exists() else "",
                            api_errors=[row for row in transcript if row.get("isApiErrorMessage")])
            if scenario == "retry":
                assert [r["status"] for r in server.records][:2] == [429, 200]
                assert transport.is_alive(role.instance_id) and not blocked
                assert any(e.type == "agent.text" and "TESTKIT_LOCAL_OK" in json.dumps(e.payload) for e in events)
            elif scenario == "persistent_rate":
                assert len(server.records) >= 2 and all(r["status"] == 429 for r in server.records)
                assert any(e.type == "agent.timeout" for e in events)
                assert not transport.is_alive(role.instance_id)
            else:
                assert server.records and all(r["status"] == 400 for r in server.records)
                assert len(blocked) == 1 and not transport.is_alive(role.instance_id)
                assert observed["api_errors"], "real CLI error transcript required"
                assert any(row.get("error") == "billing_error" and row.get("apiErrorStatus") == 400
                           for row in observed["api_errors"])
                assert blocked[0].payload["provider_error_kind"] == "billing_error"
                assert blocked[0].payload["provider_error_status"] == 400
                decision = runtime._on_agent_api_blocked(blocked[0])
                observed.update(recovery_action=decision.action if decision else None,
                                task_status=runtime.task_store.get("TASK-HTTP").status,
                                health=project_provider_health(runtime.state_dir))
            if scenario in {"quota_restore", "persistent_rate"}:
                # Explicit transport retry after the fixture recovers, not an
                # account top-up, automatic Kernel recovery or Task completion.
                before = runtime.event_log.read_all()
                task_before = runtime.task_store.get("TASK-HTTP").status
                record_count, process_count = len(server.records), len(processes)
                server.restore()
                transport._timeout_s = 30
                transport.spawn(role, [], cwd=root)
                retry_context = replace(context, dispatch_id="dispatch-http-2")
                transport.send_task(role.instance_id, context.briefing_path,
                                    "Return TESTKIT_LOCAL_OK.", context=retry_context)
                with transport._dispatch_lock:
                    worker = transport._dispatch_threads.get(role.instance_id)
                if worker:
                    worker.join(40)
                    assert not worker.is_alive(), "restored HTTP turn did not finish"
                runtime._drain_transport_events()
                after = runtime.event_log.read_all()
                assert after[:len(before)] == before
                assert len(processes) == process_count + 1
                assert len({p.pid for p in processes}) == len(processes)
                assert all(p.returncode is not None and not Path(f"/proc/{p.pid}").exists() for p in processes)
                assert str(transport.registry.get(role.instance_id)) == sid
                with SessionLock(transport.lock_dir, sid):
                    pass
                assert transport.is_alive(role.instance_id)
                assert server.records[record_count:] and all(r["status"] == 200 for r in server.records[record_count:])
                new_events = after[len(before):]
                assert any(e.type == "agent.text" and "TESTKIT_LOCAL_OK" in json.dumps(e.payload)
                           and e.payload.get("dispatch_id") == "dispatch-http-2" for e in new_events)
                assert not any(e.type in {"agent.api_blocked", "agent.timeout", "task.done",
                                          "candidate.ready", "run.goal.completed"} for e in new_events)
                assert runtime.task_store.get("TASK-HTTP").status == task_before
                observed["explicit_retry"] = {"session_id": sid, "records": server.records[record_count:],
                                              "pid": processes[-1].pid, "exit_code": processes[-1].returncode,
                                              "task_status": task_before, "new_event_types": [e.type for e in new_events]}
        finally:
            transport.shutdown()
            observed["cleanup"] = all(p.returncode is not None and not Path(f"/proc/{p.pid}").exists()
                                      for p in processes)
            record_property("http_runtime", json.dumps(observed, default=str))
        assert observed["cleanup"]
    if scenario == "quota":
        providers = observed["health"]["providers"]
        assert providers, "provider health must contain the failed account"
        if (observed["recovery_action"] == "dispatch" and observed["task_status"] == "backlog"
                and all(p["action"] == "requeue" and p["reason"] == "transport_error"
                        and not p["requires_operator"] for p in providers)):
            raise BillingFailureRequeued("ZF-TEST-104: HTTP billing_error requeued without operator action")
        assert observed["recovery_action"] == "block" and observed["task_status"] == "blocked"
        assert all(p["requires_operator"] for p in providers)
