#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  tools/start-webkanban.sh [options]

Options:
  --host HOST             Bind host (default: 0.0.0.0)
  --port PORT             Bind port (default: 8001)
  --session NAME          tmux session name (default: zf-web-<port>)
  --workspace-home PATH   Workspace registry root (default: ~/.zaofu)
  --token TOKEN           Web action token. If omitted, read .env, then reuse/create token file.
  --token-file PATH       Token file (default: <workspace-home>/web-action-token)
  --provider-token-envs N  Comma/space-separated provider token env names to load/pass.
                           Defaults to registry token_env values plus OPENCLAW_GATEWAY_TOKEN.
  --workspace-only        Start Web shell without binding default Project.
  --no-build              Skip npm web build.
  --no-restart            Fail if tmux session or port is already in use.
  --stop                  Stop the tmux session and exit.
  --status                Print session, port, token file, and API status.
  -h, --help              Show this help.

Configuration:
  ZF_WEB_HOST, ZF_WEB_PORT, ZF_WEB_TMUX_SESSION, ZF_WORKSPACE_HOME,
  repo .env: ZF_WORKSPACE_HOME, ZF_WEB_ACTION_TOKEN
  env vars: ZF_WEB_ACTION_TOKEN, ZF_WEB_ACTION_TOKEN_FILE, ZF_WEB_NO_BUILD=1,
  ZF_WEB_NO_RESTART=1, ZF_WEB_WORKSPACE_ONLY=1, ZF_WEB_PROVIDER_TOKEN_ENVS
  Codex headless env: ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX,
  ZF_KANBAN_AGENT_CODEX_HEADLESS_APPROVAL_POLICY

Notes:
  Workspace env defaults are loaded from <workspace-home>/env when present.
  The script loads requested provider token envs from the current environment
  and from an interactive bash environment snapshot. This supports tokens placed
  after the non-interactive return guard in ~/.bashrc, such as
  OPENCLAW_GATEWAY_TOKEN.
  For this trusted local WebKanban launcher, Codex headless defaults to
  sandbox=danger-full-access and approval_policy=never because some Linux hosts
  do not support Codex workspace-write sandbox namespaces. Override the env vars
  above to restore read-only/workspace-write sandboxing on a fixed host.
EOF
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
host="${ZF_WEB_HOST:-0.0.0.0}"
port="${ZF_WEB_PORT:-8001}"
workspace_home="${ZF_WORKSPACE_HOME:-$HOME/.zaofu}"
workspace_home_cli=0
session="${ZF_WEB_TMUX_SESSION:-}"
token="${ZF_WEB_ACTION_TOKEN:-}"
token_file="${ZF_WEB_ACTION_TOKEN_FILE:-}"
provider_token_envs="${ZF_WEB_PROVIDER_TOKEN_ENVS:-}"
codex_headless_sandbox="${ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX:-}"
codex_headless_approval_policy="${ZF_KANBAN_AGENT_CODEX_HEADLESS_APPROVAL_POLICY:-}"
build=1
restart=1
workspace_only="${ZF_WEB_WORKSPACE_ONLY:-0}"
mode="start"

if [[ "${ZF_WEB_NO_BUILD:-0}" == "1" ]]; then
  build=0
fi
if [[ "${ZF_WEB_NO_RESTART:-0}" == "1" ]]; then
  restart=0
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      host="${2:?--host requires a value}"
      shift 2
      ;;
    --port)
      port="${2:?--port requires a value}"
      shift 2
      ;;
    --session)
      session="${2:?--session requires a value}"
      shift 2
      ;;
    --workspace-home)
      workspace_home="${2:?--workspace-home requires a value}"
      workspace_home_cli=1
      shift 2
      ;;
    --token)
      token="${2:?--token requires a value}"
      shift 2
      ;;
    --token-file)
      token_file="${2:?--token-file requires a value}"
      shift 2
      ;;
    --provider-token-envs)
      provider_token_envs="${2:?--provider-token-envs requires a value}"
      shift 2
      ;;
    --workspace-only)
      workspace_only=1
      shift
      ;;
    --no-build)
      build=0
      shift
      ;;
    --no-restart)
      restart=0
      shift
      ;;
    --stop)
      mode="stop"
      shift
      ;;
    --status)
      mode="status"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

dotenv_value() {
  local key="$1"
  local env_file="$repo_root/.env"
  local line value
  [[ -f "$env_file" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -n "$line" && "$line" != \#* ]] || continue
    if [[ "$line" == export\ * ]]; then
      line="${line#export }"
      line="${line#"${line%%[![:space:]]*}"}"
    fi
    [[ "$line" == "$key="* ]] || continue
    value="${line#*=}"
    value="${value%$'\r'}"
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi
    printf '%s' "$value"
    return 0
  done < "$env_file"
  return 1
}

workspace_provider_token_envs() {
  python3 - "$workspace_home" <<'PY'
import json
import pathlib
import sys

workspace_home = pathlib.Path(sys.argv[1]).expanduser()
path = workspace_home / "workspaces" / "default" / "providers.json"
names = []
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    data = {}

providers = data.get("providers") if isinstance(data, dict) else {}
openclaw = providers.get("openclaw") if isinstance(providers, dict) else {}
bindings = openclaw.get("bindings") if isinstance(openclaw, dict) else {}
if isinstance(bindings, dict):
    for binding in bindings.values():
        if not isinstance(binding, dict):
            continue
        token_env = str(binding.get("token_env") or "").strip()
        if token_env:
            names.append(token_env)
print(" ".join(names))
PY
}

split_env_names() {
  local raw="$1"
  raw="${raw//,/ }"
  raw="${raw//$'\n'/ }"
  printf '%s\n' $raw
}

unique_env_names() {
  local name
  awk '!seen[$0]++ && $0 ~ /^[A-Za-z_][A-Za-z0-9_]*$/ { print }'
}

load_interactive_shell_env() {
  local names=("$@")
  local snapshot line key
  [[ ${#names[@]} -gt 0 ]] || return 0
  snapshot="$(
    bash -ic 'for key in "$@"; do value="${!key-}"; if [[ -n "$value" ]]; then printf "%s=%q\n" "$key" "$value"; fi; done' \
      _ "${names[@]}" 2>/dev/null || true
  )"
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -n "$line" && "$line" == *=* ]] || continue
    key="${line%%=*}"
    [[ -z "${!key:-}" ]] || continue
    eval "export $line"
  done <<< "$snapshot"
}

first_nonempty_env_or_dotenv() {
  local key="$1"
  local current="${!key:-}"
  if [[ -n "$current" ]]; then
    printf '%s' "$current"
    return 0
  fi
  dotenv_value "$key" || true
}

shell_quote() {
  python3 - "$1" <<'PY'
import shlex
import sys
print(shlex.quote(sys.argv[1]))
PY
}

if [[ "$workspace_home_cli" != "1" && -z "${ZF_WORKSPACE_HOME:-}" ]]; then
  workspace_home="$(dotenv_value ZF_WORKSPACE_HOME || printf '%s' "$workspace_home")"
fi

session="${session:-zf-web-${port}}"
workspace_home="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser())' "$workspace_home")"
token_file="${token_file:-$workspace_home/web-action-token}"
log_file="/tmp/${session}.log"
runtime_env_file="$workspace_home/webkanban-${port}.env"
workspace_env_file="$workspace_home/env"

if [[ -f "$workspace_env_file" ]]; then
  # shellcheck source=/dev/null
  . "$workspace_env_file"
fi

mapfile -t provider_token_names < <(
  {
    split_env_names "OPENCLAW_GATEWAY_TOKEN"
    split_env_names "$(workspace_provider_token_envs)"
    split_env_names "$provider_token_envs"
  } | unique_env_names
)
load_interactive_shell_env \
  ZF_WEB_ACTION_TOKEN \
  ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX \
  ZF_KANBAN_AGENT_CODEX_HEADLESS_APPROVAL_POLICY \
  "${provider_token_names[@]}"
if [[ -z "$token" ]]; then
  token="${ZF_WEB_ACTION_TOKEN:-}"
fi
if [[ -z "$codex_headless_sandbox" ]]; then
  codex_headless_sandbox="$(first_nonempty_env_or_dotenv ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX)"
fi
if [[ -z "$codex_headless_approval_policy" ]]; then
  codex_headless_approval_policy="$(first_nonempty_env_or_dotenv ZF_KANBAN_AGENT_CODEX_HEADLESS_APPROVAL_POLICY)"
fi
codex_headless_sandbox="${codex_headless_sandbox:-danger-full-access}"
codex_headless_approval_policy="${codex_headless_approval_policy:-never}"

port_in_use() {
  ss -ltn "sport = :${port}" 2>/dev/null | grep -q LISTEN
}

print_status() {
  echo "repo: $repo_root"
  echo "workspace_home: $workspace_home"
  echo "token_file: $token_file"
  echo "workspace_env_file: $workspace_env_file"
  echo "session: $session"
  echo "url: http://${host}:${port}/"
  echo "log: $log_file"
  echo "runtime_env_file: $runtime_env_file"
  echo "codex_headless_sandbox: $codex_headless_sandbox"
  echo "codex_headless_approval_policy: $codex_headless_approval_policy"
  if tmux has-session -t "$session" 2>/dev/null; then
    echo "tmux: running"
  else
    echo "tmux: stopped"
  fi
  if port_in_use; then
    echo "port: listening"
  else
    echo "port: free"
  fi
  if curl -fsS "http://127.0.0.1:${port}/api/workspace/projects" >/dev/null 2>&1; then
    echo "api: ok"
  else
    echo "api: unavailable"
  fi
  local name loaded=()
  for name in "${provider_token_names[@]}"; do
    if [[ -n "${!name:-}" ]]; then
      loaded+=("$name")
    fi
  done
  if [[ ${#loaded[@]} -gt 0 ]]; then
    echo "provider_token_envs_loaded: ${loaded[*]}"
  else
    echo "provider_token_envs_loaded: -"
  fi
}

if [[ "$mode" == "stop" ]]; then
  tmux kill-session -t "$session" 2>/dev/null || true
  echo "stopped tmux session: $session"
  exit 0
fi

if [[ "$mode" == "status" ]]; then
  print_status
  exit 0
fi

mkdir -p "$workspace_home"
if [[ -z "$token" ]]; then
  token="$(dotenv_value ZF_WEB_ACTION_TOKEN || true)"
fi
if [[ -z "$token" ]]; then
  if [[ -s "$token_file" ]]; then
    token="$(tr -d '[:space:]' < "$token_file")"
  else
    umask 077
    token="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(16))
PY
)"
    printf '%s\n' "$token" > "$token_file"
  fi
fi
umask 077
printf '%s\n' "$token" > "$token_file"
chmod 600 "$token_file"

if tmux has-session -t "$session" 2>/dev/null; then
  if [[ "$restart" == "1" ]]; then
    tmux kill-session -t "$session"
    for _ in $(seq 1 20); do
      if ! port_in_use; then
        break
      fi
      sleep 0.2
    done
  else
    echo "error: tmux session already exists: $session" >&2
    exit 2
  fi
fi

if port_in_use; then
  if [[ "$restart" != "1" ]]; then
    echo "error: port is already in use: $port" >&2
    exit 2
  fi
  echo "error: port $port is in use by a process outside tmux session $session" >&2
  echo "       stop it first or pass --port/--session for another instance" >&2
  exit 2
fi

if [[ "$build" == "1" ]]; then
  npm --prefix "$repo_root/web" run build
fi

web_args=(web --host "$host" --port "$port")
if [[ "$workspace_only" == "1" ]]; then
  web_args+=(--workspace-only)
fi

launch_env=(
  ZF_WORKSPACE_HOME="$workspace_home"
  ZF_WEB_ACTION_TOKEN="$token"
  ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX="$codex_headless_sandbox"
  ZF_KANBAN_AGENT_CODEX_HEADLESS_APPROVAL_POLICY="$codex_headless_approval_policy"
)
for name in "${provider_token_names[@]}"; do
  if [[ -n "${!name:-}" ]]; then
    launch_env+=("$name=${!name}")
  fi
done

umask 077
: > "$runtime_env_file"
for item in "${launch_env[@]}"; do
  key="${item%%=*}"
  value="${item#*=}"
  printf 'export %s=%s\n' "$key" "$(shell_quote "$value")" >> "$runtime_env_file"
done

tmux new-session -d -s "$session" -c "$repo_root" \
  ". $(shell_quote "$runtime_env_file"); uv run --extra web zf ${web_args[*]} 2>&1 | tee $(shell_quote "$log_file")"

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${port}/api/workspace/projects" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

print_status
echo "action_token: $token"
