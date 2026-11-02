# Reader 重派 briefing 必须展示已接纳的有效预算

> 状态: done — `6eec7022 fix: preserve effective budget on reader retry`

## 证据

LS 的 `fanout-prd-plan-evt-698cbb36` 初始派发 `evt-104b22717877` 和 retry `evt-ebfa13089002` 均引用 operation `wop-prd-plan-planner-trig-evt-698cbb36-rework-5954c54f40a6`。已接纳的 run-scope amendment 将有效 `max_usage_samples` 从冻结值 60 改为 0，但 retry briefing 写回 60，worker 因而错误认为必须在 60 次工具往返内读取完全部输入。已在运行时 budget guard 上只读复算有效值为 0；新代另有正确 briefing，不修改在途冻结合同。

## 验收

1. 对复用同一 operation 的 reader retry 生成 briefing -> verify: 使用该 operation 经已接纳 amendment 的实际预算，保留 cost 等其他维度；不能凭全局默认重引入 60。
2. 新 operation、无 operation 旧合同及未知身份 -> verify: 正确绑定新 operation；无持久身份保持兼容，不跨 Run 关联或伪造预算。
3. 回归与交付 -> verify: 实际 dispatch 入口正反测试、相关事件/恢复闭包、sentinel 和 mock E2E 通过；合并部署后保留现有 Planner 和原账本。

## 验证

- 真实重派入口 RED/GREEN；同一 operation、身份不匹配及无 ID 反例覆盖。定向闭包 265 passed，哨兵 53、mock flow 10，合并后定向 17 passed。全量 pytest 未运行；规划器基于 186 个映射文件建议全量，但本变更为 reader retry 单域，按仓库分域规则执行定向闭包。
- 真实 LS 历史派发和 manifest 只读重放返回正确 operation；代码经 control-plane-only 重载，保留当前 Planner 与历史账本。未制造新的真实失败或强行重派用于证明。
