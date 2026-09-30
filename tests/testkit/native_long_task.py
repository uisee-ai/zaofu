"""Read-only task-output observer around the existing production CLI lifecycle."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from tests.testkit.native_pipeline_recovery import transcript_rows
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.session_tailer import claude_session_path


def validate_chunks(chunks: list[bytes], seed: str, seconds: float) -> list[dict]:
    """Independent final oracle: no call into the workload's verifier."""
    assert len(chunks) == 3
    rows = [json.loads(raw) for raw in chunks]
    for index, row in enumerate(rows):
        block_hash = hashlib.sha256()
        word = hashlib.sha256(f"{seed}:{index}".encode()).digest()
        for _ in range(32768):
            block_hash.update(word)
        assert row["index"] == index and row["seed"] == seed
        assert row["digest"] == block_hash.hexdigest()
        assert type(row["passes"]) is int and row["passes"] > 0
        assert seconds <= row["cpu_seconds"] < float("inf")
        assert seconds <= row["wall_seconds"] < float("inf")
        assert type(row["pid"]) is int and row["pid"] > 0 and row["invocation"]
    return rows


def checkpoint_identity(path: Path) -> dict:
    raw, stat = path.read_bytes(), path.stat()
    return {"sha256": hashlib.sha256(raw).hexdigest(), "mtime_ns": stat.st_mtime_ns,
            "inode": stat.st_ino, "size": len(raw)}


class NativeLongTask:
    def __init__(self):
        self.evidence = {"complete": False, "chunk_cpu_seconds": 60, "chunks": 3}

    def configure(self, runtime, task, marker):
        self.marker = marker
        self.script = runtime.project_root / "long-task.py"
        shutil.copyfile(Path(__file__).parent / "fixtures/chunked_workload.py", self.script)
        self.script_sha = hashlib.sha256(self.script.read_bytes()).hexdigest()
        base = f"{shlex.quote(sys.executable)} {shlex.quote(str(self.script))}"
        arguments = f"--seed {marker} --seconds 60"
        self.run_command = f"{base} run {arguments}"
        verify_command = f"{base} verify {arguments}"
        task.contract.scope = ["README.md", "long-task/**"]
        task.contract.acceptance_criteria += [
            f"From your assigned workdir run exactly: {self.run_command}",
            "Wait for this CPU workload to finish (three chunks of 60 CPU seconds each). "
            "Use Bash timeout=300000. After interruption rerun that exact command; "
            "it validates and skips completed chunks.",
            "Do not write/edit the script or long-task files manually. Commit long-task/*.json "
            "and README.md after completion. Do not restart the workload in parallel.",
            "Verifier: do not create verification-evidence.txt. Read repository outputs without "
            "modifying them; reference actual command stdout in your official result scratch.",
        ]
        task.contract.verification = verify_command
        task.contract.validation["commands"] = [{
            "id": "chunks", "command": verify_command, "rolling_smoke": True,
            "tier": "runtime", "deterministic": True, "reusable": True,
        }]
        runtime.task_store.update(task.id, title="Run checkpointed three-minute checksum workload",
                                  contract=task.contract)
        for role in runtime.config.roles:
            role.allowed_tools.append(f"Bash({base}:*)")
            role.guardrails.append(f"Only this test workload command is additionally allowed: {base}. "
                                   "Run from assigned workdir. Never change the script or forge its output. "
                                   "Never create verification-evidence.txt; verification uses stdout and "
                                   "the kernel-provided result scratch, not extra repository files.")
        self.evidence.update(script_sha256=self.script_sha, run_command=self.run_command)

    def wait_before_stop(self, cli, dispatch, deadline):
        body = dispatch.payload
        self.workdir = Path(body["workdir"])
        self.output = self.workdir / "long-task"

        def in_second_chunk(events):
            assert not any(e.type == "workflow.operation.settled" and
                           e.payload.get("operation_id") == body["operation_id"] for e in events)
            assert TaskAttemptStore(cli.runtime.state_dir / "task_attempts.json").get(
                body["attempt_id"])["status"] == "sent"
            progress = self.output / "progress.json"
            if not progress.exists():
                return False
            current = json.loads(progress.read_text())
            if current["index"] < 1:
                return False
            assert current["index"] == 1 and not (self.output / "chunk-1.json").exists()
            first = self.output / "chunk-0.json"
            self.first_bytes = first.read_bytes()
            self.first_identity = checkpoint_identity(first)
            pid = str(current["pid"])
            probe = cli.tmux.pane_process_probe(body["role_instance"])
            assert probe["available"] and pid in {str(p["pid"]) for p in probe["processes"]}
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            assert str(self.script).encode() in cmdline and b"run" in cmdline
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            cpu_seconds = (int(stat[11]) + int(stat[12])) / os.sysconf("SC_CLK_TCK")
            assert cpu_seconds >= 59.99, "OS must independently observe a minute of tool CPU work"
            rows = transcript_rows(claude_session_path(str(self.workdir), body["provider_session_id"]))
            commands = [block.get("input", {}).get("command", "") for row in rows
                        for block in (row.get("message", {}).get("content", [])
                                      if isinstance(row.get("message", {}).get("content"), list) else [])
                        if isinstance(block, dict) and block.get("name") == "Bash"]
            assert any(self.run_command in command for command in commands)
            cli.pids.add(pid)
            self.evidence.update(interrupted_progress=current, first_checkpoint=self.first_identity,
                                 first_chunk=json.loads(self.first_bytes),
                                 owned_tool_pid=pid, observed_tool_cpu_seconds=cpu_seconds,
                                 transcript_command_observed=True)
            return True

        cli.wait(in_second_chunk, deadline)

    def after_stop(self, cli):
        assert checkpoint_identity(self.output / "chunk-0.json") == self.first_identity
        assert not (self.output / "chunk-1.json").exists(), "fault missed in-flight chunk"
        assert not Path(f"/proc/{self.evidence['owned_tool_pid']}").exists()
        self.evidence["stopped_checkpoint_preserved"] = True

    def assert_candidate(self, cli, candidate):
        commit = candidate.payload["commit"]
        chunks = [subprocess.check_output(
            ["git", "show", f"{commit}:long-task/chunk-{index}.json"],
            cwd=cli.runtime.project_root, timeout=10,
        ) for index in range(3)]
        rows = validate_chunks(chunks, self.marker, 60)
        assert chunks[0] == self.first_bytes
        assert checkpoint_identity(self.output / "chunk-0.json") == self.first_identity
        assert rows[1]["invocation"] == rows[2]["invocation"] != rows[0]["invocation"]
        assert rows[1]["invocation"] != self.evidence["interrupted_progress"]["invocation"]
        assert hashlib.sha256(self.script.read_bytes()).hexdigest() == self.script_sha
        self.evidence.update(complete=True, chunks=rows, preserved_first=True,
                             script_unchanged=True, candidate_commit=commit)
