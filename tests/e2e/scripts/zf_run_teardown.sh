#!/usr/bin/env bash
# zf_run_teardown.sh —— 只收「本 run」的尾,且留痕。
#
# 设计要点(对应两次"全没了"事故的根因):
#   * 绝不 `pkill -9 -f "venv/bin/zf start"` —— 那是按字符串匹配,会误伤
#     同时在跑的别的 flow 的 zf start(rf3/rf4 并存就互杀)。
#   * 只按 **本 run 的 tmux session 名** 精准杀(zf 所有 run 共用一个 tmux
#     server,session 名是唯一安全的边界)。
#   * 收尾先走 `zf stop --fast`: 由内核 requeue stale WIP 并 emit
#     `run.teardown`。失败时只尝试 `zf emit`,绝不直接 append events.jsonl。
#
# 用法:
#   ZF_STATE_DIR=.zf-rf4 ZF_TMUX_SESSION=zf-rf4 ZF=/.../zf ./zf_run_teardown.sh [reason]
set -uo pipefail

: "${ZF_STATE_DIR:?need ZF_STATE_DIR}"
: "${ZF_TMUX_SESSION:?need ZF_TMUX_SESSION}"
ZF="${ZF:-zf}"
REASON="${1:-manual}"

# events.jsonl 用绝对路径(ExecStopPost 的 cwd 可能不在项目根)
case "$ZF_STATE_DIR" in
  /*) STATE_ABS="$ZF_STATE_DIR" ;;
  *)  STATE_ABS="$(pwd)/$ZF_STATE_DIR" ;;
esac

echo ">>> [teardown] run=$ZF_TMUX_SESSION reason=$REASON —— 只收本 run,不动别的"

# 1) 内核 fast teardown: scoped state_dir + scoped tmux session。
STOP_OK=0
if ZF_STATE_DIR="$STATE_ABS" ZF_TMUX_SESSION="$ZF_TMUX_SESSION" \
    "$ZF" stop --fast >/dev/null 2>&1; then
  STOP_OK=1
  echo ">>> [teardown] zf stop --fast 完成"
else
  echo ">>> [teardown] zf stop --fast 失败,退到 zf emit 留痕"
fi

# 2) 留痕 fallback 仍走 zf emit,不直接写 events.jsonl。
if [[ "$STOP_OK" -eq 0 ]] && ! ZF_STATE_DIR="$STATE_ABS" "$ZF" emit run.teardown \
        --data "reason=$REASON" --data "session=$ZF_TMUX_SESSION" \
        --data "by=zf-run-teardown" >/dev/null 2>&1; then
  echo ">>> [teardown] zf emit run.teardown 失败,不直接写 events.jsonl"
fi

# 3) 只杀本 run 的 tmux session(精准,scoped; stop --fast 通常已处理)
tmux kill-session -t "$ZF_TMUX_SESSION" 2>/dev/null && \
  echo ">>> [teardown] tmux kill-session -t $ZF_TMUX_SESSION 完成" || \
  echo ">>> [teardown] session $ZF_TMUX_SESSION 已不在(无需杀)"

echo ">>> [teardown] 完成。注意:本脚本不做任何全局 pkill。"
