#!/usr/bin/env bash
# Source-checkout installer. Runtime and enrollment remain owned by zf.
set -euo pipefail
umask 077

usage() {
  cat <<'EOF'
用法: ./install.sh [选项]
  --mode standalone|observed|cloud
      standalone: 本机独立使用（默认），不需要 Controller 账号
      observed:   本机注册到 Controller，上传已授权项目的观测数据
      cloud:      前往 Controller 申请云端实例，本机不安装
  --yes                        非交互，未指定 mode 时选择 standalone
  --workspace-home PATH        工作空间目录（默认 ZF_WORKSPACE_HOME 或 ~/.zaofu）
  --workspace-config PATH      部署 zf.yaml（默认 <workspace-home>/controller/zf.yaml）
  --endpoint URL               Controller HTTPS origin
  --issuer URL                 Keycloak realm issuer
  --workspace-id ID            Controller 已批准的外部 Workspace ID
  --project ID                 明确授权采集的本地 Registry Project ID，可重复
  --model-mode MODE            self_managed_cli（默认）/self_managed_api/managed_cpa
  --code-file PATH             已签发注册码的 0600 文件，不接收明文参数
  --host HOST                  127.0.0.1（默认）/localhost/::1/0.0.0.0
  --port PORT                  Dashboard 端口（默认 8001）
  --no-start                   只安装/配置/注册，不启动后台进程
  --skip-deps                  跳过依赖同步和 Web 构建，使用已有安装
  -h, --help                   帮助

自动在用户目录安装缺少的 uv、Node.js 22；不使用 sudo、不安装系统服务。
系统需先有 bash、curl、git、tmux、tar、python3。Provider CLI 登录由用户完成。
已有 managed 实例不能用此脚本降级；云端/全面托管沿用 Controller 生命周期。
EOF
}
fail() { printf '安装未完成: %s\n' "$*" >&2; exit 1; }
step() { printf '\n[ZaoFu] %s\n' "$*"; }
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode='' yes=0 start=1 deps=1
workspace_home="${ZF_WORKSPACE_HOME:-$HOME/.zaofu}"
config='' endpoint='' issuer='' workspace_id='' code_file=''
model_mode='' host=127.0.0.1 port=8001
projects=()
while (($#)); do
  case "$1" in
    --mode) mode="${2:?缺少 mode}"; shift 2 ;;
    --workspace-home) workspace_home="${2:?缺少路径}"; shift 2 ;;
    --workspace-config) config="${2:?缺少路径}"; shift 2 ;;
    --endpoint) endpoint="${2:?缺少 URL}"; shift 2 ;;
    --issuer) issuer="${2:?缺少 URL}"; shift 2 ;;
    --workspace-id) workspace_id="${2:?缺少 ID}"; shift 2 ;;
    --project) projects+=(--project "${2:?缺少 ID}"); shift 2 ;;
    --model-mode) model_mode="${2:?缺少模式}"; shift 2 ;;
    --code-file) code_file="${2:?缺少路径}"; shift 2 ;;
    --host) host="${2:?缺少 host}"; shift 2 ;;
    --port) port="${2:?缺少端口}"; shift 2 ;;
    --yes) yes=1; shift ;;
    --no-start) start=0; shift ;;
    --skip-deps) deps=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "未知选项；请运行 ./install.sh --help" ;;
  esac
done
if [[ -z "$mode" ]]; then
  if [[ "$yes" == 1 ]]; then
    mode=standalone
  elif [[ -t 0 ]]; then
    printf '1) 本机独立使用 standalone（默认）\n2) 本机接入 Controller（observed）\n3) 使用云端托管实例\n'
    read -r -p '请选择 [1]: ' choice
    case "${choice:-1}" in
      1) mode=standalone ;; 2) mode=observed ;; 3) mode=cloud ;;
      *) fail '无效选择' ;;
    esac
  else
    fail '非交互安装请指定 --mode 或 --yes'
  fi
fi
case "$mode" in standalone|observed|cloud) ;; *) fail '无效 mode' ;; esac
if [[ "$mode" == cloud ]]; then
  printf '请在 Controller 门户注册/登录，申请云端 Workspace；审核通过后由 Controller 创建实例。\n本机不安装，不启动实例。\n'
  exit 0
fi
case "$host" in 127.0.0.1|localhost|::1|0.0.0.0) ;; *) fail '无效 host' ;; esac
[[ "$port" =~ ^[0-9]{1,5}$ ]] && ((10#$port > 0 && 10#$port <= 65535)) || fail '无效端口'
port=$((10#$port))
for tool in python3 curl git tmux tar; do
  command -v "$tool" >/dev/null || fail "缺少 $tool；请用系统包管理器安装后重试（Ubuntu: sudo apt install python3 curl git tmux tar）"
done
workspace_home="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().absolute())' "$workspace_home")"
config="${config:-$workspace_home/controller/zf.yaml}"
config="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().absolute())' "$config")"
# Refuse before dependency mutation, including damaged/missing managed pins.
for pin in "$workspace_home/controller-binding.json" "${ZF_WORKSPACE_HOME:-$HOME/.zaofu}/controller-binding.json" /etc/zaofu/zf.yaml; do
  [[ ! -e "$pin" && ! -L "$pin" ]] || fail '发现已有托管绑定；请使用 zf controller 生命周期，不允许通过安装降级'
done
[[ -z "${ZF_CONTROLLER_WORKSPACE_CONFIG:-}${ZF_CONTROLLER_CLIENT_ID:-}${ZFC_WORKSPACE_ID:-}" ]] || fail '当前环境属于 Controller 启动上下文，请使用原托管入口'
if [[ "$mode" == standalone ]]; then
  [[ -z "$endpoint$issuer$workspace_id$model_mode$code_file" && ${#projects[@]} == 0 ]] || fail 'Controller 参数只能用于 --mode observed'
  [[ ! -e "$config" && ! -L "$config" ]] || fail '已有 Controller 配置；请选择 observed，退出纳管使用 zf controller unenroll'
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
    step '安装 uv 到 ~/.local/bin'
    curl --proto '=https' --proto-redir '=https' -fsSL --retry 2 https://astral.sh/uv/install.sh -o "$scratch/uv.sh"
    UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh "$scratch/uv.sh"
  fi
  if ! command -v node >/dev/null || ! command -v npm >/dev/null || ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)'; then
    step '安装 Node.js 22 到 ~/.local/share/zaofu/node（校验官方 SHA256）'
    case "$(uname -s)" in Linux) platform=linux ;; Darwin) platform=darwin ;; *) fail '自动 Node 安装仅支持 Linux/macOS' ;; esac
    case "$(uname -m)" in x86_64|amd64) arch=x64 ;; aarch64|arm64) arch=arm64 ;; *) fail '自动 Node 安装仅支持 x64/arm64' ;; esac
    curl --proto '=https' --proto-redir '=https' -fsSL --retry 2 https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt -o "$scratch/SHASUMS256.txt"
    archive="$(awk -v suffix="-${platform}-${arch}.tar.gz" '$2 ~ (suffix "$") {print $2}' "$scratch/SHASUMS256.txt")"
    [[ "$archive" =~ ^node-v22\.[0-9]+\.[0-9]+-(linux|darwin)-(x64|arm64)\.tar\.gz$ ]] || fail '无法确定 Node 22 下载文件'
    version="${archive#node-}"; version="${version%%-$platform-*}"
    curl --proto '=https' --proto-redir '=https' -fsSL --retry 2 "https://nodejs.org/dist/$version/$archive" -o "$scratch/$archive"
    python3 - "$scratch" "$archive" <<'PY'
import hashlib, pathlib, sys
directory, name = pathlib.Path(sys.argv[1]), sys.argv[2]
expected = dict(line.split()[::-1] for line in (directory / 'SHASUMS256.txt').read_text().splitlines())[name]
if hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected:
    raise SystemExit('Node SHA256 校验失败')
PY
    node_home="$HOME/.local/share/zaofu/node"
    [[ ! -e "$node_home" && ! -L "$node_home" ]] || fail '已有用户 Node 安装不可用，请修复后重试；不会覆盖'
    mkdir -p "$(dirname "$node_home")"
    tar -xzf "$scratch/$archive" -C "$scratch"
    mv "$scratch/${archive%.tar.gz}" "$node_home"
    export PATH="$node_home/bin:$PATH"
  fi
  step '同步锁定的 Python 依赖并构建 Dashboard'
  uv sync --locked --extra web --extra stream-json --extra controller
  npm --prefix "$root/web" ci --no-audit --no-fund
  npm --prefix "$root/web" run build
else
  [[ -x "$root/.venv/bin/zf" && -f "$root/web/dist/index.html" ]] || fail '--skip-deps 需要已有 .venv 和 web/dist'
fi
command -v uv >/dev/null || fail '找不到 uv'
zf_cmd=("$root/.venv/bin/zf")
pending=0
if [[ "$mode" == observed ]]; then
  if [[ ! -e "$config" && -t 0 && "$yes" == 0 ]]; then
    [[ -n "$endpoint" ]] || read -r -p 'Controller HTTPS origin: ' endpoint
    [[ -n "$issuer" ]] || read -r -p 'Keycloak realm issuer: ' issuer
    [[ -n "$workspace_id" ]] || read -r -p '已批准的外部 Workspace ID: ' workspace_id
    if [[ ${#projects[@]} == 0 ]]; then
      read -r -p '授权采集的 Registry Project ID（空格分隔；留空仅实例心跳）: ' project_input
      read -r -a project_ids <<< "$project_input"
      for project in "${project_ids[@]}"; do projects+=(--project "$project"); done
    fi
  fi
  "$root/.venv/bin/python" tools/install_config.py --path "$config" \
    --endpoint "$endpoint" --issuer "$issuer" --workspace-id "$workspace_id" \
    --model-mode "$model_mode" "${projects[@]}"
  step '安装公钥（可提交给 Controller 所有者签发注册码）'
  "${zf_cmd[@]}" controller installation-key --workspace-config "$config"
  status_json="$("${zf_cmd[@]}" controller status --workspace-config "$config")"
  enrolled="$(printf '%s' "$status_json" | "$root/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["status"] != "unenrolled")')"
  if [[ "$enrolled" == False && -z "$code_file" && -t 0 && "$yes" == 0 ]]; then
    read -r -p '注册码 0600 文件路径（留空稍后注册）: ' code_file
  fi
  if [[ -n "$code_file" && "$enrolled" == False ]]; then
    "${zf_cmd[@]}" controller enroll --workspace-config "$config" --code-file "$code_file"
  elif [[ "$enrolled" == False ]]; then
    pending=1
  fi
  if [[ "$pending" == 0 ]]; then
    step '验证 Controller 同步确认'
    "${zf_cmd[@]}" controller sync --workspace-config "$config"
  fi
fi
if [[ "$pending" == 1 ]]; then
  printf '\n安装和公钥准备完成，但尚未注册、尚未启动。\n在 Controller 批准后使用 --mode observed --code-file /private/code --skip-deps 重试；保留相同 workspace-home/config。\n'
  exit 3
fi
if [[ "$start" == 0 ]]; then
  printf '\n安装完成；按 --no-start 未启动后台进程。\n'
  exit 0
fi
step '启动 Workspace Dashboard'
connector_session="zf-controller-$port"
if [[ "$mode" == observed ]] && tmux has-session -t "=$connector_session" 2>/dev/null; then
  fail '已有 Connector 会话，请检查后重试；不会替换已有进程'
fi
mkdir -p "$workspace_home"
launch_log="$workspace_home/install-web-${port}.log"
[[ ! -L "$launch_log" ]] || fail '启动日志不允许符号链接'
: >> "$launch_log"; chmod 600 "$launch_log"
# The existing launcher prints a credential-bearing URL; retain it only in a private log.
if ! bash tools/start-webkanban.sh --workspace-home "$workspace_home" --workspace-only \
  --host "$host" --port "$port" --session "zf-web-$port" --no-build --no-restart >> "$launch_log" 2>&1; then
  fail "Dashboard 未启动（不会替换已有进程）；检查 $launch_log"
fi
probe_host=127.0.0.1
[[ "$host" != ::1 ]] || probe_host='[::1]'
ready=0
for ((attempt=0; attempt<30; attempt++)); do
  if tmux has-session -t "=zf-web-$port" 2>/dev/null && curl --noproxy '*' -fsS --max-time 2 "http://$probe_host:$port/api/workspace/projects" >/dev/null; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 ]] || fail "Dashboard 健康检查失败；检查 $launch_log"
if [[ "$mode" == observed ]]; then
  connector_log="$workspace_home/controller-${port}.log"
  [[ ! -L "$connector_log" ]] || fail 'Connector 日志不允许符号链接'
  : >> "$connector_log"; chmod 600 "$connector_log"
  printf -v connector_command '%q ' env "PATH=$PATH" "ZF_WORKSPACE_HOME=$workspace_home" \
    "${zf_cmd[@]}" controller sync --workspace-config "$config" --watch
  printf -v connector_redirect ' >> %q 2>&1' "$connector_log"
  tmux new-session -d -s "$connector_session" -c "$root" "$connector_command$connector_redirect"
  sleep 1
  tmux has-session -t "=$connector_session" 2>/dev/null || fail "Connector 进程已退出；Dashboard 已启动，检查 $connector_log"
fi
printf '\nDashboard 就绪: http://%s:%s/\n模式: %s\n工作空间: %s\n' "$probe_host" "$port" "$mode" "$workspace_home"
printf '写操作访问链接保存在私有文件: %s\n' "$launch_log"
printf '这是用户 tmux 会话；重启机器后需重新运行。Provider CLI 请在本机自行安装并登录。\n'
if [[ "$host" == 0.0.0.0 ]]; then printf '已显式监听所有网卡；远程访问使用节点地址和上面的私有访问链接。\n'; fi
