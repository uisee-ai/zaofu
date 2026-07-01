"""ZF-TR-FINISH-001 — release/ship dirty-tree classification gate (doc 39 §2.1.7).

Before auto-ship, classify the working tree into 5 buckets:

- ``task_owned_changes``     — files declared in task.contract.scope
- ``runtime_state_changes``  — .zf/* state (events.jsonl, kanban.json,...)
- ``unrecognized_changes``   — neither owned by task nor recognised
- ``generated_artifacts``    — docs/artifacts / build output
- ``user_unowned_changes``   — files the project deliberately excludes

Rule (doc 39 §2.1.7):
- ``unrecognized_changes`` non-empty → **do not auto-ship**
- worker must not delete unrecognised files to "make the gate pass"
- dirty-tree evidence is recorded for human review either way

The module is pure: callers provide the git-status output + project
context; this module classifies and decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class DirtyTreeClassification:
    task_owned_changes: tuple[str, ...] = ()
    runtime_state_changes: tuple[str, ...] = ()
    generated_artifacts: tuple[str, ...] = ()
    user_unowned_changes: tuple[str, ...] = ()
    unrecognized_changes: tuple[str, ...] = ()

    @property
    def has_unrecognized(self) -> bool:
        return bool(self.unrecognized_changes)

    @property
    def auto_ship_safe(self) -> bool:
        """Per doc 39 §2.1.7: only when no unrecognized changes."""
        return not self.has_unrecognized

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "task_owned_changes": list(self.task_owned_changes),
            "runtime_state_changes": list(self.runtime_state_changes),
            "generated_artifacts": list(self.generated_artifacts),
            "user_unowned_changes": list(self.user_unowned_changes),
            "unrecognized_changes": list(self.unrecognized_changes),
        }


def _matches_prefix(path: str, prefixes: Iterable[str]) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/")
               for p in prefixes if p)


# Conservative defaults — overrides via classify(..., overrides=...).
_DEFAULT_RUNTIME_STATE = (".zf/",)
_DEFAULT_GENERATED = (
    "docs/artifacts/",
    "docs/runs/",
    ".coverage",
    "htmlcov/",
    "dist/",
    "build/",
)
_DEFAULT_USER_UNOWNED = (
    ".gitignore",
    "node_modules/",
    "web/node_modules/",
)


def classify_dirty_tree(
    *,
    changed_paths: Iterable[str],
    task_scope: Iterable[str] = (),
    runtime_state_prefixes: Iterable[str] = _DEFAULT_RUNTIME_STATE,
    generated_prefixes: Iterable[str] = _DEFAULT_GENERATED,
    user_unowned_prefixes: Iterable[str] = _DEFAULT_USER_UNOWNED,
) -> DirtyTreeClassification:
    """Classify ``changed_paths`` (e.g. from ``git status --porcelain``)
    into the 5 buckets. Empty input → empty classification (no
    auto-ship blockers)."""
    task_owned: list[str] = []
    runtime: list[str] = []
    generated: list[str] = []
    user: list[str] = []
    unrecognized: list[str] = []

    task_scope_list = [s for s in task_scope if s]

    for raw in changed_paths:
        path = raw.strip()
        if not path:
            continue
        if _matches_prefix(path, task_scope_list):
            task_owned.append(path)
        elif _matches_prefix(path, runtime_state_prefixes):
            runtime.append(path)
        elif _matches_prefix(path, generated_prefixes):
            generated.append(path)
        elif _matches_prefix(path, user_unowned_prefixes):
            user.append(path)
        else:
            unrecognized.append(path)
    return DirtyTreeClassification(
        task_owned_changes=tuple(sorted(set(task_owned))),
        runtime_state_changes=tuple(sorted(set(runtime))),
        generated_artifacts=tuple(sorted(set(generated))),
        user_unowned_changes=tuple(sorted(set(user))),
        unrecognized_changes=tuple(sorted(set(unrecognized))),
    )
