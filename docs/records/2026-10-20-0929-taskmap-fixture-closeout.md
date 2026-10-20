# Task map 最后单项夹具修订

> 状态: active。承接[修复验收记录](2026-10-20-0847-taskmap-repair-verification.md)，限定修复正式 Critic 的 PC-BC-F3。

## 继续执行的依据

用户原请求为“创建新的worktree,切换到新的worktree 上面发现的taskmap 高缺陷生成backlog ,执行backlog 修复”。
本次继续属于该修复授权，不存在新的用户消息或人工 Plan 批准。
前一有界代际已真实结束：Critic `evt-913761eeea2a` 明确 PC-BC-F1/F2 关闭，
仅因新增 PC-BC-F3 的矛盾负例返回 reject；失败为 `evt-bdcc9ad20bb5`，
升级为 `evt-58178a1f369d`，blocked 为 `evt-ebcee2ca71ca`。
这是语义拒绝，不是供应商容量故障；不得将其写成外部服务阻塞或忽略该结论。

此前按轮次安排的操作预算不等于用户取消修复授权。操作方基于这项已复现且范围明确的
剩余缺陷，通过 `stage-replan-new-generation` 继续有界收尾，保留旧事件与计数、
既有每代际重试上限和人工 Plan 审批。该记录描述原授权下的操作判断，不伪造新的 owner 答复。
不批准产品 Impl、数据库、部署或 push。

## 唯一修订目标

基准为 `fanout-prd-plan-evt-15069565/planner/artifacts/plan`，
task map SHA-256 `99db3de3d7298c570cbeef8eb16584a3e90346b6a3c2c18e2ac01a54b29bedf7`。
保留 54 任务、237 AC、171 命令、2,011 用例、2,015 命令收据绑定、全部来源及未决 Q；
保留已通过复核的权限方向、分端口交接、本片/阶段/最终责任和收据规则。
不以这次单项收尾为由新增任务、批量扩充用例或重写无关规格。

PC-BC-F3 的负例 `PERM-L4418-A06-deny_without_warehouse` 同时声称
“P1 未关联仓库”和“P1 合法关联 W”。按正式反馈：

- 在 `permission_cases` 的 `$.action_cases[351].vectors[16]` 和
  `clause_test_cases` 的 `$.tests[2005]` 同步明确唯一前态：
  P1 保留有效派生编辑能力，但仓库关联为空。
- 若请求携带物料 M，明确 M 属于仅由其他项目关联的 W，P1 不得回退到 W。
- 同步 target、before、action、expected_after 与最终浏览器消费描述；
  保留合法关联且派生编辑有效时允许的独立正例，保留流程/记录模式，
  不增加职责，不调用未来作业制造前态。
- 拒绝时完整受保护状态不变；最终 `CASE-CANDIDATE-MATERIAL-MODE-SWITCH`
  继续消费修正后的具名负例。

最新通用方法为当前 worktree 的
`skills/zf-plan-execution-readiness/references/execution-consistency.md`
（`2ea8371 fix: require coherent preconditions for negative test cases`）。
重点是替换冲突字段并使用独立夹具，不能将异常条件拼接到仍然成立的正例前态。

## 复核与提交

Planner 输出精确 JSON 路径的前后差异，检查同类派生负例是否存在相同拼接问题；
在限定范围内修复必要消费者，更新实际正文摘要、selector 和交接说明。
保持先前收据/角色允许与拒绝规则，重新跑机械闭包及完整 Package/Preview 读取。

Critic 独立核对本次字段差异与来源 L4400–L4401、L4418。
完整范围审查可明确复用已绑定且正文未变的前轮证据；必须说明复用范围、当前差异及
反例，不能用作者绿灯代替判断。按当前 attempt 的 result preflight/submit 提交真实 verdict。
只有正式候选通过独立审查、实际新 Preview 与下游读取验收后，才能关闭本批任务。
