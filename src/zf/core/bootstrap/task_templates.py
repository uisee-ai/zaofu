"""Task templates for the F-zaofu-bootstrap guided ritual.

4 tasks, all start in ``status="backlog"`` (default), referencing
F-zaofu-bootstrap via ``contract.feature_id`` so kanban / FeatureStore
projections group them correctly.
"""

from __future__ import annotations

from typing import Any

from zf.core.bootstrap.feature_template import BOOTSTRAP_FEATURE_ID


# Each template is a plain dict → installer converts to Task() with TaskContract().
# Kept as dicts (not Task instances at module load time) so the templates stay
# import-cheap and tests can introspect them without instantiating dataclasses.

BOOTSTRAP_TASKS: list[dict[str, Any]] = [
    {
        "id": "T-zfb-01",
        "title": "Cold-start verify — 跑 zf validate 并 emit arch.proposal.done",
        "behavior": (
            "Run `zf validate --cold-start` and confirm config is loadable. "
            "Then emit `arch.proposal.done` referencing this task to show the "
            "event flow works end-to-end."
        ),
        "verification": "zf validate --cold-start; zf events --task T-zfb-01 | grep arch.proposal.done",
        "acceptance": "exit_code=0",
    },
    {
        "id": "T-zfb-02",
        "title": "First event flow — dev 写 demo 文件并 emit dev.build.done",
        "behavior": (
            "Create a tiny demo file (e.g. `.zf/bootstrap-demo.txt`) with one "
            "line of content, then run `zf emit dev.build.done --task T-zfb-02` "
            "to demonstrate the writer-role event channel."
        ),
        "verification": "test -f .zf/bootstrap-demo.txt; zf events --task T-zfb-02 | grep dev.build.done",
        "acceptance": "exit_code=0",
    },
    {
        "id": "T-zfb-03",
        "title": "Review chain — review emit review.approved 触发 test.passed",
        "behavior": (
            "Acting as the review role, inspect the demo file from T-zfb-02 "
            "and emit `review.approved`. The orchestrator should then route a "
            "task to the test role; emit `test.passed` to close the chain."
        ),
        "verification": "zf events --task T-zfb-03 | grep -E 'review.approved|test.passed'",
        "acceptance": "exit_code=0",
    },
    {
        "id": "T-zfb-04",
        "title": "Customize CLAUDE.md — 手动加一条项目约束",
        "behavior": (
            "Open `CLAUDE.md` (or create it if missing), add a single project-"
            "specific rule under '## Project rules', then close this task with "
            "`zf kanban move T-zfb-04 done`. Manual completion — no worker dispatch."
        ),
        "verification": "manual",
        "acceptance": "manual",
    },
]


def _build_task(template: dict[str, Any]) -> "Task":
    """Materialise a template dict into a Task with TaskContract.

    Imported lazily to avoid pulling task schema into bootstrap import time
    (which is read by `zf init`, a critical path).
    """
    from zf.core.task.schema import Task, TaskContract

    return Task(
        id=template["id"],
        title=template["title"],
        status="backlog",
        priority=2,
        contract=TaskContract(
            feature_id=BOOTSTRAP_FEATURE_ID,
            behavior=template.get("behavior", ""),
            verification=template.get("verification", ""),
            acceptance=template.get("acceptance", "exit_code=0"),
        ),
    )


def materialize_bootstrap_tasks() -> list:
    """Return fresh Task instances for all bootstrap templates."""
    return [_build_task(t) for t in BOOTSTRAP_TASKS]


__all__ = ["BOOTSTRAP_TASKS", "materialize_bootstrap_tasks"]
