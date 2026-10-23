# 184/185 路由准入实施与逐分支验证

后续实施见 [有界工件执行、规划绑定与恢复隔离](185-bounded-execution-and-recovery.md)。

> 状态：部分实施、部分验证；不构成 185 全部能力或全分支真实交付签收。
> 日期：2026-10-23；worktree：`/home/min/workspace/public/184`。
> 实施基线：`b493d88608413e8042c1e62cf9cbf06d607a3df2`。
> 设计：[185](../design/185-agent-routing-delegation-and-workflow-recovery.md)；历史：[184 真实验证](184-live-validation.md)。

## 需求核对与实施范围

本 checkout 没有 184 设计正文。核对依据是 184 已提交实现、测试、实施记录和仍 active 的真实交付任务，
再对照 185 的 A—G 分期。没有把旧记录中的“入口成功”升级为“交付完成”。
用户已批准生成 backlog 并实施，八项候选从 `backlogs/` 移入 `tasks/active/2026-10-23-1456-185-*.md`。

| backlog | 基线与本轮变化 | 签收边界 |
| --- | --- | --- |
| 01 policy-binding | 原目录不能区分 v4 shadow/blocking；新增真实 topology、mode、profile 和执行策略摘要，批准绑定随策略变化失效 | 定向测试及 CLI/HTTP 过期批准零 invoke |
| 02 adaptive-planning | 已有选路；补事实评估、两阶段复核方法、双入口权限交接，修复真实 Channel 建单/启动缺口 | 方法与调用链已改；Task Map 后真实配置修订未闭环 |
| 03 bounded-execution | headless turn、legacy direct、E0 profile 均已存在 | 通用 Task 单 Agent 准入/执行/验收仍未实现，保持 active |
| 04 config-activation | 已有候选验证、CAS apply；新增实际消费者与批准快照的执行策略比较 | stale/corrupt/missing 拒绝；不热改已有 Run |
| 05 stage-recovery | 已有 Run Manager、attempt、恢复包与 v4 redrive | 本轮回归已有故障合同；新单 Agent 接手与真实跨会话完成未闭环 |
| 06 provider-delegation | 已有固定研究和 adaptive pilot；通用 E0 仍是 shadow | 未新增 Kimi adapter 或扩大 native capability 声明 |
| 07 live-blockers | 旧 tmux 首次输入问题本次未复现；修正 PRD 非目标/授权混淆，补只读来源与本地规划写入方法 | 标准 Issue 真实运行仍预算失败；不能签收完整交付 |
| 08 branch-e2e | 新增下表分支清单、隔离真实任务与 Docker 浏览器验证 | 已测、未测和失败分别登记；未达到全分支真实通过 |

## 已接入的行为

1. `workflow_route_catalog` 继续投影当前有效配置，保留兼容 `topology` 的 lane 含义，新增 `flow_topology`
   和 `execution_policy`。其目录摘要覆盖新的策略投影，因此原提案须重新预览。PRD/Issue/Refactor 的 v4
   shadow/blocking 不再得到同一个摘要；Research 不继承 delivery 的 mode。权限、模型、预算、门禁等
   执行策略变化保守地使提案失效；Web 显示和观测配置变化不触发重新批准。
2. `durable_call_workflow` 在批准完整性检查之后、实际准入之前，核对不可变 effective-config snapshot
   和消费者已加载的执行配置。只写入 YAML 而进程仍持有旧配置时拒绝准入；调用者应加载已批准配置后重试。
   没有增加静默热加载，也没有覆盖现有 Run 的配置。无提案的 legacy invoke 保留原合同。
3. Kanban 直接需求与 Channel canonical PRD 保持不同来源路径。Channel 的 ready、Owner 确认、创建 Task、
   Workflow 启动批准是不同事实。对历史 `implementation_start=false`、但 readiness 为 ready 的已确认 PRD，
   可通过新启动提案中的严格布尔参数 `authorize_channel_execution: true` 单独授权本次执行。
   准入核对当前 PRD/Leader/Task、Owner 或明确 `channel.workflow.start` 委托、精确提案 payload；
   不修改 PRD、不把普通 ready 当执行授权、不把该标记当成风险豁免，unresolved readiness 仍拒绝。
4. 保留 canonical Channel source_ref 的 state-relative 身份，之后经现有 digest 校验和完整 Markdown
   materialization 交给 intake，避免错误解析到项目根导致 source coverage 拒绝。
5. 主 Agent 已知的项目 `target_root` 在批准前补入嵌套 Workflow Plan 的缺失参数；保留显式目标和 defer。
   真实首轮失败和修改后的二轮成功均保留。PRD 产品非目标与“本轮仅规划”授权说明分开。
6. 难度和模式选择仍交给 Agent/skill：范围、依赖、写入冲突、风险、验收与预算形成建议；优先复用有效配置。
   Task Map 后再次复核承载能力；改变冻结策略必须受控修订。未增加关键词路由器或新状态机。

实现入口见 `workflow_route_policy.py`、`workflow_config_consumer.py`、`channel_execution_approval.py`、
`control_actions_workflow_request.py`、`workflow_request_identity.py`、`web/plan_extraction.py`；方法在现有 skill。
新增模块均有实际调用者，测试覆盖这些调用者。

## 分支矩阵

下面的“通过”只指对应测试层。单元/集成/mock E2E 不证明真实 Provider 产品交付。
编号用于后续补测；不声称枚举源码所有条件或任意分支组合，也不声称 branch coverage 为 100%。

| ID | 分支 | 主要验证入口/证据 | 当前结论 |
| --- | --- | --- | --- |
| B01 | 问答、否定建单、引用命令、仅跟踪、缺输入澄清、跨轮纠正 | `scripts/eval-intake-routing.py`，真实语义复测 | 正确夹具 12/12 通过；不执行提案 |
| B02 | Kanban 完整需求 → Task → Plan → 批准 → invoke | `issue/entry-round1`，实际 Codex + HTTP | 入口通过；重复批准一个 invoke |
| B03 | Channel draft/未确认/未就绪 | `test_channel_prd_task_contract`、`test_workflow_start_service` | 确定性拒绝/仅规划通过 |
| B04 | Channel ready、历史仅规划 → 独立 Owner 授权 | `test_channel_execution_approval`、`prd/owner-handoff-r2` | API 与真实入口通过，PRD 不改写 |
| B05 | 非 Owner、未绑定提案、payload 改动、旧 PRD/Leader、unresolved | `test_channel_execution_approval` | 拒绝通过；无 invoke |
| B06 | 嵌套 Plan 缺 target_root、canonical source identity | `test_web_headless_agent` 的 Channel 建单用例及 B04 | 自动继承已知目标，完整来源通过；第一轮真实失败保留 |
| B07 | PRD standard/v3 | flow smoke、真实 PRD 运行 | mock 通过；真实结果单列 |
| B08 | Issue standard/v3 | flow smoke、真实 Issue 运行 | mock 通过；真实预算失败 |
| B09 | Refactor standard/v3 | `test_refactor_pipeline_smoke`（flow smoke） | mock 通过；本轮没有真实 Refactor |
| B10 | PRD/Issue light、幂等、合法/不合法门禁 | `test_light_flow`、`test_intake_semantic_handoff_mock_e2e` | 确定性/模拟通过；未证明真实 light 完整交付 |
| B11 | PRD/Issue/Refactor v4 shadow/blocking | `test_workflow_route_policy`、v4 runtime-noise mock E2E | 策略绑定及模拟运行通过；不能推导三类真实 v4 均通过 |
| B12 | General 已注册入口、受限候选图 | `test_control_actions_research`、generic complex mock E2E | 确定性/模拟通过；非任意 YAML 生成器 |
| B13 | Research fixed、显式 adaptive pilot、缺只读能力 | `test_control_actions_research` | 调用与 fail-closed 合同通过；未运行真实助手子树 |
| B14 | 不存在路线、缺参数、旧 Task/config | `test_workflow_start_service`、`test_control_actions_research` | 拒绝通过 |
| B15 | 同路线预算/权限/门禁改变后的旧批准，CLI 和 Web | `test_workflow_policy_admission_mock_e2e` | 6 个交叉用例通过，零 invoke |
| B16 | profile/允许参数复用、配置候选、批准、CAS、并发修改、非法候选 | `test_workflow_config_apply`、`test_task_workflow_plans` | 确定性通过；不证明所有 Flow 自动重配 |
| B17 | 消费者旧配置、缺/坏快照、已加载快照、UI 变化 | `test_workflow_config_consumer`、`test_workflow_invoke_pattern_bridge` | 拒绝或准入通过；不更新既有运行 |
| B18 | Task Map 后发现初评不适配 → 受控修订 | readiness/planner skill、真实 Planner、续批 synthesis caller | 已绑定 Task Map/有效策略/代际；真实初评配置 apply/CAS 与新消费者通过，Task Map 后修订闭环仍待验证 |
| B19 | 已受理但未启动、后端选择保留、批准后跟踪 | Docker `workflow-start-receipt`、`workflow-proposal-page` | 浏览器结果单列；含 API fixture/mocked projection |
| B20 | 超时/退出、session 丢失、Kernel 重启、恢复次数耗尽 | task-attempt/recovery mock E2E、provider-stop tests、续批 tmux host | 已补终止确认及消失 pane 恢复；真实 Issue 故障暴露的缺陷已修，3 项 host 通过；真实 Provider 自动接续仍待验证 |
| B21 | 迟到成功、过期 dispatch、重复终结、语义失败归属 | `runtime/test_late_success_recovery`、`test_task_attempt_recovery` | 确定性通过 |
| B22 | Channel synthesis/evaluation 取消、进程丢失后恢复、不复活 | `test_channel_synthesis_recovery`、`test_web_headless_recovery`、Channel recovery mock E2E | 确定性/模拟通过；旧 184 真实取消证据不冒充本轮新测 |
| B23 | Provider 切换、旧 run、重复切换、次数上限 | `test_execution_policy_routing` | 受控路由及 mock caller 通过；未做真实跨 Provider 接续 |
| B24 | 预算耗尽 | provider-stop tests、实际 Issue | 确定性保护通过；真实交付失败按失败记录 |
| B25 | 写入后结果未知、外部副作用未知 | 既有 workflow unknown-delivery 边界 | 本轮未单独真实故障注入，不宣称 exactly-once |
| B26 | bounded single 的启动/重复/取消/重启/不合格结果 | backlog 03、bounded mock E2E、真实 TASK-6E4E2C | 工件交付入口已实现；确定性分支及真实执行者→独立验证→Kernel完成→TaskStore done通过；不是通用源码 writer，真实故障恢复另计 B20 |
| B27 | native delegated 的权限、子树取消、后台未收敛、唯一根结果 | E0 capability/profile 测试；backlog 06 | E0 shadow 合同通过；实际协作闭环未实现 |
| B28 | Kimi headless/native、跨 Provider State Packet 接续 | 本机 Kimi 源码检查；backlog 05/06、canary HTTP/process tests | 只读 tool-free canary 已接入实际 HTTP；15 项模拟进程测试通过，真实缺 CLI 分支明确失败且无 Task 副作用。Kimi 正向及跨 Provider 未实测，不升级能力声明 |

## 验证层与命令

证据目录：`/home/min/workspace/public/zaofu-evidence/185-validation-20261023T151157Z/`。
原始输入、冻结预算和 oracle 沿用仓库 `tests/e2e/intake_live_validation.py` 与 `fixtures/intake-live`。

- `final-focused.xml`：352 passed，262.80s。涵盖 route/catalog、consumer/apply、两种入口、提案/Plan、
  canonical PRD、headless main Agent、execution profile、attempt/recovery、Channel/Web delivery，
  以及 policy admission、semantic handoff、Channel process-loss、Generic complex、recovery-control、v4 noise mock E2E。
- 嵌套目标保留显式目标/defer 的补充回归：`target-binding.xml`，76 passed，80.80s。
- 最后 source identity 提取后补跑 `remaining-branches.xml`：98 passed，60.84s。
  覆盖受控 Workflow request、Channel 授权、source coverage、light、Research、provider stop/late result、
  fanout failure、Channel synthesis recovery、headless recovery。与上一组有重叠，不相加当作不同测试总数。
- 最终 `bash scripts/dev-premerge-gate.sh`：51 passed，33.56s；此前一次结构门禁发现模块 1002 行，
  将新来源解析集中到现有 identity owner 后通过；未放宽上限。
- `bash scripts/run-flow-smoke.sh`：10 passed，20.88s（另有 dev-verify 同组执行）。
- docs/instruction 定向测试：22 passed，21.71s；skill 分发一致性另核对。
- `npm run build` 和 `npm run test:unit` 通过；无前端产品代码变更。
- `dev-verify.py plan --base dev` 包含整个旧 184 分支差异，选择了 518 个文件。
  `dev-verify.py run --base dev --tests tests/test_intake_live_validation.py --skip-tier deterministic --skip-tier web`
  已执行并保存 `dev-verify.json`：serial、sentinel、flow smoke 通过。广泛 deterministic/web 默认 tier
  明确跳过，替代为上面的定向影响闭包与独立前端验证；没有运行全量 pytest。

Docker 浏览器仅使用 `mcp/playwright:latest`、host networking、隔离 8002 API/UI。
前几次因挂载 UID、Chrome XDG 目录、fixture 未初始化 events/onboarding 失败；补齐测试环境后重跑，
没有通过修改产品断言消除失败。最终日志为 `browser-r5.log`：4 passed，41.5s。
四项 skill 的 YAML frontmatter 与基线相同、已有分发副本一致，见 `skill-validation.json`。
通用 OpenAI quick_validate 仅接受其中一个 skill；其余因仓库既有扩展字段不兼容，未删除字段来迎合通用验证器。

## 真实任务的事实与限制

两项目均使用自定义 `.zf-validation`，Codex `0.163.0`、`gpt-6-astra/high`、原生 tmux transport、v3 standard。
每 operation 冻结 600s/1M tokens/60 samples/$10，Run 1800s/3M tokens/$30；未为了通过增加预算。
本轮在未提交工作树上边定位边复测；Issue 启动早于最终新增的若干字段/方法，不能把它称为最终提交的全量 E2E。

**Issue：入口成功、交付失败。** 主 Agent 真实建单 `TASK-05AEFF`、Plan 和批准完成；重复批准一个 invoke。
原生 triage 自动接收，无人工 Enter。运行约 582 秒后，1,070,856 tokens 超过 1M operation 上限，
记录 `workflow.budget.exceeded` 与不可重试 attempt failure。未到实施、Verify/Judge 或独立 oracle。
执行中两次 scope-write 拒绝是命令混合读取原项目路径与写隔离目录引起；补了技能中的
受控 artifact read/本地写入分离方法，未放松 guard，但尚未以新方法完成 Issue 重跑。

**Channel PRD：完整来源与两层批准通过。** canonical PRD r1 的产品非目标不再混入“不实施/不执行测试”。
readiness 为 ready、历史 implementation_start=false。Owner 确认后，实际主 Agent 发起完整交付建单和
单独启动 Plan；第一轮缺 target_root 被 422 拒绝，无 Task/invoke；修正后 `TASK-42DD30` 成功，
重复批准没有重复 invoke。该第二轮同时在输入中明确了目标，默认 target 注入的因果证明来自 HTTP 回归用例。
完整 PRD hash 为 `37bd680b1ca0769a37ff5efe701e7111a74c2f33231dcc85aa9016807158571b`。
真实执行的 product-scan 和 tech-scan 均有 admitted result，自动进入 Planner；
Planner 在 604.433 秒触发 600 秒 wall_clock 上限，当时 758,770 tokens、10 个 usage samples。
该 attempt 为不可重试失败，未到 Critic、计划批准、实施与 Verify/Judge。未增加预算继续运行。

两项目独立 `intake_live_acceptance.py` 均返回 exit 1：Issue 原行为仍失败，PRD 尚缺 ledger.py。
这是对尚未交付状态的独立确认，不是验收通过。oracle hash、日志在 `product-oracle-results.json` 和各项目目录。

尚缺完整真实产品交付，故旧 184 live-task-delivery 与相关 185 项保持 active。
下一轮必须针对实际阻断补证据，不能降低 AC、跳过 Critic/Verify/Judge、把 light 当单 Agent，
或把 E0 的 `dispatch_effect: none` 描述成已经调度助手。

## 任务审计

本轮八项加两项旧 live 任务共十项。对照最近 `git log` 和当前失败证据，旧 184 两项仍未完成；
185 仅 01/04 达到本项确定性验收，其余保持 active。没有把旧提交、已有单元测试或源码存在
当成新功能完成证明，也没有调整无关候选任务。归档时只记录实际实施 commit。

## 最终验证补充与清理

- 真实语义第一轮 8/12；误用计数器/账本项目承载登录/订单用例，四项未命中预期。
  Agent 指出需求与代码不匹配并要求澄清，保留 `semantic-recheck` 全部原始结果。
  从 184 已校验归档恢复原 `semantic-validated` 夹具，只更新 harness 路径，先登记源码摘要，
  不修改 labels；第二轮 12/12，通过后的七个夹具文件 hash 与预登记完全一致。
  覆盖否定、引用、模糊/混合目标、Issue/PRD 推荐、仅跟踪、多语言与跨轮纠正；不代表执行提案或产品交付。
- 最终 `dev-verify-final.json`：serial 10 项、sentinel 51 项、flow smoke 10 项通过；
  仍显式跳过 broad deterministic/web，采用本报告记录的定向闭包与单独 Web build/unit/browser。
  补传新模块及直接调用者测试路径供 planner 解释，不把跳过项描述成通过。
- Issue、PRD、浏览器均通过 EventWriter 记录 `simulation.done`；真实运行失败和 oracle 失败状态保留。
  自有 tmux、watcher、8002 Web 进程已停止；确认没有进程 cwd 指向模拟根后清理两个临时根。
  未操作真实 8001 或其他会话。归档排除 provider auth/private/codex-home、缓存和 symlink，
  文本脱敏，manifest 同时保存源文件与归档摘要；四个归档 SHA256 已核对。
  详情见 `archive-manifest.json`、`cleanup.json`、`semantic-summary.json`。

当前结论：八项新增 backlog 中仅 01/04 达到本项验收并可归档；其余六项与旧两项 live 任务保持 active。
尚不满足用户要求的“所有流程/分支均端到端通过”。后续先解决标准规划在冻结资源内无法完成的问题，
再完成 Task Map 后适配、通用 single 执行及单阶段恢复；native/Kimi/跨 Provider 在能力证据齐备后单独验证。
