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
8192 字节计算的下界，不含元数据/JSON 转义开销。配置任意上限时，未知大小不能当作 0
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
