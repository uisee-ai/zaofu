# R40：增量恢复保留显式任务声明

> 状态: done — 4881ee20 fix: preserve gap declarations and retained input lineage

用户授权监工修复阻塞；为 LS-v1 后续 15 项补齐证据范围时，只读预检发现 gap task 序列化丢弃原 proposal 的 description/source excerpt/决定关联/lane affinity/技能及额外能力声明。修复前实际发布入口与 writer 消费测试 3 项 RED。

## 范围与验收

- 保留本次 proposal 显式声明 -> verify: 真实 write_gap_task_map_amend_artifact 输出正文与 writer_task_items 输入一致，原 proposal 不变。
- 不默认继承旧任务语义或放宽权限 -> verify: 只保留指定可选字段；空技能声明保留，重复技能仍经 writer 拒绝。
- 验证影响闭包并合并 dev -> verify: dev-verify、gap/control-actions/Task Map/writer 回归、premerge sentinel 和 mock E2E。
- 部署再修活动合同 -> verify: runtime-load revision、保留原 workers；LS 受控 proposal 独立对账。

此项只修运输丢字段，不能证明任务说明语义正确或历史任务已完成。

验证：R40 首轮回归因测试预期异常类型不符修正为 RuntimeError 后通过；两次闭包命令引用不存在的测试文件未执行，已改为实际文件。最终20文件262 passed（含两个 deterministic mock E2E），sentinel53 passed。初始 sentinel 因模块从1000增至1002行失败，已将同属声明处理的既有可选字段代码移入新模块，未提高上限。dev-verify planner 自动闭包192文件触发全仓阈值，依 AGENTS 的测试域规则采用上述具体闭包，不运行全仓 pytest、不声称真实Provider E2E。真实 LS 两profile只读预检通过，原分区/ref/digest不变。

收口证据：已合并 dev95c82584并部署；原Grant实际派发evt-de838bd6b4aa；后续15项经逐字段对账与正式准入，30个输入profile预检通过。
