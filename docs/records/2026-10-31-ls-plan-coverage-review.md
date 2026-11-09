# ls 已批准 Plan 的来源责任纠偏记录

> 范围：只读审计和后续修订依据；本次阅读器实现没有改写运行态 Plan。

项目 `ls-0254e298`，Plan `evt-3c805b837cb9`，run
`prepare-TASK-90D92C-e4eaededbd2b74fe`。原始
`lfms/loshu-fms-handoff.md` SHA-256：
`57e24bbe2574f21094b9da33cc5093f7f9cbfbaf174c5a47a48f10cc04575e85`。
核对输入为该 Plan Package 绑定的 source_index、coverage_ledger、task_map、
source_inventory、execution_spec、acceptance_matrix 与 test_matrix。
不复制含环境凭据的原始交付正文。

## 已定位的问题

| 来源 | 当前映射/含义 | 纠偏要求 |
| --- | --- | --- |
| SRC-0003 | 组织总览分析版 M4/C1 分给 LS-VEHICLES / LS-VEHICLES-RUNTIME | 复核总览聚合、分析查询、页面呈现的实际生产任务；车辆资源只是输入，车辆 CRUD 不能证明总览功能已覆盖。 |
| SRC-0004 | 组织总览默认版 M4/C0 分给 LS-PROJECTS 及其 runtime | 复核默认总览的聚合与页面责任，不能仅因使用项目数据归给项目目录。 |
| SRC-2214–2216 | 设备安装结果的累计时长同时关联统计分析责任 | 按安装结果写入/修正事务及派生字段验收，明确最终真实生产者；不能按“时长”关键词自动归分析模块。 |

证据位置为冻结 coverage_ledger 的上述 source_id 行（早期人工定位行号分别约
183、202、43466，后续应以 ID 查找）。纠偏时必须读相邻来源和同页字段，保留父来源、
全部 mandatory AC 与最终验证义务。这里给出责任类别，不凭名称编造新的 Task ID。

其他阅读差异：source_inventory 含 62 页/6 表单，页面使用 task_ids；设计状态仍为
待设计，不能称效果图已转换成 UI 设计。execution_spec 有 28 条人工场景，正式矩阵有
54 条场景，两者不是同一个集合。完整决定登记有 16 条；审批动作问题为零不表示它们
全部关闭。2356 行来源映射的结构统计不能证明字段、行为和权限语义全部覆盖。

## 后续修订与验收

1. Planner 读取冻结 Package 与对应原文，只针对具名差异提出新版本；同时审查同类
   页面/动作的责任分配，记录已查范围和剩余不确定性。
2. 对每项记录来源 → 可观察行为 → 实际生产 Task → 依赖路径 → AC → 命令 →
   验证 owner/tier/最早时点；不能逆加成环依赖或删掉父级最终验收。
3. 同步新 Task Map、覆盖账本、页面映射、规格和矩阵。独立 Critic 重新审查序列化
   结果，经现有受控修订/审批入口准入后才替换后续派发依据。
4. 新旧版本以 Plan ID 和工件摘要区分；阅读器差异页可辅助定位，但不能替代 Critic
   的语义 verdict 或产品 Verify 的执行证据。

本批完成的是显示正确性、可审阅性与通用评审方法，未宣称以上 ls 业务错配已经
通过新 Plan 修复。入口和重载要求见[实现说明](../impl/plan-execution-readiness.md)。
