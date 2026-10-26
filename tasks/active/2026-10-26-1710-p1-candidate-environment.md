# P1 Candidate 环境可复现与 rolling smoke preflight

> 状态: active

## 问题
Candidate worktree 缺少 node_modules 或 npm cache 不完整时，rolling smoke 才暴露环境问题；setup 还会误作用于没有 app 的 reader worktree。

## 范围
- setup/provision 只对声明依赖的项目 worktree 生效。
- Candidate integration 在 smoke 前输出明确 environment readiness evidence。
- 依赖缺失时 fail fast，区分环境失败与产品测试失败。

## 验收
- step -> verify: 空依赖 candidate 返回 environment_not_ready，不执行 rolling smoke。
- step -> verify: 有 package-lock 和可用 node_modules 时 tsc/vitest smoke 通过。
- step -> verify: reader/orchestrator worktree 不执行无关 npm setup。
