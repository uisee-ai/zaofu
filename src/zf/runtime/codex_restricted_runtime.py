"""Fail-closed preparation of Codex's project-derived command approvals."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time
import tomllib

from zf.core.config.schema import RoleConfig
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.codex_hooks import CODEX_HOOK_EVENTS


def requires_restricted_codex_policy(role: RoleConfig) -> bool:
    return (
        role.backend == "codex"
        and role.permission_mode in {"restricted", "allowlist"}
        and os.environ.get("ZF_CODEX_WORKER_SANDBOX", "").strip() != "danger-full-access"
    )


def restricted_project_override(project_root: Path) -> str:
    key = json.dumps(str(project_root.resolve()), ensure_ascii=False)
    # Codex splits -c dotted keys literally, not as TOML quoted-key syntax.
    # Put paths in a parsed TOML value so quotes and dots remain path bytes.
    return f'projects={{{key}={{trust_level="untrusted"}}}}'


def guard_private_codex_home(codex_home: Path, state_dir: Path) -> None:
    """Never prepare a role home/config redirected into operator or other role state."""
    if not codex_home.resolve().is_relative_to(state_dir.resolve()):
        raise RuntimeError("Codex restricted home must remain inside the configured state directory")
    for path in (codex_home, *codex_home.parents):
        if path == state_dir:
            break
        if path.is_symlink():
            raise RuntimeError("Codex restricted home must not traverse role-local symlinks")
    for name in ("config.toml", "hooks.json"):
        path = codex_home / name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RuntimeError(f"Codex restricted {name} must be a private regular file")


def strip_explicit_approval(text: str) -> str:
    """Remove only a root policy statement; preserve all other TOML bytes/values.

    A parsed before/after equality check protects quoted keys, multiline values,
    and similarly named fields inside other tables. Unsupported layouts fail
    closed instead of rewriting arbitrary operator configuration.
    """
    original = tomllib.loads(text)
    if original.get("profile") or original.get("profiles"):
        raise RuntimeError("Codex restricted config requires migration of retired profile selectors")
    if "approval_policy" not in original:
        return text
    expected = {key: value for key, value in original.items() if key != "approval_policy"}
    lines = text.splitlines(keepends=True)
    pattern = re.compile(r'''^\s*(?:\[\s*)?(?:approval_policy|"approval_policy"|'approval_policy')\s*(?:[=.\]])''')
    for start, line in enumerate(lines):
        if not pattern.match(line):
            continue
        for end in range(start + 1, len(lines) + 1):
            try:
                fragment = tomllib.loads("".join(lines[start:end]))
            except tomllib.TOMLDecodeError:
                continue
            if set(fragment) - {"approval_policy"}:
                break
            if fragment.get("approval_policy") != original["approval_policy"]:
                continue
            candidate = "".join(lines[:start] + lines[end:])
            try:
                if tomllib.loads(candidate) == expected:
                    return candidate
            except tomllib.TOMLDecodeError:
                pass
            break
    raise RuntimeError("Codex restricted config cannot safely remove the explicit approval policy")


def prepare_restricted_approval(config_path: Path) -> None:
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    # Atomic replacement also breaks an existing hardlink instead of modifying
    # its source. No user or system config is ever written by this helper.
    atomic_write_text(config_path, strip_explicit_approval(text))


def _read_effective_config(*, codex_home: Path, project_root: Path, timeout: float = 10) -> dict:
    """Read structured configuration only: no threads, turns, tools, or model calls."""
    if os.name != "posix":
        raise RuntimeError("Codex restricted metadata preflight requires POSIX process isolation")
    codex_home, project_root = codex_home.resolve(), project_root.resolve()
    env = {**os.environ, "CODEX_HOME": str(codex_home)}
    command = ["codex", "--enable", "hooks", "-c", restricted_project_override(project_root),
               "app-server", "--stdio"]
    process = subprocess.Popen(
        command, cwd=project_root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
    assert process.stdin is not None and process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    buffer = b""
    received_bytes = 0
    deadline = time.monotonic() + timeout

    def signal_group(sig: signal.Signals) -> None:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    def send(method: str, params: dict, identity: int | None = None) -> None:
        item: dict = {"method": method, "params": params}
        if identity is not None:
            item["id"] = identity
        process.stdin.write((json.dumps(item) + "\n").encode())
        process.stdin.flush()

    def receive(identity: int) -> dict:
        nonlocal buffer, received_bytes
        while time.monotonic() < deadline:
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                if not raw:
                    continue
                item = json.loads(raw)
                if not isinstance(item, dict):
                    raise RuntimeError("Codex config preflight received an invalid response")
                if item.get("id") == identity:
                    if "error" in item or not isinstance(item.get("result"), dict):
                        raise RuntimeError("Codex config preflight request was rejected")
                    return item["result"]
                if "id" in item and "method" in item:
                    raise RuntimeError("Codex config preflight must not request interactive actions")
            if selector.select(max(0, deadline - time.monotonic())):
                data = os.read(process.stdout.fileno(), 65536)
                if not data:
                    raise RuntimeError("Codex config preflight exited before its response")
                received_bytes += len(data)
                if received_bytes > 8 * 1024 * 1024:
                    raise RuntimeError("Codex config preflight exceeded its response budget")
                buffer += data
        raise RuntimeError("Codex config preflight timed out")

    try:
        send("initialize", {"clientInfo": {"name": "zaofu_restricted_preflight", "version": "1"}}, 1)
        initialized = receive(1)
        if initialized.get("codexHome") != str(codex_home):
            raise RuntimeError("Codex config preflight did not use the role-local home")
        send("initialized", {})
        send("config/read", {"cwd": str(project_root), "includeLayers": True}, 2)
        result = receive(2)
        send("hooks/list", {"cwds": [str(project_root)]}, 3)
        result["hook_discovery"] = receive(3)
        return result
    finally:
        selector.close()
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            signal_group(signal.SIGTERM)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                signal_group(signal.SIGKILL)
                process.wait(timeout=1)
        # The CLI may be a wrapper. Reap its dedicated descendants as well,
        # even when the wrapper exited before the metadata request completed.
        signal_group(signal.SIGKILL)
        process.stdout.close()


def verify_restricted_codex_config(*, codex_home: Path, project_root: Path) -> None:
    """Reject inherited explicit policies or active project layers before spawn."""
    try:
        result = _read_effective_config(codex_home=codex_home, project_root=project_root)
        config = result.get("config")
        layers = result.get("layers")
        if not isinstance(config, dict) or not isinstance(layers, list) or not layers:
            raise RuntimeError("Codex restricted policy lacks configuration layer evidence")
        if "approval_policy" not in config or config["approval_policy"] is not None:
            raise RuntimeError("Codex restricted policy has an explicit inherited approval")
        projects = config.get("projects", {})
        target = projects.get(str(project_root.resolve()), {})
        if target.get("trust_level") != "untrusted":
            raise RuntimeError("Codex restricted policy did not bind the untrusted project")
        for layer in layers:
            if not isinstance(layer, dict) or not isinstance(layer.get("name"), dict):
                raise RuntimeError("Codex restricted policy received malformed layer evidence")
            if layer["name"].get("type") == "project" and not layer.get("disabledReason"):
                raise RuntimeError("Codex restricted policy still has an active project layer")
        discovery = result.get("hook_discovery", {}).get("data", [])
        if len(discovery) != 1 or discovery[0].get("cwd") != str(project_root.resolve()):
            raise RuntimeError("Codex restricted hooks lack project-bound discovery evidence")
        if discovery[0].get("errors"):
            raise RuntimeError("Codex restricted hooks discovery failed")
        source = str(codex_home.resolve() / "hooks.json")
        hooks = [hook for hook in discovery[0].get("hooks", []) if hook.get("sourcePath") == source]
        expected = {f'{source}:{event.rsplit(".", 1)[-1]}:0:0': engine[0].lower() + engine[1:]
                    for engine, event in CODEX_HOOK_EVENTS}
        if len(hooks) != len(expected) or {hook.get("key"): hook.get("eventName") for hook in hooks} != expected:
            raise RuntimeError("Codex restricted runtime hooks are missing from the user layer")
        if any(hook.get("source") != "user" or hook.get("enabled") is not True
               or hook.get("trustStatus") != "trusted" for hook in hooks):
            raise RuntimeError("Codex restricted runtime hooks are disabled or untrusted")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        # Config/provider output can contain credentials; never include it in
        # exceptions or worker warning events.
        raise RuntimeError("Codex restricted policy preflight could not verify configuration") from exc
