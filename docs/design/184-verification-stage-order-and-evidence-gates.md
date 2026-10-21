# 184 验证时序与命令证据门

> 状态: implemented（harness 与隔离验证）。2026-10-21；不表示原洛书 Run 已恢复或产品已验收。

## 问题与现场证据

洛书 `loshu-impl8-20261020` 的 Plan 把 G2/G3 独立 Task Verify 作为下游前置，
但 PrdFlow v3 在完整候选集成后才启动 Verify；writer 依赖可以由 Impl child
完成解锁。业务依赖图无环不代表加入验证生产关系后的执行图无环。

候选集成 `evt-ece227567ce6` 在 `candidate.ready` 前执行后续 candidate 命令，
浏览器缺少 `app/evidence/g4/candidate-context.json`，局部测试 reporter 缺少
verification/author context。这些是阶段输入问题，不能直接判为业务实现失败。

只读探针还确认：合同命令 owner 为 `task_verify` 时，Impl 必跑集合为空；
清空命令收据及 AC 到收据的关联后，保留 passed 声明仍可通过严格自检归一化。
现场六个完成任务实际有收据，探针证明的是门的缺口，不证明这些任务伪造了结果。

Autoresearch 最新隔离实验仅有配置/指令/诊断输入提交，product-scan 提出证据交接、
goal 身份与错误分类线索，tech-scan 超过 900 秒预算；没有修复代码、stuck 注入或
原问题恢复证据。通用演练结果与原问题复现必须分别报告。

## 本批边界

1. 优先以确定性回归复现，使用实际 runtime caller，不能仅测试独立工具函数。
2. 复用既有 Task Pipeline 的独立 Verify、已准入结果和串行集成能力；不在 Kernel
   硬编码 G2/G3、Q 名称或业务语义，不新建第二个 task schema。
3. 原批准 Plan、有效配置、运行代际保持不可变。采用不同执行图必须形成新的受控
   计划/运行合同；通过隔离测试不等于旧产品 Run 已恢复。
4. 命令 owner 表达验收责任；Impl 对当前本片可执行命令的自检不能抹去独立 Verify。
   后续 mandatory candidate/human 责任仍保留，禁止提前通过或删除。
5. 候选装配门只消费当前阶段可用输入。后续全局命令必须有真实消费者和完整证据；
   缺消费者时关闭失败，不能通过过滤命令静默降低验收覆盖。
6. 并行证明区分池容量、已派发、实际执行重叠、任务结果及最终验收。隔离资源、
   同一候选和证据身份是并行分片的前提；全局汇总不能重复声称分片执行。

## 验证顺序

先验证 Impl 收据闭包和 Candidate 命令时序，再验证 Task Pipeline 依赖释放与
八槽运行，最后验证分片到全局验收/Thin Judge 的交接及故障专用 Autoresearch
复现。阶段验收失败、缺失、陈旧代际和重放都要有负例。

Planner/Critic 对有效运行配置画出生产者、消费者、触发事件和解锁条件，检查整个
时序图；任务数、命令数、计划声明的阶段门不是可执行性证明。切片和并行分组仍由
agent/skill 决策；Kernel 只执行冻结合同中的机械约束。

## 实施记录

见 [本批任务](../../tasks/done/2026-10-21-1302-verification-gate-closure.md)。

- Impl 自检必须覆盖当前本片的 task_verify 命令；未来 candidate/human 及其他 producer
  的命令保持原责任。缺收据、失败收据、旧 target/digest 均不能通过。
- CandidateRebuilder 在存在 candidate.ready reader 消费者时延后 candidate 命令。
  缺消费者/命令判为合同失败；human 命令不能交给 reader 冒充完成。全部命令延后时
  仍执行候选 diff/clean 检查，不把后续命令列为已通过。
- 集成命令获得原子 sidecar `candidate-quality-context.v1`，通过
  `ZF_CANDIDATE_QUALITY_CONTEXT` / `ZF_CANDIDATE_QUALITY_CONTEXT_SHA256` 传递。
  sidecar 绑定 task、command 原文/摘要、run、generation、contract、base/target、attempt；
  legacy 缺少的 run/generation 不编造。quality check 保存 required descriptor。
  `evidence_kind=candidate_integration` 与 `independent_verification=false` 固定。
- 阶段验证额外发现：Task Pipeline 集成入口仅检查 settled/passed，能消费缺失/陈旧
  结果或退回旧一代成功结果。现读取最新 Verify operation 的 admitted control result，
  校验摘要、schema、passed、task/run/generation/contract 和当前 TaskRef commit。
  不满足时发已有 `task.pipeline.stage.waiting`，reason 为
  `waiting_for_current_verify_result`；只有真实集成收据驱动 TaskStore done、释放依赖。
- Planner/Critic 的 execution-consistency 方法增加有效 Flow 的证据生产/消费时序、
  独立验证解锁、并行资源与汇总边界复核，三份 skill 分发内容同步。
- resident debug 默认场景改为 `incident-reproduction`，读取传入原始故障引用，要求
  实际 caller 的 RED/GREEN、有界补丁与独立审查。显式 stuck 演练保持可用，
  proposal_only 策略不变。测试证明 seed/runner 交接，不声称诊断 agent 已完成真实修复。

## 验证证据与范围

| 验证 | 结果与边界 |
| --- | --- |
| 空自检收据、候选提前执行 | 首轮 3 RED / 1 PASS，修复后相关 32 PASS；随后候选/准入闭包 73 PASS |
| 集成来源准入 | 缺结果、坏摘要、旧 target/run/contract、新一代失败共 6 RED；修复后合法来源集成、解锁、重放测试通过 |
| 实际调度 caller | mock transport 下 Impl 8、Verify 8、两池同时 8+8 在途；独立工作区、容量上限、同目标 Verify、重放不重复 |
| Thin Judge 准入 | 8 份证据对应 mandatory claims；完整可准入，缺片/旧目标/旧代际拒绝；与调度测试合计 7 PASS |
| 影响闭包 | selector 初选 229 个测试文件，3340 PASS / 8 SKIP / 2 FAIL；默认场景旧断言已修，另一个为基线 fixture 缺失；新增影响域与重点复验 121 PASS |
| dev-verify 其他层 | serial 10 PASS、sentinel 51 PASS、Flow mock E2E 10 PASS |
| skill | 方法物化测试包含于重点复验；source/.codex/.claude 字节一致 |
| 洛书适配 | 独立 worktree 提交 `7714f44`、`b5a671e`；13 项 context 测试、typecheck；Kernel helper → 实际 reporter 的三条原命令共 36 个断言通过 |

`dev-verify` 因文件数 >80 建议全套；本轮按 AGENTS 的影响域规则运行其选出的确定性
测试集合（4 workers、900 秒预算），通过 `--skip-tier deterministic` 单独执行其余层。
未跑全仓 pytest、真实 provider 八路、最终 Candidate Browser 或原故障 Autoresearch
provider 实验。不得把 mock 在途计数当作模型实际并发，或把 integration 收据当作独立验收。

基线缺陷：`test_canonical_delivery_bundle_source_refs_point_to_fixture_files` 引用
`tests/fixtures/canonical_delivery_bundle/agent_skills_default/tasks/plan.md`，Git 基线不含
该文件。已在 detached `4009aa7` 重现同一失败，临时 worktree 已清理；本批未补造来源。
日志分别为 `/tmp/zf-verification-closure.log`、`/tmp/zf-verification-baseline-fixture.log`、
`/tmp/zf-verification-final-focused.log`、`/tmp/zf-verification-dev-verify-receipt.json`。
RED/命令记录见关联任务。临时路径用于本机复查，Git 中保留本表和测试断言作为长期记录。

## 原 Run 续跑条件

本批未把 PrdFlow v3 改名为 v4，也未改旧批准 Plan。原计划要求阶段独立 Verify 解锁时，
续跑应采用新的、已批准的 task-pipeline 执行合同，保留旧新任务/证据映射；两个池各 8，
`maxActiveTaskPipelines` 至少 16 才能同时容纳 8+8，背压和 ready-set 仍决定实际占用。
候选增量集成继续串行 CAS，最终 Verify 固定 frozen exact commit。外部 Q 项缺证据保持阻塞。

产品 reporter 适配尚在单独 worktree，原任务/候选 ref 不会自动变化；将其纳入后续任务
或受控候选补丁后，再以新目标重跑正式 Task Verify / Candidate Verify / Judge。
共享 8022/数据集的候选浏览器仍是串行合同，必须先提供隔离分片设计才能宣称八路运行。
