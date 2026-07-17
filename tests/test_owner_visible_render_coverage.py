"""Forcing test: push-prone problem specs must have a Chinese rendering.

The _REASON_HUMAN table rots silently — /tmp/runm.png showed the three
highest-frequency Run Manager cards shipping English internals because
nobody noticed the table did not cover them. This test makes the rot loud:
any spec likely to reach the Feishu group (whitelisted notification policy,
or an intrinsic human_required_when condition) must either be covered by
the reason table or be explicitly acknowledged below.
"""

from __future__ import annotations

from zf.runtime.event_problem_registry import EVENT_PROBLEM_SPECS
from zf.runtime.owner_visible_render import _humanize

_PUSH_POLICIES = {"owner_immediate", "owner_on_human_required"}
_INTRINSIC = {"owner_budget_decision_needed"}

# Specs whose title is not yet in _REASON_HUMAN. Shrink this list by adding
# mappings; never let it grow silently — a new push-prone spec must either
# get a Chinese rendering or be added here WITH a reason.
_ACKNOWLEDGED_UNMAPPED: set[str] = set()


def _push_prone(spec) -> bool:
    if spec.effective_notification_policy in _PUSH_POLICIES:
        return True
    return bool(_INTRINSIC.intersection(spec.human_required_when))


def test_push_prone_spec_titles_have_chinese_rendering():
    missing: list[str] = []
    for spec in EVENT_PROBLEM_SPECS.values():
        if not spec.title or not _push_prone(spec):
            continue
        if spec.event_type in _ACKNOWLEDGED_UNMAPPED:
            continue
        # _humanize returns the input unchanged when no needle matches.
        if _humanize(spec.title, "") == spec.title:
            missing.append(f"{spec.event_type}: {spec.title!r}")
    assert not missing, (
        "push-prone problem specs lack a Chinese reason mapping "
        "(add to _REASON_HUMAN in owner_visible_render.py, or acknowledge "
        f"explicitly in this test): {missing}"
    )
