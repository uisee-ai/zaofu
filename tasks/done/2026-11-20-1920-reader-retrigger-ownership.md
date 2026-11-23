# 已接纳Reader重触发排除旧失败恢复竞争

> 状态: done — `69d5f3a1` fix: retire reader recovery owned by an admitted successor

真实8×8接续中，受控stage-retrigger已启动8路同候选审核。RM仍将其原fanout的test.failed投影为needs_stage_replan，19:18:08发起另一轮，19:18:19取消正在工作的8路审核，并丢失后补的恢复说明。自动恢复与已接纳的继任者发生竞争。

- 在恢复投影及执行共用的stage-replan判断中，识别内核已接纳的同Run/同stage、显式redrive因果链继任者 -> verify: ControlledActionService重触发后真实恢复checkpoint入口不再提供旧失败；无需改写失败、success或运行态。
- 边界保持保守 -> verify: 异Run/异stage/异来源、未接纳触发及worker自报不屏蔽旧失败；继任者自己的新失败仍可恢复；乱序/cycle不获认可。
- 按事件原始前缀重放此次真实竞争 -> verify: 修复前产生旧replan，修复后不产生，保留原发现与其归属；单测不替代接续验收。
- 聚焦、影响闭包、哨兵与mock通过后提交部署，从合法新审核代际恢复 -> verify: 全部变更发生在已关闭观察窗口之外。

验证：1红7绿的实际ControlledActionService→恢复checkpoint回归转绿，54聚焦、4161影响闭包（7 skipped）、53哨兵与10流程通过。真实事件前缀21149条：旧代码planned=true，修复后planned=false/owned_by_admitted_reader_retrigger。未写运行态或改历史结果。部署前精确kernel provider_stop_confirmed+cancelled operation以及宿主进程/tmux不存在共同证明8个Reader已停止；保留未收到stop hook的旧记录，不伪造Provider收尾。

闭环：真实前缀21149条回放证明旧恢复被排除；部署后8路现有c6870392审核已启动，未再出现旧失败竞争。既有取消历史保留，当前业务验收未完成。
