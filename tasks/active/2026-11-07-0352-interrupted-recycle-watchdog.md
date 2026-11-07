> 状态: active

# 重启 watcher 接管中断的上下文回收

用户授权监工修复卡顿；主会话批准本片。Retention 在 03:41:50 UTC 开始
context recycle，控制面重载落在新 pane 启动过程中。新 watcher 看到
持久化 recycling 和死 provider，却因旧 worker 活性尚新而继续等待。

## 实施和验收

- 重建 runtime，从事件 hydrate recycling，确认 dead provider 和活动 Task/fanout -> verify: 不等待300秒旧活性超时，经现有 role 锁执行 fresh-session 恢复。
- 保留原任务与归属 -> verify: operation/active_attempt/dispatch/lease/status 不变；原 reader briefing 重投；代际工作目录与未完成文件保留。
- 保留现有安全边界 -> verify: alive、无活动 obligation、reader terminal、其他 role、safe halt、blocked human 不恢复；锁争用不双 spawn。
- 本地回收不触发新增即时接管 -> verify: 有本地 marker 且活性仍新时不抢占；真实 recycle.failed 后仍走既有 stale recovery，不能永久屏蔽。
- 实际 caller -> verify: `_capture_logs` 和 `run_once` 链路、既有 context recycle deterministic mock E2E 通过。

实现说明：[中断回收接管](../../docs/impl/interrupted-recycle-watchdog.md)。

## 验证

RED：3 failed、5 passed；真正创建 old runtime 写入状态，然后 new runtime
hydrate。失败为 Task/fanout 未即时恢复以及锁路径未触达，非 collection error。
日志 `/tmp/zf-supervision-20261107/interrupted-recycle-red.log`。

最终 focused 闭包 104 passed（29.14秒），命令：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest \
  tests/test_interrupted_recycle_recovery.py tests/test_capture_logs_watchdog.py \
  tests/test_orphan_recycle_rescue.py tests/test_context_threshold_detection.py \
  tests/test_task_pipeline_worker_liveness.py tests/test_tmux_transport_lifecycle.py \
  tests/test_tmux_transport_env.py tests/test_source_repair_restart_actuator.py \
  tests/test_session_mutex.py tests/integration/test_context_recycle_e2e.py -q --no-cov
```

日志 `/tmp/zf-supervision-20261107/interrupted-recycle-closure.log`。
`bash scripts/dev-premerge-gate.sh`：50 passed、1 skipped，3.16秒，日志
`interrupted-recycle-sentinel.log`。没有执行全套 pytest 或真实 provider E2E。

planner 实况：`dev-verify.py plan --base dev` 纳入分支历史并选出776测试文件；
本片起点 `--base 094d8299` 仍因通用生命周期调用闭包选出173文件并升级全套。
主会话明确要求 focused 而非全套，所以运行
`dev-verify.py run --base 094d8299 --skip-tier deterministic --skip-tier serial`
（不是声称跳过项通过）：contract tier 50 passed、1 skipped，mock_e2e 的
`scripts/run-flow-smoke.sh` 10 passed。实际 focused 替代闭包为上述10文件，
包括 SessionLock 和 context recycle fake-provider 集成链路。
自动收据 `/tmp/zf-supervision-20261107/interrupted-recycle-verify-receipt.json`，
详细命令/退出码见 `interrupted-recycle-verify-run.log`。

本片仅修改隔离 worktree 代码/测试/文档。0200任务未动；不自行 commit、同步、
reload 或操作业务运行态。任务保持 active，等待主会话审查提交并记录commit。

## 明确延后

hard-cap 计时当前只在低于 warning 阈值时清零：若 native compact 后仍处于
warning 与 hard-cap 之间，可能保留旧计时。状态 defer：后续独立批次先用
最新 usage 下降但未低于 warning 的序列复现，再决定修复。本片不改阈值/计时。
actuator 优雅等待也未实施：原 `_start_recycle` 不持 respawn lease，不能仅
等待该锁就宣称所有在途替换都已收敛。
