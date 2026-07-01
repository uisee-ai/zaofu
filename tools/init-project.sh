#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
zaofu_root="$(cd "$script_dir/.." && pwd)"

project_dir=""
source_config=""
preset=""
workspace="default"
workspace_register=1
state_dir_arg=""
force_init=0
dry_run=0
yes=0
git_policy="auto"
initial_commit_message="chore: initialize project for ZaoFu"
run_start_dry_run=1
run_validate=1

usage() {
  cat <<'EOF'
Usage:
  tools/init-project.sh --project-dir PATH [options]

Purpose:
  Bootstrap a project so it is ready for ZaoFu runtime startup:
  - materialize zf.yaml from --source-config or --preset
  - initialize runtime state with zf init
  - generate AGENTS.md and CLAUDE.md if missing
  - keep project.state_dir out of git
  - ensure git repo + HEAD when worktree mode is enabled
  - validate config/instructions and optionally run zf start --dry-run

Options:
  --project-dir PATH          Target project directory. Created if missing.
  --source-config PATH        Copy this config to <project>/zf.yaml if missing.
  --preset NAME              Generate zf.yaml through `zf init --preset NAME`.
  --workspace NAME           Workspace registry name. Default: default.
  --no-workspace-register    Do not register into the workspace registry.
  --state-dir PATH           Explicit runtime state dir passed to zf init.
  --force-init               Re-initialize runtime state if it already exists.
  --git-policy MODE          auto|always|require|skip. Default: auto.
                              auto ensures git only when worktree mode needs it.
  --initial-commit-message M Commit message if a new HEAD must be created.
  --yes                      Non-interactive approval for git init/commit.
  --skip-start-dry-run       Skip final `zf start --dry-run --no-watch`.
  --skip-validate            Skip validation commands.
  --dry-run                  Print actions without changing files.
  -h, --help                 Show this help.

Notes:
  Existing AGENTS.md, CLAUDE.md, and zf.yaml are not overwritten.
  Runtime state remains under project.state_dir and is not committed.
EOF
}

log() {
  printf '[zf-init-project] %s\n' "$*"
}

fail() {
  printf '[zf-init-project] ERROR: %s\n' "$*" >&2
  exit 1
}

run() {
  if [[ "$dry_run" == "1" ]]; then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

run_in_project() {
  if [[ "$dry_run" == "1" ]]; then
    printf '+ cd %q &&' "$project_dir"
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  (cd "$project_dir" && "$@")
}

run_zf() {
  run_in_project env "PYTHONPATH=$zaofu_root/src${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m zf.cli.main "$@"
}

abs_path() {
  python3 - "$1" <<'PY'
import sys
from pathlib import Path
print(Path(sys.argv[1]).expanduser().resolve())
PY
}

confirm_or_fail() {
  local message="$1"
  if [[ "$yes" == "1" ]]; then
    return 0
  fi
  if [[ ! -t 0 ]]; then
    fail "$message Pass --yes to allow this in non-interactive mode."
  fi
  printf '%s [y/N] ' "$message"
  local answer
  read -r answer
  case "$answer" in
    y|Y|yes|YES) return 0 ;;
    *) fail "operator declined: $message" ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-dir)
      project_dir="${2:?--project-dir requires a value}"
      shift 2
      ;;
    --source-config)
      source_config="${2:?--source-config requires a value}"
      shift 2
      ;;
    --preset)
      preset="${2:?--preset requires a value}"
      shift 2
      ;;
    --workspace)
      workspace="${2:?--workspace requires a value}"
      shift 2
      ;;
    --no-workspace-register)
      workspace_register=0
      shift
      ;;
    --state-dir)
      state_dir_arg="${2:?--state-dir requires a value}"
      shift 2
      ;;
    --force-init)
      force_init=1
      shift
      ;;
    --git-policy)
      git_policy="${2:?--git-policy requires a value}"
      shift 2
      ;;
    --initial-commit-message)
      initial_commit_message="${2:?--initial-commit-message requires a value}"
      shift 2
      ;;
    --yes)
      yes=1
      shift
      ;;
    --skip-start-dry-run)
      run_start_dry_run=0
      shift
      ;;
    --skip-validate)
      run_validate=0
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -z "$project_dir" ]]; then
        project_dir="$1"
        shift
      else
        fail "unknown argument: $1"
      fi
      ;;
  esac
done

[[ -n "$project_dir" ]] || fail "--project-dir is required"
case "$git_policy" in
  auto|always|require|skip) ;;
  *) fail "--git-policy must be one of: auto, always, require, skip" ;;
esac
if [[ -n "$source_config" && -n "$preset" ]]; then
  fail "--source-config and --preset are mutually exclusive"
fi

project_dir="$(abs_path "$project_dir")"
[[ -z "$source_config" ]] || source_config="$(abs_path "$source_config")"

log "project: $project_dir"
run mkdir -p "$project_dir"

if [[ -n "$source_config" ]]; then
  [[ -f "$source_config" ]] || fail "source config not found: $source_config"
  if [[ -e "$project_dir/zf.yaml" ]]; then
    if cmp -s "$source_config" "$project_dir/zf.yaml"; then
      log "zf.yaml already matches source config"
    else
      fail "$project_dir/zf.yaml already exists; refusing to overwrite"
    fi
  else
    log "copying source config to zf.yaml"
    run cp "$source_config" "$project_dir/zf.yaml"
  fi
fi

if [[ ! -e "$project_dir/zf.yaml" && -z "$preset" && ! ( "$dry_run" == "1" && -n "$source_config" ) ]]; then
  fail "zf.yaml not found. Provide --source-config PATH or --preset NAME."
fi

resolve_state_dir() {
  if [[ -n "$state_dir_arg" ]]; then
    abs_path "$state_dir_arg"
    return 0
  fi
  if [[ -e "$project_dir/zf.yaml" ]]; then
    run_in_project env "PYTHONPATH=$zaofu_root/src${PYTHONPATH:+:$PYTHONPATH}" \
      python3 - <<'PY'
from pathlib import Path
from zf.core.config.project_context import resolve_project_context
ctx = resolve_project_context(cwd=Path.cwd(), load_config_with_explicit=True)
print(ctx.state_dir)
PY
    return 0
  fi
  printf '%s\n' "$project_dir/.zf"
}

is_initialized_state_dir() {
  local state_dir="$1"
  [[ -f "$state_dir/events.jsonl" && -f "$state_dir/session.yaml" && -f "$state_dir/kanban.json" ]]
}

init_args=(init --workspace "$workspace")
if [[ "$workspace_register" == "1" ]]; then
  init_args+=(--workspace-register)
else
  init_args+=(--no-workspace-register)
fi
if [[ -n "$state_dir_arg" ]]; then
  init_args+=(--state-dir "$state_dir_arg")
fi
if [[ -n "$preset" ]]; then
  init_args+=(--preset "$preset")
fi
if [[ "$force_init" == "1" ]]; then
  init_args+=(--force)
fi

state_dir="$(resolve_state_dir)"
if [[ "$force_init" != "1" && -n "$state_dir" && -d "$state_dir" ]] && is_initialized_state_dir "$state_dir"; then
  log "runtime state already initialized: $state_dir"
else
  log "running zf init"
  run_zf "${init_args[@]}"
fi

if [[ "$dry_run" == "1" && ! -e "$project_dir/zf.yaml" ]]; then
  log "dry-run stops before generated zf.yaml exists; rerun without --dry-run to apply"
  exit 0
fi

[[ -e "$project_dir/zf.yaml" ]] || fail "zf.yaml was not created"
state_dir="$(resolve_state_dir)"

ensure_state_dir_ignored() {
  local pattern
  pattern="$(python3 - "$project_dir" "$state_dir" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1]).resolve()
state = Path(sys.argv[2]).resolve()
try:
    rel = state.relative_to(root)
except ValueError:
    raise SystemExit(0)
text = rel.as_posix().rstrip("/")
if text:
    print(text + "/")
PY
)"
  [[ -n "$pattern" ]] || return 0
  local gitignore="$project_dir/.gitignore"
  if [[ -f "$gitignore" ]] && grep -Fxq "$pattern" "$gitignore"; then
    log "$pattern already present in .gitignore"
    return 0
  fi
  log "adding $pattern to .gitignore"
  if [[ "$dry_run" == "1" ]]; then
    printf '+ append %q to %q\n' "$pattern" "$gitignore"
    return 0
  fi
  {
    if [[ -s "$gitignore" ]]; then
      printf '\n'
    fi
    printf '# ZaoFu runtime state\n%s\n' "$pattern"
  } >> "$gitignore"
}

ensure_agents_md() {
  local path="$project_dir/AGENTS.md"
  if [[ ! -e "$path" ]]; then
    log "creating AGENTS.md shell"
    if [[ "$dry_run" == "1" ]]; then
      printf '+ create %q\n' "$path"
    else
      printf '# AGENTS.md\n\nProject-specific agent rules live above the ZaoFu managed block.\n' > "$path"
    fi
  fi
  run_zf update agents-md --write
}

ensure_claude_md() {
  local path="$project_dir/CLAUDE.md"
  if [[ -e "$path" ]]; then
    log "CLAUDE.md already exists"
    return 0
  fi
  log "creating CLAUDE.md"
  if [[ "$dry_run" == "1" ]]; then
    printf '+ create %q\n' "$path"
    return 0
  fi
  cat > "$path" <<'EOF'
# CLAUDE.md

本项目使用 ZaoFu 作为本地 multi-agent harness。

- 开始工作前先阅读 `AGENTS.md`。
- `zf.yaml` 是唯一控制面配置。
- `project.state_dir` 下的运行态文件由 ZaoFu 管理,不要当作源代码。
- 状态变更通过 `zf` CLI 或事件流完成,不要直接写 runtime truth 文件。
- 交付前运行项目约定的测试;无法运行时在报告里说明阻塞项。
EOF
}

config_workdir_mode() {
  run_in_project env "PYTHONPATH=$zaofu_root/src${PYTHONPATH:+:$PYTHONPATH}" \
    python3 - <<'PY'
from pathlib import Path
from zf.core.config.project_context import resolve_project_context
ctx = resolve_project_context(cwd=Path.cwd(), load_config_with_explicit=True)
cfg = ctx.config
if cfg is None:
    print("false dry-run")
else:
    workdirs = cfg.runtime.workdirs
    print(f"{str(bool(workdirs.enabled)).lower()} {workdirs.mode}")
PY
}

is_git_repo() {
  git -C "$project_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1
}

has_git_head() {
  git -C "$project_dir" rev-parse --verify HEAD >/dev/null 2>&1
}

ensure_git_ready() {
  local enabled mode need_git=0
  read -r enabled mode < <(config_workdir_mode)
  if [[ "$git_policy" == "always" || "$git_policy" == "require" ]]; then
    need_git=1
  elif [[ "$git_policy" == "auto" && "$enabled" == "true" && "$mode" == "worktree" ]]; then
    need_git=1
  fi
  if [[ "$git_policy" == "skip" || "$need_git" == "0" ]]; then
    log "git readiness skipped (policy=$git_policy, workdirs=$enabled/$mode)"
    return 0
  fi

  if ! command -v git >/dev/null 2>&1; then
    fail "git is required for policy=$git_policy"
  fi

  if [[ "$dry_run" == "1" ]]; then
    if ! is_git_repo; then
      log "would initialize git repository"
    fi
    if ! has_git_head; then
      log "would create initial git commit"
    fi
    return 0
  fi

  if ! is_git_repo; then
    if [[ "$git_policy" == "require" ]]; then
      fail "project is not a git repository"
    fi
    confirm_or_fail "Project is not a git repository; initialize git and create HEAD?"
    log "initializing git repository"
    if ! run git -C "$project_dir" init -q -b main; then
      run git -C "$project_dir" init -q
    fi
  fi

  if ! has_git_head; then
    if [[ "$git_policy" == "require" ]]; then
      fail "git repository has no HEAD commit"
    fi
    confirm_or_fail "Git repository has no HEAD; create an initial commit?"
    log "creating initial git commit"
    run git -C "$project_dir" add -A
    if git -C "$project_dir" diff --cached --quiet; then
      run git -C "$project_dir" \
        -c user.name="ZaoFu Bootstrap" \
        -c user.email="zaofu-bootstrap@example.invalid" \
        commit --allow-empty -q -m "$initial_commit_message"
    else
      run git -C "$project_dir" \
        -c user.name="ZaoFu Bootstrap" \
        -c user.email="zaofu-bootstrap@example.invalid" \
        commit -q -m "$initial_commit_message"
    fi
  fi

  log "git repository has HEAD: $(git -C "$project_dir" rev-parse --short HEAD)"
}

ensure_state_dir_ignored
ensure_agents_md
ensure_claude_md
ensure_git_ready

if [[ "$run_validate" == "1" ]]; then
  log "validating generated project"
  run_zf update agents-md --check
  run_zf validate --instructions
  run_zf validate
fi

if [[ "$run_start_dry_run" == "1" ]]; then
  log "running startup dry-run"
  run_zf start --dry-run --no-watch
fi

log "done"
log "project_dir=$project_dir"
log "state_dir=$state_dir"
