"""Explain existing signed result authority without granting a new write scope."""
from pathlib import Path


def result_workspace_guidance(scratch: Path) -> list[str]:
    return [
        '## Workspace and result authority',
        'Product edits obey assigned write scopes. '
        'Reader source checkouts and evidence inputs are read-only.',
        f'Authorized scratch: `{scratch}`, even outside the product checkout. '
        'Edit only that existing file; not permission to write its directory.',
        "Use native Edit/apply_patch, or its standalone exec_command/Bash adapter: "
        "`apply_patch <<'PATCH'`, quoted heredoc Update File patch, then `PATCH`; "
        "no command prefix/suffix, substitutions, output redirection, add/delete/move.",
        'Do not use shell/Python redirection to bypass denial; report it via assistance.',
        'Never edit events.jsonl, kanban.json, session.yaml, leases or other scratch. '
        'Submission CLI owns canonical transitions.',
    ]
