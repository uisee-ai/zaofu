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
