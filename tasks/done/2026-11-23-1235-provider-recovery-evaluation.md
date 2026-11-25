# 把 Mock 重放与真实 Provider 进程恢复分开执行；测量本地单/双模型在相同任务与独立验收条件下的实际表现

> 状态: done · `bfbda3b3 feat: deliver scoped workflow inputs and recover blocked candidate reviews`

把 Mock 重放与真实 Provider 进程恢复分开执行；测量本地单/双模型在相同任务与独立验收条件下的实际表现。

中断真实本地 Codex 并从原会话恢复 -> verify: 原判断经精确核验后准入，宿主执行不重复；相同 SQL 的单/双模型配对评测记录耗时、token 和业务得分。

真实 Codex 连续两次中断、原会话严格判词经准入，新增宿主命令为零。两轮单/双模型对账均 0/5，已记录真实耗时和用量。 详见 [实施记录](../../docs/impl/workflow-inputs-and-milestone-recovery.md)。
