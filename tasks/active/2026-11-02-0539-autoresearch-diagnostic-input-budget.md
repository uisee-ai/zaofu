# Autoresearch 诊断模拟反复消耗 operation token 预算

> 状态: active

## 复现证据

LS 的历史诊断 `ar-stage-replan-a1992dd5b6b40c11`、`ar-stage-replan-941255352b815ab1`、`ar-stage-replan-5e6de0263869af45`、`ar-stage-replan-cae76cef872568c9` 分别在隔离模拟的 Planner 或 tech-scan 触发 `workflow_budget_exceeded:tokens`，每次 operation 上限 1,500,000 tokens；观测 1,516,462、1,527,468、1,615,597、1,523,847 tokens，其中 cache_read 分别 1,429,248、1,389,952、1,568,256、1,250,176。上述运行有各自 worktree 与 inner-runner.log，非 LS 主运行预算；对应 usage_samples 12、22、10、21，未触及样本数 60。

## 验收

1. 用隔离 fixture 和同类来源证明输入、重复加载及缓存归属 -> verify: operation 新增/缓存用量、provider、来源和阶段分别记录，不能根据输入大小推测费用。
2. 核对触发请求是否确需完整产品重规划及现有有界诊断/输入选择 -> verify: 保留原始故障上下文和重放证据，验证复现所需最小输入及关键边界，未证明不删除来源或跳过门禁。
3. 对证实的重复加载或诊断路线缺陷实施最小修复 -> verify: 无真实 provider 的正反回归及执行入口测试覆盖完整来源、不同权限和恢复；对真实 provider 效果另列证据，不能用 mock 证明成本改善。
4. 预算或模型调整须另行按现有配置和用户授权处理 -> verify: 不擅自改 LS 的预算、样本限制、生产授权或终态；失败、未知成本和原记录保留。

## 调查进度（2026-11-02）

- 默认 `LoopConfig -> run_iteration -> AutoresearchRunConfig` 使用 PRD Flow，`incident-reproduction` 的 seed 包含原事件上下文与独立复现/验证要求；四次预算失败发生在内层 Planner/tech-scan。既有预算 guard 明确把 cache_read 纳入 token 用量，不能称其为计费错误或仅凭用量改预算口径。
- 需要验证是否可利用已注册的适配路线和有界上下文完成同等的故障复现与独立验证；在取得等价证据前不改默认流程或降低门禁。
- 后续实际诊断 `rmar-bd116d51bd21` 的隔离 Planner 再次因 operation token 超限失败（1,563,452 / 1,500,000，其中 cache_read 1,504,384，usage_samples 12；终态 `evt-0b75f3618336`）。诊断引用链的结构化丢失已单独修复，但它不能证明预算问题的因果或成本改善。
- 针对 `rmar-bd116d51bd21` 实际追溯：最初 Planner 的 `fanout.child.failed evt-8d685599a90a` 为 abstained，称源事件不在活动文件；事件实际在源 state 的归档中，执行 `zf events --id evt-b728d435b9c5 --state-dir <源state> --json` 可从隔离实验工作树读到。后续重规划 operation 才触发 token 上限。场景方法已补充归档感知查询，但其真实模型效果和预算能否通过仍待验证。
- 该诊断二次 Planner 的 briefing 39,268 字节、5 项 required / 22 项来源；12 条 usage 样本的 operation 增量包括 54,251 fresh input、1,504,384 cache read、4,817 output（subscription，未计价）。不能把缓存量直接解释为账单、断言全部由某个文件导致，或把必读数与最终验收范围等同。
