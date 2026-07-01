"""Installer entry point — called by `zf init`.

Idempotent: rerunning install_bootstrap_feature on a state dir that already
contains F-zaofu-bootstrap is a no-op (unless overwrite=True).
"""

from __future__ import annotations

from pathlib import Path

from zf.core.bootstrap.feature_template import (
    BOOTSTRAP_FEATURE_DESCRIPTION,
    BOOTSTRAP_FEATURE_ID,
    BOOTSTRAP_FEATURE_TITLE,
)
from zf.core.bootstrap.task_templates import (
    BOOTSTRAP_TASKS,
    materialize_bootstrap_tasks,
)


def install_bootstrap_feature(
    state_dir: Path,
    config=None,
    *,
    skip: bool = False,
    overwrite: bool = False,
) -> bool:
    """Install F-zaofu-bootstrap if not already present.

    Args:
        state_dir: ZaoFu runtime state dir (usually ``.zf/``).
        config: ZfConfig — currently unused, reserved for future per-config
            customisation (e.g. picking a different role set if preset is
            ``code-assist`` vs ``minimal``).
        skip: if True, no-op (returned by --skip-bootstrap flag).
        overwrite: if True, replace existing F-zaofu-bootstrap (deletes
            existing tasks and re-adds). Use with caution.

    Returns:
        True if the feature was installed (or replaced); False if skipped
        or already present.
    """
    _ = config  # reserved
    if skip:
        return False

    # Lazy imports — keep `zf init` import surface lean
    from zf.core.feature.schema import Feature
    from zf.core.feature.store import FeatureStore
    from zf.core.task.store import TaskStore

    feature_store = FeatureStore(state_dir / "feature_list.json")
    task_store = TaskStore(state_dir / "kanban.json")

    existing = feature_store.get(BOOTSTRAP_FEATURE_ID)
    if existing is not None and not overwrite:
        return False

    if existing is not None and overwrite:
        # Remove existing bootstrap tasks before re-adding
        for tmpl in BOOTSTRAP_TASKS:
            if task_store.get(tmpl["id"]) is not None:
                # TaskStore has no delete; mark cancelled instead
                task_store.update(tmpl["id"], status="cancelled")
        feature_store.update(
            BOOTSTRAP_FEATURE_ID,
            status="cancelled",
        )

    feature_store.add(Feature(
        id=BOOTSTRAP_FEATURE_ID,
        title=BOOTSTRAP_FEATURE_TITLE,
        description=BOOTSTRAP_FEATURE_DESCRIPTION,
        status="active",
        priority=1,
        user_message="(auto-installed by zf init bootstrap ritual)",
    ))

    for task in materialize_bootstrap_tasks():
        # Skip if exists from a previous partial install (idempotent at task level too)
        if task_store.get(task.id) is None:
            task_store.add(task)

    # Mirror to .zf/bootstrap.md for editor view
    bootstrap_md = state_dir / "bootstrap.md"
    bootstrap_md.write_text(BOOTSTRAP_FEATURE_DESCRIPTION, encoding="utf-8")

    return True


__all__ = ["install_bootstrap_feature"]
