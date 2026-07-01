"""ω-1.a (2026-05-18): kernel fast-forward task ref onto main HEAD.

zaofu kernel historically wrote ``task.ref.updated`` events as metadata
only (see ``src/zf/runtime/task_refs.py``), leaving the actual
``git update-ref`` to whichever role got dispatched next. r-next-10
proved this doesn't work: critic v1 / v2 / v3 each saw the task branch
sitting at the old vertical-3 SHA while main advanced to vertical-4,
producing a 3-attempt rework loop that only resolved when the LLM
orchestrator role itself ran ``git update-ref`` (evt-e06c5d9c0096).

Audit doc 37 §3 Class A1 + design doc 38 §3 require kernel to be the
canonical owner of this sync.

This module provides a pure function that:
  1. Refuses to do anything dangerous (never rewrites task branches that
     have their own commits ahead of main — diverged → no_op + signal)
  2. Fast-forwards safely when task ref is strictly an ancestor of main
  3. Never raises (catch + log + return a ``no_op`` result so the dispatch
     loop is never blocked on a network or local-git glitch)
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


_GIT_TIMEOUT_S = 10
_SHA_SHORT = 12


@dataclass(frozen=True)
class BaselineSyncResult:
    """Outcome of one fast-forward attempt over a task ref."""
    ok: bool                       # fast-forward actually happened
    diverged: bool                 # task ref has commits main doesn't
    no_op: bool                    # nothing to do (absent / already at main / git glitch)
    task_branch: str               # ref name (e.g. "task/TASK-X")
    task_head_before: str          # 12-char SHA before
    task_head_after: str           # 12-char SHA after (= main if ok)
    main_head: str                 # 12-char SHA of main
    reason: str                    # diagnostic

    def to_payload(self) -> dict:
        return {
            "ok": self.ok,
            "diverged": self.diverged,
            "no_op": self.no_op,
            "task_branch": self.task_branch,
            "task_head_before": self.task_head_before,
            "task_head_after": self.task_head_after,
            "main_head": self.main_head,
            "reason": self.reason,
        }


def _short(sha: str) -> str:
    return sha[:_SHA_SHORT] if sha else ""


def _run_git(
    project_root: Path,
    *args: str,
    timeout: int = _GIT_TIMEOUT_S,
) -> tuple[int, str, str]:
    """Run a git command; return (returncode, stdout, stderr).

    Never raises (catches FileNotFoundError, OSError, TimeoutExpired).
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return -1, "", str(exc)


def _rev_parse(project_root: Path, ref: str) -> str:
    rc, out, _err = _run_git(project_root, "rev-parse", ref)
    if rc != 0 or not out:
        return ""
    return out.splitlines()[0].strip()


def _is_ancestor(project_root: Path, *, ancestor: str, descendant: str) -> bool:
    rc, _out, _err = _run_git(
        project_root, "merge-base", "--is-ancestor", ancestor, descendant,
    )
    return rc == 0


def fast_forward_task_ref_onto_main(
    project_root: Path,
    *,
    task_id: str,
    main_ref: str = "main",
    task_ref_prefix: str = "task",
) -> BaselineSyncResult:
    """Fast-forward ``refs/heads/<task_ref_prefix>/<task_id>`` to the
    current HEAD of ``main_ref``, **only when safe**.

    Safe = task ref is a strict ancestor of (or equal to) main. Any
    divergence (task has its own commits not on main) → leave task ref
    alone, return ``diverged=True``.

    Never raises. Returns a result the caller can inspect to decide what
    event to emit (``task.baseline_synced`` / ``task.baseline_diverged``).
    """
    task_branch = f"{task_ref_prefix}/{task_id}".strip("/")
    task_ref_short = task_branch  # for events
    task_ref_full = f"refs/heads/{task_branch}"

    main_head = _rev_parse(project_root, main_ref)
    if not main_head:
        return BaselineSyncResult(
            ok=False, diverged=False, no_op=True,
            task_branch=task_ref_short,
            task_head_before="", task_head_after="", main_head="",
            reason=f"main ref {main_ref!r} not resolvable",
        )

    task_head = _rev_parse(project_root, task_ref_full)
    if not task_head:
        return BaselineSyncResult(
            ok=False, diverged=False, no_op=True,
            task_branch=task_ref_short,
            task_head_before="", task_head_after="", main_head=_short(main_head),
            reason="task ref does not exist (first dispatch — let arch create)",
        )

    if task_head == main_head:
        return BaselineSyncResult(
            ok=False, diverged=False, no_op=True,
            task_branch=task_ref_short,
            task_head_before=_short(task_head),
            task_head_after=_short(task_head),
            main_head=_short(main_head),
            reason="task ref already at main HEAD",
        )

    # Is task an ancestor of main? Safe to FF.
    if _is_ancestor(project_root, ancestor=task_head, descendant=main_head):
        rc, _out, err = _run_git(
            project_root, "update-ref", task_ref_full, main_head, task_head,
        )
        if rc != 0:
            return BaselineSyncResult(
                ok=False, diverged=False, no_op=True,
                task_branch=task_ref_short,
                task_head_before=_short(task_head),
                task_head_after=_short(task_head),
                main_head=_short(main_head),
                reason=f"git update-ref failed: {err[:160]}",
            )
        return BaselineSyncResult(
            ok=True, diverged=False, no_op=False,
            task_branch=task_ref_short,
            task_head_before=_short(task_head),
            task_head_after=_short(main_head),
            main_head=_short(main_head),
            reason="fast-forwarded task ref onto main",
        )

    # Task has commits main doesn't → diverged. Don't touch.
    return BaselineSyncResult(
        ok=False, diverged=True, no_op=False,
        task_branch=task_ref_short,
        task_head_before=_short(task_head),
        task_head_after=_short(task_head),
        main_head=_short(main_head),
        reason=(
            "task ref has commits not on main — kernel refuses to "
            "rewrite (operator or LLM orchestrator must resolve)"
        ),
    )
