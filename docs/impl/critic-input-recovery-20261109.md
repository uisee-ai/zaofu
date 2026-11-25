# Critic 输入预检与失败恢复

实现任务：[2026-11-09 Critic 修复](../../tasks/active/2026-11-09-0430-critic-input-recovery.md)。

## 边界

Agent 通过技能承担范围判断、规格压缩及评审覆盖；Kernel 仅计量固定输入、检查配置预算、
绑定读取身份并收敛生命周期。没有自动删任务、裁剪需求、批准 Plan 或放宽读取 gate。

在 `workflow.stages[].aggregate` 中可配置 `review_input_max_bytes` 和
`review_input_max_pages`（非负整数，0 保持旧部署行为、不启用相应上限）。示例：

```yaml
aggregate:
  review_input_max_bytes: 4194304
  review_input_max_pages: 512
```

对于 `kind: PrdFlow`，在该文档的 `spec` 下使用 `reviewInputMaxBytes: 4194304`
及 `reviewInputMaxPages: 512`，展开后只绑定 Plan aggregate，不改变 impl/verify lane。

仅作用于 Plan synth，在 CanonicalHandoffResolver 固定 manifest、建立必读策略后，
operation 注册及 provider 发送之前检查。按必读选区 SHA 去重计量正文；分页数是按
8192 字符计算的下界，不含元数据/JSON 转义开销；旧快照缺少字符数时使用 UTF-8 四字节
上界推导保守的最低页数，字节预算仍独立生效。配置任意上限时，未知大小不能当作 0
通过。超限保存诊断 sidecar 并沿 reader execution failure 路径返回，不形成 Critic
语义批准/否决，不消耗语义 replan 额度；重试必须先提供实际修复和受控恢复决定。

## 只读诊断

```bash
zf plan inspect-review --attempt <attempt_id>
```

这是本地操作者元数据查询，不提供 artifact 正文、不签发 worker 凭据、不产生读取回执。
读取固定清单及当前 ledger，显示 available 与 required 的去重字节数、最大来源、评审模式、
必读覆盖及最后读取时间。覆盖计数只证明已记录完整输出，不证明当前模型理解。
查询中的预算使用当前操作者配置，并明确标记 `current_operator_config`，不冒充历史 attempt 配置。

范围对账使用已绑定的新旧 Task Map 和 rework task_ids；输出实际变化数量、范围外任务
样本及全局字段变化。该比较不是局部范围证书，也不会替代独立 Critic 的语义判断。

## 生命周期

新 synth timeout 标记 `failure_kind=infra`、`failure_class=reader_execution_failure`。
reader recovery sweep 同步关闭已失败 synth 对应的 operation；旧 `synth_timeout` 事件也
可以按精确 dispatch/run/request/attempt 关联收敛。后来的 redrive、不同 attempt、请求身份
不匹配或已终止 operation 不受旧事件影响。使用 WorkflowOperationService 发布终止事件，
不直接改 JSON 投影。收敛不自动解除 run 的 blocked 状态、不补造语义 verdict。

## 方法与恢复

Planner 对账已接纳 package 与当前 task 合同，避免顺手重写范围外任务。历史文件引用已
绑定的原始正文，保留未决事项，避免递归复制旧包；增量证明不成立时保持完整评审。
现有精确读取断点和增量评审 gate 保留；较大规格的分区方法只用于已配置的 reader/fan-in
工作流，并保留最终全局覆盖检查，不引入第二套 Task schema 或状态机。

真实 datal 故障的口径：可读取来源共 27,330,729 字节；当轮策略实际必读 30 条、
19,682,377 字节，覆盖 7 条。两者须区分。请求修复 2 个任务，候选与绑定基线之间有
43 个任务记录变化（41 个范围外）及 8 个全局字段变化，不能直接声明局部修复。

## 混合版本部署

运行中的旧 worker 可能仍使用旧 harness 的绝对 CLI 路径。新 `PrdFlow.spec` 键会被旧版
fail-closed 拒绝，不能仅验证新控制面能读取配置。升级时需核实仍活跃的旧 CLI。
本轮 datal 使用显式 `ZfConfig.spec.workflow.stages[prd-plan]`，完整保留展开的 stage 合同，
只添加两个 aggregate 上限；旧 loader 可以读取，新运行时执行预算。按 stage id 对账后，
所有 stage 定义及其他配置保持相同；显式 stage 位于展开列表前部，顺序变化已记录。
待所有 worker 使用支持新键的 harness 后，可以回到更简洁的 PrdFlow 配置。

真实恢复证据在目标项目 `diagnostics/critic-input-recovery-20261109/`，包括配置备份、
验证日志、受控恢复请求及事件身份；控制面由本工作区加载，活跃 worker 保留。新 Planner
已经派发，是否生成合格输入并通过独立 Critic 仍须观察后续结果。


## 第一批局部 Plan P0 增补

实现及验收见 [局部 Plan P0](local-plan-p0-20261109.md)。补充原生分区的精确比较、
Impl/Verify 未读完输入时的技术失败收敛、Planner 提交前成本观察及字符分页口径。

### 首次独立 Critic 派发前的交接修复

零 Writer 诊断的 Planner 已准入、Critic 尚未签发 WRC 时，操作员可通过
`stage-replan-new-generation` 提交 `retry_critic_handoff_event_id`、`workflow_run_id`、
`candidate_event_id`、`planner_admitted_event_id`、`planner_control_sha256` 和
`approval_ref`。控制面绑定当前冻结、原始签名结果和失败的 fanout。
若通用 replan 已产生一个因果上直接相连的后继，只有它以基础设施失败终止且未签发任何
Reader WRC 时才能收回。恢复沿用原 Planner、原 trigger 和证据，由已有 fanout
调度器第一次签发 Critic。重复执行同一授权会返回原回执。业务裁定仍等待独立 Critic 的正式结果。

恢复标记划分了本次 Critic 的结果范围。新 WRC 签发前，只有携带该标记的本轮技术失败能结束本次交接。
签发后，控制面按 operation、attempt 和 request hash 接受当前结果。若结果已准入、汇总前控制面退出，
重启会消费当前签名结果一次；首次派发前的旧失败仍保留在历史中。
