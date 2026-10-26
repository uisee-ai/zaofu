# Task Workspace 写入守卫绑定与真实 Impl/Verify 恢复

> 状态: done — 38288b17 fix: bind task workspace guards and recover blocked implementation；补充 ca42ce88 fix: preserve task workspace observations through transport routing

用户批准修复 loshu-fms 的 Task Workspace 与写入守卫冲突。基线 dev=ca7a3cff；原任务 FMS-SCAFFOLD 于 evt-168467251356 提交 dev.blocked，未产生产品代码。

## 实施与验收

- [x] 复现实际 hook 入口的合法 Task Workspace 写入被拒 -> verify: 回归先红，覆盖 Codex/Claude 和当前任务合同路径。
- [x] 由内核 operation/派发事实解析当前写入目录和任务 -> verify: 当前任务可写；旧角色目录、其他任务、旧代际及越界路径仍拒绝；legacy 路径保持兼容。
- [x] 聚焦闭包、sentinel 和 mock E2E -> verify: 记录命令、准确结果和限制。
- [x] 合并加载修复并受控恢复同一任务 -> verify: 不改写 runtime truth，不伪造 worker 完成。
- [x] 真实 worker 源码写入、Impl 产物提交、Verify 派发 -> verify: 记录实际文件/提交/事件证据，失败介入与恢复记录保留；未达成不标 done。

## 边界

保留 38 项已批准 Plan 与 8 Impl/8 Verify 配置。事件消费延迟属已记录的独立 proposed backlog，不在本次顺手扩展。保留主工作树 index.md 和产品工作区现有修改。

## 修复与验证记录

- 真实 hook 入口回归：基线 6 failed / 5 passed；工作目录与合同身份修复后 11 passed，新增代际/放置与无 legacy 目录覆盖后，10 个影响范围文件 137 passed。
- 当前任务身份查找提取到 hook_dispatch_context，小模块承接 pipeline/legacy dispatch 解析；hook_recv 从 1000 行下降，不增加超大模块上限。
- 发现已有操作员恢复不能让 settled + blocked Impl 继续，补充 recover task-impl：预览/显式 apply、精确旧结果绑定、原预算内下一 operation generation，不重写终态结果。新入口基线 8 failed；恢复、rework、reconciler、generation、projection、result、原 recover CLI 7 文件 80 passed。
- 第一版 guard diff 的 sentinel 51 passed。最终加入恢复入口后的影响闭包与门禁另行记录。
- 说明：[任务上下文和 Workspace 恢复](../../docs/impl/task-pipeline-capsule-recovery.md)。

- 最终恢复闭包 7 文件 81 passed；Run Contract 显式 state_dir 加载检查 11 passed。真实结果 dev.blocked 规范化为 semantic_verdict=failed，补充 blocked/failed 两种编码后，恢复入口/请求投影/调度 57 passed。
- dev-verify plan/run 对共享边界建议全量；本批按 AGENTS 使用上述直接调用闭包替代 deterministic/无关 serial，没有跑全量 pytest。最终 sentinel 51 passed，三流 mock E2E 10 passed；Ruff 与 diff --check 通过。
- 真实 CLI 预览成功，精确绑定 evt-168467251356，下一 operation generation=2；仍未 apply。停止前 active operation=[]，zf stop 正常完成 18 步。
- 所有本批证据在 /home/min/workspace/public/zaofu-evidence/workspace-guard-20261025/。运行中 hook 目录取自 kernel dispatch，未扩大到整个 workdirs，也未关闭 guard。

- 实现 38288b17 fix: bind task workspace guards and recover blocked implementation；合并 ccd0cd0e。合并后 sentinel 51 passed、新入口与 guard 38 passed。CLI apply 收据 evt-cc591816c4b8，精确重试原 operation 的 g2；Dashboard 保留环境重载，PID=3131501。真实 Impl/Verify 验证进行中。

- g2 实际派发 evt-6574c39e3111；worker 已在 Task Workspace 写入 app/、scripts/runtime/、tests/scaffold/ 并下载工具链，未再被写入 guard 阻挡。Impl 产物和 Verify 仍待观察。
- 真实进程探针发现 Codex cwd 与 -C 参数仍为旧角色目录，worker 根据 briefing 在正确任务目录执行了写入。进一步复现：CompositeTransport 未转发生命周期快照，同 binding 恢复时 path_mismatch 无法被观察。实际重绑定入口回归先红（2 failed），补齐底层快照转发；不打断正在推进的 Impl。新增回归与加载时机继续记录。

- 快照转发修复：7 文件聚焦影响闭包 74 passed；sentinel 51 passed，Ruff/diff --check 通过。dev-verify 对共享 transport 扩大到 220 文件并建议全量，本批明确以直接调用闭包替代，未跑全量；dev-verify 的 skipped receipt 本身不算测试通过证据。三流 mock E2E 与 control-plane-only 启动保留 worker 回归合计 47 passed。

- 补充实现 ca42ce88 fix: preserve task workspace observations through transport routing，合并 2e0affdd；合并后 sentinel 51 passed、新增绑定 4 passed。使用 zf start --control-plane-only 重载控制面，旧 watcher 3131545 退出，新 watcher 3154798；保留 Codex PID 3137853 与原启动身份，不重排活跃任务。


## 真实恢复验收（2026-10-25 UTC）

- worker 实际提交：956168e feat: add reproducible TypeScript scaffold and health checks；21d8ffa feat: load explicit modules with fail-closed dispatch protocols。最终工作区干净。
- 最终 HEAD 的 smoke/runtime/static 收据均 passed、exit_code=0、dirty=false。15 项运行时测试、2 项静态测试与类型检查通过；clean-checkout 独立安装、构建、进程启动和检查收据 passed。
- 11:44:36 结果接纳 evt-9e5e25e9afac；规范结果 dev.build.done=evt-39d06191cfdf，operation g2 settled/passed。
- 11:46:34 task.ref.updated=evt-8da21566eb67，将 task/FMS-SCAFFOLD 接纳至 21d8ffac72a45bbde1d1eb1b1079cae950a1afa5。
- 11:47:19 Verify 实际派发 evt-aa4585e3ee7d，角色 verify-lane-0，operation=wop-verify-tp-FMS-SCAFFOLD-39102b23f7680a25-9367b723b872。tmux %409 的真实 cwd 为当前 Task Workspace，已读取 verify-g2-p1.md；不是仅创建队列或会话。
- 真实证据：zaofu-evidence/workspace-guard-20261025/real-recovery-e2e.json、real-impl-test-receipts.json、real-workspace-write-proof.json、control-plane-reload.json；这些外部本地证据未作为 runtime state 加入源码仓库。
- 验收范围是写入阻塞修复、终态 blocked 后受控重试、Impl 接纳和独立 Verify 启动。Verify 最终结论及全部 38 项项目交付仍未完成，不以本任务 done 代表产品交付 done。保留原 Plan、合同、8 Impl/8 Verify 配置与运行中 worker。
- 事件消费延迟仍是独立未实施项：本次 Impl 接纳至 Verify 派发约 163 秒。原 backlogs/2026-10-25-0845-runtime-wake-backlog-latency.md 仍 proposed，不把重载后的推进当成性能根因已修复。

## 补充修复验证命令

```bash
PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest tests/test_task_pipeline_transport_binding.py tests/test_role_lifecycle_runtime.py tests/test_composite_transport.py tests/test_composite_transport_by_instance.py tests/test_tmux_transport_lifecycle.py tests/test_task_pipeline_generation.py tests/test_task_pipeline_capsule.py -q --no-cov -n 4 --dist loadfile
# 74 passed
bash scripts/dev-premerge-gate.sh
# 51 passed；合并后重跑同门禁 51 passed
PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest tests/e2e/test_task_pipeline_v4_runtime_noise_mock_e2e.py tests/test_cli_start.py -q --no-cov -n 4 --dist loadfile
# 47 passed
.venv/bin/python -m pytest tests/test_task_pipeline_transport_binding.py -q --no-cov
# 合并后 4 passed
```
