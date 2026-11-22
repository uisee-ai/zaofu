# 8×8 最终审核恢复闭环修复

> 状态: active — 按 Owner 已授权的遇错修复、评估后断点恢复执行；实际运行基线 1d6cb7f7，本批不修改业务验收标准、模型、权限或采证限额。

## 实证与边界

实验 workflow-ddd320aa0dc7c2ef 已完成16个原始Task，真实8 Impl +8 Task Verify重叠88.551秒。两轮最终Reader聚合失败，第二轮4 abstained+4 rejected，未达Judge/Goal/交付。窗口已记录未通过，再通过受控run-pause暂停（evt-7869e08eb0c7）。现有完成任务、Plan、所有失败及宿主收据保留。

证据目录：/home/min/workspace/zaofu-worktrees/eight-unattended-20261121T132814Z/reports/eight-unattended-20261121T132814Z/。入口：qualification-window-closeout.json、candidate-retry-host-binding-gap.json、second-host-browser-collection-failure.json、research-reader-long-tail.md。

## 已实施并验证

- [x] R1 Candidate mixed/semantic 聚合先有持久分诊owner：统一Run/锚点绑定，防止通用stage resume机械重复Reader；通过既有OA checkpoint保留全部成员。step -> verify: 无event.task_id但有合法Run/PDD的真实聚合经RM实际入口 -> 一次分诊，重放/重启不重复，未决语义不被自动通过或丢弃。
- [x] R2 合法固定输入恢复复用既有冻结身份及host收据：用精确绑定的Agent裁决/恢复授权接通现有受控入口；不把历史mixed改写成infra，不扩权限；真正产品修复继续既有受控Task/Plan路径。step -> verify: 混合聚合→裁决→合法fanout→新Reader读取/准入，既有合法收据不改写、无需重复执行已适用命令；跨Run/真实合同变化/缺失成员失败关闭。
- [x] R3 宿主执行后采证失败保留事实并进入只读诊断：保留受界限约束的exit/stdout及采证错误详情；既有失败也有持久诊断owner，未知不盲重试。step -> verify: 单文件超限/符号链接/总量超限等实际collector错误→失败工件/原start身份→RM诊断；必要媒体不足仍不能验收通过，安全gate仍阻止未经许可的执行。
- [x] R4 门禁与调度投影一致：动作preflight通过但continuation被阻塞时，显示准确门禁和诊断下一步。step -> verify: 两条verification gate→next_operation为空时不得显示无阻塞的自动执行；每道同Run unknown host门禁独立认证后允许只读诊断可继续，不放开其他副作用。
- [x] R5 消除恢复与结果编写中的无效往返：已完整observed交付但发生压缩时，resume先给历史交付/当前记忆未知/精确恢复入口；不默认重吐整批正文；不完整交付仍补缺页。Product Acceptance错误返回字段路径/合法枚举。方法沉淀到现有skill/prompt。step -> verify: 压缩后历史完整、缺页、伪造输出、换来源版本分开测试；结果准入的完整交付门槛不变；精确枚举错误可一次改正。

## 验证与交付

使用隔离环境，清除继承的ZF_*变量；先证明失败，再修复。跑影响闭包、premerge sentinel及相关确定性mock E2E；不把mock称为真实provider证明。按现有源码基线做真实断点重启/恢复，明确其属于修复后的观察段，不回写本轮无人窗口为成功。

暂不纳入：业务min/max/average验收裁决、任意提升采证限额、改变模型矩阵、清理旧非本批worktree、合并或推送dev。


## 现场补充（本批R4）

受控暂停后旧运行时仍产生orphaned_task（evt-de3817304d5f）及goal idle rescans
exhausted（evt-d9e0a5766389）。修复两处入口识别Run/项目暂停，并在orphan恢复计时
扣除暂停区间并集。step -> verify: 暂停不重排Task/不消耗rescan，恢复不立刻超时，
其他Run继续受超时监测；现有误报只通过精确受控dismiss并保留审批/理由/历史。


## 验证结果

隔离清除继承ZF_*环境，基于实际运行源码1d6cb7f7执行本批精确变更路径的
`dev-verify.py plan/run --base dev --paths ... --workers 4`：最终影响闭包12185通过、
18跳过；premerge sentinel53通过；flow mock E2E10通过。回执与完整路径参数保存于
`/tmp/zf-candidate-recovery-final-{plan,command,receipt}.json`，日志为
`/tmp/zf-candidate-recovery-final-closure.log`。覆盖既有candidate恢复、design199
mock E2E及实际主tick caller；Agent语义结果、tmux和容器在确定性测试中为mock。

针对性验证：诊断及直接调用115通过，暂停及调用66通过，2/8成员聚合与OA恢复及新
Reader消费旧宿主收据串联26通过。计数存在重叠，不相加。最初闭包暴露旧collector
异常断言、收据success fixture错误及manifest恢复case吞并，均已修正并重跑。
结构门的单模块越1000行已通过提取Candidate preflight至其责任模块解决。

实现已达5/5；真实断点部署和观察后再归档。无人窗口仍失败，未宣称真实交付完成。
