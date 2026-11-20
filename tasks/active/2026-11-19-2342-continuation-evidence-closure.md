> 状态: active

# 冻结候选续跑：证据接力与恢复闭环

用户已批准上一轮复盘的修复及真实断点恢复。基线 e122a6b9，隔离分支
`wip/codex-20261120-continuation-evidence`。现场为 K7 Discovery 弃权，
16 Task 和 8 Candidate Reader 已通过；不重新执行已完成 Plan/Impl。

## 本批验收

- C01 历史证据接力 → verify: 实际 Reader 输入解析器及 Goal 消费者都取得授权
  continuation、原 Task Verify/Impl 原始结果与引用的工具事实；保留原 Run/目标，
  错摘要、无授权、错候选拒绝；来源缺失显式保留，不能由计数生成通过结论。
- C02 恢复语义 → verify: 证据不可用与执行错误保持区别；诊断知道当前可用能力，
  不把关闭的 reflect/repair 建议成可执行动作；既有暂停、人工批准及结果门禁保留。
- C03 端到端接力 → verify: mock 覆盖 Candidate 之后的 Discovery 输入及 Goal
  历史来源；真实从原 Discovery 断点继续，观察 Judge/Goal/本地交付或记录并修复
  新的具体阻碍。产品交付与无人资格分别评估，原人工恢复历史不得抹掉。

本 session 负责监工、独立核对和缺口记录；真实 provider 已获用户授权，先 mock。
本批不 push、不测试 Go、不直接改写事件/Task/Run 真值。代码变更记录沿
`docs/impl/unattended-progress-recovery.md`，运行证据放 reports。

## 实施与验证进度

C01/C02 已实施。实际 Reader 派发输入缺失与诊断能力表缺失均 RED→GREEN；
新增损坏证据不能吞掉诊断请求、Goal 消费同源证明、受控范围说明的反例。
真实账本预检找到16 Verify/8 Impl以及原派发/hook，新增来源约276KB；仅物化
不可变证据侧车，尚未改变产品状态或派发真实 provider。

dev-verify --base dev 选中775个测试文件，确定性层9208 passed/10 skipped/1 failed。
唯一失败在基线e122a6b9也复现：prompt-response测试使用默认关闭的resident却期望
observing。为该正例显式启用resident，保留关闭优先规则；相关调用方113 passed。
随后按同一验证计划补跑其余tiers，保留首跑失败记录，不把它重写成一次全绿。
记录位于主工作区 reports/continuation-evidence-20261120。

C03 断点预检确认只触发flow.discovery.requested，Run/候选/代际不变；真实
加载及Judge/Goal/交付仍待验证，因此本任务保持active。

3021c89e 已提交并实际加载；同一候选的 Discovery 已恢复。Reader 两轮只读
部分材料后提前退出，Run Manager 两次自行接回，未新增人工派发；新增提示补丁
明确同轮读取到完成或具体阻塞，实际派发回归先 RED，再修复。该补丁的 provider
效果和最终 Judge/Goal 仍待观察。历史人工恢复计数 17，不声明无人资格通过。

提示增量：基于已验证3021c89e重新规划18个影响测试文件，无缺失映射；420 passed。
另有针对性37 passed、premerge 53 passed、mock flow 10 passed（计数重叠不相加）。
未改变Worker的当前输入或结果门禁；仅后续自然续读派发采用新提示。
