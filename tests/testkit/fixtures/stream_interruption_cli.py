#!/usr/bin/env python3
"""Owned, offline stream-json CLI for the real Claude SDK subprocess boundary."""

import json
import os
from pathlib import Path
import sys
import time
import subprocess


def main() -> None:
    scenario = sys.argv[-1]
    assert scenario in {
        "success",
        "exit-41",
        "truncated",
        "no-result",
        "error-result",
        "hang-silent",
        "hang-partial",
        "await-release",
        "spawn-child",
        "spawn-child-ignore-term",
        "spawn-child-success",
        "rate-limit-before",
        "rate-limit-after",
    }
    sid_flag = "--resume" if "--resume" in sys.argv else "--session-id"
    session_id = sys.argv[sys.argv.index(sid_flag) + 1]
    audit = Path(os.environ["ZF_STREAM_AUDIT"])
    assert audit.parent.resolve() == Path.cwd().resolve()
    audit_tmp = audit.with_suffix(".tmp")
    audit_tmp.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "scenario": scenario,
                "cwd": str(Path.cwd()),
                "session_id": session_id,
                "resume": sid_flag == "--resume",
            }
        ),
        encoding="utf-8",
    )
    audit_tmp.replace(audit)
    if scenario == "hang-silent":
        time.sleep(8)
        sys.exit(42)  # A cleanup failure must not masquerade as SDK termination.
    if scenario.startswith("spawn-child"):
        child_audit = Path(os.environ["ZF_STREAM_CHILD_AUDIT"])
        assert child_audit.parent.resolve() == Path.cwd().resolve()
        child = subprocess.Popen(
            [sys.executable, "-c", (
                "import signal, sys, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN) "
                "if sys.argv[1] == 'ignore' else None; "
                "print('ready', flush=True); time.sleep(8)"
            ), "ignore" if scenario == "spawn-child-ignore-term" else "default"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert child.stdout.readline() == b"ready\n"
        child.stdout.close()
        child_tmp = child_audit.with_suffix(".tmp")
        child_tmp.write_text(
            json.dumps(
                {
                    "pid": child.pid,
                    "parent_pid": os.getpid(),
                    "parent_pgid": os.getpgrp(),
                    "child_pgid": os.getpgid(child.pid),
                }
            ),
            encoding="utf-8",
        )
        child_tmp.replace(child_audit)
        audit.with_suffix(".ready").touch()
        if scenario != "spawn-child-success":
            time.sleep(8)
            child.wait()
            sys.exit(42)
    print(
        json.dumps({"type": "system", "subtype": "init", "session_id": session_id}),
        flush=True,
    )
    if scenario == "rate-limit-before":
        print(
            json.dumps({"type": "rate_limit_event", "retry_after_ms": 250}), flush=True
        )
        return
    print(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "offline-fixture",
                    "content": [{"type": "text", "text": "partial evidence"}],
                },
            }
        ),
        flush=True,
    )
    if scenario == "exit-41":
        sys.exit(41)
    if scenario == "rate-limit-after":
        print(
            json.dumps({"type": "rate_limit_event", "retry_after_ms": 250}), flush=True
        )
        return
    if scenario == "hang-partial":
        time.sleep(8)
        sys.exit(42)
    if scenario == "truncated":
        sys.stdout.write('{"type":"result","is_error":')
        sys.stdout.flush()
        return
    if scenario == "no-result":
        return
    if scenario == "await-release":
        audit.with_suffix(".ready").touch()
        deadline = time.monotonic() + 8
        while not audit.with_suffix(".release").exists():
            if time.monotonic() >= deadline:
                sys.exit(42)
            time.sleep(0.01)
    print(
        json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution"
                if scenario == "error-result"
                else "success",
                "session_id": session_id,
                "is_error": scenario == "error-result",
                "result": "provider interrupted"
                if scenario == "error-result"
                else "complete",
                "duration_ms": 1,
                "duration_api_ms": 1,
                "num_turns": 1,
                "total_cost_usd": 0,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
