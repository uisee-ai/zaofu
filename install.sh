#!/usr/bin/env bash
# Source-checkout installer. Runtime and enrollment remain owned by zf.
set -euo pipefail
umask 077

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]
       ./install.sh remove [--yes] [--workspace-home PATH]
  --mode standalone|observed|cloud|remove
      standalone: Use ZaoFu locally (default); no Controller account required
      observed:   Enroll this node with a Controller and report authorized project telemetry
      cloud:      Request a cloud workspace in the Controller portal; no local installation
      remove:     Stop this installation and remove its runtime dependencies/builds
  --yes                        Non-interactive; accept removal, or default to standalone
  --workspace-home PATH        Workspace directory (default: ZF_WORKSPACE_HOME or ~/.zaofu)
  --workspace-config PATH      Deployment zf.yaml (default: <workspace-home>/controller/zf.yaml)
  --endpoint URL               Controller HTTPS origin
  --issuer URL                 Keycloak realm issuer
  --workspace-id ID            Approved external Controller workspace ID
  --project ID                 Authorized local registry project ID; repeatable
  --model-mode MODE            self_managed_cli (default)/self_managed_api/managed_cpa
  --code-file PATH             Owner-only (0600) enrollment code file; never pass the code itself
  --host HOST                  127.0.0.1 (default)/localhost/::1/0.0.0.0
  --port PORT                  Dashboard port (default: 8001)
  --no-start                   Install/configure/enroll without starting background processes
  --skip-deps                  Skip installation/build; still verify the existing Herdr protocol
  -h, --help                   Show this help

Installs missing uv, Node.js 22 and Herdr 0.9.5 in your user directory; no sudo or system services.
Requires bash, curl, git, tmux, tar and python3. Sign in to your provider CLI separately.
Managed installations cannot be downgraded here; use the Controller lifecycle.
Removal preserves source code, projects, workspace data, credentials and shared tools.
EOF
}
fail() { printf 'Operation failed: %s\n' "$*" >&2; exit 1; }
step() { printf '\n[ZaoFu] %s\n' "$*"; }
mark_service() {
  local service_id
  service_id="$(tmux list-sessions -F '#{session_id} #{session_name}' | awk -v name="$1" '$2 == name {print $1}')"
  [[ "$service_id" =~ ^\$[0-9]+$ ]] || fail 'Could not identify the newly started service'
  tmux set-option -t "$service_id" @zf_install_root "$root"
  tmux set-option -t "$service_id" @zf_workspace_home "$workspace_home"
}
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode='' yes=0 start=1 deps=1
workspace_home="${ZF_WORKSPACE_HOME:-$HOME/.zaofu}"
config='' endpoint='' issuer='' workspace_id='' code_file=''
model_mode='' host=127.0.0.1 port=8001
projects=()
while (($#)); do
  case "$1" in
    remove) mode=remove; shift ;;
    --mode) mode="${2:?Missing mode}"; shift 2 ;;
    --workspace-home) workspace_home="${2:?Missing path}"; shift 2 ;;
    --workspace-config) config="${2:?Missing path}"; shift 2 ;;
    --endpoint) endpoint="${2:?Missing URL}"; shift 2 ;;
    --issuer) issuer="${2:?Missing URL}"; shift 2 ;;
    --workspace-id) workspace_id="${2:?Missing ID}"; shift 2 ;;
    --project) projects+=(--project "${2:?Missing ID}"); shift 2 ;;
    --model-mode) model_mode="${2:?Missing model mode}"; shift 2 ;;
    --code-file) code_file="${2:?Missing path}"; shift 2 ;;
    --host) host="${2:?Missing host}"; shift 2 ;;
    --port) port="${2:?Missing port}"; shift 2 ;;
    --yes) yes=1; shift ;;
    --no-start) start=0; shift ;;
    --skip-deps) deps=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown option; run ./install.sh --help" ;;
  esac
done
if [[ -z "$mode" ]]; then
  if [[ "$yes" == 1 ]]; then
    mode=standalone
  elif [[ -t 0 ]]; then
    printf '1) Install standalone (default)\n2) Install and enroll with a Controller (observed)\n3) Use a cloud workspace\n4) Remove ZaoFu\n'
    read -r -p 'Select an option [1]: ' choice
    case "${choice:-1}" in
      1) mode=standalone ;; 2) mode=observed ;; 3) mode=cloud ;; 4) mode=remove ;;
      *) fail 'Invalid selection' ;;
    esac
  else
    fail 'Non-interactive operation requires --mode or --yes'
  fi
fi
case "$mode" in standalone|observed|cloud|remove) ;; *) fail 'Invalid mode' ;; esac
if [[ "$mode" == cloud ]]; then
  printf 'Sign in to the Controller portal and request a cloud workspace. The Controller provisions it after approval.\nNothing is installed or started on this node.\n'
  exit 0
fi
case "$host" in 127.0.0.1|localhost|::1|0.0.0.0) ;; *) fail 'Invalid host' ;; esac
[[ "$port" =~ ^[0-9]{1,5}$ ]] && ((10#$port > 0 && 10#$port <= 65535)) || fail 'Invalid port'
port=$((10#$port))
required_tools=(python3)
[[ "$mode" == remove ]] || required_tools+=(curl git tmux tar)
for tool in "${required_tools[@]}"; do
  command -v "$tool" >/dev/null || fail "Missing $tool; install system prerequisites and retry (Ubuntu: sudo apt install python3 curl git tmux tar)"
done
workspace_home="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().absolute())' "$workspace_home")"
config="${config:-$workspace_home/controller/zf.yaml}"
config="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().absolute())' "$config")"
# Refuse before dependency mutation, including damaged/missing managed pins.
for pin in "$workspace_home/controller-binding.json" "${ZF_WORKSPACE_HOME:-$HOME/.zaofu}/controller-binding.json" /etc/zaofu/zf.yaml; do
  [[ ! -e "$pin" && ! -L "$pin" ]] || fail 'Managed binding detected; use the zf controller lifecycle before changing this installation'
done
[[ -z "${ZF_CONTROLLER_WORKSPACE_CONFIG:-}${ZF_CONTROLLER_CLIENT_ID:-}${ZFC_WORKSPACE_ID:-}" ]] || fail 'Controller launch context detected; use the managed lifecycle entrypoint'
if [[ "$mode" == remove ]]; then
  [[ -z "$endpoint$issuer$workspace_id$model_mode$code_file" && ${#projects[@]} == 0 ]] || fail 'Enrollment options cannot be combined with removal'
  remove_python=python3
  [[ ! -x "$root/.venv/bin/python" ]] || remove_python="$root/.venv/bin/python"
  remove_args=(--workspace-home "$workspace_home" --workspace-config "$config")
  [[ "$yes" == 0 ]] || remove_args+=(--yes)
  exec "$remove_python" "$root/tools/remove_install.py" "${remove_args[@]}"
fi
if [[ "$mode" == standalone ]]; then
  [[ -z "$endpoint$issuer$workspace_id$model_mode$code_file" && ${#projects[@]} == 0 ]] || fail 'Controller options require --mode observed'
  [[ ! -e "$config" && ! -L "$config" ]] || fail 'Controller configuration exists; select observed, or use zf controller unenroll for release'
fi

export ZF_WORKSPACE_HOME="$workspace_home"
export PATH="$root/.venv/bin:$HOME/.local/bin:$HOME/.local/share/zaofu/node/bin:$PATH"
cd "$root"
scratch=''
cleanup() { [[ -z "$scratch" ]] || rm -rf -- "$scratch"; }
trap cleanup EXIT
if [[ "$deps" == 1 ]]; then
  scratch="$(mktemp -d "${TMPDIR:-/tmp}/zf-install-XXXXXXXX")"
  if ! command -v uv >/dev/null; then
    step 'Installing uv into ~/.local/bin'
    curl --proto '=https' --proto-redir '=https' -fsSL --retry 2 https://astral.sh/uv/install.sh -o "$scratch/uv.sh"
    UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh "$scratch/uv.sh"
  fi
  if ! command -v node >/dev/null || ! command -v npm >/dev/null || ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)'; then
    step 'Installing Node.js 22 into ~/.local/share/zaofu/node (official SHA256 verification)'
    case "$(uname -s)" in Linux) platform=linux ;; Darwin) platform=darwin ;; *) fail 'Automatic Node installation supports Linux/macOS only' ;; esac
    case "$(uname -m)" in x86_64|amd64) arch=x64 ;; aarch64|arm64) arch=arm64 ;; *) fail 'Automatic Node installation supports x64/arm64 only' ;; esac
    curl --proto '=https' --proto-redir '=https' -fsSL --retry 2 https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt -o "$scratch/SHASUMS256.txt"
    archive="$(awk -v suffix="-${platform}-${arch}.tar.gz" '$2 ~ (suffix "$") {print $2}' "$scratch/SHASUMS256.txt")"
    [[ "$archive" =~ ^node-v22\.[0-9]+\.[0-9]+-(linux|darwin)-(x64|arm64)\.tar\.gz$ ]] || fail 'Could not select a Node.js 22 release asset'
    version="${archive#node-}"; version="${version%%-$platform-*}"
    curl --proto '=https' --proto-redir '=https' -fsSL --retry 2 "https://nodejs.org/dist/$version/$archive" -o "$scratch/$archive"
    python3 - "$scratch" "$archive" <<'PY'
import hashlib, pathlib, sys
directory, name = pathlib.Path(sys.argv[1]), sys.argv[2]
expected = dict(line.split()[::-1] for line in (directory / 'SHASUMS256.txt').read_text().splitlines())[name]
if hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected:
    raise SystemExit('Node SHA256 verification failed')
PY
    node_home="$HOME/.local/share/zaofu/node"
    [[ ! -e "$node_home" && ! -L "$node_home" ]] || fail 'Existing user Node installation is unusable; repair it first. It will not be overwritten'
    mkdir -p "$(dirname "$node_home")"
    tar -xzf "$scratch/$archive" -C "$scratch"
    mv "$scratch/${archive%.tar.gz}" "$node_home"
    export PATH="$node_home/bin:$PATH"
  fi
  step 'Syncing locked Python dependencies'
  uv sync --locked --extra web --extra stream-json --extra controller
else
  [[ -x "$root/.venv/bin/zf" && -f "$root/web/dist/index.html" ]] || fail '--skip-deps requires an existing .venv and web/dist'
fi
command -v uv >/dev/null || fail 'uv was not found'
step 'Preparing Herdr for Web Terminal'
herdr_args=()
[[ "$deps" == 1 ]] || herdr_args+=(--check-only)
"$root/.venv/bin/python" tools/install_herdr.py "${herdr_args[@]}"
if [[ "$deps" == 1 ]]; then
  step 'Building the Dashboard'
  npm --prefix "$root/web" ci --no-audit --no-fund
  npm --prefix "$root/web" run build
fi
zf_cmd=("$root/.venv/bin/zf")
pending=0
if [[ "$mode" == observed ]]; then
  if [[ ! -e "$config" && -t 0 && "$yes" == 0 ]]; then
    [[ -n "$endpoint" ]] || read -r -p 'Controller HTTPS origin: ' endpoint
    [[ -n "$issuer" ]] || read -r -p 'Keycloak realm issuer: ' issuer
    [[ -n "$workspace_id" ]] || read -r -p 'Approved external workspace ID: ' workspace_id
    if [[ ${#projects[@]} == 0 ]]; then
      read -r -p 'Authorized registry project IDs (space-separated; empty for instance heartbeat only): ' project_input
      read -r -a project_ids <<< "$project_input"
      for project in "${project_ids[@]}"; do projects+=(--project "$project"); done
    fi
  fi
  "$root/.venv/bin/python" tools/install_config.py --path "$config" \
    --endpoint "$endpoint" --issuer "$issuer" --workspace-id "$workspace_id" \
    --model-mode "$model_mode" "${projects[@]}"
  step 'Installation public key (submit it to the Controller owner for an enrollment code)'
  "${zf_cmd[@]}" controller installation-key --workspace-config "$config"
  status_json="$("${zf_cmd[@]}" controller status --workspace-config "$config")"
  enrolled="$(printf '%s' "$status_json" | "$root/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["status"] != "unenrolled")')"
  if [[ "$enrolled" == False && -z "$code_file" && -t 0 && "$yes" == 0 ]]; then
    read -r -p 'Enrollment code file with mode 0600 (leave empty to enroll later): ' code_file
  fi
  if [[ -n "$code_file" && "$enrolled" == False ]]; then
    "${zf_cmd[@]}" controller enroll --workspace-config "$config" --code-file "$code_file"
  elif [[ "$enrolled" == False ]]; then
    pending=1
  fi
  if [[ "$pending" == 0 ]]; then
    step 'Verifying Controller synchronization acknowledgement'
    "${zf_cmd[@]}" controller sync --workspace-config "$config"
  fi
fi
if [[ "$pending" == 1 ]]; then
  printf '\nInstallation and public key are ready; enrollment is pending and services have not started.\nAfter Controller approval, retry with --mode observed --code-file /private/code --skip-deps and the same workspace-home/config.\n'
  exit 3
fi
if [[ "$start" == 0 ]]; then
  printf '\nInstallation complete; no background processes were started (--no-start).\n'
  exit 0
fi
step 'Starting the Workspace Dashboard'
connector_session="zf-controller-$port"
if [[ "$mode" == observed ]] && tmux has-session -t "=$connector_session" 2>/dev/null; then
  fail 'A Connector session already exists; inspect it before retrying. Existing processes will not be replaced'
fi
mkdir -p "$workspace_home"
launch_log="$workspace_home/install-web-${port}.log"
[[ ! -L "$launch_log" ]] || fail 'The launch log must not be a symlink'
: >> "$launch_log"; chmod 600 "$launch_log"
# The existing launcher prints a credential-bearing URL; retain it only in a private log.
if ! bash tools/start-webkanban.sh --workspace-home "$workspace_home" --workspace-only \
  --host "$host" --port "$port" --session "zf-web-$port" --no-build --no-restart >> "$launch_log" 2>&1; then
  fail "Dashboard startup failed (existing processes are preserved); inspect $launch_log"
fi
# Runtime ownership tags let removal distinguish our services from unrelated sessions.
mark_service "zf-web-$port"
probe_host=127.0.0.1
[[ "$host" != ::1 ]] || probe_host='[::1]'
ready=0
for ((attempt=0; attempt<30; attempt++)); do
  if tmux has-session -t "=zf-web-$port" 2>/dev/null && curl --noproxy '*' -fsS --max-time 2 "http://$probe_host:$port/api/workspace/projects" >/dev/null; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 ]] || fail "Dashboard health check failed; inspect $launch_log"
if [[ "$mode" == observed ]]; then
  connector_log="$workspace_home/controller-${port}.log"
  [[ ! -L "$connector_log" ]] || fail 'The Connector log must not be a symlink'
  : >> "$connector_log"; chmod 600 "$connector_log"
  printf -v connector_command '%q ' env "PATH=$PATH" "ZF_WORKSPACE_HOME=$workspace_home" \
    "${zf_cmd[@]}" controller sync --workspace-config "$config" --watch
  printf -v connector_redirect ' >> %q 2>&1' "$connector_log"
  tmux new-session -d -s "$connector_session" -c "$root" "$connector_command$connector_redirect"
  mark_service "$connector_session"
  sleep 1
  tmux has-session -t "=$connector_session" 2>/dev/null || fail "The Connector exited; the Dashboard is running. Inspect $connector_log"
fi
printf '\nDashboard ready: http://%s:%s/\nMode: %s\nWorkspace: %s\n' "$probe_host" "$port" "$mode" "$workspace_home"
printf 'The authenticated access link is stored in this private file: %s\n' "$launch_log"
printf 'Services run in user tmux sessions; rerun after a reboot. Install and sign in to your provider CLI separately.\n'
if [[ "$host" == 0.0.0.0 ]]; then printf 'Listening on all interfaces as requested. Use the node address and the private access link for remote access.\n'; fi
