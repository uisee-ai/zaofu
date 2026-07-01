#!/usr/bin/env bash
# W5-E2E Baseline — Prepare workspace for a real multi-agent run.
#
# Idempotent: safe to re-run. Creates /tmp/zaofu-w5e2e/ worktree, copies
# a config, and runs 8 preflight checks. Does NOT start the harness —
# `zf start` is the user's decision (costs real money / tokens).
#
# See docs/runbooks/w5-e2e-baseline.md for the full runbook.

set -u  # don't set -e — we want to print partial preflight results

WORKTREE_PATH="${WORKTREE_PATH:-/tmp/zaofu-w5e2e}"
BRANCH_NAME="${BRANCH_NAME:-experiment/w5e2e-baseline}"
# MVP (default): safe-team 6 role. Set CONFIG_MODE=full to require
# critic + test_spec roles (need W5E2E-T1 shipped first).
CONFIG_MODE="${CONFIG_MODE:-mvp}"

cd "$(git rev-parse --show-toplevel)" || {
  echo "ERROR: must run inside a git worktree of zaofu" >&2
  exit 1
}

REPO_ROOT="$(pwd)"

# ---- Helpers ----
fail=0
ok() { printf "  [PASS] %s\n" "$1"; }
bad() { printf "  [FAIL] %s — %s\n" "$1" "$2"; fail=$((fail + 1)); }

# ---- 1. Git is clean (don't blast a half-finished change) ----
if [[ -n "$(git status --porcelain)" ]]; then
  bad "git_clean" "working tree has uncommitted changes"
else
  ok "git_clean"
fi

# ---- 2. Required CLIs ----
if command -v tmux >/dev/null 2>&1; then ok "tmux_cli"; else bad "tmux_cli" "missing"; fi
if command -v claude >/dev/null 2>&1; then ok "claude_cli"; else bad "claude_cli" "missing (login required)"; fi
if command -v codex >/dev/null 2>&1; then ok "codex_cli"; else bad "codex_cli" "missing (optional for MVP)"; fi

# ---- 3. zf CLI importable ----
if PYTHONPATH=src python3 -c "from zf.cli.main import main" 2>/dev/null; then
  ok "zf_import"
else
  bad "zf_import" "cannot import zf.cli.main — did you pip install -e '.[dev]'?"
fi

# ---- 4. pytest baseline green ----
if PYTHONPATH=src python3 -m pytest tests/ --no-cov -q -x > /tmp/w5e2e-pytest.log 2>&1; then
  ok "pytest_baseline"
else
  bad "pytest_baseline" "tests failed (see /tmp/w5e2e-pytest.log)"
fi

# ---- 5. No pre-existing zf-w5e2e tmux session ----
if tmux has-session -t zf-w5e2e 2>/dev/null; then
  bad "tmux_session_name_clean" "session zf-w5e2e already exists (run 'tmux kill-session -t zf-w5e2e')"
else
  ok "tmux_session_name_clean"
fi

# ---- 6. Worktree slot free or reusable ----
if [[ -d "$WORKTREE_PATH" ]]; then
  # Allow reuse if it's a zaofu worktree on the expected branch
  if (cd "$WORKTREE_PATH" && git rev-parse --show-toplevel >/dev/null 2>&1 && \
      git branch --show-current 2>/dev/null | grep -q "$BRANCH_NAME"); then
    ok "worktree_slot_reusable"
  else
    bad "worktree_slot" "$WORKTREE_PATH exists and is not a matching worktree"
  fi
else
  ok "worktree_slot_free"
fi

# ---- 7. Preset yaml exists ----
PRESET="examples/safe-team.yaml"
if [[ "$CONFIG_MODE" == "full" ]]; then
  bad "config_mode_full" "'full' mode requires W5E2E-T1 ship (critic + test_spec roles); not yet available. Run with CONFIG_MODE=mvp."
elif [[ -f "$PRESET" ]]; then
  ok "preset_yaml_exists ($PRESET)"
else
  bad "preset_yaml" "$PRESET not found"
fi

# ---- 8. Topology validation passes on chosen preset ----
if PYTHONPATH=src python3 -c "
from pathlib import Path
from zf.core.config.loader import load_config
from zf.core.workflow.topology import WorkflowTopology
from zf.runtime.wake_patterns import WAKE_PATTERNS, reactor_handler_events
config = load_config(Path('$PRESET'))
topology = WorkflowTopology.from_config(config)
report = topology.check(
    reactor_handlers=reactor_handler_events(),
    wake_patterns=set(WAKE_PATTERNS),
)
if report.unwoken_events:
    print(f'unwoken: {report.unwoken_events}')
    raise SystemExit(1)
" 2>/dev/null; then
  ok "topology_clean"
else
  bad "topology" "$PRESET has silent route breaks (handler ↔ wake gap)"
fi

echo
if [[ $fail -gt 0 ]]; then
  echo "Preflight FAILED ($fail checks). Fix above issues before running zf start."
  exit 1
fi

# ---- All green — materialize the workspace ----
echo "Preflight OK. Preparing $WORKTREE_PATH ..."

# Create worktree if missing
if [[ ! -d "$WORKTREE_PATH" ]]; then
  git worktree add "$WORKTREE_PATH" -b "$BRANCH_NAME" 2>/dev/null || {
    # Branch may already exist from prior run
    git worktree add "$WORKTREE_PATH" "$BRANCH_NAME" 2>/dev/null || {
      echo "ERROR: could not create worktree at $WORKTREE_PATH" >&2
      exit 1
    }
  }
  echo "  Created worktree at $WORKTREE_PATH (branch $BRANCH_NAME)"
fi

# Copy config
cp "$PRESET" "$WORKTREE_PATH/zf.yaml"

# Add W5-E2E-specific tweaks to zf.yaml:
#   session.tmux_session = zf-w5e2e
#   workflow.gan_rounds = 2 (P2/P4 GAN loop)
# Use Python for safe yaml edit (avoid sed sensitivity).
PYTHONPATH=src python3 <<PY
import yaml
from pathlib import Path
p = Path("$WORKTREE_PATH/zf.yaml")
data = yaml.safe_load(p.read_text())
data.setdefault("session", {})["tmux_session"] = "zf-w5e2e"
data.setdefault("project", {})["name"] = "zaofu-w5e2e"
data.setdefault("workflow", {})["gan_rounds"] = 2
p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
print(f"  Wrote {p} (tmux_session=zf-w5e2e, gan_rounds=2)")
PY

# Initialize .zf/ manually (avoid `zf init` which would overwrite zf.yaml)
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

cd "$REPO_ROOT"

echo
echo "W5-E2E baseline workspace ready at $WORKTREE_PATH"
echo "Next: follow docs/runbooks/w5-e2e-baseline.md to ignite."
