# Task Verify 的 Skill 评分卡

[Spec 214](../spec/214-workflow-skill-evaluation-and-optimization.md) 的前两阶段覆盖使用 `task-verify` 输出协议的工作流。卡片保留原任务裁决和独立的机械评分。Operator 可以先看关键项，再看分数的分母和覆盖率。即使可判定项全部通过，独立业务裁决仍可能待证。

## 查询和回填

在归属项目目录执行。项目的 `zf.yaml` 决定状态目录与事件签名策略；回放历史配置时需要兼容该配置的加载器。目标可选一个 attempt、task 或 run，还可以缩小到 operation 和 dispatch。

```sh
zf attempt skill-evaluation <attempt-id>
zf attempt skill-evaluation --task <task-id> --history
zf attempt skill-evaluation --run <workflow-run-id> --replay
zf attempt skill-evaluation <attempt-id> --operation <operation-id> --dispatch <dispatch-id>
```

`--replay` 读取归档、活动事件段与冻结的证据文件，在标准输出生成当前证据的 JSON。正常模式读取已发布版本。任务详情的 Evidence 标签使用同一查询，可展开 Skill、逐项证据和历史修订。项目级 JSON 入口为 `GET /api/projects/{project_id}/tasks/{task_id}/skill-evaluations?history=true`。

## 证据和修订

- 新的 Verify 派发保存物化事件 ID、简报摘要、后端及配置指定的模型。旧事件依据派发前同一角色、任务和工作目录的最近物化记录恢复冻结包，并标记关联方式。共享同一个包的重派仍各有一张卡。没有冻结版本时只列已知名称。
- 受控 Skill 工具调用和文件返回各有证据 ID。文件返回需同时命中物化路径和 Skill 名。旧 Hook 缺少派发 ID 时要求已绑定的 Provider 会话、相同角色和派发时间窗。未观测到的使用、适用性、单个 Skill 的增益和成本保留未知或未测量。
- 核对目标与合同绑定、必需验收项是否有结果、非通过结果的理由，以及已归档探针与声明是否一致。已准入的判词来自原业务角色。独立审核项在这一阶段保持待证。
- 默认 watcher 在独立线程中增量消费事件，在配置的状态目录下维护 `projections/skill-evaluations.sqlite3`。消费者停机期间工作流可以继续。下次启动从事件重建索引状态并补卡。同一证据摘要重复发布时沿用版本，迟到的匹配工具返回或补齐的文件可产生后续修订，并保留前版摘要。
- 页面呈现事件 ID、可见证据引用和汇总，原始工具输出留在原证据中。任务的履职分单独计算；确定某项 Skill 应当默认加载或转按需，需要阶段 3 的独立复核和阶段 4 的同案配对实验。

## 验收记录

| 冻结的 8×8 批次（UTC） | Task Verify 卡 | 通过 | 拒绝 | 弃评 |
|---|---:|---:|---:|---:|
| 2026-11-21 13:28:14 | 26 | 23 | 3 | 0 |
| 2026-11-23 15:16:37 | 18 | 16 | 1 | 1 |
| 2026-11-23 17:11:20 | 16 | 16 | 0 | 0 |

三批的派发身份、冻结 Skill 版本与五项评分均逐卡对上历史回填。60 张卡都有合同和验收项覆盖证据。只有两张卡有能核对的归档探针，60 张的业务正确性都待独立复核。后两批使用了工作流分支较新的 PrdFlow 参数，验收时用兼容的分支加载原项目配置。

隔离测试覆盖同一 lane 重派、目标错误的构造负例、缺证据、失败、阻塞、取消、并发发布、消费者重启、迟到证据以及项目间同名任务。另一个非 8×8 的 mock 项目经标准派发与结果准入完成 Impl 和 Verify，评分卡可从真实派发事件追到冻结 Skill 包。
