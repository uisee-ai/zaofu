"""Opt-in, tool-free Kimi root canary. No native delegation or task completion."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
from tempfile import TemporaryDirectory
import time
from typing import Any

from zf.web.agent_session_runtime import agent_session_process, agent_session_run_cancelled, run_key


class KimiHeadlessBackend:
    backend_id = "kimi-headless-canary"

    def __init__(self, *, command: str | None = None) -> None:
        self.command = command or os.environ.get("ZF_KIMI_CANARY_CMD", "kimi")

    def available(self) -> bool:
        parts = shlex.split(self.command)
        return bool(parts and shutil.which(parts[0]))

    def run_turn(
        self, *, prompt: str, cwd: Path, system_prompt: str, thread_id: str,
        provider_session_id: str, on_session_id: Any, on_message: Any,
        timeout_s: float, execution_deadline: float | None = None,
        image_inputs: list[dict[str, str]] | None = None, thinking_level: str = "",
        run_id: str = "", run_thread_id: str = "", project_id: str = "",
        conversation_id: str = "", permission_profile: str = "read_only",
    ) -> Any:
        from zf.web.headless_agent import HeadlessMessage, HeadlessTurnResult, _headless_subprocess_env

        def result(status: str, *, error: str = "", reply: str = "", messages=None):
            return HeadlessTurnResult(
                ok=status == "completed", status=status, backend=self.backend_id,
                thread_id=thread_id, provider_session_id=provider_session_id,
                reply=reply, messages=messages or [], usage={"available": False},
                error=error, resumed=False,
                completion_reason="root_turn_only" if status == "completed" else "",
                telemetry={"canary": True, "native_delegation": False,
                           "tools": [], "task_completion_admitted": False},
            )

        if permission_profile != "read_only" or provider_session_id or image_inputs or thinking_level:
            return result("unsupported", error=(
                "Kimi canary supports fresh, text-only, tool-free read_only turns; "
                "resume, write permissions, thinking overrides and delegation are not admitted"
            ))
        if timeout_s <= 0:
            return result("timeout", error="Kimi canary requires a positive deadline")
        remaining = min(timeout_s, 600.0)
        if execution_deadline is not None:
            remaining = min(remaining, execution_deadline - time.monotonic())
        if remaining <= 0:
            return result("timeout", error="Kimi canary execution deadline expired")
        key = run_key(run_id=run_id, thread_id=run_thread_id or thread_id,
                      project_id=project_id, conversation_id=conversation_id)
        if agent_session_run_cancelled(key):
            return result("cancelled")
        # Source contract: Kimi custom profiles preserve tools: [] and
        # subagents: []; -p alone forces auto and is NOT a readonly boundary.
        with TemporaryDirectory(prefix="zf-kimi-canary-") as directory:
            root = Path(directory)
            profile = root / "root.md"
            profile.write_text(
                "---\nname: zaofu-root-canary\ndescription: Tool-free root turn\n"
                "tools: []\nsubagents: []\n---\n"
                + (system_prompt or "Answer using only supplied context.") + "\n", encoding="utf-8",
            )
            args = [*shlex.split(self.command), "--agent-file", str(profile),
                    "--output-format", "stream-json", "--prompt", prompt]
            process = None
            try:
                with (root / "stdout").open("w+") as stdout, (root / "stderr").open("w+") as stderr:
                    process = subprocess.Popen(
                        args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                        env=_headless_subprocess_env(), start_new_session=True,
                    )
                    with agent_session_process(key, provider=self.backend_id, process=process) as cancelled:
                        if not cancelled:
                            deadline = time.monotonic() + remaining
                            while process.poll() is None:
                                if (os.fstat(stdout.fileno()).st_size > 2_000_000
                                        or os.fstat(stderr.fileno()).st_size > 8_000):
                                    return result("failed", error="Kimi canary output limit exceeded")
                                try:
                                    process.wait(timeout=min(0.25, max(0, deadline - time.monotonic())))
                                except subprocess.TimeoutExpired:
                                    if time.monotonic() >= deadline:
                                        raise
                    if agent_session_run_cancelled(key):
                        return result("cancelled")
                    stdout.seek(0)
                    output = stdout.read(2_000_001)
                    stderr.seek(0)
                    diagnostic = stderr.read(8_001)
                if len(output) > 2_000_000 or len(diagnostic) > 8_000:
                    return result("failed", error="Kimi canary output limit exceeded")
                if process.returncode != 0:
                    return result("failed", error=f"Kimi canary exited {process.returncode}")
                # Background failures can otherwise exit zero. Never accept
                # best-effort drain as proof that a compound operation settled.
                if "background" in diagnostic.lower():
                    return result("failed", error="Kimi background settlement is unproven")
                messages = []
                for line in output.splitlines():
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        return result("failed", error="Kimi stream row must be an object")
                    if row.get("role") == "tool" or row.get("tool_calls"):
                        return result("failed", error="Kimi tool-free canary observed tool activity")
                    if row.get("type") == "error":
                        return result("failed", error="Kimi stream reported an error")
                    if row.get("role") == "assistant" and isinstance(row.get("content"), str):
                        messages.append(HeadlessMessage(type="assistant", content=row["content"]))
                reply = "\n".join(message.content for message in messages).strip()
                if not reply:
                    return result("failed", error="Kimi stream has no root answer")
                if on_message:
                    for message in messages:
                        on_message(message)
                return result("completed", reply=reply, messages=messages)
            except subprocess.TimeoutExpired:
                return result("timeout", error="Kimi canary execution deadline exceeded")
            except (OSError, ValueError) as exc:
                return result("failed", error=f"Kimi canary protocol/process failure: {type(exc).__name__}")
            finally:
                if process is not None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
