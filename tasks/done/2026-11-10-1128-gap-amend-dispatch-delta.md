> 状态: done — 8fede5f9 fix: bind gap amendment dispatch delta to current tasks

# 局部恢复继承旧派发批次导致准入拒绝

用户授权：持续监工，记录并修复 bug，修复后恢复任务。

## 复现

Datal Privacy R7 → R8 的局部恢复从当前已准入 Task Map 生成新修订。
`build_gap_task_map_amend` 浅复制原图，更新 `amend.gap_task_ids`，却保留旧
`dispatch_delta_task_ids` 的 14 项清单。Writer selector 因本次仅请求 R8
而拒绝。生产 artifact writer 同样调用该 builder，没有后置归一化。

## 修复边界

仅重绑定原图已声明的顶层/metadata 派发 delta 到本次实际 amendment delta；
保留全图、历史元数据、权限、验证命令和 selector 的不一致拒绝能力。
不修改生产 canonical state，不重开全局 Plan。

## 验收

- 顶层、metadata、两处同时声明旧 delta -> verify: successor 只选择新任务，原输入不变，其他任务请求仍拒绝。
- 真实 flow.gap_plan.ready 经 Orchestrator -> verify: 仅新任务实际派发，重启重放不重复派发。
- 恢复影响闭包及机械门禁 -> verify: focused pytest、premerge sentinel、deterministic mock E2E。
- 部署已验证代码后恢复 Privacy -> verify: 当前 binding 预检通过，再由原 orchestrator 正式提交；记录真实事件和遗留阻塞。

## 初步证据

新增 6 项回归在修复前均失败：/tmp/zf-gap-delta-red-20261110.log。

## 验证结果

- 新增回归 6 RED → 6 GREEN。
- gap builder、真实 Orchestrator、Writer admission/runtime/fallback、input policy、dependency/replacement/baseline 12 个测试文件：334 passed（21.43s）。
- `dev-verify.py plan --base dev --tests tests/test_gap_dispatch_delta.py`：推荐广域 230 文件；相对本批基线 005b174f 为 209 文件。按 AGENTS 的影响闭包规则，本次已明确执行上述直接调用/合同闭包，未声称全量测试。前批 Unicode 167 项已有通过记录，本批未改变该实现。
- `dev-verify.py run --base dev --tests tests/test_gap_dispatch_delta.py --skip-tier deterministic --skip-tier serial --receipt /tmp/zf-gap-delta-tiers-20261110.json`：机械 sentinel 53 passed、deterministic mock E2E 10 passed。跳过两层用于避免选择器膨胀触发全量；不跳过本批 focused 测试。
- `git diff --check` 通过。
- 生产部署及受控恢复已验证，见下方事件记录。

## 生产后验（2026-11-10 12:12 UTC）

控制器 PID2114755 从 gap-partitions-20261110 已验证源码运行。
`evt-d55a5c6f2c96` 局部重试生成包，`evt-8dc80ba40170` 正式准入；
依用户先前批准授权，operator 经 `zf plan approve evt-e6857feb62f6`
产生 `evt-8d4de307f395`，内核 `evt-9ce21ed4eb08` 派发 Privacy R8。

新工作树 base `4ecb9f9a57c4ddafa5e721b6439e93d5e51a84f9`：
Privacy 三目录与原部分成果9352b324逐树相同，Ingest及已验收fixture与
Candidate9d28c014逐树相同，9d28亦为祖先；工作树初始干净。
Task Map 下游P19/P20Q/P20仅将blocked_by中R7替换R8，其他正文完全相同；
已完成Ingest与在途Dataset无状态/authority/contract变更。

此处done指本项harness缺陷及生产恢复入口验证完成，不代表Privacy产品验收完成。
正式Impl门禁、独立Verify、Candidate集成仍由runtime执行；50/59产品任务done。
历史损坏账本unknown与Privacy原600秒超时原因仍单列，不改写也不豁免。
证据存于Datal diagnostics/consumer-recovery-20261110，包括recovery-status.json、
privacy-after-adopt-audit.json、privacy-downstream-plan-audit.json及测试日志。
