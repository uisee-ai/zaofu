# 按 occurrence 创建独立、限权的模型会话，冻结输入、目标、批准和预算

> 状态: done · `bfbda3b3 feat: deliver scoped workflow inputs and recover blocked candidate reviews`

按 occurrence 创建独立、限权的模型会话，冻结输入、目标、批准和预算。验证调度、回执、未知结果处置、停用和崩溃对账。

以冻结输入并发触发相同 occurrence 并中断 -> verify: 只有一次模型调用；有回执则复用，无回执由 owner 对账。

覆盖同一 occurrence 并发、崩溃、授权变化和未知结果。一个真实只读报告的用量为输入 246、输出 10。 详见 [实施记录](../../docs/impl/workflow-inputs-and-milestone-recovery.md)。
