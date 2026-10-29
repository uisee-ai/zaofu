# 194 阶段定义与有界工作流：实施与评估

日期：2026-10-29。基线：`50ae24e535d083168f7167515fffd05482f2e1df`。
工作分支：`wip/codex-20261029-design-194`。
实现提交：`3ff0b9a6 feat: harden staged workflow recovery and bounded continuation`。六项任务已归档至 `tasks/done/`。
设计输入：本地 `docs/design/194-stage-templates-and-dynamic-agent-workflows.md`，
SHA256 `3c49d9c77af4cbe76d16410b756c77ba7a3e622d4645e28d0909b1263a5266cb`。

本批完成六项实施任务，覆盖 194 的交接恢复、注册只读阶段绑定、有界展开、OA 方法、
资料核验示例及确定性验收。它是沿现有运行链的增量实施，不代表 194 的全部长期目标已完成。
没有启用任意多节点 fragment、Provider 原生子 Agent、新的常驻控制 Agent 或学习型路由。
真实模型的语义质量、耗时和成本未测，默认 rollout 策略保持原配置。

## 机制与接线

```text
Preparation: Requirement -> FlowSpec -> Proposal -> 已接纳 Run
                                              |
                     OA / 阶段 Owner 提出已注册只读 pattern
                                              |
                  Run Manager -> parent/generation 锁 + 容量核对
                                              |
                   operation.request_ref -> immutable stage binding
                             |                 |
                      身份/输入/预算       定义 + 方法正文 + profile
                             +-----------------+
                                              |
                   workflow.invoke -> 真实 fanout/briefing/resume
                                              |
                     必需子结果 admission -> 父阶段结果 admission

风险审查: 路由事件 -> OA 请求 -> TaskStore 阻塞
                重放逐项补齐，generation 不符则拒绝旧结果

最终交付: claim -> Gate -> durable request -> ShipService -> receipt
                         |          |                         |
                    重查 blocker    已启动但结果未知           Gate 重评
                                    -> 核对/诊断               -> Goal
```

| 任务 | 改动 | 实际消费入口与证据 |
| --- | --- | --- |
| 01 交接重放 | acceptance 路由持久化后补齐 OA 请求和 Task 阻塞；过滤旧 generation；shadow 投影异常不取得派发权 | `task_pipeline_acceptance.py`、`prepare_task_pipeline_dispatch`；`test_task_pipeline_acceptance.py`、`test_task_pipeline_shadow.py` |
| 02 交付恢复 | Gate/交付锁串行化；未启动请求重新核对 blocker 后恢复；精确 ship 回执补齐 settled；已启动未知和执行中异常先核对 | `goal_completion_gate.py` → `goal_delivery_recovery.py`；Run Manager 诊断及 controlled ship-retry 拒绝未知副作用；`test_goal_delivery_recovery.py` |
| 03 定义绑定 | 在 operation 的既有 immutable request 中绑定完整解析阶段、方法包正文/资源、执行 profile；角色权限沿用当前配置及原会话权威 | `read_only_dynamic_continuation`、invoke、reader fanout、排队恢复、briefing、retry、criteria、timeout；`test_stage_definition_binding.py` 和 Doc152 mock E2E |
| 04 有界展开 | 每个父阶段共用容量上限；限制单层只读、wait-for-all、可知预算、direct 子角色；required child 必须成功接纳；父取消 fence 阻止迟到推进 | 原 reservation/currentness、CallResultAdmissionService、reader recovery sweep；`test_dynamic_continuation_bounds.py`、`test_dynamic_continuation_lifecycle.py`、Doc152 mock E2E |
| 05 方法与实例 | OA 区分 Product Flow 与 Legacy；按缺口选择最小路线、局部委派和有界修订；资料核验沿注册 Generic 模板 | [资料核验示例](../../examples/prod/controller/evidence-review-194.md)、两份 General 配置、`zf-evidence-synthesis`；`test_design194_methods.py` |
| 06 验证评估 | 运行影响闭包、结构/事件哨兵、串行合同及 mock E2E；保存可复现命令及限制 | 本文与 [验证记录](194-stage-workflow-verification.json) |

没有新增 Task schema、canonical store、业务调度器或轮询服务。
新增 `stage-definition-binding.v1` 是 operation 引用的 required sidecar，写入沿用
`write_immutable_json_sidecar`；`stage_definition_ref` 和 `reserved_child_count` 保存在
现有 operation request 内。`effect_state` 是已有 delivery 事件的恢复事实。
业务语义留在 skill，Kernel 只检查身份、证据、能力、容量、预算与生命周期。

源阶段修改/删除后恢复原定义；方法包或执行 profile 变化则阻塞并报告，要求受控修订。
方法正文已留存，但本批没有把旧方法自动覆盖到当前角色会话。角色能力漂移同样拒绝，
不能通过模板恢复扩大已冻结权限。无新绑定的旧 operation 保留兼容路径。
方法包含资源与声明依赖，每包上限 16 MiB；外部 agent/plugin 定义尚无不可变适配，
该试点会明确拒绝。物理 worker slot 继续由既有调度器管理。

未知/中断子工作保留逻辑容量；已接纳终态才能释放，取消事件本身不冒充 Provider 已停止。
本批只读子工作不能创建 writer；mock 证明取消 fence、manifest 关闭和迟到结果拒绝，
不证明真实 Provider 进程取消的物理时延。

## 验收矩阵

| 194 要求 | 证据与边界 |
| --- | --- |
| Impl/Verify 流水、依赖、写入冲突、集成/rework | `test_task_pipeline_*` 316 项补充组合覆盖相关运行时及选路；复用既有实现，不新增第二个 scheduler |
| 请求交接断点 | acceptance route/OA request/TaskStore 间断点重放；delivery 未启动、已有回执、开始后未知、异常及并发恢复 |
| 模板定义不漂移 | 源修改、删除、损坏；角色/profile/方法资源漂移；queued restart 后按原 briefing 派发 |
| 父子展开边界 | 同父竞争只允许容量内分支；未知预算拒绝；父取消关闭读 fanout；拒绝迟到结果；required child 失败不能被父成功掩盖 |
| PRD / Issue / Refactor | `run-flow-smoke.sh` 的三流矩阵与 V4 noise mock E2E；OA run-plan/adoption/currentness 测试覆盖已配置 checkpoint |
| Research / General | Research fanout 结果接纳测试；Generic mock E2E 覆盖输入消费、一次修订、新旧 generation、重启及 artifact 完成；没有强制代码 commit |
| Preparation → Run | Workflow Synthesis 的 stale revision、缺失能力、不可用 Provider、恢复身份测试；现有 Proposal admission |
| OA shadow/blocking | 新 shadow fault 测试与既有 OA semantic-control mock E2E；没有把 shadow 建议转为执行权 |
| 真实收益 | 未验证。mock 的任务完成、调用次数和 pytest 用时不代表模型交付收益 |

## 验证命令与结果

先运行 `uv sync --extra dev --extra web`。
`dev-verify.py plan --base dev` 最终发现 400 个受影响测试文件。
规划器的 >80 文件策略会退化为全量；本批按 AGENTS 的影响闭包规则使用明确文件列表分批，
而不是因文件数量触发全量。第一次误启动的全量在中断前 3,394 passed / 14 skipped，
该不完整运行不计作验证通过。

1. 初始影响闭包 398 文件按 100/100/100/98 分批，4 个 xdist worker，
   每批 300 秒，排除 serial/perf/host/real_provider。
   结果分别为 **963 / 1,205 / 2,210 / 1,542 passed**，后两批共 19 skipped。
   总计 **5,920 passed、19 skipped**。后续新增调用方和修正另行补测。
2. `uv run pytest tests/test_task_pipeline_*.py tests/test_workflow_synthesis.py tests/test_generic_workflow.py tests/test_controller_blocking_rollout.py tests/test_design194_methods.py -q --no-cov -n 4 --dist loadfile`
   → **316 passed**。
3. 最终 fanout/briefing/recovery/admission/Generic E2E 定向闭包
   → **82 passed**；最终 delivery/ShipService/controlled action 闭包 → **34 passed**。
4. `uv run python scripts/dev-verify.py run --base dev --skip-tier deterministic --receipt <receipt.json>`
   → 串行 **10 passed**；sentinel **50 passed、1 skipped**；流程 smoke **10 passed**。
   `deterministic` tier 由上述显式分批覆盖；不得把 skip 本身视为通过。
5. `git diff --check`、改动 Python 的仓库 ruff 规则通过。
   技能使用项目原生 metadata/materialization 校验；通用 skill-creator validator
   不认识 ZaoFu 的 `stages/tags/auto_inject/load_on_demand` 扩展，不能替代原生校验。

完整显式测试路径、补测命令与 JUnit 汇总保存在验证 JSON；本机原始日志位于
`/tmp/zf-194-verification-20261029/`。批次结果有重叠，不应相加为独立用例数。
验证期间修复了结构门发现的超大文件增长和技能索引遗漏、timeout 兼容调用，
并重跑相应调用方。全量测试、coverage、真实 Provider、宿主 tmux/browser E2E 未作为本批通过项。

## 评估与后续条件

194 值得按当前增量方式演进：收益首先是交接可恢复、定义不漂移和并发边界可核验，
不是增加 OA turn。OA 方法职责增加，状态转换、派发、结果接纳和交付权仍由 Kernel 管理。
当前代码达到了继续做**隔离小范围 canary**的机制门槛；没有证据支持全量切换 blocking，
也没有直接执行/固定 fanout/动态 fanout 的真实性能胜者。

| 组织方式 | 适用条件 | 本次结论 |
| --- | --- | --- |
| 直接执行 | 单个明确工作单元，输入/验证范围已知 | 保留为真实比较基线 |
| 固定 fanout | 已知且互补的独立分工 | Generic 模板链通过 mock 闭环；不据此声称更省成本 |
| 有界动态 fanout | 证据缺口在阶段执行中才显现 | 只读单 pattern 的定义、容量、重启与生命周期已验证；语义收益待测 |

真实 canary 启动前固定任务集、model/backend/tool、输入 digest、总 token/美元预算、
同一验收标准，分别运行三组；优化样本与评估任务分开。
代表任务至少覆盖独立工作、强依赖、文件冲突、验证失败、用户修订、进程重启，
每条路线单独统计完成率、遗漏、错误路由、重试、token/费用、完成时延及尾部时延、
人工介入、取消确认、重复副作用、旧证据拒绝、最终集成成功率。
出现质量退化、未知副作用或控制权越界则停止扩面；若 OA 只增加耗时/成本，则减少检查点。
未取得这些可比数据前保持 shadow 观测或既有显式 blocking 试点范围。

后续工作需独立验收：跨路线真实 Provider canary、物理取消确认和精确父子费用归属、
方法自动从快照重建受限会话、任意多节点 fragment、经验驱动的模板晋升。
当前没有将这些项标记为完成。
