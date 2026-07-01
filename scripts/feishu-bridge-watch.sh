#!/usr/bin/env bash
# tmux launcher for the Feishu always-on bridge (doc 99 §4.1).
# Until the systemd unit (W4) lands, this runs `zf feishu bridge --watch` inside a
# detached tmux session so you can attach to watch it live, and Ctrl-C triggers the
# bridge's graceful drain (SIGINT → drain in-flight runs → release ws lock).
#
#   scripts/feishu-bridge-watch.sh start    # create tmux session + run the bridge
#   scripts/feishu-bridge-watch.sh attach   # attach to watch live (detach: C-b d)
#   scripts/feishu-bridge-watch.sh status   # session state + recent pane output
#   scripts/feishu-bridge-watch.sh stop     # graceful drain (C-c) then kill session
#
# Reads FEISHU_APP_ID / FEISHU_APP_SECRET from the environment or a sourced .env in
# the working dir. The bridge's own single-instance guard (ws-<app_id>.lock) still
# prevents a second WS consumer even if two sessions are started by mistake.
set -euo pipefail

WORKDIR="${ZF_BRIDGE_WORKDIR:-$(pwd)}"
SESSION="${ZF_BRIDGE_TMUX:-zf-feishu-bridge}"
DEBOUNCE_MS="${ZF_BRIDGE_DEBOUNCE_MS:-600}"
# The zf entrypoint. Override for a dev worktree, e.g.
#   ZF_BIN="python3 -m zf.cli.main" PYTHONPATH=src scripts/feishu-bridge-watch.sh start
ZF_BIN="${ZF_BIN:-zf}"

cmd="${1:-start}"

_alive() { tmux has-session -t "$SESSION" 2>/dev/null; }

start() {
  if _alive; then
    echo "bridge tmux session '$SESSION' already running (attach to watch)"; exit 0
  fi
  if [ ! -f "$WORKDIR/zf.yaml" ]; then
    echo "no zf.yaml in $WORKDIR (run from a project dir or set ZF_BRIDGE_WORKDIR)" >&2
    exit 1
  fi
  [ -f "$WORKDIR/.env" ] && { set -a; . "$WORKDIR/.env"; set +a; }
  if [ -z "${FEISHU_APP_ID:-}" ] || [ -z "${FEISHU_APP_SECRET:-}" ]; then
    echo "FEISHU_APP_ID / FEISHU_APP_SECRET not set (export or put in $WORKDIR/.env)" >&2
    exit 1
  fi
  # Pass creds into the tmux pane's environment explicitly (the tmux server does
  # not inherit a freshly-sourced .env). Forward PYTHONPATH too so a dev ZF_BIN
  # override (python3 -m zf.cli.main) resolves inside the pane.
  pp_arg=()
  [ -n "${PYTHONPATH:-}" ] && pp_arg=(-e "PYTHONPATH=$PYTHONPATH")
  tmux new-session -d -s "$SESSION" -c "$WORKDIR" \
    -e "FEISHU_APP_ID=$FEISHU_APP_ID" -e "FEISHU_APP_SECRET=$FEISHU_APP_SECRET" \
    "${pp_arg[@]}"
  tmux send-keys -t "$SESSION" \
    "$ZF_BIN feishu bridge --watch --debounce-ms $DEBOUNCE_MS" Enter
  echo "bridge started in tmux session '$SESSION' — attach: $0 attach"
}

stop() {
  if ! _alive; then echo "no tmux session '$SESSION'"; exit 0; fi
  # graceful: SIGINT to the foreground bridge → drain in-flight runs + release lock
  tmux send-keys -t "$SESSION" C-c
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    _alive || break
    # once the bridge prints "stopped." the drain is done; kill the (idle) shell
    if tmux capture-pane -p -t "$SESSION" 2>/dev/null | grep -q "\[bridge\] stopped."; then
      break
    fi
    sleep 1
  done
  tmux kill-session -t "$SESSION" 2>/dev/null || true
  echo "bridge stopped (graceful drain + session killed)"
}

case "$cmd" in
  start) start ;;
  stop) stop ;;
  restart) stop || true; sleep 1; start ;;
  attach) _alive && exec tmux attach -t "$SESSION" || { echo "not running"; exit 1; } ;;
  status)
    if _alive; then
      echo "running (tmux session '$SESSION')"; echo "--- recent pane output ---"
      # the pane is usually taller than the output, so drop blank lines first
      tmux capture-pane -p -t "$SESSION" 2>/dev/null | grep -vE '^\s*$' | tail -n 8
    else
      echo "not running"
    fi ;;
  *) echo "usage: $0 {start|stop|restart|attach|status}" >&2; exit 2 ;;
esac
