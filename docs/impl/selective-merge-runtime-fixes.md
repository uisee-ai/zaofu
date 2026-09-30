# feature 工作树的通用修复选择性整合

2026-09-30，来源 `/home/uisee/zaofu_v7`，目标
`/home/uisee/zaofu-worktrees/feature-issue-stability`。仅描述已经迁入的补丁，
不是整个合并完成、全套测试通过或发布许可。原台账的历史 fixed 不自动等于目标已验收。

| 台账 | 迁入边界 | 不改变的行为 |
| --- | --- | --- |
| ZF-TEST-020 | `regression_case` 使用原子写入保存回归 artifact | 不改变 case_id 校验或报告语义 |
| ZF-TEST-110 | fanout 丢派发扫描识别已完成 recovery handoff | 不改变重派判定或 reader 结果语义 |
| ZF-TEST-045 | `flow_verification_identity` 将精确绑定验证 ID 的 `flow.goal.closed` 识别为已消费 | 不改变 candidate、Judge gate、closure identity 或 `/issue-merge` |
| ZF-TEST-063/066 | respawn 结果与 Task Pipeline preflight 共用生命周期合同 | 不放宽 Provider readiness 或 redrive 门禁 |
| ZF-TEST-065 | writer timeout/resume 保留 manifest 中已完成 Task | 不伪造未完成 Task |
| ZF-TEST-069/070/071 | stream-json 完整终态、停止和自有进程组清理 | 不宣称真实 Provider 已验收 |
| ZF-TEST-089 | `fanout_retry_briefing` 为 reader retry 保留已有 Task 首行锚点 | 不虚构 Task，不扩大 reader 写权限 |
| ZF-TEST-090 | `CompositeTransport.lifecycle_snapshot` 转发已选 adapter 的快照 | 不改 watchdog 判死/重派策略，不编造缺失的 PID |
| ZF-TEST-091 | `TmuxReadinessMixin` 在活性探测耗尽 deadline 时退出循环，进入既有失败诊断 | 不延长 timeout，不放宽 ready 判定 |
| ZF-TEST-092 | `goal_completion_gate` 按 claim 校验并读取绑定的不可变 Run Contract | 仅无绑定的历史 claim 回退 active pointer；坏绑定不回退、不阻塞其他 claim |
| ZF-TEST-093 | `run_isolation` 校验快照引用后比较完整 config body 的稳定摘要 | 快照摘要绑定保留；未知摘要、显式坏 ref 和实际配置差异仍拒绝 |
| ZF-TEST-094 | `workflow_delivery` 在 submit payload 与 invoke 字段透传已确认 scope | 不从 target_root 推导写权限 |
| ZF-TEST-095 | `runtime_authority_bridge` 按源 invoke ID 在状态目录锁内去重拒绝事件 | 重放仍修复第二次 admission 拒绝写入；不同源事件独立 |
| ZF-TEST-096 | preview 与 Task binding decision 共用活动 Run 归属检查 | 不改 revision/origin rotation；不声称多进程 submit 原子化 |
| ZF-TEST-097 | `run_isolation` 将项目 target_root 身份与相对写 scope 分离验证 | 保留同项目、子树、符号链接归属和范围重叠门禁 |
| ZF-TEST-098 | light task-map 采用显式请求写范围，原 target 范围仅作为验证读范围 | 未声明 scope 的旧单 Run 路线兼容 |
| ZF-TEST-099 | `load_writer_task_map` 调用 `run_write_scope`，以 admitted.source_event_id 找到原 invoke 后检查 writer 范围包含关系 | 不修改 agent 的计划；不声称宿主沙箱隔离 |

## feature 保护边界

未迁入 Self-Issue、Triage、External Issue、Feishu 或前端生产改动。
`orchestrator.py`、`self_issue_target.py` 与整合前 feature 备份逐字一致；
`orchestrator_lifecycle.py` 只保留已登记的 Task-stage/stream 生命周期最小调用面补丁。
通用运行器会被多条流程复用，不代表将 v7 的 issue 管理实现
替换到 feature；不得整文件覆盖混合边界。

正式并发探针仍有保留红灯：General 与 PRD/Refactor 的有效配置 body 中
`self_issue.settings_config_path` 分别指向临时候选配置与正式 `zf.yaml`。
这是实际 config body 差异，不能为了通过而在比较时忽略 Self-Issue 字段。
已对捕获的两个有效配置快照逐字段比较；本轮不修改受保护的 schema/render/loader。
这与更早发现的该字段无法重载问题分别记录，不声称根因已经修复。

## 验证与剩余工作

直接运行隔离、scope、拒绝、准入和 light 回归为 178 passed；身份/活动 Task 正式
入口为 6 passed。planner 范围及终态正式探针 4 failed，停在上述配置门禁，不能用
直接测试代替完整 ZF-TEST-099 场景证明。其他批次、门禁与精确文件名见
[整合状态](../../tests/testkit/MERGE_STATUS.md)。

自动影响规划器将六个运行边界关联到 303 个测试文件并建议全套 deterministic；
本轮是选择性文件整合，保留用户未提交修改，未执行该全套方案，使用明确列出的
聚焦调用方、共享契约及确定性正式入口测试。不能将聚焦结果称为全仓验证。
尚未提交、合入 dev 或发布；来源差异已经按下方最终审计裁决，不按文件差异覆盖。

产品 TestKit 已补齐并完成自测收尾：`.venv` 环境下 360 项通过，3 项为 Schemathesis
版本契约失败（manifest 期望 4.26.1，当前探测到 4.28.0），保留为 harness failure，
不修改框架判据或把产品/环境问题改写成通过。

## 收尾审计结果（2026-09-30）

147项裁决全部完成：71项保护feature、33项源码对齐、4项按hunk整合、8项逻辑等价、
31项审核后保留feature。逐文件理由及双方摘要见
[最终差异审计](../../tests/testkit/MERGE_AUDIT.json)。没有遗留“待评审”的合并项。

另补齐已在v7存在的063/065/066/067/068/069/070/071/072/073/075/076/078/079/080/
102/103/105/106/107/109/110/112修复的相关调用面，以及台账K562-F1、K563-F1、
K583-P的tmux同源身份和双窗口采集；保留feature自己的Task Pipeline readiness等待、
角色根目录选择和受保护Issue/Triage生产实现。生命周期工作目录解析独立到
`lifecycle_workdir.py`，避免扩大原超长文件；实际生命周期调用方50项通过。

最终tmux调用闭包136项通过，产品框架自测360项通过/3项版本契约失败，
预合并哨兵仍为45项通过/6项失败。产品失败由Schemathesis期望4.26.1、实际4.28.0引起，
不改基线伪造通过。精确批次和未运行层级见[整合状态](../../tests/testkit/MERGE_STATUS.md)。
这是本地文件选择性合并完成，不是完整产品验收；没有提交、合入dev或推送。
