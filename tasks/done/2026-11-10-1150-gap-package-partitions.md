> 状态: done — 31e698f5 fix: rebind gap input partitions before package admission

# 局部恢复计划包的分区继承与来源索引衔接

用户授权持续监工、修复 bug 并恢复任务。Privacy R8 的 Task Map 修订成功，
但 `evt-9991b1eda513` / `evt-72747d787197` 包准入拒绝：缺 R8 输入分区。

## 已复现的原因

1. 包构建在已有 gap partition rebinder 之前执行 dispatch partition validation。
   历史测试没有要求消费该端口，漏测真实 required port 路径。
2. Orchestrator gap 入口没有传递 source_index_ref 给已有 amendment writer，
   ready 又直接携带旧 ref，导致来源索引的新任务分区未更新。
3. 影响闭包发现既有回归：选中旧代 baseline 后，prior_body 仍为 latest 包正文。
   同一失败在未修改的 8fede5f9 worktree 上复现，属于同一继承链路问题。

## 验收

- 新任务继承父分区后才校验 -> verify: required/native policy 及 unknown parent 拒绝测试。
- 实际 flow.gap_plan.ready 入口生成修订 source index -> verify: 新任务分区可读取，原 artifact 不变，仅一次派发。
- baseline 包身份与正文同代 -> verify: 历史代恢复测试通过，错误 lineage 仍拒绝。
- 当前真实输入副本预演 -> verify: 临时 state package 构建成功；不发生产事件。
- focused 影响闭包、sentinel、mock E2E -> verify: 全部通过才部署；受控恢复后记录实际派发/剩余卡点。

测试夹具最初为 legacy 场景误加 required_plan_ports=[]，已纠正为省略字段。
纠正后补丁前 3 failed / 2 passed，补丁后 5 passed；既有旧代包回归另行基线复现。

## 验证结果

- focused 15 文件：346 passed / 18.67s（前一次345通过、1个基线既有回归失败，已修复）。
- dev-verify plan --base dev 已执行，选择器跨广域模块膨胀到全套；遵循 AGENTS 影响闭包要求，显式测试以上15文件和直接事件入口，未声称全量pytest。
- dev-verify run --base dev --tests tests/test_gap_source_index_entrypoint.py --tests tests/test_gap_package_input_partitions.py --skip-tier deterministic --skip-tier serial：sentinel53、mockE2E10通过。CLI曾误用单个--tests带两个路径，退出2后按重复--tests正确重跑。
- partition_package_replay.py 仅复制真实输入到临时state；完整prepare成功，13个required ports，未发生产事件。日志保留于Datal diagnostics/consumer-recovery-20261110/partition-package-replay.log。
- 原测试、权限、作用域及独立Verify门禁未削减。部署恢复已验证，见下方事件记录。

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
