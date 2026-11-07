# 增量任务替换的目标映射连续性

> 状态: done — 40881c9d `fix: reject task replacements that drop goal ownership`

用户授权持续监工修复。FMS04后继的原验收仍在，但替换任务漏了原GOAL-04映射，gap入口仅验命令/结构，未阻止责任链接丢失。语义角色负责目标分配；Runtime只校验被移除前任的既有目标是否仍有明确承接，不自行发明分配。

- [x] 在当前HEAD复现明确goal_claim_ids的前任可被无映射后继删除 -> verify: 真实gap写入入口先失败，拒绝前不发布新Task Map。
- [x] 检查每个被替换前任的原目标集合被显式替换链覆盖，保留合法拆分及已有同合同重放 -> verify: 单个/多个前任、拆分、空前任映射、原前任已不在图的重放。
- [x] 影响闭包、sentinel、mock E2E、相关docs与归档 -> verify: 记录精确测试层级。

现场GOAL-04修复另列安全断点：纯内存回放同合同补映射成功（36项保留），但直接更新TaskContract会改变活跃attempt合同revision。待当前FMS04终态或受控的安全断点处理；不得改写不可变包或伪造完成，不因新门禁倒退其他已准入任务。

验证：新增7项测试初始5项真实缺陷RED、1项fixture重叠、1项通过；fixture改为互斥路径后聚焦90 passed，18文件影响闭包340 passed，serial10、sentinel53、flow-smoke10。验证规划因共有builder升级190文件至全量deterministic；以明确18文件闭包替代，并用--skip-tier deterministic记录。曾误传步骤id导致启动全量，发现后终止独立测试进程组；中断全量不计通过。
