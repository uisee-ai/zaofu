"""Remove generated ZaoFu runtime files; preserve source, user data and shared tools."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


class RemovalError(ValueError):
    """An actionable, non-secret removal diagnostic."""


def command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, check=False)


def sessions(root: Path) -> list[tuple[str, str, str]]:
    if not shutil.which("tmux"):
        return []
    result = command(["tmux", "list-sessions", "-F", "#{session_id}\t#{session_name}\t#{@zf_install_root}\t#{@zf_workspace_home}"])
    if result.returncode:
        if any(message in result.stderr.lower() for message in ("no server running", "no sessions", "no such file")):
            return []
        raise RemovalError("Cannot inspect tmux sessions; removal was not started")
    owned = []
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 4:
            raise RemovalError("Unexpected tmux session metadata")
        identity, name, source, workspace = fields
        if source and Path(source).resolve() == root:
            if not name.startswith(("zf-web-", "zf-controller-")) or not workspace:
                raise RemovalError("Incomplete service ownership metadata; stop the session manually")
            owned.append((identity, name, workspace))
    return owned


def processes() -> dict[int, tuple[int, str]]:
    result = command(["ps", "-axo", "pid=,ppid=,args="])
    if result.returncode:
        raise RemovalError("Cannot inspect running processes; removal was not started")
    rows = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3:
            rows[int(parts[0])] = (int(parts[1]), parts[2])
    return rows


def descendants(rows: dict[int, tuple[int, str]], pids: set[int]) -> set[int]:
    found = set(pids)
    while True:
        more = {pid for pid, (parent, _) in rows.items() if parent in found} - found
        if not more:
            return found
        found.update(more)


def ensure_no_other_processes(root: Path, owned: list[tuple[str, str, str]]) -> None:
    rows = processes()
    excluded, pid = set(), os.getpid()
    while pid in rows and pid not in excluded:
        excluded.add(pid)
        pid = rows[pid][0]
    # Only the current installer and the services about to be stopped are exempt.
    panes = []
    if shutil.which("tmux"):
        result = command(["tmux", "list-panes", "-a", "-F", "#{session_id}\t#{pane_pid}\t#{pane_start_path}"])
        panes = [line.split("\t", 2) for line in result.stdout.splitlines()]
        if result.returncode and owned:
            raise RemovalError("Cannot inspect the installation's running services")
    owned_ids = {item[0] for item in owned}
    service_pids = set()
    for identity, pid_text, path in panes:
        if identity in owned_ids:
            service_pids.add(int(pid_text))
        elif path and Path(path).resolve() == root:
            raise RemovalError("An unrecognized tmux session uses this checkout; stop it manually first")
    excluded.update(descendants(rows, service_pids))
    for pid, (_, argv) in rows.items():
        if pid in excluded:
            continue
        try:
            in_checkout = Path(f"/proc/{pid}/cwd").resolve(strict=True) == root
        except OSError:
            in_checkout = False
        if str(root) + "/" in argv or in_checkout:
            raise RemovalError(f"Process {pid} still uses this installation; stop its work before removal")


def protected_data(root: Path, homes: set[Path], config_path: Path, targets: list[Path]) -> None:
    paths = [root / "zf.yaml", config_path, *homes]
    descriptors = []
    for home in homes:
        pin = home / "controller-binding.json"
        if pin.exists() or pin.is_symlink():
            raise RemovalError("Managed binding detected; complete the Controller release lifecycle first")
        for registry in (home / "workspaces").glob("*/projects.json"):
            raw = json.loads(registry.read_text())
            descriptors.extend(raw["projects"])
    for row in descriptors:
        paths.extend(Path(row[key]).expanduser().resolve() for key in ("root", "config_path", "state_dir_hint") if row.get(key))
    # Resolve configured state directories rather than assuming a .zf directory.
    config_paths = {path for path in paths if path.name == "zf.yaml" and path.is_file()}
    if config_paths:
        sys.path.insert(0, str(root / "src"))
        try:
            import yaml
            from zf.core.config.loader import load_config
            from zf.runtime.workflow_runtime_start import _runtime_owner
        except ImportError as exc:
            raise RemovalError("Cannot check project state with this Python environment; restore dependencies before removal") from exc
        for path in config_paths:
            raw = yaml.safe_load(path.read_text())
            if isinstance(raw, dict) and raw.get("kind") == "WorkspaceConfig":
                from zf.core.config.controller_workspace import load_controller_workspace
                paths.append(load_controller_workspace(path).state_dir.resolve())
                continue
            config = load_config(path)
            state = Path(config.project.state_dir).expanduser()
            state = (state if state.is_absolute() else path.parent / state).resolve()
            paths.append(state)
            if _runtime_owner(state):
                raise RemovalError("An active project runtime must be stopped before removal")
            registry = state / "terminal-resource-registry.v1.json"
            if registry.exists() and any(item.get("state") == "active" for item in json.loads(registry.read_text())["sessions"]):
                raise RemovalError("Active Web Terminals must be stopped with Stop CLI before removal")
    for target in targets:
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise RemovalError(f"Refusing to remove a linked or non-directory target: {target}")
        for path in paths:
            resolved = path.resolve()
            if resolved == target or target in resolved.parents:
                raise RemovalError(f"Removal would include workspace or project data: {target}")


def remove(root: Path, workspace_home: Path, config_path: Path, *, yes: bool) -> None:
    root = root.resolve()
    targets = [root / ".venv", root / "web/node_modules", root / "web/dist"]
    if not (root / "install.sh").is_file() or not (root / "pyproject.toml").is_file():
        raise RemovalError("Run removal from a ZaoFu source checkout")
    if (root / "web").is_symlink():
        raise RemovalError("Refusing removal through a linked web directory")
    owned = sessions(root)
    homes = {workspace_home.resolve(), *(Path(item[2]).resolve() for item in owned)}
    if not owned and not any(path.exists() or path.is_symlink() for path in targets):
        ensure_no_other_processes(root, [])
        print("ZaoFu runtime is already removed; preserved data is unchanged.")
        return
    protected_data(root, homes, config_path.resolve(), targets)
    ensure_no_other_processes(root, owned)
    print(f"Remove ZaoFu runtime from: {root}")
    for _, name, _ in owned:
        print(f"  Stop service: {name}")
    for target in targets:
        print(f"  Remove generated directory: {target}")
    print("Keep source code, project/workspace data, credentials, uv, Node.js and Herdr.")
    print("Controller enrollment is retained; removal does not revoke the remote identity.")
    if not yes:
        if not sys.stdin.isatty():
            raise RemovalError("Review the removal plan and rerun with --yes to apply it")
        if input("Remove this installation's runtime? [y/N]: ").strip().lower() not in {"y", "yes"}:
            print("Removal cancelled; no files or services were changed.")
            return
    # Revalidate before the first mutation; never stop a session by name alone.
    if sessions(root) != owned:
        raise RemovalError("Service ownership changed; rerun removal")
    protected_data(root, homes, config_path.resolve(), targets)
    ensure_no_other_processes(root, owned)
    for identity, _, _ in owned:
        if command(["tmux", "kill-session", "-t", identity]).returncode:
            raise RemovalError("Could not stop an installation service; runtime files were preserved")
    for attempt in range(30):
        try:
            ensure_no_other_processes(root, [])
            break
        except ValueError:
            if attempt == 29:
                raise
            time.sleep(0.2)
    for target in targets:
        if target.is_symlink():
            raise RemovalError("Removal target changed to a symlink; remaining files were preserved")
        if target.exists():
            shutil.rmtree(target)
    print("ZaoFu runtime removed. Preserved data can be reused by running ./install.sh again.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-home", type=Path, required=True)
    parser.add_argument("--workspace-config", type=Path, required=True)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    try:
        remove(Path(__file__).resolve().parents[1], args.workspace_home, args.workspace_config, yes=args.yes)
    except Exception as exc:
        message = str(exc) if isinstance(exc, RemovalError) else "Could not verify or remove the installation; inspect permissions and runtime state"
        print(f"Removal failed: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
