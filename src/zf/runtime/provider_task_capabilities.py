"""Opt-in task capability observations under the normal Codex workspace policy."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

CAPABILITIES = ("local-listen", "docker", "git-metadata")

_GIT_METADATA = """
import json, os, pathlib, subprocess, sys
rows = []
for flag in ('--git-dir', '--git-common-dir'):
    raw = subprocess.check_output(['git', 'rev-parse', flag], text=True).strip()
    path = pathlib.Path(raw).resolve()
    readonly = bool(os.statvfs(path).f_flag & os.ST_RDONLY)
    rows.append({'kind': flag, 'path': str(path), 'readonly': readonly,
                 'access_write': os.access(path, os.W_OK)})
print(json.dumps(rows))
sys.exit(1 if any(r['readonly'] or not r['access_write'] for r in rows) else 0)
"""


def probe_task_capabilities(root: Path, capabilities: list[str], *, codex: str = "codex") -> list[dict]:
    """Observe selected prerequisites, never alter policy or execute a Git write.

    These are bounded local probes, not product checks. Git stat/access does not
    prove commit success; Docker inspection does not prove browser acceptance.
    """
    unknown = set(capabilities) - set(CAPABILITIES)
    if unknown:
        raise ValueError("unsupported capability: " + ", ".join(sorted(unknown)))
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("capability workdir must be a directory")
    commands = {
        "local-listen": ([sys.executable, "-c",
            "import socket; s=socket.socket(); s.bind(('127.0.0.1', 0)); "
            "s.listen(1); s.close(); print('loopback listen observed')"], "loopback_bind_only"),
        "docker": (["docker", "--host", "unix:///var/run/docker.sock", "image", "inspect",
                    "mcp/playwright:latest", "--format", "{{.Id}}"],
                   "existing_image_inspection_only"),
        "git-metadata": ([sys.executable, "-c", _GIT_METADATA], "metadata_mount_access_only"),
    }
    results = []
    for capability in dict.fromkeys(capabilities):
        command, qualification = commands[capability]
        argv = [codex, "sandbox", "--permission-profile", ":workspace", "--include-managed-config",
                "-C", str(root), "--", *command]
        try:
            result = subprocess.run(argv, cwd=root, capture_output=True, text=True,
                                    timeout=20, check=False)
            code, stdout, stderr = result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            code, stdout, stderr = 124, "", "capability probe timed out after 20 seconds"
        except OSError as exc:
            code, stdout, stderr = 127, "", str(exc)
        results.append({"capability": capability, "workdir": str(root), "command": argv,
                        "returncode": code, "stdout": stdout, "stderr": stderr,
                        "status": "observed" if code == 0 else "blocked",
                        "qualification": qualification})
    return results
