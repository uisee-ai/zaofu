"""Linux foreground ownership evidence when tmux reports the pane shell."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Process:
    pid: int
    name: str
    parent: int
    group: int
    session: int
    tty: int
    foreground: int
    started: int


def _read(pid: int, root: Path) -> Process:
    body = (root / str(pid) / "stat").read_text()
    prefix, fields = body.rsplit(")", 1)
    number, name = prefix.split("(", 1)
    values = fields.split()
    if int(number) != pid or values[0] in {"Z", "X"}:
        raise ValueError("process identity is unavailable")
    return Process(pid, name, *(int(values[i]) for i in (1, 2, 3, 4, 5, 19)))


def foreground_process(pane_pid: str, *, proc_root: Path = Path("/proc")) -> Process | None:
    """Require a live foreground leader descended from this exact pane shell.

    Terminal text and a background provider are insufficient evidence. Missing
    procfs, process exit, PID reuse, or ownership changes return no evidence.
    No argv, environment, or provider credentials are read.
    """
    try:
        pid = int(pane_pid)
        if pid <= 1:
            return None
        pane = _read(pid, proc_root)
        if pane.tty == 0 or pane.foreground <= 1 or pane.foreground == pid:
            return None
        worker = _read(pane.foreground, proc_root)
        if (worker.group != worker.pid or worker.foreground != worker.group
                or worker.tty != pane.tty or worker.session != pane.session):
            return None
        parent = worker.parent
        seen = {worker.pid}
        for _ in range(32):
            if parent == pid:
                # Re-read identity and terminal ownership before admitting evidence.
                current = _read(pid, proc_root)
                latest = _read(worker.pid, proc_root)
                if (current.started == pane.started and current.tty == pane.tty
                        and current.foreground == worker.pid and latest == worker):
                    return worker
                return None
            if parent <= 1 or parent in seen:
                return None
            seen.add(parent)
            parent = _read(parent, proc_root).parent
    except (OSError, ValueError, IndexError, TypeError):
        return None
    return None


def same_process_group(process: Process, *, proc_root: Path = Path("/proc")) -> bool:
    """Recheck a previously owned leader even if its pane has since exited."""
    try:
        current = _read(process.pid, proc_root)
        return (current.started == process.started and current.group == process.group
                and current.session == process.session and current.group == current.pid)
    except (OSError, ValueError, IndexError):
        return False
