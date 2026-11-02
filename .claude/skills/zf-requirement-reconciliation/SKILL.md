---
name: zf-requirement-reconciliation
description: "编写或评审 PRD/Plan 时，对账原始输入、保留定性约束，并区分当前阻塞与未来待定决定。"
stages: [plan, replan, critic]
tags: [requirements, planning, acceptance]
auto_inject: false
load_on_demand: true
---

# 需求对账与未决问题

用于 Channel 正式 synthesis、Planner 与独立 Critic。先读取当前权威输入及其版本，
再做转换；不能只根据摘要补写需求。沿调用角色已有的覆盖、决定、风险和评审工件
保存结果，不增加第二份需求 Store，不直接修改 canonical state。

## 聊天中的需求来源

Channel 的 Owner 聊天和已绑定附件都是待核对的输入，不要求用户预先标记。
逐条结合上下文判断产品需求、补充或更正、候选草稿、未决问题和平台操作指令；
不要把“生成 PRD”“启动讨论”等操作指令提升为产品需求，也不能把候选草稿
本身当成权威需求。保留消息 ID、正文摘要和附件引用／摘要；明确时间顺序、
授权来源、冲突以及更正对既有条款的影响。历史显式来源标记仍是证据，不排除
未标记消息。来源范围必须绑定本次上下文包与 PRD 版本，后续消息只能成为新版本输入。

## 从来源到可观察结果

按来源条款逐项对账：来源 ref/位置 → 现有 requirement/AC ID → PRD 或规格位置 →
保留、等义转化、移入 companion、冲突待定或经授权排除 → 决定/证据引用。
这是一种阅读和评审结构，不是新的运行时 schema；使用当前角色协议的字段和 sidecar。

- 功能标题不能代替异常路径、权限、事务、数据约束或定性要求。将“容易理解”等
  要求保留原意，提出可审查的场景和观察方法，不能擅自发明产品阈值。
- 细节移到 companion 时保留完整正文及引用；不能用“下游会处理”掩盖丢失。
- 两份来源冲突时同时列出，不静默选一份。新增 Owner 决定须有实际授权来源。
- 产品 Non-goal 与“本轮只批准规划”分开；后者不取消未来交付义务。
- 已有稳定 ID 沿用；拆分/合并条款保留旧新映射，原 mandatory 范围不因改写消失。

完成后反向抽查：每条来源能找到去向，每条新增规格能解释来源或明确标为提议。
Critic 以具体遗漏、矛盾或不可执行条件提出返工；无问题可明确记录无阻断发现，
不要求凑 finding 数量。

## Owner 在 Plan Preview 中修订

先区分纯措辞调整与产品要求变化。改变行为时，给出受影响的 Requirement 文本叶子与
稳定 claim ID 的精确 before/after；同时检查澄清回答、例子和验收中的同一约束。
保留未改条款、强制性和验证责任。引用以外的文档正文不得借机改写。
只有用户实际 Apply 的受控修订及其新 Requirement 快照成为当前 Run 权威；草稿、
Agent 对话和旧版本都不能替代它。Planner/Critic 使用新快照生成一致的 Plan/AC/test matrix，
不再反问用户已明确且已授权的同一产品决定。新 Plan 仍按原有政策等待审阅。

## 按最迟决定时点处理问题

每项未决问题写明：影响的行为/Task、决定者、最迟在哪个动作前解决、暂行假设、
允许继续的范围、重访触发条件及决定来源。未知不等于可以默认批准。

| 情形 | 当前动作 |
| --- | --- |
| 当前写入命令的事务边界未定 | 阻塞该命令的执行计划；无关的只读调查可以继续 |
| 后续通知渠道未定 | 保留通知 Epic 与验收义务，触达该接口前重访 |
| 验收服务不可用 | 记录环境阻塞与恢复责任，不报告产品通过 |
| 等待尚未产生的 Plan 批准/最终测试 | 规划生产者、触发条件和门禁，不以未来结果阻塞首次提交 |

Channel 当前协议若要求 owner frontier 清零，必须遵守；本方法不允许绕过其门禁。
Planner 的草稿可保留未来问题；受影响任务只有形成完整合同并经现有准入后才能派发。
如完整 Goal 尚不能形成合法 Task Map，保留草稿/阻塞或请求有权者明确阶段范围，
不能靠删 AC、伪造已解决决定或提交占位任务继续。

## 完成检查

在既有报告中指出本次核对的来源版本、保留/转化/延期项、仍阻塞的具体动作与责任人。
评审者核对实际正文及决定证据。加载本 Skill 或覆盖表字段齐全，不代表语义对账通过。

## PRD 五项质量闭环

适用于 Channel 的 conversation 与 multi_lens，以及后续 Planner/Critic。沿既有
`decision_log`、`requirement_coverage`、`verification_commands` 和
`implementation_companion` 保存清单；不建立平行需求或 Task Store。

1. **来源与兼容基线**：在 companion.source_baseline 中逐项写旧版本、接口/Schema、
   部署环境、来源 ref/digest、核验人/方法、结果与缺口。明确区分“来源已读”、
   “artifact 可读”和“事实已核验”。一个路径、摘要、目标架构或作者声明不能证明
   v1.3 等旧系统的部署/兼容事实。缺资料先由具备访问能力的成员核验；确实取不到时
   列责任人、最迟需要时点与受影响范围，不伪造稳定 artifact。
2. **Owner 决策**：对适用的质量阈值、SLO、容量、部署、数据驻留、灾备与留存，记录
   授权消息/问题 ID、原始回答、决定者、影响行为、最迟决定阶段、暂行假设、允许继续
   范围和重访条件。没有批准的数值保持 TBD；不擅自把“暂缓”改成 MUST 或非目标。
   错误码、Unicode 字符长度、normalization、fallback 等已定约束在正文、场景、AC、
   companion 中逐项核对，不能用“或其他结果”稀释原决定。
3. **独立评审**：按来源和风险检查资深开发、安全等职责是否覆盖；要求独立视角时
   记录真实 member/reviewer、职责、检查范围、同版 PRD ref/digest、发现与处置。
   邀请、角色名、签字数量或作者自评都不能代替实际 review。缺角色走现有受控邀请/
   注册，不授予额外权限；缺证据如实标未完成。首次 synthesis 只列当前版本的评审
   计划，不能要求尚未产生的文档先完成签收。
4. **验收数据与证据**：在 companion.acceptance_data_plan 中按 AC ID 记录数据来源/
   版本、真实/批准脱敏/mock/未知类别、授权/脱敏要求、环境、命令、负责人、预期观察、
   producer_paths 与执行阶段。引用既有 verification_commands ID，不再复制命令目录。
   未执行写 NOT_RUN；环境/授权阻塞说明责任与恢复条件。声称通过时必须有对应版本、
   运行结果及证据 ref/digest，不能由计划、空路径或 mock 冒充真实产品验证。
5. **质量清单与交接**：作者自检标明身份，独立 reviewer 对原文及当前版本形成自己的
   checklist。检查价值/范围与阅读体验、独立故事、原子需求和各自场景、可度量结果、
   权限/状态/异常、关键实体、依赖/风险、来源与决定一致性；不能靠全篇词数或场景数
   宣布逐条覆盖。companion.handoff 写来源/Requirement/AC/命令到当前 PRD 和后续
   plan/tasks 的索引、生产者、触发条件与状态。ZaoFu canonical PRD、prd-plan.md、
   task_map.json/source_index.json 可映射 Spec-kit 的 spec/plan/tasks；固定文件名
   不是质量门槛。OpenSpec 保留 Requirement/Scenario，只有变更才区分
   ADDED/MODIFIED/REMOVED。Spec-kit analyze 在 tasks 生成后运行，assess 是可选扩展。

产品正文说明 what/why、用户可见行为与有来源的约束；实现设计在 companion。
不能给所有产品强加 ZaoFu 控制面、事件账本或某技术栈。已有架构约束也不能因分层丢失。
分别报告文档质量、来源忠实度、规划就绪度、实施授权和产品验证；文档质量 PASS 可以与
真实产品验证 NOT_RUN 并存。checklist 的完成标记表示需求已审查，不表示功能已实现。
独立评估的版本化方法与输入一起冻结；作者的自检不能替代该评估。
