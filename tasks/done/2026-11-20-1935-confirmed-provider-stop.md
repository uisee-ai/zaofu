# 已确认取消停止进入Provider活动投影

> 状态: done — `4d1564df` fix: retire provider turns after confirmed cancellation stops

第二次候选接续8路全部provider_turn_active延期。已有精确kernel取消与provider_stop_confirmed收据、无残留进程，但活动投影只看Provider hook关闭及其他生命周期，没有消费取消停止的正式回执。

- 只清除匹配可信取消开始/确认停止的一组旧turn -> verify: 真实stop_cancelled_dispatch→Orchestrator活动查询释放旧占用；不写Provider stop hook、不增加业务通过。
- 保留身份和竞争边界 -> verify: 错取消/dispatch/operation/attempt/hash、未确认、非kernel、缺取消开始均不能清除；开始停止后新出现的turn继续活动。
- 真实账本只读重放 -> verify: 修复前8活动，修复后8退出；后续启动从已接纳pending fanout继续，无需再建审核代际。
- 聚焦、影响闭包、哨兵和mock后提交部署 -> verify: 当前观察窗口先关闭，部署与恢复发生在窗口外。

验证：使用与实际Orchestrator一致的kernel EventWriter重新对基线复现失败；69聚焦、3267影响闭包、53哨兵和10流程通过。真实账本只读重放由8个活动角色变为0；未知/不匹配回执和停止期间的新turn反例保持。未补写hook或修改历史状态。

闭环：真实账本重放8→0活动；部署后从同一pending fanout直接派发8/8真实Reader，provider_turn_active延期消失，未新建trigger。验证证据见reports/eight-qualification-20261120T122929Z/recovery3-dispatch-proof.json。
