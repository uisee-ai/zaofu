# 设计 182 · 洛书重规划与综合验收

> 状态: done。设计 182 的公共修复、受控重规划及综合复核已完成；产品 Plan 保持待人工审批。
> 设计：[182](../design/182-plan-review-task-slicing-and-evidence-closure.md)；[实施记录](../impl/plan182-review-and-evidence-closure.md)。

## 审查边界和入口

本次在 `/home/min/.codex/worktrees/9f60/zaofu`、分支
`wip/codex-2026-10-20-plan182` 实施。原项目的 Plan 和事件保留；新规划使用
`/home/min/workspace/loshu-plan182`、配置状态目录 `.zf-plan182`。

- [最终新 Plan 完整 Preview](http://10.9.100.141:8002/?project=loshu-plan182-eae8133e&page=inbox&plan=evt-3f6b832282a3)
- [新项目 Inbox](http://10.9.100.141:8002/?project=loshu-plan182-eae8133e&page=inbox)
- [修复后的旧 Plan 完整 Preview](http://10.9.100.141:8002/?project=loshu-033ea7f8&page=inbox&plan=evt-642cfd6f51d0)
- [旧 Plan 的只读 API](http://10.9.100.141:8002/api/projects/loshu-033ea7f8/plans/evt-642cfd6f51d0/preview)

当前授权涵盖 ZaoFu 修复、重新规划和评估。业务代码、数据库迁移及发布未执行；
没有自动批准洛书产品 Plan。生成了方案、方案可执行、产品已通过是三个不同结论。

## 固定的来源分母

原稿 `/tmp/gm/loshu-fms-handoff.md` 共 5,082 行，内容 SHA-256 为
`582c21ea932e124daaa3041496f2a7064e817a24e6ec67b8b62e89716fcfc2ce`。
本轮沿用六行凭据脱敏后的 MD，SHA-256 为
`61dd290e611fda6260b5f769cc98f4c775d038ce29f6a175222bc405a9e89b7e`，不复制凭据。

| 范围 | 必须保留的含义 |
| --- | --- |
| 62 个主页面、6 类表单、80 个 ASCII | 清单完整只是结构证据；C01 六标签、D01 五标签必须继续细分追溯 |
| E01–E26 | 验收场景要绑定行为生产者、命令和最早可验证时点 |
| Q01–Q15 | 保持未决事实，按受影响阶段关闭；不能猜测答案或因尚未决定就删需求 |
| A18、Q05、E25/E26 | 核心与扩展条件分别保留；A01 默认 M4/C0，扩展验收后 M4/C1 互斥替换 |
| A27、Q08 | 条件范围不能默认启用，也不能连同核心 A26 删除 |
| 取消、Q15 | 现有取消态只读与新增取消命令分开；不能把取消等同物理删除 |
| RTK、Q07 | 核心账号功能保留，阈值决定单独标记 |
| PostgreSQL、59 表草案 | PostgreSQL 是已确认方向；草案与既有测试记录不等于本轮真实迁移或产品运行结果 |

原 Plan 有 26 个任务、13 个波次，2,260 个行级条目、300 个条款、82 个 AC、
81 条命令和 24 行浏览器矩阵。以上用于追溯和新旧映射，不作为目标任务数或功能通过率。

配套 `schema-validation.json` 实际记录 57 项历史测试，其中编号 29–57 为 29 项运营统计
用例；不能再相加成 86 项。该记录使用 PGlite，`remote_database_accessed=false`，不证明
本次 PostgreSQL 多连接竞争、真实 API、升级迁移或外部服务协调已验证。

## 已验证的公共修复

| 检查 | 实际证据 | 判断 |
| --- | --- | --- |
| 完整正文与摘要 | 旧 Plan API 返回 45,491 字节，与绑定正文一致；页面单独展开摘要 | 已修复正文选择和引用交接 |
| 长文与附件 | Docker Chromium 在桌面和手机读到末尾及 Q15；12 个附件内容与绑定 digest 一致 | 真实页面链路通过 |
| 只读预览 | 浏览器未发起非 GET API 请求，前后审批仍 pending，无 JS 错误 | 没有因查看触发审批 |
| 切换 Plan | 延迟旧响应后关闭并打开新 Plan，旧响应不能覆盖新正文 | 真实 React/API 回归通过 |
| 超长正文 | 12,000 条中文/emoji 触发分页，逐页拼接与 API 全文逐字一致 | 正文保护与分页不会丢失内容 |
| 分层完成 | 实际 writer handoff 保留未来 mandatory AC blocked；当前失败仍拒绝 | 局部完成不冒充候选完成 |
| Verify 准入 | Runtime 固定当前阶段；普通和 typed 路径均检查必验项、owner 和未来签收 | 不能靠改报告 owner 或 waived 绕过 |
| 方法注入 | 实际 Planner 的工具读取事件及物化文件 hash 与 canonical skill 一致 | 消费证据与独立语义复审分开取证 |

测试：影响闭包 867 passed；sentinel 51 passed；确定性流程 smoke 10 passed；
新增 Docker 页面用例 4 passed；前端 build/typecheck 通过。最后的摘要/CLI 修复额外
验证相关调用者 230 passed，并重跑 sentinel、mock smoke 与浏览器；文档检查 9 passed。自动扩大回归原有 38 个失败
已在原 dev 复现；手机 contract-health 表格溢出同样在原 dev 复现。完整原始结果和失败分类
见实施记录，不能把本次聚焦通过写成全仓全绿。

## 正式修订及新旧差异

新工作流为 `loshu-plan182-review`，沿正式 flow intake/submit 进入两个扫描、Planner 和
Critic。中间 Plan `evt-60ffe3d0ec60` 经受控拒绝后返工，原版本保留。当前正式收集的候选为
`fanout-prd-plan-evt-4ac1afb9` 的 Planner 产物。正式 Critic 完成事件
`evt-bc99c0e49fbc`，聚合事件 `evt-56357575dfc5` 的 recommendation 为 approve；
Kernel 于 2026-10-20 04:36 UTC 生成 Plan `evt-3f6b832282a3` 和审批请求
`evt-e9b5532a5842`。Critic 建议通过没有替代人工审批。

- Package SHA-256：`31d383653b17c918aa3aa9cd6689d5614548c6620f06ab29e3edafb7f2b121e6`。
- 正文 SHA-256：`5085a2912eb9752fcd1192e6fbf39a9a7fb512fb6ed5c669a5a0645ed112eddc`。
- task_map SHA-256：`88d41976674947a1f007708235fd3675489156cb55f377d1fc79a42d88392ceb`。
- 正文为 137,993 字符、222,432 字节；Docker 浏览器逐字比对全文并验证 12 个附件哈希，
  桌面及 390×844 手机视口无 JS 错误、无审批写操作，状态保持 pending。
- 事件账本无 `plan.approved`，仅 scan/plan fanout；新项目没有 `app/`，未派发产品实施。

| 比较项 | 旧正式 Plan | 当前修订 | 判断 |
| --- | --- | --- | --- |
| 任务 / 波次 | 26 / 13 | 54 / 32 | 行为拆分增加粒度；不证明总耗时更短或并行度最优 |
| AC / 命令 | 82 / 81 | 186 / 120 | 122 条 task_verify、64 条 candidate_verify；不能用数量替代充分性 |
| 页面 / 表单 / ASCII | 62 / 6 / 80 | 62 / 6 / 80 | 来源清单保留，行为签收仍需实现后证据 |
| E 场景 / Q 决定 | 26 / 15 | 26 / 15 | 场景映射已同步；Q 仍按事实保持 open |
| 浏览器矩阵 / 旅程 | 24 行 / 50 旅程 | 5 个阶段 runner 行 / 53 旅程 | 新行聚合 G2/G3/G4，另有 G2/G3 当前阶段验收；不是少了 19 个页面 |
| 正式旧新映射 | 无本次重拆对照 | 26 任务、82 AC 全部具名 | 75 个产品 AC 映射到新责任；7 个 AC-PLAN-* 转回规划工作流 |
| 来源原子化 | 2,260 行条目 | 2,043 记录 + 15 显式混合子条款 | 共同 2,030 行，旧表头/标签 230 行，新开篇/目录 13 行；没有未映射业务条款 |
| 首次应用入口 | 最后装配承担首次正式接线 | LS-ACCESS 在第 2 波建立入口 | 唯一写入责任与下游扩展约定明确 |
| 集成验证 | 主要在最终装配 | G2 第 22 波、G3 第 30 波、G4 第 32 波 | G2/G3 当前阶段通过才解除后续依赖，最终候选仍复验 |

本轮正式产物已包含 24 个配套文件的逐项消费记录、主项目实际读取路径和 SHA-256。
Planner 与工作流 Critic 均实际查看 8 张 PNG；读取数据库目录得到 59 表、534 列、106 条 FK、
161 个索引。这里只核对交接材料，未运行 SQL。旧扫描的缺失记录没有被改写，新索引明确
说明哪些历史结论已被补充事实替代；真实服务、维护者、接口和条件决定仍未凭空填入。

旧基础任务中的 7 个规划 AC 不再要求产品 writer 将来补做。旧新对照保留原 AC 文本、
mandatory、原 owner/tier、来源、拆分理由和条件范围，并给出新任务/AC 绑定。
E05 的设备跨项目竞争、E14 的历史状态补录经复核补齐；26 个 E 在旧新对照与追踪表中的
AC 集合完全一致，没有用所属任务的首条 AC 兜底。G0 演示改用自身拥有的安装/清单命令，
不依赖后续 LS-ACCESS 的 Web 启动器。

只读检查得到 54 份合法 canonical contract snapshot；64 条未来 AC 在 Impl 模板中为
`blocked`。这是内存中的契约兼容性探针，没有写 TaskStore、事件或产品通过收据。
Planner 的 13,526 项检查属于规划引用与结构闭包，不计为产品测试通过数。

## 为什么旧 Preview 不完整

旧链路在 `task_map.ready` 已有正式 Plan/Package，但转成 `plan.approval.requested` 时
没有完整传播这些引用；Preview 又优先读取 `digest_ref`。因此主要问题是审批交接和正文
选择，不能据此推断 Planner 没生成正文。旧正文实际有 45,491 字节，修复后可以完整读取。
此外，Preview 的 task_map_summary 只列前 50 项，矩阵与正文是不同产物；摘要不能承担全部规划内容。
最终复审还发现 CLI 错把 state-relative task_map 当成 project-relative 路径，摘要仅认旧
`verification` 字段而漏掉 canonical `validation.commands`，导致已存在的命令被误报缺失。
已复用同一受限路径解析和 canonical 命令解析器。当前网页摘要从 hash 绑定的 task_map
重新投影，`digest_source=bound_task_map`；历史 digest 文件和事件不改写，未绑定的旧摘要保留。

修复把正文、摘要和同版本附件明确分开。不存在可信全文时显示不可用；不会猜测目录中
哪个文件“看起来最新”。长文仍使用现有安全渲染保护：超过阈值可退为纯文本，超长时分页。
已用逐字重建测试证明这两个保护机制不丢正文，不能把纯文本显示误判成内容缺失。
但实图发现其继承了聊天消息 320px 的内层滚动框。已限制修复到 Preview，使其使用主阅读
区域滚动；新增 Range 断言先失败后通过，验证末行在桌面和手机真实可见。此前仅检查
textContent 和外层 scrollTop 的断言不充分，最终浏览器记录已用此更强断言重跑。

## 任务拆分的评判依据

旧图的 26 个任务、13 个波次和无环依赖，证明的是结构合法。新图是否更合理，要看行为
是否可独立验收、前置是否齐备、共享文件是否有唯一 writer、原子操作是否保持完整。

- 车辆创建/详情、保险、质保、状态变更、跨项目调拨有不同前置和失败边界，拆开合理。
- 设备登记、装拆、替换、生命周期分别验证；替换内部仍要原子提交，不能把拆旧/装新做成两个成功点。
- 全站契约清单可做静态盘点；真实服务、库存、并发和恢复义务应移交其行为生产者。
- 单次维保作业含库存差量、设备事实、幂等和失败回滚，保留一个事务 owner 合理；通过片内小步 TDD 控制复杂度。
- 现有取消态只读与新增取消命令分开；只读不能因为等待物理删除而失去独立交付性。
- 早期真实入口由单一 owner 建立，下游走扩展约定；G2/G3 要有当前阶段可执行验收，G4 才承担最终全量复验。

首次新图仍有两个语义缺陷，独立 Critic 确实拒绝了它：G2/G3 的验收全挂在未来候选阶段；
R-L2659 把历史局限与真实 PostgreSQL 并发/恢复义务合并成静态盘点。返工已新增 7 条阶段 AC、
6 条命令和 15 个显式子条款，正式 Critic 复核通过。这个过程证明机械闭包检查不能替代
语义 Critic，也不能把技能注入成功等同一次生成就合格。

## TDD 与 SDD 的结论口径

SDD 在这里指规格驱动开发。规划证据应形成原稿条款 → 可观察结果 → 行为生产者 → AC →
命令/测试边界 → 验证阶段，并保留未决条件和变更理由。任务数量、测试命令数量及文件完整
都不能单独证明规格充分性。混合条款返工是这项检查的实际反例。

本批 ZaoFu 的行为修复已有基线失败到修复通过的记录；测试夹具、依赖或环境失败单列，
没有混入功能 RED。开发与 Verify 方法要求将规格、测试名、预期失败、同一测试的 GREEN、
基线和实现版本绑定。纯规则、数据库事务、UI 行为分别使用适合的测试层级。

洛书新 Plan 只能证明 TDD 方法和测试责任已被规划；产品尚未实施，不能认证产品已遵循
完整 RED→GREEN→REFACTOR。`expected_red=false` 表示交付时测试必须通过，不等于关闭 TDD。
文档盘点等无行为变化的工作可以说明不适用，不应为形式制造失败测试。

## 业务与技术的复审清单

下表绑定上述正式修订，核对规划责任是否存在；不能当作已经运行成功的功能清单。

| 范围 | 新图的主要责任 | 必须保留的验收边界 |
| --- | --- | --- |
| 身份、成员、组、授权、API 治理 | ACCESS、MEMBERS、GROUPS、GRANTS、ADMINISTRATORS、API-RULES | 可信身份、范围、即时失权、受保护 API；前端隐藏不替代服务鉴权 |
| 场景、项目、型号、线路 | SCENES、PROJECTS、VEHICLE-MODELS、DEVICE-MODELS、ROUTES、ROUTE-CLOSE | 树结构、完整字段、选项所属和线路关闭后的车辆结果 |
| 车辆与档案 | VEHICLE-CORE、INSURANCE、WARRANTY、ATTACHMENTS、VEHICLE-LIFECYCLE、VEHICLE-TRANSFER | 业务时间、历史保留、报废恢复、跨项目原子调拨和关联对象身份 |
| 设备 | DEVICE-CORE、DEVICE-INSTALL、DEVICE-REPLACE、DEVICE-LIFECYCLE | 类型字段、唯一占用、装拆历史、替换原子性；安装状态与寿命状态分开 |
| 充电设施 | CHARGING-CORE、CHARGING-RELATIONS、CHARGING-TRANSFER | 桩/TBOX/臂的绑定规则不同；桩迁移不能误用车辆级联规则 |
| RTK | RTK | 核心账号和车辆关系保留，Q07 阈值尚待明确 |
| 仓库与物料 | WAREHOUSES、MATERIAL-CATEGORIES、MATERIALS、MATERIAL-IMPORT | 共享真源、导入原子性、原物料回补、差量扣减和并发无负库存 |
| 维保准备 | MAINTENANCE-CONFIG、DEMANDS、MAINTENANCE-PLANS、RECORDS | 模式、派生状态、同车占用、完工记录与历史 |
| 维保办理 | FLOW-READ、TODOS、FLOW-START、FLOW-ENTRY、WORK、FLOW-ACCEPTANCE、FLOW-RETURN、FLOW-CORRECTIONS、FLOW-DELETE | E06–E12/E15 等按行为验证；同 key 异请求拒绝、库存差量、失败全体不变、删流程保留设备事实 |
| 寿命与核心总览 | LIFE-OVERVIEW、DASHBOARDS | 同口径风险和下钻；A01 默认 M4/C0，不能用未接入分析数据冒充核心结果 |
| 运营统计扩展 | ANALYTICS-SOURCE、ANALYTICS-VIEW | Q05、E25/E26；历史归属、分段依据、聚合/明细不重叠、迟到修订、范围权限 |
| 条件功能 | CONDITIONAL；取消态读取另归 FLOW-READ | Q08 的 A27 与 Q15 的取消命令等待决定；A26 核心及已取消态只读不删除 |
| 交互和视觉 | EXPERIENCE 与各业务片；G2/G3/G4 浏览器验收 | 主页面、表单、标签和宽/窄视口；截图、构建通过不能替代业务断言 |
| 工程与交付 | SCAFFOLD、ACCESS、G2、G3、HANDOVER、G4 | PostgreSQL 已确认，权威服务仍需核实；草案不能自动变成第二套主库，迁移/并发/恢复需要真实隔离环境证据 |

技术架构的改进在于职责边界：Kernel 管引用、阶段和证据合法性；Planner/Critic 决定业务
切片及语义充分性；开发者在唯一写入范围中实现；Web 只投影正式产物并使用受控审批。
没有新增与 canonical contract 竞争的任务结构，也没有把洛书业务规则硬编码进通用 Runtime。

## 本次运行暴露的输入管理限制

主项目补交文件后，已有 worker checkout 没有自动变成新 checkout；重新启动角色也不能
仅凭元数据中的 source ref 就推断文件已经更新。operator intent 进入事件账本同样不等于
当前 worker 已消费。本轮补救要求通过受控返工显式提供只读补充来源及哈希，不重写历史扫描或活动
worker 的冻结状态。输入完整性和实际消费需要分别取证，这一运行限制不属于产品覆盖率。

本次新项目初始配置还缺少 `workflow.admission_replan` 的启用项：人工拒绝后生成了
`orchestrator.replan_requested`，但没有下游规划触发。已在该项目唯一配置 `zf.yaml` 中
开启既有能力并指定 `prd.scan.completed`。只读恢复规划器据已有拒绝事件产出一个续跑请求；
由正常 Runtime 恢复，不手工补写成功事件或改历史快照。冷启动检查的 config_valid 通过，
但空白产品项目缺 README/src/tests，整体冷启动分数为 3/5；未用占位产品代码掩盖这一事实。

## 最终评估与证据限度

182 的七项改进已经形成实际调用链、回归证据及正式重规划案例，可以关闭本批 backlog。
新方案在已明确范围内保留了来源、业务责任、条件和验收路线，未发现遗失的旧业务条款；
54 个切片的依赖与唯一写入范围通过检查，关键事务边界和阶段门经过实际返工复核。
这支持进入人工 Plan 评审，不能认证所有产品功能已实现或全仓测试全绿。

Q01–Q15 仍是实施前或受影响阶段的决策输入。LS-CONDITIONAL 负责默认关闭及决策边界；
Q08/Q15 若获启用，应再受控增加相应实现切片，不能把此任务当作两项扩展已经实现。
32 波次保守地保持前置与两个 writer 的约束，是否需要缩短关键路径要基于实际实施耗时。
产品 TDD 的 RED/GREEN、真实 PostgreSQL 并发与恢复、迁移、接口和最终浏览器业务断言，
均需在产品实施后另行验收。

可复核证据位于本 worktree 的 `reports/plan182/`：`final-structure-review.json`、
`final-contract-compatibility.json`、`final-cross-artifact-review.json`、
`evt-3f6b832282a3-browser-validation.json`、`tdd-evidence.json` 及对应日志和截图。
这些是本地评审证据；正式 Plan/Package 保留在项目配置的运行状态目录中。
