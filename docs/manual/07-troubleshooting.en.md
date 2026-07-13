# ZaoFu Troubleshooting

> Audience: first-line diagnosis for startup failures, stalled workers, Web 500 responses, missing skills, and stuck real E2E runs.

## 1. Fast Diagnostic Sequence

Start with:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main validate --path zf.yaml
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main validate --cold-start
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main doctor
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main skills doctor
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main kanban --board
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main events --last 50
```

For a specific task:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main task trace <task_id>
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main kanban show <task_id>
```

## 2. `zf.yaml not found`

Typical error:

```text
Error: zf.yaml not found. To fix: run 'zf init'
```

Initialize a new project:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main init --preset safe-team
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main validate --path zf.yaml
```

For an existing project, do not overwrite configuration. First confirm that
the current directory is the project root.

## 3. State Directory Does Not Exist

Typical error:

```text
Error: .zf not found. To fix: run 'zf init'
```

Run:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main init
```

If `project.state_dir` is not `.zf`, validate `zf.yaml` and use that configured
path in diagnostic commands.

## 4. Lock Held

Typical error:

```text
Error: Another harness is running (lock held). To fix: run 'zf stop' first
```

Try a graceful stop:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main stop
```

If no harness should be running, inspect processes before touching the lock:

```bash
tmux ls
ps -ef | rg "zf.cli.main start|tests.e2e.run_mixed|autoresearch"
```

Only remove `project.state_dir/loop.lock` after proving that it is stale.

## 5. Worker Does Not Progress

Inspect the live session and truth projections:

```bash
tmux ls
tmux attach -t <session>
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main events --last 100
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main status --workers
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main kanban --board
```

Common causes include:

- The harness was started with `zf start --no-watch`, or its watcher exited.
- The role's `triggers` do not include the upstream event.
- A completion event lacks a dispatch token or contract evidence.
- `max_rework_attempts` has been exhausted.
- The provider CLI is waiting for login, hook review, or interactive approval.
- Context is too large and the worker is waiting for recycle.

Then run:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main validate --cold-start
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main task trace <task_id>
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main doctor
```

## 6. Codex Hooks Need Review

If output says `hooks need review`, attach to the relevant Codex pane, open
`/hooks`, and review or trust the project `.codex/hooks.json`. Restart the
harness or redispatch afterward. ZaoFu writes project hook configuration, but
Codex still controls whether those hooks are trusted to execute.

## 7. Skills Are Missing or Incomplete in Web

Check CLI truth first:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main skills list
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main skills doctor
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main validate --strict-skills
```

Common causes:

- A `skill_sources.path` does not exist.
- Multiple candidates provide the same skill name.
- `runtime.skills.strict=false` reports only a warning.
- The role does not declare the skill.
- The Web list is paginated or collapsed even though runtime truth is present.

Inspect runtime artifacts:

```bash
jq . < .zf/skills.lock.json
find .zf/workdirs -path '*/runtime/skills-manifest.json' -print
```

## 8. Web Returns 500 or Shows Degraded

Confirm that Web points at the intended state directory:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main web \
  --state-dir "$(pwd)/.zf" \
  --host 0.0.0.0 \
  --port 5175
```

Then inspect:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main doctor
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main events --last 20
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main kanban --board
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main runs rebuild
```

Typical causes are unreadable `kanban.json` or `events.jsonl`, Web started from
the wrong project, a deleted worktree referenced by `--state-dir`, a port
collision, or projections created by an incompatible runtime version.

## 9. `kanban move done` Is Rejected

This normally means the completion gate is working. Find the missing evidence:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main task trace <task_id>
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main events --last 100
```

Common missing events are `review.approved`, `test.passed`, `judge.passed`, and
`discriminator.passed`. Return the task to the missing stage; never edit
`kanban.json` manually.

## 10. Workdir or Ref Is Unhealthy

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main doctor workdirs
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main refs verify
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main workdir repair dev-1
```

When roles in different worktrees see different refs, inspect task evidence and
the candidate ref. Review and test must not accept a verbal claim about `dev` in
place of the actual candidate revision.

## 11. Pane-Grid Binding Is Wrong

Diagnose role-to-pane mapping:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main doctor panes
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main panes doctor
```

If live panes still run in their `.zf/workdirs/<instance>/project` directories,
repair bindings without restarting them:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main panes repair
```

The repair sets `@zf_instance_id`, attempts to restore pane titles, and rewrites
`.zf/pane_bindings.json`. It does not kill panes or send work. Codex TUI may
overwrite titles, so trust `@zf_instance_id`, the binding file, and pane cwd.
The command fails closed if a configured tmux role has no live pane.

## 12. Clean Runtime State

Preview first:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main state clean --dry-run
```

After archiving evidence:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main state clean --confirm --archive
```

This removes rebuildable projections only. Do not manually delete truth files
unless the entire run is intentionally being discarded.

## 13. Real E2E Is Stuck

Check for an existing fatal or blocker first:

```bash
PYTHONPATH=src python3 -m tests.e2e.mixed_phase_report \
  --state-dir /tmp/zaofu-codex-smoke/.zf
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main events \
  --state-dir /tmp/zaofu-codex-smoke/.zf \
  --last 100
```

Stop only the affected session:

```bash
tmux ls
tmux kill-session -t <target-session>
```

Never kill every tmux session. Confirm that the target name comes from
`session.tmux_session` in `zf.yaml` or from the E2E runner output.
