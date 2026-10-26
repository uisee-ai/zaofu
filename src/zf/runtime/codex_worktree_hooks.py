"""Keep Codex evidence hooks local when linked checkouts redirect discovery."""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.codex_hooks import CODEX_HOOK_EVENTS, codex_hook_hash

_BEGIN = "# BEGIN ZaoFu linked-worktree hooks\n"
_END = "# END ZaoFu linked-worktree hooks\n"


def configure_linked_worktree_hooks(
    state_dir: Path, *, project_root: Path, worker_root: Path, codex_home: Path,
) -> None:
    """Use the private user layer; never write hooks into the shared checkout.

    Codex redirects linked-worktree project hook discovery to the main Git
    checkout. Suppress only inherited ZaoFu bridge hooks in this private home
    to avoid double emission or routing evidence into a different runtime.
    Custom project hooks remain enabled.
    """
    if not (project_root / ".git").is_file():
        stale = codex_home / "hooks.json"
        if stale.exists() or stale.is_symlink():
            stale.unlink()
        return
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=project_root, capture_output=True, text=True, check=True, timeout=10,
    )
    shared_root = Path(common.stdout.strip()).resolve().parent
    inherited = shared_root / ".codex" / "hooks.json"
    source = worker_root / ".codex" / "hooks.json"
    home_hooks = codex_home / "hooks.json"
    atomic_write_text(home_hooks, source.read_text(encoding="utf-8"))
    config_path = codex_home / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    if _BEGIN in text:
        before, rest = text.split(_BEGIN, 1)
        _old, after = rest.split(_END, 1)
        text = before + after
    lines = [_BEGIN.rstrip()]
    for _engine, event in CODEX_HOOK_EVENTS:
        label = event.rsplit(".", 1)[-1]
        key = f"{home_hooks}:{label}:0:0"
        lines.extend([
            f"[hooks.state.{json.dumps(key)}]",
            f"trusted_hash = {json.dumps(codex_hook_hash(state_dir, _engine, event))}",
        ])
    for key in _inherited_bridge_keys(inherited):
        lines.extend([f"[hooks.state.{json.dumps(key)}]", "enabled = false"])
    lines.append(_END.rstrip())
    atomic_write_text(config_path, text.rstrip() + "\n\n" + "\n".join(lines) + "\n")


def _inherited_bridge_keys(path: Path) -> list[str]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    keys = []
    for engine, event in CODEX_HOOK_EVENTS:
        for group_index, group in enumerate(data.get("hooks", {}).get(engine, [])):
            for handler_index, handler in enumerate(group.get("hooks", [])):
                if handler.get("type") != "command":
                    continue
                try:
                    argv = shlex.split(handler.get("command", ""))
                except ValueError:
                    continue
                if ("hook-recv" not in argv or "--state-dir" not in argv
                        or argv[-4:] != ["--backend", "codex", "--event", event]):
                    continue
                label = event.rsplit(".", 1)[-1]
                keys.append(f"{path}:{label}:{group_index}:{handler_index}")
    return keys
