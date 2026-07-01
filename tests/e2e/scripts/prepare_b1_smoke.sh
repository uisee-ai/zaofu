#!/usr/bin/env bash
# 2026-04-21: B1 preset (Dev → Gate → Test → Review → Judge → Ship) — Prepare
# workspace for a real live run against examples/e2e-full.yaml.
#
# v2 (2026-04-21 later): requires BOTH claude and codex CLIs (review+test
# use codex, others use claude-code). Also uses tmux_layout: pane_grid
# so workers appear as panes in a single window.
#
# Parallel to prepare_mixed_e2e.sh / prepare_w5e2e.sh but for B1 flow.
# Idempotent: safe to re-run. Creates /tmp/zaofu-b1/ worktree, copies
# e2e-full.yaml, runs preflight checks. Does NOT start the harness —
# `zf start` is the user's decision (real token spend, ~$0.5-3 per task).
#
# See docs/runbooks/b1-vs-a-flow.md for the flow comparison.

set -u  # don't set -e — print partial preflight results

WORKTREE_PATH="${WORKTREE_PATH:-/tmp/zaofu-b1}"
BRANCH_NAME="${BRANCH_NAME:-experiment/b1-smoke}"
PRESET="examples/e2e-full.yaml"

cd "$(git rev-parse --show-toplevel)" || {
  echo "ERROR: must run inside a git worktree of zaofu" >&2
  exit 1
}

REPO_ROOT="$(pwd)"

# ---- Helpers ----
fail=0
ok() { printf "  [PASS] %s\n" "$1"; }
bad() { printf "  [FAIL] %s — %s\n" "$1" "$2"; fail=$((fail + 1)); }

# ---- 1. Git is clean (WARN only — prep doesn't require clean tree) ----
if [[ -n "$(git status --porcelain)" ]]; then
  printf "  [WARN] git_clean — working tree has uncommitted changes (ok for prep, "
  printf "but commit before 'zf start' if you want reproducibility)\n"
else
  ok "git_clean"
fi

# ---- 2. Required CLIs (v2 needs both claude + codex) ----
if command -v tmux >/dev/null 2>&1; then ok "tmux_cli"; else bad "tmux_cli" "missing"; fi
if command -v claude >/dev/null 2>&1; then ok "claude_cli"; else bad "claude_cli" "missing (login required)"; fi
if command -v codex >/dev/null 2>&1; then
  ok "codex_cli"
  # Codex must actually run (not just --help)
  if codex --version >/dev/null 2>&1; then
    ok "codex_runnable"
  else
    bad "codex_runnable" "codex --version failed — run 'codex login' and try again"
  fi
else
  bad "codex_cli" "missing — v2 e2e-full uses codex for review+test roles"
fi

# ---- 3. zf CLI importable ----
if PYTHONPATH=src python3 -c "from zf.cli.main import main" 2>/dev/null; then
  ok "zf_import"
else
  bad "zf_import" "cannot import zf.cli.main — did you pip install -e '.[dev]'?"
fi

# ---- 4. Preset yaml exists + loads + has B1 triggers ----
if [[ -f "$PRESET" ]]; then
  ok "preset_exists"
  b1_check=$(PYTHONPATH=src python3 -c "
from pathlib import Path
from zf.core.config.loader import load_config
cfg = load_config(Path('$PRESET'))
roles = {r.name: r for r in cfg.roles}
ok = (
  'dev.build.done' in roles['test'].triggers
  and 'test.passed' in roles['review'].triggers
  and roles['judge'].triggers == ['review.approved']
)
print('ok' if ok else 'bad')
" 2>&1)
  if [[ "$b1_check" == "ok" ]]; then
    ok "preset_b1_triggers"
  else
    bad "preset_b1_triggers" "triggers not B1 (got: $b1_check)"
  fi
else
  bad "preset_exists" "$PRESET not found in repo root"
fi

# ---- 5. Topology is clean ----
topo_check=$(PYTHONPATH=src python3 -c "
from pathlib import Path
from zf.core.config.loader import load_config
from zf.core.workflow.topology import WorkflowTopology
cfg = load_config(Path('$PRESET'))
t = WorkflowTopology.from_config(cfg)
orphans = t.orphan_events()
deadends = t.dead_end_roles()
if orphans or deadends:
  print(f'BAD orphan={orphans} deadend={deadends}')
else:
  print('ok')
" 2>&1)
if [[ "$topo_check" == "ok" ]]; then
  ok "topology_clean"
else
  bad "topology_clean" "$topo_check"
fi

# ---- 6. pytest baseline green (unit tests only) ----
if PYTHONPATH=src python3 -m pytest \
    tests/test_e2e_full_preset.py tests/test_config_schema.py \
    --no-cov -q > /tmp/b1-smoke-pytest.log 2>&1; then
  ok "pytest_b1_unit"
else
  bad "pytest_b1_unit" "see /tmp/b1-smoke-pytest.log"
fi

# ---- 6.5 Wipe leftover claude session files for this worktree path.
# Learned 2026-04-21: claude CLI refuses to spawn a session whose UUID
# already has a ~/.claude/projects/<hash>/<uuid>.jsonl. Previous smoke
# runs leave these and block re-spawn with "Session ID is already in use".
# Safe to delete: these are this-worktree conversation logs, not code.
CLAUDE_PROJ_DIR="$HOME/.claude/projects/$(echo "$WORKTREE_PATH" | sed 's|/|-|g')"
if [[ -d "$CLAUDE_PROJ_DIR" ]]; then
  rm -rf "$CLAUDE_PROJ_DIR"
  ok "claude_session_wipe ($CLAUDE_PROJ_DIR)"
else
  ok "claude_session_wipe (none needed)"
fi

# ---- 7. Prepare worktree ----
if git worktree list | grep -q "$WORKTREE_PATH"; then
  ok "worktree_exists"
else
  if git show-ref --verify --quiet "refs/heads/$BRANCH_NAME"; then
    git worktree add "$WORKTREE_PATH" "$BRANCH_NAME" 2>&1 >/dev/null \
      && ok "worktree_created" \
      || bad "worktree_created" "git worktree add failed"
  else
    git worktree add -b "$BRANCH_NAME" "$WORKTREE_PATH" HEAD 2>&1 >/dev/null \
      && ok "worktree_created" \
      || bad "worktree_created" "git worktree add -b failed"
  fi
fi

# ---- 8. Copy preset into worktree as zf.yaml ----
if [[ -d "$WORKTREE_PATH" ]]; then
  cp "$PRESET" "$WORKTREE_PATH/zf.yaml"
  ok "zf_yaml_placed"

  # 9. Init .zf/ in the worktree (idempotent — skip if session.yaml exists)
  if [[ -f "$WORKTREE_PATH/.zf/session.yaml" ]]; then
    ok "zf_init_done (already initialized)"
  elif (cd "$WORKTREE_PATH" && PYTHONPATH="$REPO_ROOT/src" python3 -m zf.cli.main init > /dev/null 2>&1); then
    ok "zf_init_done"
  else
    bad "zf_init_done" "zf init failed in $WORKTREE_PATH"
  fi
else
  bad "zf_yaml_placed" "worktree missing"
fi

# ---- 10. Dry-run sanity (zero cost, verifies runtime accepts B1 topology) ----
if [[ -d "$WORKTREE_PATH/.zf" ]]; then
  if (cd "$WORKTREE_PATH" \
        && PYTHONPATH="$REPO_ROOT/src" python3 -m zf.cli.main start --dry-run \
        > /tmp/b1-smoke-dry-run.log 2>&1); then
    # Parse wake patterns count from log
    wake=$(grep -oP '\d+(?= wake patterns)' /tmp/b1-smoke-dry-run.log | head -1)
    if [[ -n "$wake" && "$wake" -ge 40 ]]; then
      ok "dry_run ($wake wake patterns)"
    else
      bad "dry_run" "suspicious wake patterns count: $wake (see /tmp/b1-smoke-dry-run.log)"
    fi
  else
    bad "dry_run" "zf start --dry-run failed (see /tmp/b1-smoke-dry-run.log)"
  fi
fi

# ---- 11. Env: ANTHROPIC_API_KEY or ~/.claude login ----
if [[ -n "${ANTHROPIC_API_KEY:-}" ]] || [[ -f "$HOME/.claude/.credentials.json" ]]; then
  ok "claude_auth"
else
  bad "claude_auth" "no ANTHROPIC_API_KEY and no ~/.claude/.credentials.json — run 'claude login'"
fi

# ---- 12. Event signing secret (preset defaults event_signing=on) ----
if [[ -n "${ZF_EVENT_SECRET:-}" ]]; then
  ok "zf_event_secret"
else
  printf "  [WARN] zf_event_secret — not set; preset enables event_signing but will fallback to unsigned. "
  printf "export ZF_EVENT_SECRET=\$(openssl rand -hex 32) to enable.\n"
fi

# ---- Summary + next steps ----
echo ""
echo "========================================"
if [[ $fail -eq 0 ]]; then
  echo "Preflight PASS. Worktree: $WORKTREE_PATH"
  echo ""
  echo "Next (real run, ~\$1-5):"
  echo "  export ZF_EVENT_SECRET=\$(openssl rand -hex 32)"
  echo "  cd $WORKTREE_PATH"
  echo "  zf start --foreground   # or run in background + zf attach"
  echo ""
  echo "  # In another terminal:"
  echo "  zf chat \"implement a hello function that returns 'hello'\""
  echo ""
  echo "  # Watch events:"
  echo "  tail -f $WORKTREE_PATH/.zf/events.jsonl | \\"
  echo "    grep -E '\"type\":\"(dev|test|review|judge|task)\\.'"
  echo ""
  echo "  # Verify B1 order after done:"
  echo "  PYTHONPATH=src python3 -c \"
  from pathlib import Path
  import json
  events = [json.loads(l) for l in Path('$WORKTREE_PATH/.zf/events.jsonl').read_text().splitlines()]
  order = [e['type'] for e in events if e['type'] in ('dev.build.done','test.passed','review.approved','judge.passed')]
  print('Order:', order)
  expected_prefix = ['dev.build.done','test.passed','review.approved','judge.passed']
  assert order[:4] == expected_prefix, f'B1 violated: {order}'
  print('B1 order verified')
  \""
  echo ""
  echo "  # Stop:"
  echo "  zf stop"
  exit 0
else
  echo "Preflight FAIL ($fail issues). Fix the FAIL items above before running."
  exit 1
fi
