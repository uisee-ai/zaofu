> 状态: active

# v3 分片 Verify 与产品验收收口

来源：用户授权自主完成 Impl / Verify 各 8 worker 的配置、重规划和实际执行。洛书本轮
PC8-F1/F2 暴露逐任务 reader 与全产品验收责任冲突。仅修改并发容量不能修复结果准入。

当前复现边界：v3 affinity reader 带 `upstream_task_id` 和候选 commit，被选为
`candidate-verify`，因而每个 reader 都需要完整 Product Acceptance Report。
现有 `flow.discovery.requested` / `flow-discovery` 在全部 reader 完成之后运行，
可作为全量审计消费者；不能将最终 Judge 当成其前置验收的生产者。
`prepare_candidate_verification_authority` 对 v3 返回空值本身不证明派发失败：
既有 task snapshot / candidate target 交接必须保留。

## 实施范围与验收

- 用有效 package / task authority 重现分片被要求全量 PAS
  -> verify: 回归测试先 RED，不能以无效 fixture 或缺少绑定身份制造失败。
- 为存在后续完整验收消费者的 v3 分片保留 task Verify 契约
  -> verify: 实际派发 caller 固定 task scope、candidate commit、当前 task authority；
  static global Candidate Verify 与 v4 路由不变。
- 通过已有 post-Verify discovery 提交全量产品报告
  -> verify: 分片结果不成为全局通过；缺报告、缺 mandatory journey、错版本拒绝；
  合法报告通过受控结果准入持久化并进入 Goal closure 的必读证据。
- 同步通用评审/复扫方法与实现说明
  -> verify: Planner 明确各命令的真实生产者、时点和全量消费者；不添加项目硬编码。
- 完成影响闭包与 mock E2E 后提交
  -> verify: focused tests、premerge sentinel、相关 deterministic E2E；
  mock 并发不冒充洛书真实 8 路执行。

本任务不降低 mandatory AC，不直接改 runtime truth，不切换 rollout NO-GO 的 v4，
不替用户编造真实后端、身份、数据库或部署资料。

实现说明：[v3 分片 Verify 与完整产品验收](../../docs/impl/v3-task-verify-product-closeout.md)。

验证：影响闭包 20 个测试文件 240 passed（含实际 Orchestrator / submit caller
mock E2E）；premerge sentinel 51 passed；三流 smoke 10 passed。技能三份一致、
diff check 通过。`dev-verify plan --base dev` 保守扩为全套，未执行全套；使用上述
任务/候选/结果准入/交接/收口闭包，`dev-verify run` 单独执行 contract、mock_e2e tiers。
真实洛书 8 路并发仍需部署后现场观察，不属于本代码修复的 mock 证明。
