#!/usr/bin/env bash
# run_zf_scoped.sh —— 把一次 zf flow 跑进「独立的 systemd --user 瞬态服务」。
#
# 解决什么(对应两次"全没了"):
#   1) 与登录会话解耦:作为 user 瞬态 service 跑,配合已开的 linger,
#      你 SSH 断了 / keepalive 超时,这个 run 照跑,不会被当"登录会话残留"清掉,
#      回来 `tmux attach -t zf-rf4` 续看,或 `systemctl --user status zf-run-rf4`。
#   2) 干净收尾:停的时候自动走 zf_run_teardown.sh(留痕 + 只杀本 run 的
#      tmux session),绝不全局 pkill,不会误伤别的 flow。
#   3) 一个清晰把手:整 run 在 zf-run-<tag>.service / zf-run-<tag>.slice 下,
#      `systemctl --user stop zf-run-<tag>` 就能干净停,不用 pkill 满天飞。
#
# 用法:
#   ENVFILE=.rf4.env ./run_zf_scoped.sh            # 启动一个 flow
#   ENVFILE=.rf4.env ./run_zf_scoped.sh --stop     # 优雅停(留痕+只杀本run)
#   ENVFILE=.rf4.env ./run_zf_scoped.sh --status   # 看状态
#   tmux attach -t zf-rf4                           # 续看面板
set -euo pipefail

ACTION="run"
case "${1:-}" in
  --stop)   ACTION="stop" ;;
  --status) ACTION="status" ;;
  "" )      ACTION="run" ;;
  *) echo "未知参数: $1 (支持 --stop / --status / 无参=启动)"; exit 2 ;;
esac

# 载入 env(.rf4.env 这类:export ZF / ZF_STATE_DIR / ZF_TMUX_SESSION ...)
if [[ -n "${ENVFILE:-}" ]]; then
  [[ -f "$ENVFILE" ]] || { echo "ENVFILE 不存在: $ENVFILE"; exit 2; }
  set -a; # shellcheck disable=SC1090
  source "$ENVFILE"; set +a
fi
: "${ZF:?需要 ZF(在 .env 里 export ZF=/.../.venv/bin/zf)}"
: "${ZF_STATE_DIR:?需要 ZF_STATE_DIR}"
: "${ZF_TMUX_SESSION:?需要 ZF_TMUX_SESSION}"

TAG="${ZF_TMUX_SESSION#zf-}"          # zf-rf4 -> rf4
UNIT="zf-run-${TAG}"
PROJ_ROOT="$(pwd)"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEARDOWN="$HERE/zf_run_teardown.sh"

case "$ACTION" in
  status)
    systemctl --user status "${UNIT}.service" --no-pager 2>/dev/null || echo "无 ${UNIT}.service"
    echo "--- tmux ---"; tmux has-session -t "$ZF_TMUX_SESSION" 2>/dev/null \
      && echo "session $ZF_TMUX_SESSION 在" || echo "session $ZF_TMUX_SESSION 不在"
    exit 0 ;;
  stop)
    # 优雅停:systemctl stop 会触发 ExecStopPost=zf_run_teardown.sh(留痕+只杀本run)
    echo ">>> 停 ${UNIT}(只影响本 run)"
    systemctl --user stop "${UNIT}.service" 2>/dev/null || true
    # 兜底:即便 service 已不在,也确保本 run 的 tmux 被精准收掉 + 留痕
    ZF="$ZF" ZF_STATE_DIR="$PROJ_ROOT/$ZF_STATE_DIR" ZF_TMUX_SESSION="$ZF_TMUX_SESSION" \
      bash "$TEARDOWN" "manual-stop"
    exit 0 ;;
  run)
    [[ -x "$TEARDOWN" ]] || chmod +x "$TEARDOWN" 2>/dev/null || true
    echo ">>> 启动 ${UNIT}(独立 user service;SSH 断不影响;停用 ./run_zf_scoped.sh --stop)"
    # 瞬态 user service:后台、与登录会话解耦;ExecStopPost 保证 stop 时收干净。
    systemd-run --user \
      --unit="$UNIT" \
      --slice="zf-run-${TAG}.slice" \
      --working-directory="$PROJ_ROOT" \
      --setenv=ZF="$ZF" \
      --setenv=ZF_STATE_DIR="$ZF_STATE_DIR" \
      --setenv=ZF_TMUX_SESSION="$ZF_TMUX_SESSION" \
      -p "ExecStopPost=/usr/bin/env ZF=$ZF ZF_STATE_DIR=$PROJ_ROOT/$ZF_STATE_DIR ZF_TMUX_SESSION=$ZF_TMUX_SESSION bash $TEARDOWN systemctl-stop" \
      -- "$ZF" start
    echo ">>> 已起。续看:tmux attach -t $ZF_TMUX_SESSION   状态:./run_zf_scoped.sh --status"
    ;;
esac
