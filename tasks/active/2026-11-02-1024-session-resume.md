# 同任务可信 session 断点恢复

> 状态: active

## 问题与目标

LS 恢复中已结算 operation、活动 session 和工作树容易混淆；复用同一 Codex/Claude session 需要可核验的任务、合同、计划、基线、角色、provider/session 和执行状态。只恢复可证明的同一执行，不重复已完成工作；来源不完整或不一致时保守回退，未知外部副作用不得盲目重做。

## 验收

- 同 Run、任务、角色、session、合同/计划及工作区基线一致，且原 operation 仍可续接 -> verify: 实际恢复入口保留 provider session 与有效断点，不重复派发活动 operation。
- session 缺失、身份或合同/代码发生变化、活动租约冲突、原 operation 已结算 -> verify: 不误判为同一活动执行，保留日志/工作树并按现有受控生命周期处理。
- 未知工具结果或外部副作用 -> verify: 不以 session/heartbeat 推断成功，不自动重复不可证明安全的动作。
- 定向恢复与真实调用方、mock provider、事件和状态合同 -> verify: 回归通过，读取兼容真实 provider 路径，不声称真实 provider E2E。

## 实施进展（2026-11-02）

- 已修复 Claude 在 worktree 启动时检查主项目目录 session 的问题，按真实 cwd 查找；独立测试覆盖正确与错误目录。
- 已修复停机后 `fanout_synth` 被错误取消：受控重驱原 operation/request，Provider 重启后重发原 briefing 并重新记录 running；重发前检查操作状态，恢复提示要求核对未知工具结果。
- 尚需完成跨计划/合同变化、活跃租约和未知外部副作用的端到端组合验收；当前测试不证明真实 Provider 的运行及未知外部效果不会被模型重复。
