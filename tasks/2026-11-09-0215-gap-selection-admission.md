> 状态: done；0d12c6ea `fix: validate reader gap selections before settlement`

# Reader gap 输入选择在结果准入时回传纠错

LS-v1 evt-b0e31451b28c：reader的gap task写task_verify而非task-verify，结果先获准入并settle，后续task-map amend才拒绝。当前0382a622在真实已准入report上normalize_workflow_read_result返回issues=[]，可复现。既有Call Result协议纠错可回传原operation，本项只提前复用同一机械schema校验，不自动批准/修复语义或重建Plan。

- [x] 当前真实输入与服务入口复现 -> verify: invalid字段产生精确protocol correction，未准入/settle，也不增加semantic attempt；同operation修正后保留语义rejected结果并可准入。
- [x] 提取共享输入选择shape校验并接入reader结果适配 -> verify: canonical task map与reader使用同一校验，无选择声明、有效声明、空/重复/非字符串/缺必需端口分别覆盖；保留最终包的绑定/分区/完整矩阵校验。
- [x] 影响闭包、调用方/协议纠错mock、哨兵通过后提交合dev、加载并记录 -> verify: 不修改既有已准入report，不重派LS业务worker，精确报告测试级别与局限。

授权：用户要求持续监工、分析修复阻塞、遇到问题修复并记录。本项基于本轮真实阻塞，通用语义恢复自动化和纯追加/替换规划改进不在此最小修复范围。

验证：0382a622真实旧report normalize得到issues=[]；正确绑定workflow-read的实际AdmissionService回归RED（错误结果被admitted）。修复后35项schema/准入/消费回归通过，自动影响闭包77文件1122通过；追加原operation纠错调度与checkpoint mock36通过。新增实际SemanticResultSubmitService+provider stop+reconcile回传测试，证明同operation修正后仍为语义rejected且只请求一次operation。测试fixture两处配置/返回值假设已修正，未据此改动生产语义。哨兵53通过；未全量、未真实ProviderE2E。

补充回放发现：AdmissionService会按新schema检查再次提交的旧legacy结果；原即时纠错入口缺少已settled/取消/替换及request hash守卫，可能对已结束operation发指导。新增最小终态/hash守卫，保留协议诊断及原immutable结果，不改变事件准入语义。实际submit+stop+reconcile回归、旧结果回放、直接调用闭包71通过。

最终闭包：101个精确影响测试文件1511通过/76.36s，包含8类mock E2E；最新直接协议调用71通过，纠错liveness13通过，哨兵53通过。自动planner因101>80按文件数扩到全套，与本会话AGENTS要求冲突，已停止该次意外全套并保存日志，手动执行完整101文件闭包；不声称全量验证通过。

落地：06ac1f65合dev，合并后84/53通过；Watcher和Web验证加载06ac1f65，LS UI Impl与OA operation/attempt/dispatch/pane均保留。记录：LS docs/records/2026-11-09-gap-selection-admission/。未push。
