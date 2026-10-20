# Task map 修复验收记录

> 状态: active。B12–B17 的实现与证据已落地；首个续跑候选经正式 Critic 退回，正在同一有界代际内返工。承接[有界续跑](2026-10-20-0820-taskmap-bounded-continuation.md)。

## 已完成的工程工作

- 已安全删除合并到 dev 的 `9f60/zaofu`，删除前归档并逐字节校验 171 个证据及本地状态文件；归档摘要和环境迁移见[首轮记录](2026-10-20-0725-taskmap-semantic-repair.md)。
- 当前工作区 `/home/min/.codex/worktrees/taskmap-fixes-20261020/zaofu`，分支 `wip/codex-2026-10-20-taskmap-readiness`。
- `c199520 feat: strengthen plan execution consistency review`：实际动作生产者、状态相关权限、片内 TDD 与最终候选边界、完整修正事务及决定快照的通用方法。
- `d90d365 fix: verify plan specifications reach implementation inputs`：要求从实际下游入口验证完整规格可消费。
- `00d2442 fix: relocate referenced plan ports for downstream consumers`：显式引用端口经正式 fanout 搬移，保留摘要和路径守卫；真实 caller 回归覆盖删除作者目录后的 Package 读取、摘要失配和越界拒绝。
- `2ea6374 fix: review task actions and stage evidence boundaries`：补充本地命令内部的未来业务动作审查，以及阶段命令与收据路径的一致性检查。
- `37a7b20 fix: audit evidence paths across every verification tier`：将收据核对扩大到全部本片、阶段和最终候选用例；浏览器、runtime、专项命令分别绑定真实输出。

## 已复核的正式候选

受控 generation `stage-replan-4ad4cec31b7ac808a825` 的 Planner 提交事件为
`evt-2a3e8bc23ca8`，Critic 分派为 `evt-78ae5c181a4c`。
冻结目录为项目状态目录下
`artifacts/fanouts/fanout-prd-plan-evt-af1999cf/planner/artifacts/plan`。

从冻结文件独立读取结果：

| 检查 | 结果及边界 |
| --- | --- |
| 原范围保留 | 54 任务、237 AC、171 命令、68 页面/表单、2,043 来源记录；原任务/AC/页面/来源 ID 无缺失，原 mandatory/owner/tier 无降级 |
| 权限动作 | 175 来源动作行细化为 411 个动作；全部 producer/AC/command 绑定无机械错配，语义仍由 Critic 判断 |
| 状态相关权限 | 活动作业完成与已完成结果修正分离；仅操作员/仅编辑者有独立允许与拒绝用例，保留继承、范围和撤权 |
| 片内 TDD | 已登记步骤无未来命令；本地命令内部的动作边界另行审查，不能仅凭命令名通过 |
| 最终候选 | G2 明确 IC01–IC07、C01 六标签及 E18–E23 等集合，独立于中间阶段排除范围 |
| 完整结果修正 | 11 个具名事务用例，包括 T1→T2、设备目标、占用/位置/时序/关联/撤权、逐写故障、独立后续事实和幂等回执 |
| 决定快照 | 全部 Q 的登记与 PAS 内容无差异；Q01–Q15 保持真实未决，不虚构具名负责人或外部答案 |
| 阶段收据 | 原 9 处 G2/G3 阶段用例路径错位已消除；阶段与最终候选各消费对应命令的输出 |
| 用例 | 1,993 条计划用例；没有把计数当成已执行的产品测试 |

机械审计为 `reports/taskmap-readiness/continuation-formal-audit.json`。
作者修订还涉及 10 个本片动作边界、8 个实际生产者或最终候选补充用例，完整审查仍须独立确认。

## 规格交接与验证

原 `source_index` 为 17,282,596 bytes，被 4 MiB Package 与 8 MiB Preview 上限拒绝。
新候选将正文拆为独立必需端口，索引约 604 KB，最大端口 3,251,719 bytes，未扩大公共限制。
26 个必需端口经真实 fanout 搬移、删除临时作者目录、完整 Package hydration、源清单和 Preview 读取通过；216 次任务选择器读取通过。
23 个显式端口与冻结运输验收正文、摘要逐项一致；6 个内嵌正文端口按 canonical JSON 摘要比较，引用文件按原始字节摘要比较。
证据为冻结 `package_hydration_validation.json` 和本 worktree 的 `continuation-live-wiring.json`。
这证明运输和读取，不替代语义裁决，也没有选择或批准产品 Package。

代码验证：影响闭包 619 passed、8 skipped；pre-merge sentinel 51 passed；
三流确定性 mock E2E 10 passed。最新方法增补另验 20 passed、1 skipped，
文档/指令/结构检查 25 passed。前端构建、类型检查及 bundle budget 通过。
未执行全量 pytest，未运行洛书产品测试、数据库或部署。

## 流程与尚待验收

常规 PrdFlow 为产品/技术双扫描 → Planner → 独立 Critic → 人工 Plan 审批。
Kernel 调度不计 Agent；本项目另有非阻断的 orchestrator shadow。
本次返工复用扫描输入，实际顺序为 Planner → Critic；没有新增并行评审者。
Critic 保留完整范围审查和具体反例责任，不能以作者自检或操作方机械检查替代。

Dashboard 为 `http://10.9.100.141:8002/?project=loshu-plan182-eae8133e&page=inbox`。
旧 rejected Plan 已通过 Docker 桌面/手机格式化、源码/下载一致及附件摘要验证；
它只证明迁移后的服务可用，不能替代本轮新 Plan Preview。
正式 Critic `evt-17ec380bd7dd` 已返回 reject，失败事件为 `evt-8d48e26c0aa3`；
Runtime 在同一代际发起 `fanout-prd-plan-evt-15069565` 的第二次 Planner 返工。
B12–B17 及关联 B08/B09/B11 保持 active，未生成新的待审批 Preview。

Critic 在本轮继续审查时发现最终浏览器及专项用例还有收据错位。
操作方将机械审计从阶段子集扩展到所有 case/command，复现 53 处绑定差异：
G2/G3/G4 最终浏览器用例 50 处，契约登记/全局契约/资源权限专项各 1 处。
该结果不推翻已修的 9 处阶段错位，但证明此前只查阶段子集不足以关闭 B14/B17。
详见 `reports/taskmap-readiness/continuation-formal-full-receipt-audit.json`；
各条仍须按实际命令责任修订，不应盲目统一字符串替换或放宽证据要求。

另一项正式发现 `PC-BC-F2`：来源 L4417–L4418 允许作业操作员、流程编辑者
取得项目物料编辑能力后维护物料和编辑当前库存；新 recipe 与两个用例却要求
“仅作业职责不能独立改库存”，和来源、同版 AC/权限表相矛盾。
必须分清独立物料维护、活动作业完成、已完成作业修正三种动作，并保留派生能力方向。
方法已补充 A 派生 B 与 B 不能反向执行 A 的反例，不将职责隔离错误扩大为取消 B。
正式报告保存在 `reports/taskmap-readiness/continuation-first-critic.json`。
