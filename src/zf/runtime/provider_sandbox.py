"""Bounded, model-free Codex sandbox capability probe for install/qualification."""
from __future__ import annotations

from pathlib import Path
from secrets import token_hex
import subprocess
from tempfile import TemporaryDirectory

from zf.core.config.schema import ZfConfig


def probe_configured_codex_sandbox(config: ZfConfig, root: Path) -> dict | None:
    """Check the shared sandbox substrate only for explicitly restricted roles."""
    roles = [
        role.instance_id for role in config.roles
        if role.backend == "codex" and role.provider_session is not None
        and role.provider_session.sandbox == "workspace-write"
    ]
    if not roles:
        return None
    state_dir = (root / config.project.state_dir).resolve()
    try:
        with TemporaryDirectory(prefix=".codex-input-probe-", dir=state_dir) as temporary:
            # Workers run in their own cwd and receive absolute references outside it.
            worker = Path(temporary, "worker")
            worker.mkdir()
            marker = Path(temporary, "bound-input")
            expected = token_hex(16)
            marker.write_text(expected, encoding="ascii")
            result = probe_codex_sandbox(worker, bound_input=marker)
            if result["phase"] == "bound_input":
                if result["returncode"] or result["stdout"] != expected:
                    result["returncode"] = result["returncode"] or 1
                    result["stderr"] = (
                        f"Worker sandbox could not read the bound input in {state_dir}. "
                        + result["stderr"].strip()
                    ).rstrip()
                result["stdout"] = ""
    except OSError as exc:
        result = {"phase": "bound_input", "command": [], "returncode": 1,
                  "stdout": "", "stderr": f"Cannot prepare sandbox input probe in {state_dir}: {exc}"}
    return {"roles": roles, "probe": result}


def probe_codex_sandbox(root: Path, *, codex: str = "codex", bound_input: Path | None = None) -> dict:
    """Probe the installed interface; never infer capability from unshare or bypass."""
    def run(command: list[str], phase: str) -> dict:
        try:
            result = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                    timeout=20, check=False)
            return {"phase": phase, "command": command, "returncode": result.returncode,
                    "stdout": result.stdout, "stderr": result.stderr}
        except subprocess.TimeoutExpired as exc:
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
            return {"phase": phase, "command": command, "returncode": 124,
                    "stdout": "", "stderr": f"sandbox probe timed out: {stderr or ''}"}
        except OSError as exc:
            return {"phase": phase, "command": command, "returncode": 127,
                    "stdout": "", "stderr": str(exc)}

    help_result = run([codex, "sandbox", "--help"], "interface")
    if help_result["returncode"]:
        return help_result
    if any(flag not in help_result["stdout"] for flag in ("--permission-profile", "--include-managed-config")):
        return {**help_result, "returncode": 2, "stderr":
                "Unqualified Codex sandbox interface; require --permission-profile and --include-managed-config. "
                "No weaker fallback was attempted."}
    command = [codex, "sandbox", "--permission-profile", ":workspace", "--include-managed-config"]
    command.extend(["-C", str(root.resolve()), "--"])
    if bound_input is not None:
        return run([*command, "/bin/cat", str(bound_input)], "bound_input")
    return run([*command, "/usr/bin/true"], "launch")
