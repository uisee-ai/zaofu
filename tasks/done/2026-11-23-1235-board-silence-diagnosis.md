# 从运行事实识别全部板成员静默并交 Supervisor/Run Manager

> 状态: done · `bfbda3b3 feat: deliver scoped workflow inputs and recover blocked candidate reviews`

从运行事实识别全部板成员静默并交 Supervisor/Run Manager。验证活跃成员、有效等待、合法停顿、重新活跃和同一静默窗口幂等。

运行真实 Supervisor/RM tick 并重复巡检 -> verify: 无人推进只派一次诊断；有效等待、租约与恢复进展均被识别。

两次真实 Supervisor / Run Manager tick 消费同一静默窗口，得到一次诊断。有效租约、合法等待、已完成任务与延迟心跳均有反例。 详见 [实施记录](../../docs/impl/workflow-inputs-and-milestone-recovery.md)。
