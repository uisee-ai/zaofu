> 状态: done；00b7f2af `fix: route reader execution failures through repair recovery`

# P2：Reader 执行弃权误消耗 Plan 语义返工次数

Owner 已授权持续监工、记录并修复执行中问题。DL 的 Planner 因必读输入与
60 usage samples 冲突而提交 failed/abstained；结果已准入 evt-e9999dddca9d。
阶段事件 evt-8c6de42e9c86 保留 reader_execution_failure，却未被共同分类器识别为
infra。受控修复预览拒绝它，Kernel 反而按语义失败消耗第二次 Plan 重规划额度。
本次已通过配置修复使新 Planner 正常运行；该分类缺陷仍在当前 dev 可独立复现。

- 区分执行失败 -> verify: 已准入 Critic/Reader 的执行失败不会自动语义重规划。
- 保留语义次数 -> verify: 历史 infra 事件不使后续产品/Plan 内容失败提前耗尽额度；既有 lineage 不重置。
- 修复后恢复 -> verify: 已注册 ControlledActionService 接受精确故障与批准证据，只重开一次；过时/无授权/终态拒绝不变。
- 保留失败事实 -> verify: 未读材料不被标记已读，原失败不转通过，显式 failure_kind 与未知分类行为保持。

当前 HEAD 复现：新增 4 项回归中 3 failed、1 passed（分类、计数、受控恢复）。
只改变共同分类器与历史 infra 计数，不改产品输入、审批或语义标准。
关联文档：docs/impl/plan-producer-failure-admission.md。

验证：分类/受控恢复/Reader/结果准入/候选返工/恢复调用者/EventWriter 与 Mock flow
闭包 518 passed；并发 serial 10 passed；premerge sentinel 53 passed。
dev-verify 映射扩张为 230 个测试文件，按 AGENTS 的影响闭包执行上述精确集合；
automatic run 使用 --skip-tier deterministic，不宣称全量或真实 Provider E2E。

已合并 dev 并部署 0ff4b571。合并后 sentinel 53 passed、重点回归 23 passed。
真实旧失败事件只读重放现在分类为 infra、自动重规划返回 infrastructure_failure；
已启动的新 Planner 与原 Critic 均保持 running，原 Critic hash/attempt/读取账本前缀
及五个角色进程启动时间保持，旧 Verify 拒绝仍 blocked。恢复入口已由受控动作
测试覆盖，不重放已经被新 Planner 取代的旧失败。
证据：DL artifacts/supervision/reader-failure-kind-deployment-2026-11-02.json。
