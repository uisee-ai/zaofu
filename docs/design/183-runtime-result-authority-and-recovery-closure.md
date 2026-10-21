# 运行时结果身份与恢复效果闭环补丁

> 状态：P0/P1 implemented；现场恢复验收 active；2026-10-21。本设计由操作者明确批准实施，完成状态以关联任务的验证与提交为准。

## 问题与证据

洛书 `loshu-impl8-20261020` 的七个本地任务发生 29 次结果拒收。RTK 原始事件
`evt-3c4c0c4505cb` 返回 authority `r7f8…`，与 dispatch、合同快照、TaskStore 一致；
fanout child 的排队 payload 仍为 `r676…`，结果绑定将正确身份及自检身份覆盖成旧值。
legacy implementation adapter 又将 `impl_self_check` 丢成空 `self_check`。
这两项均在基线 `8406826` 上通过真实输入的只读函数回放复现。

恢复链将带 `task_contract_unsatisfiable` 的运行时故障归类为 design_issue，进入
discovery/replan。恢复动作仅以请求事件出现作为成功；随后 reader 失败耗尽 stage cap，
`evt-3f8927ad7587` 将 Run 封为 blocked。Autoresearch 的隔离配置因 86 项技能多来源
冲突而 validate_failed，候选被 dismissed，未形成源码修复。

## 权威与行为契约

1. TaskStore 管理当前合同；不可变 operation request 和 dispatch snapshot 绑定该次执行。
   排队 payload 不能覆盖派发身份。补充遗漏身份只能来自匹配 operation/request hash 的
   dispatch；显式冲突、真实过期返回仍须被拒收。retry/restart 必须保持这一规则。
2. legacy 适配必须保留实现证据及身份。`impl_self_check` 与 `self_check` 是输入兼容名，
   输出仍使用现有唯一 result schema；冲突报告错误，不通过改写证据来“修复”身份。
3. 内核结构化准入证据优先于 worker 对故障类别的自述。真过期结果按过期处理；
   harness 接收故障交由 Run Manager/诊断处理；产品范围判断交给 agent/skill，
   不能把任何 stale 错误一概认作 harness bug。
4. action accepted/applied 是交付事实，effect passed 才是恢复效果。
   重启/重新规划请求不能直接关闭 recovery case。效果绑定 case、run、task、generation，
   无关成功不能清除故障计数；失败和超时进入既有有界恢复策略。
5. Autoresearch 使用固定源码来源和隔离状态。相同物理技能来源去重，真实不同版本仍报错。
   环境失败表示诊断未完成，保留候选与可审计失败原因，不冒充已证伪。
6. 历史终态事件保持不可变；终态后的诊断/维修在独立操作中执行。现有
   `stage-replan-new-generation` 可在显式授权后追加同 Run 新阶段代次和 active 事件；
   不直接设置状态或绕过 cap。保留已有提交、未提交工作和历史事件，不编辑冻结 Plan。
7. Orchestrator agent 给出语义建议；Kernel 维持机械调度。shadow 建议必须明确未应用，
   不把 agent enabled 当作故障已接管。产品接入事实不由 runtime 补造。

## 实施切片与验收

| 批次 | ID | 交付及验收 |
| --- | --- | --- |
| 第一批 | P0-1 | 派发/结果身份一致；排队旧身份、重试、真过期结果正反例及真实 caller 回归 |
| 第一批 | P0-2 | legacy 结果无损适配；自检、身份、引用保留及冲突拒收 |
| 第一批 | P0-3 | 准入故障分流；有结构化 runtime 错误时不误走产品 replan，产品缺口仍可重规划 |
| 第二批 | P1-1 | 请求交付与效果分离；只有匹配效果关闭 case，无关成功/失败/重复回放受测试 |
| 第二批 | P1-2 | 诊断 worktree 技能来源和版本一致；实际配置 validate 通过并携带故障输入 |
| 第二批 | P1-3 | 诊断启动失败保留未确认候选；归档原因、允许有界重诊断，不生成无证据补丁 |
| 第二批 | P1-4 | 终态维修与受控续接；保留旧终态，授权新阶段代次，复用经重新验证的产物，状态一致 |

先写失败测试，记录 RED 原因，再实现 GREEN；mock E2E 覆盖实际 runtime caller。
使用 dev-verify 的影响闭包、sentinel gate 与相关确定性 E2E；真实 provider/浏览器验证
单独记录，不将 mock 并发计为真实产品八路并发。

## 现场恢复与 Q01–Q03

统一 watcher、Run Manager、Autoresearch、Dashboard 的运行源码及版本。盘点 task refs、
worker commits、dirty worktrees 后建立续接证据；仅复用同合同且重新验证的结果。
先用一个结果验证交接，再恢复 ready frontier；Verify 依赖候选集成，不能提前伪造 candidate。

Q01 正式工程/复用边界，Q02 真实 API/字段/联调，Q03 身份/会话/授权/撤权传播，
分别登记来源、版本、责任人和实际验证。现有文件只有本地前端授权，外部事实尚未齐备；
用户已要求全部完成，因此主动查证并索取缺失事实，不能通过 mock 或虚构负责人关闭。

## 关联任务

- [第一批 P0](../../tasks/done/2026-10-21-0316-result-authority-p0.md)
- [第二批 P1](../../tasks/done/2026-10-21-0316-recovery-closure-p1.md)
- [现场恢复与接入验收](../../tasks/active/2026-10-21-0316-loshu-resume-q01-q03.md)

历史架构 142 文档未包含于当前公开 checkout；以 AGENTS.md、现有 store/event/service
边界及回归测试为准，不补写或假称读取缺失文档。

## 补丁验收与限制

最终源码影响闭包 530 passed，serial 10 passed，sentinel 51 passed，flow-smoke 10 passed；
实际诊断配置 validate 通过。完整确定性套件发现 20 项未修改基线已有失败，已在
独立基线复现，具体分类与日志见关联 P0/P1 任务；未将聚焦验证称为全套全绿。

恢复请求交付后保持 verifying；匹配 worker 请求因果链或对应任务完成事实才结算。
新效果等待具有 1800 秒默认期限，可由动作 effect_timeout_seconds 指定，历史未声明
期限的 pending 不追溯超时。普通 task_map.ready 不可代替待验收的恢复效果。
诊断失败保留 unverified；重复消费幂等，修好环境后可发起新的有界诊断请求。
