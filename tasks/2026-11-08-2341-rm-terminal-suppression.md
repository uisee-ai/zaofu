> 状态: done — d413cdb8 fix: retire completed task recovery suppression by run

# 已完成任务退出旧恢复抑制并隔离运行终态

用户授权持续监工、修复并记录。基线5b3dccc6只读复现：FMS-16-GAP-EVIDENCE-01在19:05:28以kernel task.done evt-f3f2795d2cc1完成、TaskStore为done，但23:40 active_immediate_replan_task_ids仍返回它。根因：抑制入口只检查后续计划替代/run终态，没有关闭同任务正式完成；并且任何run.goal.completed/blocked都可释放本run抑制。该残留不是当前FMS12输入读取或FMS24 Q06阻塞的原因。

- [x] 补真实入口回归 -> verify: kernel同任务同run完成释放；其他任务/其他run/外部完成不释放；已完成后新失败重新受控；不同run终态不解除本run；别名沿用已有run_scope。
- [x] 有界实现 -> verify: 只消费正式终态，不把普通dispatch/新计划提议视为原合同问题已解决；保留原替代证明，完整事件快照，按调用重新计算。
- [x] 现场完整回放 -> verify: FMS16残留精确移除，其他pending恢复不改变，记录源事件与范围。
- [x] 定向影响闭包、哨兵、mock E2E -> verify: 通过实际RM投影调用测试；不宣称full或real-provider。
- [x] 提交、合并dev、受控加载、记录清理 -> verify: 两项在途worker身份和进程保持，原用户修改保留，不push。

现场证据：LS-v1 docs/records/2026-11-08-rm-terminal-suppression-observation.json。为何OA/RM未消除：任务完成和重排抑制是两个投影入口，旧抑制没有消费正式task.done；运行角色没有自动改写这一确定性路由逻辑。

验证：12新回归先4RED/8GREEN；实现首轮88通过，自动72文件影响闭包1311通过/7跳过。首次53哨兵中1项结构检查失败（原模块1005行），未合并；将新增终态匹配职责放到现有rework_progress，原模块996行，未放宽门禁。结构调整后最终307直接调用/共享范围闭包、53哨兵、10 mock E2E全部通过。不是full pytest或真实provider证明。

最终完整175870事件及TaskStore/session冻结回放：pending输出全部不变，只从active抑制移除正式完成的FMS16，无新增抑制；绑定正式task.done evt-f3f2795d2cc1，临时副本清理。此为正确性修复，不宣称性能提升（两次active约1.119→1.453秒）。自身backlogs为空；其他候选未动。实现d413cdb8→dev合并6b5f3087，合后88定向+53哨兵通过；23:47–23:48受控加载Watcher3728639/Web3729494，health/UI200，FMS24 Impl和FMS12 Verify两项operation/attempt/dispatch/workdir/pane/PID完全保持，未push。
