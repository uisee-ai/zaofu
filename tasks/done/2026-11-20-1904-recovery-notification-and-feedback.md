# 候选恢复通知关闭与说明传递

> 状态: done — `8255b300` fix: preserve recovery feedback and notification outcomes

真实接续：Web已接纳关闭误报通知（evt-ed0bcc3bf382），RM仍记录human_decision.rejected（evt-f25b0d126e21）；只读投影确认通知已关闭，拒绝计数是消费路径误分类。另审查恢复入口发现stage-retrigger不传operator_feedback，无法将已核对的宿主收据和原需求范围说明送入新审核。

- 合法dismiss仅关闭通知并记录已应用 -> verify: 实际Web到RM tick，保持ack/source身份与reason、不执行恢复、不产生产品通过；重复消费幂等，未知token保持拒绝。
- 显式reader retrigger保留operator_feedback -> verify: 实际ControlledActionService到冻结repair context，原target/任务/工件保留；非法feedback在产生新trigger前拒绝，无feedback仍保持既有行为。
- 测试、提交后部署 -> verify: 聚焦与影响闭包、哨兵及流程通过；真实重试仍独立判断所有业务义务，反馈不是验收豁免。

验证：3项红回归已复现；修复后22项聚焦通过，当前增量影响闭包6112 passed、9 skipped，53哨兵和10流程mock通过。采用上一已验证提交5a0a9682作为增量执行基线，同时保留相对dev的全分支验证计划；真实接续另行记录。

闭环：真实evt-c18077d908f2正确接纳dismiss并保留ack来源及reason；8/8审核输入固定critical-reader-repair-context、原mixed发现和18份收据。后续发生的旧恢复竞争另由1920修复，未追认候选通过。
