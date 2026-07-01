#!/usr/bin/env bash
# 1203-T2: Mixed Backend E2E — Prepare workspace for a real claude+codex run.
#
# Adapts prepare_w5e2e.sh for the mixed-team preset. Idempotent: safe to
# re-run. Creates /tmp/zaofu-mixed/ worktree, copies mixed-team.yaml, and
# runs preflight checks including codex-specific ones (codex CLI, login,
# ~/.codex/sessions writable). Does NOT start the harness — `zf start`
# is the user's decision (costs real money / tokens on two backends).
#
# See docs/runbooks/mixed-backend-e2e.md for the full runbook.

set -u  # don't set -e — we want to print partial preflight results

WORKTREE_PATH="${WORKTREE_PATH:-/tmp/zaofu-mixed}"
BRANCH_NAME="${BRANCH_NAME:-experiment/mixed-backend-e2e}"
PRESET="examples/mixed-team.yaml"

cd "$(git rev-parse --show-toplevel)" || {
  echo "ERROR: must run inside a git worktree of zaofu" >&2
  exit 1
}

REPO_ROOT="$(pwd)"

# ---- Helpers ----
fail=0
ok() { printf "  [PASS] %s\n" "$1"; }
bad() { printf "  [FAIL] %s — %s\n" "$1" "$2"; fail=$((fail + 1)); }

# ---- 1. Git is clean ----
if [[ -n "$(git status --porcelain)" ]]; then
  bad "git_clean" "working tree has uncommitted changes"
else
  ok "git_clean"
fi

# ---- 2. Required CLIs (both mandatory for mixed run) ----
if command -v tmux >/dev/null 2>&1; then ok "tmux_cli"; else bad "tmux_cli" "missing"; fi
if command -v claude >/dev/null 2>&1; then ok "claude_cli"; else bad "claude_cli" "missing (login required)"; fi
if command -v codex >/dev/null 2>&1; then
  ok "codex_cli"
  # Codex must actually run (not just --help exit)
  if codex --version >/dev/null 2>&1; then
    ok "codex_runnable"
  else
    bad "codex_runnable" "codex --version failed — run 'codex login' and try again"
  fi
else
  bad "codex_cli" "missing — mixed run requires codex installed"
fi

# ---- 3. zf CLI importable ----
if PYTHONPATH=src python3 -c "from zf.cli.main import main" 2>/dev/null; then
  ok "zf_import"
else
  bad "zf_import" "cannot import zf.cli.main — did you pip install -e '.[dev]'?"
fi

# ---- 4. pytest baseline green ----
if PYTHONPATH=src python3 -m pytest tests/ --no-cov -q -x > /tmp/mixed-e2e-pytest.log 2>&1; then
  ok "pytest_baseline"
else
  bad "pytest_baseline" "tests failed (see /tmp/mixed-e2e-pytest.log)"
fi

# ---- 5. No pre-existing zf-mixed tmux session ----
if tmux has-session -t zf-mixed 2>/dev/null; then
  bad "tmux_session_name_clean" "session zf-mixed already exists (run 'tmux kill-session -t zf-mixed')"
else
  ok "tmux_session_name_clean"
fi

# ---- 6. Worktree slot free or reusable ----
if [[ -d "$WORKTREE_PATH" ]]; then
  if (cd "$WORKTREE_PATH" && git rev-parse --show-toplevel >/dev/null 2>&1 && \
      git branch --show-current 2>/dev/null | grep -q "$BRANCH_NAME"); then
    ok "worktree_slot_reusable"
  else
    bad "worktree_slot" "$WORKTREE_PATH exists and is not a matching worktree"
  fi
else
  ok "worktree_slot_free"
fi

# ---- 7. Preset yaml exists + topology clean ----
if [[ -f "$PRESET" ]]; then
  ok "preset_yaml_exists ($PRESET)"
else
  bad "preset_yaml" "$PRESET not found"
fi

if PYTHONPATH=src python3 -c "
from pathlib import Path
from zf.core.config.loader import load_config
from zf.core.workflow.topology import WorkflowTopology
from zf.runtime.wake_patterns import WAKE_PATTERNS, reactor_handler_events
config = load_config(Path('$PRESET'))
topology = WorkflowTopology.from_config(config)
assert topology.orphan_events() == [], f'orphan: {topology.orphan_events()}'
assert topology.dead_end_roles() == [], f'dead-end: {topology.dead_end_roles()}'
" 2>/dev/null; then
  ok "topology_clean"
else
  bad "topology" "$PRESET has orphan events or dead-end roles"
fi

# ---- 8. Codex-specific: ~/.codex/sessions writable ----
CODEX_SESSIONS="$HOME/.codex/sessions"
mkdir -p "$CODEX_SESSIONS" 2>/dev/null
if [[ -w "$CODEX_SESSIONS" ]]; then
  ok "codex_sessions_writable"
else
  bad "codex_sessions_writable" "$CODEX_SESSIONS not writable"
fi

echo
if [[ $fail -gt 0 ]]; then
  echo "Preflight FAILED ($fail checks). Fix above issues before running zf start."
  exit 1
fi

# ---- All green — materialize the workspace ----
echo "Preflight OK. Preparing $WORKTREE_PATH ..."

if [[ ! -d "$WORKTREE_PATH" ]]; then
  git worktree add "$WORKTREE_PATH" -b "$BRANCH_NAME" 2>/dev/null || {
    git worktree add "$WORKTREE_PATH" "$BRANCH_NAME" 2>/dev/null || {
      echo "ERROR: could not create worktree at $WORKTREE_PATH" >&2
      exit 1
    }
  }
  echo "  Created worktree at $WORKTREE_PATH (branch $BRANCH_NAME)"
fi

# B-W5-01 variant (2026-04-21 mixed-e2e): Claude session uuids are
# deterministic (UUID5 of project_root + role), so re-running against
# the same worktree path reuses the same uuid. If the previous run left
# a completed claude session at ~/.claude/projects/<escaped-cwd>/<uuid>.jsonl,
# SpawnCoordinator will pass --resume <uuid> on the next boot; claude
# replays the finished conversation, exits, and the briefing falls into
# bash. Purge the project-specific claude session dir so each prepare
# run starts from a clean slate.
CLAUDE_PROJECT_DIR=$(PYTHONPATH=src python3 -c "
from pathlib import Path
p = Path('$WORKTREE_PATH').resolve()
# Claude escapes '/' → '-' and prepends '-'
print('-' + str(p).lstrip('/').replace('/', '-'))
")
CLAUDE_SESSIONS="$HOME/.claude/projects/$CLAUDE_PROJECT_DIR"
if [[ -d "$CLAUDE_SESSIONS" ]]; then
  rm -rf "$CLAUDE_SESSIONS"
  echo "  Purged stale claude sessions: $CLAUDE_SESSIONS"
fi

cp "$PRESET" "$WORKTREE_PATH/zf.yaml"

# Add mixed-specific tweaks:
#   session.tmux_session = zf-mixed
#   project.name = zaofu-mixed
#   cost hard-cap (global_budget_usd = 5 per risk §3 in backlog)
PYTHONPATH=src python3 <<PY
import yaml
from pathlib import Path
p = Path("$WORKTREE_PATH/zf.yaml")
data = yaml.safe_load(p.read_text())
data.setdefault("session", {})["tmux_session"] = "zf-mixed"
data.setdefault("project", {})["name"] = "zaofu-mixed"
# Cost cap to prevent runaway spend during exploratory runs.
data.setdefault("cost", {})["global_budget_usd"] = 5.0
p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
print(f"  Wrote {p} (tmux_session=zf-mixed, budget=\$5)")
PY

cd "$WORKTREE_PATH"
mkdir -p .zf/artifacts .zf/briefings .zf/logs .zf/memory
[[ -f ".zf/events.jsonl" ]] || touch .zf/events.jsonl
[[ -f ".zf/kanban.json" ]] || echo "[]" > .zf/kanban.json
[[ -f ".zf/session.yaml" ]] || cat > .zf/session.yaml <<'YML'
session_id: ""
runtime_state: initialized
latest_event_offset: 0
YML
[[ -f ".zf/feature_list.json" ]] || echo "[]" > .zf/feature_list.json

# 1202-T1: pre-render .codex/hooks.json so codex picks it up on first
# spawn. start.py also runs this but we anchor it in preflight so the
# runbook can show the resulting file to the operator before ignition.
PYTHONPATH=$REPO_ROOT/src python3 <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
from pathlib import Path
from zf.cli.start import _write_codex_hook_settings
_write_codex_hook_settings(Path("$WORKTREE_PATH/.zf"))
print(f"  Wrote $WORKTREE_PATH/.codex/hooks.json (5 events)")
PY

cd "$REPO_ROOT"

echo
echo "Mixed-backend E2E workspace ready at $WORKTREE_PATH"
echo "Next: follow docs/runbooks/mixed-backend-e2e.md to ignite."
