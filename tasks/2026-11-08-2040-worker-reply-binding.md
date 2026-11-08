# 阻止终态或换代后的定向操作员回复

> 状态: done — `0d6d82c7` `fix: reject stale operation replies before delivery`

授权：用户继续监工LS-v1，分析修复阻塞并记录。生产20:22:07的evt-71b3abe3236b带operation_id与task_id；目标20:23:25 dev.blocked后，20:24运行时仍发送continue current task，worker多读5分钟且写域已回落。安全hook拒绝旧工作区写入，没有伪造产品结果。根因候选为AgentViewRuntimeMixin回复执行忽略operation/attempt与当前绑定。

- 原dev上用实际Orchestrator入口重放已终止定向回复 -> verify: 无transport发送、无briefing文件且有caused拒绝；先记录RED。
- 定向回复执行时机械核验operation/task/instance/attempt当前身份 -> verify: terminal、旧attempt、换lane/任务和未知operation拒绝，合法当前及无绑定resident回复兼容。
- canonical身份与事件结果保持原owner，失败可追踪/重放不重复投递 -> verify: focused调用方/Event契约/队列测试、哨兵、mock E2E；不运行真实provider。
- 更新199实施说明，提交并合并dev，受控上线保留LS在途 -> verify: 每个operation/attempt/pane绑定不变，健康检查通过；记录发现/修复/验证/恢复，未请求push。

实现：新增worker_reply_binding，在实际AgentView回复入口执行前校验显式operation当前running、尚无已准入结果及请求提供的task/attempt/dispatch/lease/run/instance；拒绝发生在生成工件及transport之前。无绑定resident保持兼容。

验证：实际Orchestrator排队重现先1 RED；最终相关模块/调用方/共享事件闭包156 passed，premerge哨兵53 passed，三流mock E2E10 passed。dev-verify planner因反向调用190文件退化全量，本轮按AGENTS有界闭包执行，未跑全量或真实provider。生产历史只读回放单独记录。

交付：合入dev d29329c6，合并后40定向/53哨兵通过；20:50受控重载Watcher3441756与Web3442011，三在途operation/attempt/dispatch/pane/PID保持，Web200，运行源码d29329c6。生产旧请求在请求时允许、投递时拒绝的只读回放已保存。无push。
