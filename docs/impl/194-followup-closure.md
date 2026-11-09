# 194 §9.2–9.6 后续实施与验收

> 2026-10-29；工作分支 `wip/codex-20261029-design-194`；增量基线 `fe920497`。
> 七项实施与评估已执行，194 的完整路线资格与收益验收仍为 **HOLD**。
> 本文承接 [首批实现](194-stage-workflow-implementation.md)，机器证据见
> [后续验证记录](194-followup-verification.json)。不能把完成本批任务等同于全量 rollout。

## 按顺序完成的增量

1. **取消确认与容量释放**：`stop_cancelled_dispatch` 复核当前 dispatch，要求 transport
   明确返回“不再存活”；停止确认通过 `worker.state.changed` 持久化后才释放 worker。
   operation/request/attempt 身份随回执记录，写入失败保持 blocked。已有 run 取消协调入口
   重试未完成停止，已取消 operation 不妨碍重试；复用的 worker 不受旧取消影响。
   动态容量仅在对应 fanout 已关闭且所有已派发子操作都有精确停止回执时释放。
   假 Provider 拒绝 SIGTERM、丢回执、恢复及旧 dispatch 已测试；未以此声称真实宿主已获资格。
2. **父子用量对账**：从事件与现有 cost receipts 派生父子归属，不建立新的 canonical store。
   `exclusive` 与含明细的 `inclusive` 通过精确 sample refs 区分；覆盖缺失、重叠、未知价格、
   缺少 operation 身份均保持 unknown。动态 continuation 消费 `max_cost_usd/max_tokens`
   的父预算，同父已绑定上限不能通过后续提案提高。旧累计快照不再被当成计数器重置。
   未声明覆盖范围的旧 disk receipts 不冒充精确归属；旧全局成本汇总仍是保守门，
   本次未把它替换成新的计费系统。真实取消后的最终用量缺失仍需阻塞／对账。
3. **跨路线矩阵**：按下面的 caller/test 映射运行配置、合同与 mock 验收；每条路线保留
   独立真实资格状态，不继承其他路线的成功。
4. **三组运行器**：新增 `tests/e2e/staged_workflow_comparison.py`，固定三组 argv、输入、
   source 文件 digest、模型及预算；在每组前后校验身份。失败与未知用量留样，保留原始
   stdout/stderr、Provider audit 和证据目录；超时清理进程组。样本、场景、质量、生命周期、
   runtime evidence 任一不满足即 HOLD。CLI 负责实验执行，不成为新的业务调度器。
5. **资料核验**：新增 `tests/e2e/evidence_review_194.py`。真实 author 与独立 verifier 仅接收
   来源与问题，评分答案不下发；工具调用会使该样本失效。第一次保留冲突／缺失，收到
   补充资料后重新核验。模型产物通过现有 Generic Workflow 的 Proposal、refs/digests、
   admission、重启、旧代际拒绝和 Goal Gate；没有新增业务专属 kernel scheduler。
6. **方法候选**：复用 `SkillOptimizationService`，train/selection/test 来源与 fixture 分离。
   本次仅添加方法文字，角色、依赖、必需证据、完成合同保持一致，不新增模板类型。
   选择集持平，optimizer 返回 `strict_selection_tie`；未导出、晋升或更改生产路线。
7. **结项评估**：保留失败、限制和后续触发条件，未把 smoke 或小样本结果算作完整收益证明。

## 跨路线与控制权矩阵

| 194 条款 | 运行入口／消费方 | 本次证据 | 真实资格 |
| --- | --- | --- | --- |
| PRD/Issue/Refactor，light/full 与 OA 边界 | `orchestrator_agent_policy`、`run_admission`、Kernel Orchestrator | `test_orchestrator_agent_policy`、`test_run_admission`、`test_orchestrator_agent_semantic_control_mock_e2e`，flow smoke | HOLD：未运行三路线完整真实 canary |
| Standalone/preparatory Research | `workflow_route_policy`、`workflow_synthesis`、`run_admission` | research policy isolation、registered template/capability、research terminal 测试 | HOLD：未运行独立真实 Research canary |
| General/bounded-agent | `workflow_synthesis`、Generic Workflow admission、artifact delivery/Goal Gate | source_write 能力检查、executor/verifier 保留、复杂重启场景 | 混合真实试跑通过直接组；完整宿主资格 HOLD |
| 简单代码／只读 Task | route policy、light invoke | 小路线保留与跨路线误启动拒绝测试 | HOLD |
| Preparation → Run | `workflow_prepare`、controlled workflow-submit | config/合同/旧 preview 与 revision 拒绝测试 | HOLD |
| OA 不可用／迟到／重复 | OA checkpoint、Task Pipeline shadow/adoption/results | shadow 不围栏、blocking 接纳与过期结果过滤、重放幂等 | mock 通过；真实故障 canary HOLD |
| generation 交接 | reader/writer fanout recovery、operation/result admission | 旧 writer 取消、旧结果拒绝、显式保留未替换证据 | mock 通过；真实 writer 交接 HOLD |

上述映射是本批执行的证据，不表示每个 route × fault 的笛卡尔积均已测试。

## 真实试跑结果与限制

模型统一为本机配置的 `gpt-6-astra`，Codex CLI `0.163.0`，reasoning=low，
inline 来源、无工具；每 turn 超时 120 秒。组织实验目标预算为每组 60,000 token。

| 组别 | Provider turns | 已观测 token | 总耗时 | 结果 |
| --- | ---: | ---: | ---: | --- |
| direct | 4 | 59,871 | 32.76 s | 两轮隐藏评分与独立核验通过；artifact Goal terminal 存在 |
| fixed | 6 | 89,618 | 34.74 s | 补查阶段预算耗尽，保留失败 |
| dynamic | 5 | 74,171 | 37.82 s | 第一轮综合阶段预算耗尽，保留失败 |

这是 **hybrid_real_provider**：author/planner/collector/synth/verifier 的业务判断调用真实
Provider；外围 Proposal/replan/重启由已有确定性场景驱动，runtime launcher 显式使用测试
驱动。动态组织实验的分组由模型提出并受两组上限约束，但不是通过线上 dynamic continuation
路径完成全部实际派发。这里不能证明任意动态图或完整真实宿主执行。

- 初次在 pytest 外运行暴露 session 未初始化，三组均在 Provider 前失败；原始记录保留。
- 第二次使用显式测试驱动，Provider 业务判断与 Goal Gate 闭环有效。该轮 source digest
  沿用初次 manifest，修复期间代码有变化，且 request read projection 仍显示 running；
  **不将该轮纳入严格可比性或完整投影验收**。运行器后来加入 source 文件前后校验，
  终态指标改为 Goal terminal + dossier ready，原始失败记录不回写。
- Token 上限当时按已返回用量观测停止，最多两个在途调用造成超额，不能声称硬 token 额度
  已验证。最终驱动另有原子 call-count 上限；Provider 未提供请求前可强制的精确 token 上限。
- 成本金额均为 unknown；没有以零美元代替，也没有推算未验证价格。
- 本轮没有覆盖全部六类代表场景、每路线重复采样、真实取消、真实文件冲突／副作用。
  因此不能宣布 direct 的普遍优势，更不能晋升 fixed/dynamic。
- 方法实验另外执行 4 次真实调用，共 62,122 token；选择集与留出集的 base/candidate 均
  1/1 正确，只支持“未观察到质量增益”。一次 optimizer 参数错误在账本接纳前发生，
  修复后校验已保存 prompt digest，复用原始 Provider 结果对账，未再次计费。
  原记录与校正后记录均保留；单题留出集不足以支持泛化收益结论。

全部真实调用共 19 次，观测 285,782 token；费用未返回。没有推送、合并、生产配置更新或
对外发布。临时执行目录已经由 runner 清理；证据快照单独保留在
`/tmp/zf-194-followup-20261029/`，它们不是待运行的 runtime。

## 验证层级

- 定向机制：取消／动态容量等 62 passed；用量／调用方等 61 passed。
- 跨路线配置、合同与 mock：142 passed。
- 主要影响面（cost、worker、transport、reader/writer fanout、Generic E2E）：449 passed。
- Evolution/定义绑定/结构：46 passed、1 skipped。
- 最终新增 evaluator/归属/结构调用方：34 passed、1 skipped。
- `dev-verify.py plan/run --base dev`：对两个 E2E driver 显式补充 direct tests，
  serial 10 passed、sentinel 50 passed/1 skipped、flow smoke 10 passed。
  planner 的 >80 文件规则拟运行全量 deterministic；本批显式跳过该 tier，使用上述影响面闭包，
  不把 skip 算作通过。初次 planner 因未映射 E2E driver 拒绝执行，修正映射参数后通过。
- 这些批次有重叠，不应累加为独立测试数量。未运行 full pytest、coverage、Web/browser 或
  全路线真实 Provider E2E。准确日志与哈希见验证 JSON。

## 条款结论与解除 HOLD 的条件

| 条款 | 结论 | 下一次必须补齐的证据 |
| --- | --- | --- |
| 9.2 OA 绑定、有界展开、生命周期 | 机制及调用方闭包通过；宿主资格 HOLD | 同一路线真实 transport 的取消／重启、精确最终用量和硬预算准入，unknown 不放行 |
| 9.3 模板与路由优化 | 候选链与隔离方法已执行，持平拒绝晋升 | 冻结代码下更大独立留出集、可计价成本与可复现改善；复用现有受控 canary/rollback |
| 9.4 收益与退出 | HOLD | 冻结 source/输入/模型/工具/预算，覆盖六类场景，至少三轮；预算超额、失败和未知必须留样 |
| 9.5 跨路线控制权 | 配置／mock 有证据，真实资格 HOLD | PRD、Issue、Refactor、Research、General 分别完整 canary，包括中途修订和 writer 交接 |
| 9.6 通用资料核验 | 真实判断 + 机械闭环 pilot 通过；完整闭环 HOLD | 所有阶段通过真实受控 runtime 调用，request/Web projection 收敛，真实 bounded continuation 与宿主恢复 |

目前不扩大 blocking 范围，不晋升方法候选，不退役历史兼容恢复路径。
先补资格和计量，再比较组织收益；无需为本轮结果另建模板、DSL 或常驻控制 Agent。

## 复现入口

```bash
# 仅生成并冻结输入，不调用 Provider；manifest 路径应置于临时实验目录。
uv run python -m tests.e2e.evidence_review_194 --write-manifest /tmp/zf-194-next-manifest.json
# 执行新的独立实验（会调用真实 Provider）；原始 pilot 失败不得被该结果覆盖。
uv run python -m tests.e2e.staged_workflow_comparison /tmp/zf-194-next-manifest.json /tmp/zf-194-next-result.json
```

最终新增补测：取消与 result admission 80 passed/1 skipped；runner、失败 closeout、
隐藏评分和 Event 合同 20 passed；docs/instruction 8 passed。当前 manifest 生成器明确提供
4 次 Provider call 的硬上限，token 门仍按已观测用量停止；不把 call 上限冒充硬 token 配额。
