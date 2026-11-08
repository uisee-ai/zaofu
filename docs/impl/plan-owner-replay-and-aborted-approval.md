# Owner 需求修订重放与失效审批

关联：[199](../design/199-worker-failure-recovery-and-role-handoff.md)、[本次修复](../../tasks/2026-11-08-1357-plan-owner-replay.md)。

初次 Plan Package 准入会用受控 Owner 修订替代旧的 requirement ref/inline body；同一代重放现在采用相同的权威解析和同份事件快照，再比较有效正文。新 Owner 修订使有效正文变化时，旧代仍不能复用；其他显式端口仍逐一校验，不忽略未知或改变的输入。

kernel 的 fanout.cancelled 若 failure_scope=plan_admission，且 trigger_event_id 与 causation_id 一致，该 ready event 对应的审批退出 pending 和已批准等价复用集合。CLI、Web 及恢复等待使用同一投影；批准不能使原已取消准入恢复有效。原始取消/批准记录保留，修复后由 ControlledActionService 重新触发输出，再走正常准入及审批。新的 trigger 不继承旧审批权威。

该修复不改变 Plan Package reducer 对取消准入的隔离，也不修改任务要求、Owner 决定、完成成果或 active worker 身份。
