# 原 8×8 Candidate 阻塞与孤儿告警分诊

> 状态: done · `bfbda3b3 feat: deliver scoped workflow inputs and recover blocked candidate reviews`

用原失败及全部已准入成员回放 -> verify: 选择一个 OA checkpoint，预算、纯运行错误及未准入成员保持原恢复入口。

重放历史锚点孤儿告警 -> verify: 仅放行当前 Candidate 的有界语义分诊；两次 tick 仅一个请求，宿主执行次数不增加。

原失败 evt-36dfa17cd84c 只读重放和实际控制面均产生同一分诊。RM 实发 evt-42f547657f65；OA 操作启动 evt-4a5813f2545a。此项验收限于路由，产品最终交付需独立验收。 详见 [实施记录](../../docs/impl/workflow-inputs-and-milestone-recovery.md)。
