> 状态: active

# 无人执行故障修复及真实 PRD 复验

用户批准：按故障复盘建议修复两批缺口，完成后继续真实 PRD 验证。来源：reports/unattended-faults-20261116T090219Z/review.md；实现说明追加 docs/impl/continuity-handoff-spec-001.md。

- R1 P0 Reader先准备目标再激活 -> verify: 实际fanout caller在受管Provider持锁模型下成功派发；外部持锁不激活，释放后同一fanout继续。
- R2 P0 工作流等待和重试有界 -> verify: 跨重启保持等待期限，超限形成基础设施失败和Run Manager诊断；Task done不屏蔽阶段故障；正常释放和已完成旧实例不误报。
- R3 P1 诊断效果与业务效果分开 -> verify: 绑定request的建议完成诊断，无关建议不能结算，不等待task.done。
- R4 P1 候选依赖解析 -> verify: 无依赖包和父级依赖可运行；真正缺失声明依赖仍阻塞。
- R5 P1 Verify提交纠错 -> verify: 失败枚举明确、同一operation反复拒绝有界升级，保留独立验收与准入约束。
- R6 P1 宿主验证预检和受限授权 -> verify: 真实Docker/端口与精确合同命令执行能力在复验前确认；不扩大Provider权限，人工和自动路径明确区分。
- R7 P2 明确退出快速恢复 -> verify: 仅确认无在途/资源持有者时提前恢复；未知进程或活跃持有者保持保护。
- R8 真实复验 -> verify: 固定源码，恢复隔离PRD；Reader占用释放后真正派发，候选验证/Judge记录真实结论，遇缺口记录并修复。

不触碰主dev脏文件，不merge/push，不删除实验成果。先RED回归，再实现，再影响闭包、sentinel和mock E2E，最后显式真实Provider/Docker层。

## 实施与验证进展

R1-R5、R7代码/方法与定向回归已实施；R6采用现有受控host-assist及真实能力预检，
保留精确manifest审批，未新增任意宿主命令或未来变更的自动授权。
补充修复实测观测漂移误报、Dashboard投影失败保留、operation-v2交接歧义。
最终影响闭包481文件：6032 passed, 8 skipped；serial10、sentinel53、mock E2E10；
Web typecheck/unit通过。详情 reports/unattended-repair-20261116T1053Z/review.md。
R8真实复验待固定代码后执行，因此任务暂不标done。

## 真实复验中新增缺口

实现提交593b0441（feat: bound reader recovery and separate diagnosis effects）。
占用释放后9.85秒派发，两个Reader各激活一次，无反复挂起；lane-1真实结果准入，
报告Docker/端口权限阻塞。lane-0误判候选输入绑定失效：已补充独立候选绑定证明，
RED→GREEN；复用结果准入currentness，保留所有身份/摘要/冻结/取消检查。
补充影响闭包756 passed/7 skipped，sentinel53/mock10通过。
当前冻结候选d8088354在受控宿主路径完成测试、构建、Docker桌面及移动端断言，
均退出0；明确记录operator_authorized_host，尚待新的Reader/Judge独立结论。
