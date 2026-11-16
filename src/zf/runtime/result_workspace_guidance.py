"""Explain existing signed result authority without granting a new write scope."""
from pathlib import Path


def result_workspace_guidance(scratch: Path) -> list[str]:
    return [
        '## Workspace and result authority',
        'Product edits remain limited to the dispatched checkout and assigned write scopes; '
        'Reader source checkouts and evidence inputs are read-only.',
        f'The Kernel-prepared `{scratch}` is this operation\'s authorized result output, '
        'even when it is outside the product checkout. Update only that existing file '
        'with the provider native Edit/apply_patch tool; this is not permission to write its directory.',
        'Do not use shell/Python redirection to bypass a denied write. If the native edit is denied, '
        'report the exact denial through the existing assistance/result channel.',
        'Never directly edit events.jsonl, kanban.json, session.yaml, leases, other attempts\' scratch, '
        'or required evidence inputs. Submission CLI performs the canonical transition.',
    ]
