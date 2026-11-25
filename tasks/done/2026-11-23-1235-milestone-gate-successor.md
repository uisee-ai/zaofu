# 固定到实际候选与合同的静态门禁、OA 接手与恢复

> 状态: done · `bfbda3b3 feat: deliver scoped workflow inputs and recover blocked candidate reviews`

固定到实际候选与合同的静态门禁、OA 接手与恢复。正式生产者证据解除同目标门禁，错误版本仍待证。无原对话的 OA 可以读取上一里程碑并沿既有准入提交，重放不重复执行。

在绑定的 clean Git 候选执行门禁并派发闭合前 OA -> verify: 只接受同合同、代次与目标的证据；新会话消费冻结 roster，重复唤醒至多一个有效 successor。

正式静态门和 Candidate 结果绑定入口、全新 OA checkpoint 及过时目标拒绝均通过。重复唤醒只启动一次后继。 详见 [实施记录](../../docs/impl/workflow-inputs-and-milestone-recovery.md)。
