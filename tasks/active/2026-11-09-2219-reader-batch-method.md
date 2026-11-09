> 状态: active

# 监工修复：统一Impl/Verify有界多页读取方法

用户授权持续监工、记录异常并实施修复。本项由真实Ingest旧任务仍按单页读取触发；
不重开已验收P0/P1，也不修改在途合同或运行态。开发技能仍要求每页单独调用，
Verify技能上下文段允许少量批读、执行段却要求单页，遗漏已支持的CLI四页入口。

## 验收

- [x] 统一两项方法并同步两套分发副本 -> verify: 源/分发字节一致，元数据与指令检查通过。
- [x] 保留完整消费、游标、鉴权及截断补读义务 -> verify: 现有多页CLI/观察交付回归通过。
- [x] 现场核实不改变attempt的续读 -> verify: ta-00c78c19a9940d37393d于22:17:27 UTC
  确认采用--pages4，原游标继续；ingest-multipage-guidance.json记录实际调用与身份。

实现边界：只改技能方法，不提高预算、不删除required reads、不篡改读取账本。
实施记录见[读者方法收口](../../docs/impl/datal-p0-p1-execution-readiness-20261109.md)。

验证：dev-verify自动指令/技能/结构闭包41 passed、1 skipped；多页/CLI/交付回归48 passed；
两项元数据通过，canonical/.codex/.claude逐字节一致。现场worker另确认四页输出无截断。
