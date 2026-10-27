# Plan 引用编辑实测期间的 Autoresearch 项目隔离故障

关联：[Inbox/Workflow 真实验证](2026-10-26-2343-plan-inbox-agent.md)、[187 实施说明](../impl/187-ai-sdlc-graph-workbench.md)。时间为 UTC。故障现场保留，本文不把历史验证证据当作当前状态。

## 发生了什么

2026-10-26 23:43–23:55，真实 ty 项目的 Agent 局部修改、Apply、Planner/Critic 重新规划和 Inbox Agent Diff 均已通过。新版 Plan 为 `evt-fbb3199ea3ba`，人工审批尚未发生。证据见关联记录。

23:56:25，ty 的 Autoresearch resident 开始 incident-reproduction 试跑，worktree 为 `/tmp/zaofu-autoresearch-resident/worktrees/rmar-1addab70749b`。试跑继承 `ZF_PROJECT_ROOT` / `ZF_STATE_DIR`；`run_mixed.initialize_flow_baseline()` 调用 `zf init --force` 时，ProjectInitializer 通过环境变量解析到父项目 `.zf-ty`，覆盖其活动 events.jsonl、kanban.json 与 session.yaml。随后清理命令 `zf stop` 也指向 ty，日志明确为 `Stopped harness session: zf-ty`。试跑自己的 watcher 使用独立配置启动，因此正常的试跑启动日志不能证明其全部命令隔离正确。

最终刷新时，原 Plan API 返回 404。旧语义工件与浏览器验证证据仍在，但没有找到足以恢复完整账本的备份。未拼造旧事件、未恢复虚构的任务状态、未批准 Plan。保留原 ty 现场，停止关联 resident/loop/runner，停止该试跑的残余 resident 和 run-manager session，并向其独立账本追加 `simulation.done`。受损后的关键文件另保存在权限 0700 的 `/tmp/zf-plan-recovery-20261027T001129Z/`，它是故障现场副本，不是故障前备份。其他项目没有清理。

## 修复与验证

- `src/zf/autoresearch/orchestrator.py::_env` 与 `tests/e2e/run_mixed.py::_subprocess_env` 清除父项目路由环境变量。各子项目命令重新从自己的 cwd/zf.yaml 解析配置；显式 `--parent-state-dir` 仍可用于父项目报告路径。
- 新回归使用两个不同的非默认 state_dir，给父项目写入标记账本，在继承父环境的情况下真正启动子进程执行 `zf init --force`。修复前两个入口均改写父账本；修复后父账本逐字不变，子项目账本正确创建。见 [RED](2026-10-26-plan-quote-entry/isolation-red.log)、[67 项聚焦验证](2026-10-26-plan-quote-entry/isolation-green.log)。
- dev-verify 对此次隔离变更的直接调用闭包 **157 passed**。首次环境未将 venv 加入 PATH，4 项调用 `python` 的用例失败；补齐 PATH 后同一闭包通过，没有修改测试绕过失败。见 [最终验证](2026-10-26-plan-quote-entry/isolation-verification-final.log)。
- sentinel gate **50 passed, 1 skipped**，见 [gate](2026-10-26-plan-quote-entry/isolation-gate.log)。没有运行全量 pytest，也没有 commit、merge 或 push。

## 当前可审阅项目

新建独立 `ty-review`（`ty-review-42cb2551`，`/home/uisee/workspace/ty-review`，`.zf-review`），显式启用人工 Plan 审批并关闭 Autoresearch resident，保留原 ty 的故障现场。通过真实 UI Start Workflow 启动 `workflow-3dc6ff5623188714`，执行真实 Codex scan/Planner/Critic。00:46 UTC 的 Plan `evt-38c86009183e` 已通过真实 Inbox 引用编辑、headless 局部 Diff、审批锁和手机宽度检查，仍待人工批准；证据见关联记录。这是新运行，不是原 ty 历史恢复。

新项目初始化曾尝试 `workflow prepare --task TASK-E5EDD7`，该入口报告 objective 缺失；本轮改用明确 `--objective` 完成准备，保留失败输出，不声称这次覆盖或修复了 CLI 的该输入组合。

8002 使用修复 worktree；8001 仍为 dev 旧构建，未重启。隔离修复尚未合并到 dev。
