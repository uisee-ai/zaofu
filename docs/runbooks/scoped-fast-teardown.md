# 按 Run 精确收尾

适用于临时模拟、E2E 和故障诊断，入口为 `tests/e2e/scripts/zf_run_teardown.sh`。

先保存验证报告、Run ID、候选提交及恢复依据。从该项目的 `zf.yaml` 读取 `project.state_dir` 和本 Run 的 tmux session；确认绝对路径，避免落到真实 dev 的运行目录。模拟结束使用 `zf emit simulation.done --state-dir <绝对路径> --payload '{"reason":"validation complete"}'` 留痕。

在对应项目目录执行 `zf stop --fast`；需要同时停止本 Run 的 Run Manager 时加 `--include-run-manager`。自动脚本需显式传入 `ZF_STATE_DIR`、`ZF_TMUX_SESSION` 和 `ZF` 可执行路径。失败时用 `zf emit run.teardown --payload <JSON>` 留痕，保留失败输出；随后仅清理核实属于本 Run 的 session 和 Web PID。临时 Web 使用 8002 及以上端口，8001 留给真实 dev。

- Do not use `pkill`：禁止按进程名或命令片段批量终止；同机多个 Run 共用 tmux server。只可使用精确的本 Run session/PID。
- No script should append directly to `events.jsonl`：事件必须走 `zf emit`、EventWriter 或批准的受控动作。CLI 失败不是直接改账本的理由。
- 不删除未提交的 worker/worktree 内容。停止后核对目标 session 和进程确已退出，再清理可丢弃的临时状态；需要恢复或审计的产物保留。

收尾测试见 `tests/test_teardown_scripts.py`；完成批次记录见 `tasks/active/2026-10-26-0507-hello-pipeline-remaining-validation.md`。
