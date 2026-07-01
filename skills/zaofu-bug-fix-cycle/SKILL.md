---
name: zaofu-bug-fix-cycle
description: "Operator playbook for resuming cangjie work after zaofu.bug.detected — stash, fix in ZaoFu repo, restart watcher, resume."
---

# zaofu-bug-fix-cycle

When cangjie's events.jsonl periodic scan emits `zaofu.bug.detected`, an
operator-mediated fix cycle takes over. The cangjie task state is paused;
zaofu kernel is patched + pushed; watcher restarts pick up the new code;
cangjie resumes with no work loss.

**Important**: zaofu must **not** modify itself. The fix cycle is an
operator activity (or a separate sprint-card-driven zaofu repo activity),
not an in-flight kernel patch.

## Triggers

`zaofu.bug.detected` event payload structure (β-1 output):

```json
{
  "type": "zaofu.bug.detected",
  "actor": "zf-cli",
  "payload": {
    "signature": "ship_block_loop | respawn_failure_cascade | judge_failure_loop",
    "confidence": "high | medium | low",
    "evidence_event_ids": ["evt-...", "evt-..."],
    "suggested_fix_area": "src/zf/runtime/<module>.py:<symbol>",
    "cangjie_state_snapshot": {
      "pdd_id": "F-...",
      "task_id": "TASK-...",
      "blockers": ["..."],
      "occurrence_count": N
    }
  }
}
```

## Cycle (4 steps)

### 1. Stash cangjie task state

Cangjie's git working tree may be partially mid-task. Snapshot it:

```bash
cd /path/to/project
git stash push -u -m "zaofu-fix-pause-<bug-signature>"

# Record the bookmark so resume knows the precise return point.
mkdir -p .zf/tmp
echo "stashed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > .zf/tmp/fix-cycle-bookmark
echo "feature_id=<F-id from payload>" >> .zf/tmp/fix-cycle-bookmark
echo "task_id=<TASK-id from payload>" >> .zf/tmp/fix-cycle-bookmark
echo "branch=$(git branch --show-current)" >> .zf/tmp/fix-cycle-bookmark
echo "head=$(git rev-parse HEAD)" >> .zf/tmp/fix-cycle-bookmark
```

### 2. Fix zaofu (in /path/to/zaofu)

Read the evidence events to confirm the failure pattern, then patch the
suggested fix area:

```bash
cd /path/to/zaofu

# Read the evidence
for ev_id in <evidence_event_ids from payload>; do
    grep "\"id\":\"$ev_id\"" /path/to/project/.zf/events.jsonl
done

# Implement the fix in suggested_fix_area
# Validate-First Discipline: confirm the bug still reproduces against
# the current zaofu HEAD before writing the fix (see zaofu/CLAUDE.md §
# "Validate-First Discipline").

# TDD: add a regression test that replays the evidence events.
.venv/bin/python -m pytest tests/test_<area>.py --no-cov -q

# Full suite
.venv/bin/python -m pytest --no-cov -q

# Commit + push
git add ...
GIT_AUTHOR_NAME="..." GIT_AUTHOR_EMAIL="..." \
GIT_COMMITTER_NAME="..." GIT_COMMITTER_EMAIL="..." \
  git commit -m "fix: <bug-sig> — <one-line>"
git push origin dev
```

### 3. Restart cangjie watcher

In-process zaofu instance is on pre-fix code. Restart to load the new
kernel:

```bash
cd /path/to/project
/path/to/zaofu/.venv/bin/python -m zf.cli.main stop
/path/to/zaofu/.venv/bin/python -m zf.cli.main start &
```

Wait for `session.started` + `loop.started` in
`.zf/events.jsonl`.

### 4. Resume cangjie task

```bash
cd /path/to/project
git stash pop  # restore mid-task working tree

/path/to/zaofu/.venv/bin/python -m zf.cli.main emit \
  cangjie.bug.zaofu_fix_applied \
  --payload '{
    "bug_signature": "<from payload>",
    "fix_commit": "<git rev-parse HEAD in zaofu repo>",
    "evidence_event_ids": [...]
  }'
```

The orchestrator's existing reactors will re-evaluate the paused task
state. For ship-block-loop the operator may need one extra ship retry
via `python /tmp/cangjie-ship-retry.py` (or the upcoming
`zf bug-fix-cycle resume` automation in β-3).

## Rules

- **Never** push to zaofu `master` from the fix cycle. Stay on `dev`.
- **Never** force-push or amend pushed commits. Each fix is a new commit
  for clear history.
- **Always** add a regression test with the exact evidence events before
  declaring the fix done.
- **Always** validate-first: confirm the bug still reproduces against
  current zaofu HEAD before writing the fix.

## β-3 automation (planned)

The above 4 steps will be wrapped by `zf bug-fix-cycle <signature>` CLI:
- auto-extract bookmark from `zaofu.bug.detected` payload
- prompt operator at each step
- emit `cangjie.bug.zaofu_fix_applied` automatically on resume

Until β-3 lands, follow this markdown by hand. The cycle should be
≤3 explicit confirms total (vs the prior ≥5 manual touch-points seen
in r-next-8/9).
