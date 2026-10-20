# Task map 修复的有界续跑

> 状态: active。承接[已授权修复](2026-10-20-0725-taskmap-semantic-repair.md)和[规格交接修复](2026-10-20-0806-taskmap-artifact-handoff.md)，属于 B12–B17 原执行范围。

## 操作依据与边界

本次仍依据用户原话“创建新的worktree,切换到新的worktree 上面发现的taskmap 高缺陷生成backlog ,执行backlog 修复”完成修复验证，没有新增用户消息或产品批准。
前一记录的“一次”是操作方对首轮预算的安排；该代际已按原上限结束，历史不改写。
两次规划均产出候选，但 Critic 供应商会话因 `server_overloaded / Selected model is at capacity` 退出，没有正式语义 verdict。
第二次失败为 `evt-68ebecb83230`，升级 `evt-6ac61bc236ce`，blocked 为 `evt-cfd747de70d1`。

修复 `00d2442 fix: relocate referenced plan ports for downstream consumers` 已提供新的可验证输入。
操作方在既有修复授权内继续一轮有界 generation，经 `stage-replan-new-generation` 请求，保留旧计数、审批门和升级记录。
不自动批准 Plan，不执行产品 Impl、数据库或部署，不 push；本轮再次耗尽后记录外部阻塞，不无限续跑。
这是原授权下的操作判断，不是伪造新的人工审批。

## Planner 必须修订

以前一正式候选 `fanout-prd-plan-evt-fbf8c23f/planner/artifacts/plan` 为基准，保留原需求范围、来源、任务/AC/命令映射及 PC-F1–PC-F5 已修内容。

1. **正文交接**：17,282,596 bytes 的 `source_index` 实际被完整 Package 的 4 MiB 与 Preview 的 8 MiB 上限拒绝。不要再把 12 份正文内嵌一份索引，不增大公共上限，不删减正文。把完整语义文档序列化为独立具名端口，通过 `{logical_name, ref, sha256}` 和 `required_plan_ports` 正式交接；索引保留逻辑名、摘要、选择器。更新 `semantic_input_contract`、task selectors、所有消费说明，不能继续要求从 `source_index.planning_semantics.documents` 取已移走正文。Kernel 已支持引用端口搬移，必须验证真实 Package hydration、源清单及 Preview 读取。
2. **本片测试不依赖未来动作**：Critic 尚未正式提交，但其只读审查已指出保养计划本片测试要求执行后续车辆调拨；充电设备基础片的 `AC-CHARGING-CORE-ARM` recipe 要求登记后绑定/报废，`CASE-AC-CHARGING-CORE-CHARGER` 要求生产调入。逐项核对实际 producer、依赖 DAG、用例动作与命令，而不只检查命令路径。已有状态可作为本片显式 fixture，后续真实动作放到实际 owner 和 mandatory candidate；保留完整最终验收，不能删掉业务承诺。
3. **阶段证据一致**：核对全部 G2/G3 stage 用例、`test_matrix`、`real_e2e_matrix` 与 `evidence_contract`，实际阶段命令写出的收据必须与该阶段引用相同，不能误指仅最终 candidate 才产生的路径。最终 candidate 的独立全集仍须保留。
4. **全量一致性**：继续检查全部权限动作实际 owner、状态相关允许/拒绝、完整作业修正向量、Q 快照、早期 UI 范围；不要仅修上述示例。记录已修、未审及未决条件。

## 已验证的运输方案与独立复核要求

真实冻结候选的 12 份语义正文各自采用紧凑 JSON 后均低于 4 MiB，最大 2,703,085 bytes。
另加轻量 `source_index`，13 端口经正式 `prepare_fanout_synth_reports` 搬移、删除作者目录、物化、完整 Package hydration 和 Preview 读取后，所有正文与原 JSON 相等。
原候选 SHA 未变，临时环境已删除。证据为本 worktree
`reports/taskmap-readiness/split-port-proof.json` 和 `verify_split_ports.py`。
这只是运输证明，不是正式 Plan 修订或语义批准；Planner 必须自行提交新候选，Critic 独立复核。

Planner/Critic 使用当前新 worktree 的方法资源。按 artifact manifest/CLI 做受控读取；优先具名 JSON selector 和程序化交叉检查，把结果/反例留作证据，避免把巨型正文反复全部输出到上下文。
Critic 必须经本 attempt 的 `result preflight` / `result submit` 交付真实 verdict；发现缺陷时尽早固化具名返工项，不能只以聊天总结结束。
