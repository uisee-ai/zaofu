# SDD 生命周期方法：独立前向评估快照

对应[批次验证记录](2026-10-31-sdd-lifecycle-validation.md)。以下是隔离评估者的原始报告、
窄范围澄清复核和完整合成输入/脚本/收据，供临时目录清理后复核。报告中的本地路径是
当时实际路径；评估没有访问在线 ls/FMS，不是产品部署或最终 Verify 通过证明。

每个小节记录原文件 SHA-256。除 probe-results.json 为避免冗长而等价压紧 JSON 外，
代码块内容保持原字节（UTF-8、LF、末尾换行）；压紧版本另外记录存档 SHA-256。
报告原文中的相对链接指向本快照的同名小节。初评的三项方法歧义由随后复核更新，
原始报告和执行事实均保留，不用后续文字澄清冒充重新执行。

复现时将 input/ 与 output/ 各节分别恢复到一个新的自有 /tmp/zf-* 目录。probe.py
根据自身路径寻找输入，不依赖仓库或外网；先核对 manifest，再执行 python -B
<新目录>/output/probe.py。它会重写自己的 probe-results.json，因此勿覆盖原始存档。
run-guide.md 保留当时绝对路径，迁移后须明确替换为新目录；新运行的时间、trace_id、
路径自然不同，比较行为断言而不是要求新收据字节相同。脚本退出 0 只代表采集完成。

## output/eval-results.md

原文件 SHA-256：`2e97440be55deaa266708d4b4a028f42370cf2999390e38117dcc260d09291c9`。

````markdown
# SDD 生命周期方法的隔离前向评估

## 结论与身份边界

任务 A：M-7 已授权 24→72 小时，无需重复询问该授权。P2 正文、API 附件、客户端、UI 与测试尚未形成一致合同，不能把旧 C-IDEMPOTENCY 通过结论移用为 P2 的 72 小时通过。C-READ 的规范审查结论可在不变语义范围内复用。应产出有界修订并进入原评审流程，保留 P1 和原任务历史。

任务 B：固定 CLI 样例不能收口为交付完成。实测确认同 key 不同请求未拒绝、取消不返还库存且没有 trace_id、原指南没有可复现初始化步骤；另有未授权的备注导出入口。已测正常列表、预留、同请求重放和数量边界有效，保留这些成果，不整体重做。

这些判断仅针对合成输入。不存在可识别的在线 Kernel candidate commit/run/generation，也没有正式结果准入、最终 Verify 或产品通过结论。本报告和辅助文件是离线方法评估产物。

## 输入校验、实际读取与方法使用

在阅读正文和执行应用前，先读取 input/manifest.json 并按 SHA-256 校验四个文件，全部一致：

| 原始输入 | SHA-256 |
| --- | --- |
| revision.md | `3677f318b993191bd97e175bafe2477447b05889c3ea5d7079204a762c720a45` |
| product.md | `018c969a5784adf769309b0c5c6873544fdf877f6feb5b58c15f83c647d07616` |
| README.md | `7cbc4f8ad51723b22e302aa40c28816be1adabe7b530f648cfce494c1a9bfd88` |
| app.py | `671989fbba5664040c4e17710f1b641187f23e8a010e26687d3fffdb31b0dddd` |

证据文件 probe-results.json 的 input_digests 保存机器可读原值。

实际阅读范围只有这四份正文、manifest 和以下指定方法；方法文件初读与定稿摘要记录在 [method-digests.json](method-digests.json)。定稿前已重读 done-contract 中改为中文的交付段，核对其指南归属、写入范围和自检/独立 Verify 界线，未改变本报告结论。该文件初读 SHA-256 为 `f3a8b2c8320deb122985dc707f4676064f12961a37b1fbd5e2067e5f8b244818`，定稿 SHA-256 为 `01f9d6523e55a6610be1e8abde7dbd33b4cf896f1ceb72cc24049a6ee4c46abe`。实际采用方式如下：

- `zf-plan-execution-readiness/references/spec-revision-review.md`：用于 A 的授权判断、传播链、选择性复用、增量与评审交接。
- `zf-plan-execution-readiness/references/delivery-validation.md`：用于指南准备、初始化、命令与验收绑定，以及指南创建和最终验证的先后责任。
- `zf-verify-rescan-replan/references/product-convergence.md`：用于 B 的逐条 mandatory 核对、反向审查未授权能力、问题证据及最小增量。
- `zf-harness-done-contract/SKILL.md`：采用其可重现证据、精确命令和自检/独立验证区分；其 Impl/Fix 提交、commit、hook/事件查询和完成命令不适用于本次评估角色，未执行。

未读仓库源码、测试、backlog、其他评估记录或父会话记录，未联网，未启动外部服务、Docker 或 provider，未进行任何运行时事件/Store 操作。除 Python 和 shell 自身执行所需环境外，没有把其他项目内容引入判断。写入全部位于本评估 output/；应用运行使用源码副本及隔离 JSON，结束后再次校验输入摘要一致。

## A. P2 规格修订评审

### 授权和修订边界

当前批准基线是样例中的 P1。M-7 的明确决定是“把重放窗口改为 72 小时，其他行为维持；修订计划并进入原有评审流程”。这是修改该产品约定及其必要消费者合同的充分授权，不是 P2 已批准或新实现已通过的证据。

本次能读到的是 revision.md 中的 P1/P2 内容和审查记录摘要，没有完整 Plan Package、旧用例正文、原始审查收据或新的候选绑定信息。下表对这些给定摘要作语义评审；正式交接时仍须绑定真实版本、refs/digests 和已有评审端口。P1 不改，P2 修订在新候选中完成。

### 具名修订影响与可复用结论

| 变更与依据 | 生产者/消费者和受影响项 | 旧结论及本次处理 | 剩余义务、责任与可继续范围 |
| --- | --- | --- | --- |
| A-01：M-7，R-BOOK 从 24h 改为 72h | P2 产品正文→API/24h 的新合同版本→T-PROVIDER 重放保留/过期/冲突→C-IDEMPOTENCY | 旧结论依赖 API/24h；P2 附件仍为 24h，与正文矛盾。必须重审，旧通过保留在 P1 | Planner/合同生产者统一新正文与附件，再交 Critic。提供者新增实现只能沿既有 gap/replan 路径；不得把 T-PROVIDER 从 done 直接改状态 |
| A-02：同一 M-7 的消费者传播 | API 新合同→T-CLIENT 的断网/结果未知后重试窗口→T-UI 的时限提示和结果反馈 | 旧客户端 24h、UI“超过 24 小时请重新提交”会在新的有效重放期内提前放弃重试/指导重新提交，不能复用为 72h 合格设计 | 客户端和 UI 生产者把窗口与文案改为同一 72h 约定；Critic 检查 24–72h 内未知结果恢复场景。可以继续设计与补充计划，尚不能宣告实现成立 |
| A-03：R-BOOK 新时间范围 | C-REPLAY-23H→C-IDEMPOTENCY→T-PROVIDER/T-CLIENT/T-UI 相关验收 | 23h 用例仍是 72h 内有效的回归场景；它没有证明新增的 24–72h 区间。旧 P1 运行收据不能成为新候选收据 | Verify 责任方补充 48h/接近 72h、到期边界、扩展期冲突和客户端/UI 行为。最终在真实新候选绑定后执行；本样例无此收据 |
| A-04：P2 列表章节错字 | R-READ、列表 API、T-READ、C-OWN-LIST、C-READ | 样例明确语义、API 合同和用例内容均未变；本次变更未引入 R-BOOK 窗口对“只列出本人”的依赖。可复用 P1 C-READ 的规范审查结论及理由，纯编辑不触发 T-READ 重做 | 正式引用确切 P1 审查版本与不变范围；这不是新候选的执行通过。若实际修订改变引用对象则重新评估，而不是仅凭 ID 相同复用 |
| A-05：工程师提出允许取消别人预约 | R-CANCEL 的“本人”权限边界、取消入口和其验收；样例未列出取消任务 ID | 与已批准 R-CANCEL 相反，且 M-7 要求其他行为维持，故未获授权。当前仅是提议，不能认定代码已违规，也不能纳入 P2 正文使其自动合法 | 由 Owner 对扩大取消权限单独决定；只阻止这项权限扩展。本人取消仍按 P1 继续，缺少取消实现/测试资料保持未验，不臆造 T-CANCEL 或历史通过 |
| A-06：后续报表时区待定 | 未来报表，不被当前预约/列表消费 | 当前没有依赖，不是 P2 重放修订或现有列表的阻塞 | 报表能力首次进入批准范围/被消费前，由该需求 Owner 明确；当前记录为未来决定，不强行填时区 |

C-READ 的复用理由是“语义、依赖目标和旧审查依据不变”，不是“任务 done”“标题相同”或“文件未变”。C-IDEMPOTENCY 的旧收据也没有作废为历史证据，只失去证明新 72h 目标已经通过的能力。

### 可交给 Planner 的 P2 修订正文草案

以下内容是待纳入新候选 Plan 人读正文的草案，尚未生效，不代表修改冻结 P1：

> 依据 Owner 消息 M-7，将 R-BOOK 的重放有效期从 24 小时扩展至 72 小时。同租户同用户、相同 key/相同内容在有效期内返回同一结果；相同 key/不同内容冲突。租户/用户边界及其他行为沿用 P1。API 重放合同须形成与 72 小时正文一致的新版本，保留 API/24h 作为 P1 历史引用；新附件实际 ref/digest 由本版装配生产者生成并绑定。客户端的断网及结果未知后的重试窗口与 UI 时限提示均采用同一 72 小时合同。R-READ 与列表合同仅修正文字；R-CANCEL 保持仅可取消本人预约。客服跨用户取消不包含在本次批准范围。报表时区不影响本版范围。
>
> 当前仍须完成重放合同和客户端/UI 的一致性修订，以及新窗口验证设计。P1 C-READ 的规范审查结论在上述不变边界内复用；P1 C-IDEMPOTENCY 重新审查，C-REPLAY-23H 保留为回归用例。所有旧任务与证据保持原身份。计划实施增量后，需在新集成候选上取得 current 验证证据，才可判断实现是否满足 72 小时目标。

### 有界增量、验收与后续交接

这里的 A-DELTA-* 是本报告中的建议项标识，不是新建的 Task 或运行态。

| 建议增量及责任 | step → verify | 最早可继续与关闭时点 |
| --- | --- | --- |
| A-DELTA-01：Planner/合同、客户端和 UI 设计生产者完成同版修订 | 统一 R-BOOK、API、重试、文案→verify：沿每个实际引用点核对 72h，并保留旧新来源/合同映射；没有遗留的有效 24h 约定 | 已有 M-7，可以立即编写并送原 Critic 流程；本版规格生效前关闭 |
| A-DELTA-02：原责任模块的实施增量，经既有 replan 产生 | 实现提供者 72h 和消费者对应行为→verify：可控时间下 23h 回归及 48h/临界点行为；扩展期同内容重放、不同内容冲突；24–72h 断网重试和 UI 提示；回归租户/本人边界 | 本样例没有代码，不能断言实现一定仍是 24h；先依据完成的合同核实实际差距，再下发最小实现。不得凭任务 done 跳过核实 |
| A-DELTA-03：Verify/评审负责方取得真实新候选证据 | 在受控候选上运行新/复用的适用用例→verify：目标身份、命令、结果、refs/digests 可追溯，失败与未跑完整保留 | 可以提前准备测试设计；执行和最终判定等待真实候选，不反过来阻塞指南/规格的创建 |

72h 临界点及到期后的具体响应需沿用并明确完整旧合同的规则。当前摘要未给出精确边界和到期后的结果处理，不能擅自添加“自动新建预约”等语义。扩展窗口内的冲突和重试是已有要求的传播；新增客服权限则是另一项产品决定，两者不能混在同一个修订里。

当前可继续：72h 必要修订与原评审流程、T-READ 的文字修正和既有语义复用、按原 R-CANCEL 的本人取消、验证设计和指南准备。当前不可结束：声称 P2 72h 已通过，或将客服权限建议作为已批准行为。正式交接应将影响摘要放入本版 Plan 人读正文，并将完整依据经现有受控评审工件/端口绑定；本 output/ 路径仅用于评估，不能被当作在线消费者已选读的 port。

## B. 离线 CLI 的产品收口

### 实际执行记录与已成立范围

完整记录：[probe-results.json](probe-results.json)；复现程序：[probe.py](probe.py)。环境为 Linux x86_64、Python 3.12.3，所有 CLI 调用是不同进程；`PYTHONDONTWRITEBYTECODE=1`，实际命令保留 `python app.py ...`，没有更改 CMD-LIST/CMD-RESERVE/CMD-CANCEL 的字符串后冒充其收据。

单次最终诊断执行 16 次 CLI。10 次覆盖的正常/拒绝断言成立；3 次暴露产品断言失败（其中取消后的列表是同一取消缺陷的持久化反证）；2 次是准备缺失的预期失败；1 次证明未授权入口可执行。程序退出 0 只表示采集完成，不是 16 项产品验收通过。团队“pytest 全绿”没有原始证据，未被采信；本评估未执行 pytest。

| 条款/设计义务 | 生产者与实际证据 | 结论边界 |
| --- | --- | --- |
| R-LIST | app.py:9–10；LIST-INITIAL 返回 bolt=5，随后跨进程读取库存变化 | 已测断言成立；取消后显示 3 是取消未返还的结果，不重复记为列表缺陷 |
| R-RESERVE | app.py:14–18、CLI 整数解析；RESERVE-VALID 扣减 5→3；数量 6/0/-1 拒绝，1.5/abc 由参数解析拒绝；RESERVE-EXACT 使库存 0，RESERVE-EMPTY 拒绝 | 当前批准 CLI 的这些数量场景成立；拒绝后 JSON 字节不变。没有扩展成 Python 内部 run() 任意类型调用的产品合同 |
| R-IDEM 的同请求部分 | app.py:12–13；IDEM-SAME 返回与首次完全相同的 qty 和 trace_id，JSON 字节不变 | 同请求跨进程重放成立；不同请求部分失败，见 F-IDEM-01 |
| R-CANCEL | app.py:20–21；CANCEL-EXISTING、LIST-AFTER-CANCEL | 未满足，见 F-CANCEL-01 |
| R-AUDIT | app.py:17 为预留生成 trace_id；取消输出只有 ok | 部分满足，见 F-AUDIT-01；批准的日志辅助支撑不需要另造独立产品故事，也没有依据要求外部日志服务 |
| STOCK_FILE 设计与交付初始化 | app.py:33–36 确实使用环境变量指定的 JSON；正常预留由下一次进程重新读到 | 装配后路径和普通持久化成立；原 README 缺少装配步骤，见 F-DELIVERY-01 |

### 有证据的 findings

所有 finding 的目标都是 app.py SHA-256 `671989fbba5664040c4e17710f1b641187f23e8a010e26687d3fffdb31b0dddd` 与同一 manifest 的 product.md/README.md。严重度为本次评审语言，不是新的 runtime verdict/gap_category。

| ID / 分类 / 严重度 | 来源、Task/入口及精确反例 | 影响与最小修复 |
| --- | --- | --- |
| F-IDEM-01 / contradicts / 高，mandatory 阻塞 | R-IDEM；T-RESERVE；先 `python app.py reserve --key k1 --qty 2`，再 `python app.py reserve --key k1 --qty 3`。IDEM-CONFLICT 退出 0、返回原 qty=2 和同一 trace_id。app.py:12–13 仅检查 key 存在，未检查请求内容 | 不同请求被错误地报告为成功重放；状态不变这一半成立。最小增量：在返回旧结果前比对当前请求与已存请求内容，冲突拒绝且不写状态；保留同请求结果和 trace_id |
| F-CANCEL-01 / contradicts / 高，mandatory 阻塞 | R-CANCEL；T-CANCEL；CMD-CANCEL 对已预留 qty=2 的 k1 返回 `{"ok": true}`，执行前后 JSON 相同，bolt 仍为 3，k1 仍是原记录。随后 CMD-LIST 仍返回 3；app.py:20–21 没有状态更新 | 取消的核心效果不存在且错误报成功。最小增量：取消现有预留、返还对应数量并持久化可观察的取消状态。规格未要求必须删除记录，不能为了修复强加某种内部表示或破坏重放证据 |
| F-AUDIT-01 / partial / 高，mandatory 阻塞 | R-AUDIT；T-AUDIT、T-CANCEL；RESERVE-VALID 有非空 trace_id；CANCEL-EXISTING 输出中没有该字段，app.py:21 固定返回 ok | 仅预留满足追踪要求。和取消增量一起增加取消 trace_id；保留日志辅助函数作为批准的必要工程支撑，无需新产品故事 |
| F-DELIVERY-01 / missing / 高，交付阻塞 | 批准设计明确存储目录/初始 JSON 由交付任务提供；T-DELIVERY。README 仅要求使用作者电脑库存文件。ENV-UNSET 的 CMD-LIST 退出 1，KeyError STOCK_FILE；ENV-MISSING-FILE 退出 1，FileNotFoundError | 独立执行者缺少配置、数据位置和初始化步骤，不能仅靠该 README 启动。最小增量是更新交付指南，给出版本绑定、Python、隔离目录、STOCK_FILE、种子、业务与失败断言、证据及定向清理；无需增加服务或应用自动初始化能力 |
| F-SCOPE-01 / unrequested / 中，发布范围需裁定 | product.md 明确不包含内部采购备注导出且无新增授权；app.py:22–23，入口 action 未作 choices 限制。`python app.py export-notes` 退出 0 并返回 `"synthetic-only"`，见 UNREQUESTED-EXPORT | 这是能从正式 CLI 到达的额外能力，不能解释成 R-AUDIT 必要支撑。交有权者处理：若保持批准范围，则通过受控增量去除/关闭该入口；若确需新增，则先走授权及规格修订。评估未自行删除，也未声称已泄露真实秘密 |

F-CANCEL-01 和 F-AUDIT-01 可以由同一补充实现项解决，避免两个等价方案重复等待。样例未给出的“取消后再次 reserve 同 key”“重复取消/取消不存在 key”的精确语义不得由评估者随意填成额外 mandatory；若修复会影响既有幂等承诺，应由 Planner/Critic 在批准范围内明确状态与重放关系。

### 最小补充工作与责任

不修改原 T-LIST/T-RESERVE/T-CANCEL/T-AUDIT/T-DELIVERY 的 done 历史，也不重编号。下列 B-DELTA-* 仅为提交既有 gap/replan 的建议：

| 增量/下一责任 | step → verify | 依赖与边界 |
| --- | --- | --- |
| B-DELTA-01，原预留模块 Impl | 修复 F-IDEM-01→verify：同请求返回同结果；同 key 不同 qty 明确拒绝且库存/预留文件不变 | 已批准 R-IDEM 足以授权必要修复；保留已成立的数量校验 |
| B-DELTA-02，取消与 audit Impl | 修复 F-CANCEL-01/F-AUDIT-01→verify：预留 2 后取消，库存由 3 回 5，取消状态持久化，取消响应有 trace_id；再次启动 CLI 核对文件 | 明确取消与既有幂等状态的交接，不强制某种存储形状，不另立日志产品故事 |
| B-DELTA-03，T-DELIVERY 后续生产者 | 将同版运行指南纳入受控交付→verify：全新自有目录只靠指南完成配置/初始化/CLI 旅程/失败检查/清理 | 本报告附有可用草案并已自检；真实项目应由生产者更新正式指南，评估不能替代其源码交付。指南可以先创建，行为失败如实保留 |
| B-DECISION-01，Owner/原评审流程 | 对 F-SCOPE-01 决定受控处置→verify：批准范围与最终可达命令一致；新增能力若获授权则有版本化规格与适用验收 | 仅待裁定额外导出；不阻止已授权的幂等、取消和指南修复 |
| B-VERIFY-01，独立 Verify/discovery | 固定修复后的实际候选，从空数据按正式指南重现→verify：R-LIST/RESERVE/IDEM/CANCEL/AUDIT 与交付义务都有 current 收据，额外入口处置有证据 | 需要真实候选及正式受控输入；不能用团队自报、旧样例收据或本指南作者自检替代 |

### 可复现指南、已验/未验与清理

完整指南：[run-guide.md](run-guide.md)。其中准备、初始化、六步主要 CLI 顺序及定向清理的原文 shell 块已实际自检，记录在 [manual-guide-results.json](manual-guide-results.json)。固定源码摘要、环境、STOCK_FILE、数据、命令和当前预期失败都有记载；没有添加 Docker、账号或未授权恢复能力。

已验：本 Linux/Python 3.12.3 环境的隔离初始化，实际 CLI 与 JSON 普通跨进程持久化，上表正常/拒绝/失败行为，额外导出可达性，指南装配和清理。原 README 作为独立执行者输入不足；补充指南由评估者编写后自检，不能称为另一位执行者的独立最终 Verify。

未验/不作推断：Python 3.11 和其他支持平台；新修复候选；原团队 pytest 的内容与结果；应用崩溃中途写入、文件损坏恢复、多进程同时写入等未明确批准的运行保证；A 的任何真实实现或时间推进；在线候选/任务/评审的接线和准入。这些不自动构成 missing/contradicts。既有 mandatory 缺陷仍须修复，未验也不能用来冲淡已经复现的错误。

最终诊断已清理自有 probe-work/；手工指南自检已清理唯一 manual-run.* 目录。没有常驻进程、端口、账号或外部服务需要停止。收据、脚本、指南及报告保留在 output/。输入摘要在执行后仍一致。

## 方法使用效果、歧义与局限

1. **实际有效的传播检查**：规格方法使审查从“正文改成 72h”推进到 API→提供者→客户端→UI→时间边界证据，确实找出了三类旧消费者约定和旧用例不足；同时允许 C-READ 的纯编辑复用，避免全产品重做。这个收益由 A-01 至 A-04 的具名映射支持，不是泛泛赞同。
2. **实际有效的双向收口**：正向从 R-CANCEL 找到无状态变更，反向从 app.py 的 action 分支找到 export-notes；批准日志支撑没有被误判成未授权能力。16 次实际调用表明 done 和团队 pytest 摘要不足以支持当前产品完成。
3. **方法存在的复用粒度歧义**：规格文档说证据目标相同且旧审查可追溯可复用，但没有直接示范“规范审查复用”和“新候选运行收据”的不同粒度。本样例要求同时复用 C-READ 并拒绝把 P1 收据当 P2 current。评估通过显式分层解决；若直接写“C-READ 通过”容易被下游误解为新候选执行通过。可补一个这种对照例，而非新 runtime 枚举。
4. **分类存在重叠**：取消分支存在但核心状态变更缺失，可称 missing，也可因假成功称 contradicts；R-IDEM 整条则是 partial，而不同请求场景是 contradicts。方法没有给唯一分级/主分类规则。本报告按断言选主分类，保留实际现象、来源和最小修复，避免分类争论取代证据；这未影响处置。
5. **输入缺口不是方法结论**：A 的精确到期边界、原始审查 artifact/版本、正式端口均未提供；B 的取消后重放细节也未提供。方法要求保留未知，但不能自行提供这些答案。本次没有为了填表编造审批、时区、Task ID、真实 candidate identity 或控制面 refs。真实运行前仍须现有生产者/运行时提供绑定。
6. **独立性与交付责任需要解释**：指南方法要求独立 Verify，又避免验证者成为交付实施者；本任务明确授权评估者输出可复现指南，因此创建了评估草案并自检。它解决下一人的复现问题，但没有消灭 T-DELIVERY 正式交付责任，也没有凭同一作者再跑一次产生“独立验证”。方法可再明确这种诊断草案与最终产物的界线。
7. **done contract 适用范围限制明显**：该文件只适用于 Impl/Fix 完成提交。本次只能借用证据质量要求，不能照抄提交/commit/hook 流程。若无 scope 判断而机械应用，会超出本评估的只读和无运行时操作边界；本次没有发生。
8. **本评估未证明方法接线成功**：这些文档帮助产出了可用影响表、可复现反例和有界补充工作，但无法仅凭四份参考文档证明既有 Plan/Preview/Verify 真能消费它们，也没有证明 runtime gate/最终 Judge 会作出相同判断。需要真实受控输入、端口和候选证据的后续验证；不建议据此增加关键词自动门禁或宣布方法覆盖率达标。

本次完成的是两项离线方法应用及其证据化交接。P2 尚待一致修订和原评审；CLI 样例存在明确 mandatory 缺口，不能交付通过。
````

## output/clarification-review.md

原文件 SHA-256：`4a41c6c21c36e61f700e1e525970d52a33c6366af05e6bfe46da001efde09e0f`。

````markdown
# 方法补充说明的窄范围复核

复核时间：2026-10-31T12:27:44.002118+00:00。

结论：三项已反馈的操作性歧义均得到文档层面的解决。新增说明没有要求扩大测试、重做未受影响行为或增加运行态。此次仅重读指定新增段及少量相邻上下文、计算方法文件摘要、核对原报告的 product.md 摘要；没有重新执行样例或修改输入、方法文件。

## 当前方法身份与阅读范围

| 方法文件（相对于指定 worktree） | 新增说明位置 | 本次 SHA-256 |
| --- | --- | --- |
| `skills/zf-plan-execution-readiness/references/spec-revision-review.md` | 34–36 行 | `9159690f1ff4b77366b32c73d07eb805e1ed35e3ce0629142e685718ddcb1a9d` |
| `skills/zf-verify-rescan-replan/references/product-convergence.md` | 24–25 行、47–48 行 | `fd52eefa23a9761b05bdf2600ffd05235cccdc421ee0b3bb9482b175b2bca30a` |

摘要绑定整份方法文件；语义复核只覆盖上述新增说明及检索显示的相邻上下文。没有以此声称重新评审了其他变更。

## 对原反馈的逐项判断

| 原歧义 | 新增说明的实际作用 | 判断与仍保留的边界 |
| --- | --- | --- |
| 规范结论复用与新候选执行收据的粒度（原报告第 3 项） | 明确要求分别标明规范审查与运行收据，使用未变列表权限作例；旧语义审查可复用，新候选仍需准入规则允许的 current 证据；是否重跑取决于目标和证据适用性 | 已解决。本例可以写 C-READ 规范结论可复用，同时保留 P2 执行证据义务；不再容易把“无需重审”误写为“新候选测试通过”。current 的机械准入条件仍交现有规则，本文没有给出也不应猜测 |
| missing/partial/contradicts 重叠（原报告第 4 项） | 要求按具名断言选主类，允许一个行为关联多个条款，明确不因类别重叠重复生成修复；保留实际反例、影响和最小修复优先 | 已解决到可执行程度。取消假成功可按具体断言归 contradicts，取消 trace_id 缺失可单列条款，但允许一个增量修复两者。它没有强制唯一分类，这属于保留语义判断，不再构成任务重复的必要原因 |
| 诊断指南作者自检与正式交付、独立 Verify 的责任（原报告第 6 项） | 明确受命诊断可提供指南草案；草案自检不替代交付 owner 的正式产物，也不形成另一位验证者的独立验收；最终候选按原合同取证 | 已解决。本次 run-guide.md 和自检收据仍是离线诊断产物，T-DELIVERY 的正式交付和最终独立验证责任完整保留。并非任何验证者都因此获得跨路径写入授权 |

以上判断更新原报告第 3、4、6 项对方法表述的评价，不改写原始执行事实。A 的 P2 合同不一致、B 的幂等/取消/追踪/指南与授权范围问题仍成立；新增方法说明本身不会修复产品或产生候选收据。

## product.md 摘要表核对

manifest 中的值与 input/product.md 实际 SHA-256 均为：

`018c969a5784adf769309b0c5c6873544fdf877f6feb5b58c15f83c647d07616`

本次读取 eval-results.md 时，表格已经是该正确值，不存在多出的 `b`，也没有保留“见下方校正”或重复校正说明。该笔误已在前次最终检查中修正；本次无需再次写入 eval-results.md。没有保留已知错误值。

## 本次仍不能证明的事项

- 本次是新增文字的可执行性复核，没有重跑或新增行为证据；既有样例执行收据保持原身份。
- 未证明真实 Planner/Critic/Verify 会消费这些新增段，或能将规范复用、主分类和指南责任正确提交到已有端口；运行时接线和准入仍需独立证据。
- 新说明没有提供缺失的实际 candidate 身份、原始审查 artifact、精确到期边界或取消后重放语义；这些输入局限继续保留，不能借澄清文字补造答案。
- 判断依赖和证据是否仍适用、按断言选主类与最小修复仍由评审者负责；本次没有建议新增关键词门禁或互斥类别枚举。
````

## input/revision.md

原文件 SHA-256：`3677f318b993191bd97e175bafe2477447b05889c3ea5d7079204a762c720a45`。

````markdown
# 样例：预约服务的规格修订
本文件是隔离方法评估的合成输入，标识不代表在线运行。

已批准 P1：R-BOOK 同租户同用户的预约请求，相同 key/相同内容在 24 小时内返回同一结果；不同内容冲突。R-READ 只列出本人的预约。R-CANCEL 用户可取消本人预约。
任务：T-PROVIDER 实现重放存储；T-CLIENT 处理断网后重试；T-UI 显示结果；T-READ 列表。全部标为 done。
P1 审查记录：C-IDEMPOTENCY 依据 API/24h 与用例 C-REPLAY-23H 通过；C-READ 依据 R-READ 及 C-OWN-LIST 通过。
P1 合同 API/24h：24 小时过期。客户端重试窗口 24 小时。UI 文案：超过 24 小时请重新提交。
P1 验证收据只覆盖原 P1，未提供可绑定新候选的运行收据。

Owner 消息 M-7：授权把重放窗口改为 72 小时，其他行为维持；请修订计划并进入原有评审流程。
候选 P2 产品正文已改为 72 小时；API 附件仍为 API/24h，客户端设计和 UI 文案未改，测试仍只有 C-REPLAY-23H。
候选 P2 还把列表章节错字修正，R-READ、API 列表合同和用例内容未变。
工程师建议追加：允许用户取消别人的预约以方便客服；未提供 Owner 授权。
后续报表时区待定，当前预约与列表均不消费报表接口。
请评审 P2，提供具名修订影响与可复用结论、当前可继续范围和后续交接，不修改 P1。
````

## input/product.md

原文件 SHA-256：`018c969a5784adf769309b0c5c6873544fdf877f6feb5b58c15f83c647d07616`。

````markdown
# 样例：离线物料预留 CLI
本样例是独立合成应用，全部文件的 snapshot SHA-256 见 manifest.json；没有真实 Kernel candidate identity。
范围：一个单机离线 CLI，Python 3.11+，无服务、外网、账号或 Docker 需求。
R-LIST 列出物料可用数量。R-RESERVE 预留正整数数量，库存不足拒绝且状态不变。
R-IDEM 同 key/相同请求返回同一结果；同 key/不同请求拒绝且状态不变。
R-CANCEL 可以取消现有预留并返还库存。R-AUDIT 预留与取消均返回 trace_id。
不包含内部采购备注导出；未提供增加该能力的授权。

批准设计：状态数据从环境变量 STOCK_FILE 指定的 JSON 加载并写回；存储目录和初始文件由交付任务提供可复现初始化步骤。
记录格式：{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"}。
批准日志辅助函数是 R-AUDIT 的必要工程支撑，无需独立产品故事。
任务 T-LIST/T-RESERVE/T-CANCEL/T-AUDIT/T-DELIVERY 全部标为 done，团队摘要声称 pytest 全绿；未提供独立最终验证收据。
命令目录：CMD-LIST python app.py list；CMD-RESERVE python app.py reserve --key k1 --qty 2；CMD-CANCEL python app.py cancel --key k1。
当前运行指南：README.md。允许在 output/ 下创建隔离数据与运行本地 Python CLI，禁止修改 input/、使用外部服务或触碰在线状态。
请依据规格和实际实现收口，输出有证据的 findings、最小补充工作，以及独立执行者可复现的运行指南和已验/未验范围。
````

## input/README.md

原文件 SHA-256：`7cbc4f8ad51723b22e302aa40c28816be1adabe7b530f648cfce494c1a9bfd88`。

````markdown
# 运行
执行 `python app.py list`。先用作者电脑上的库存文件。预留命令通过就算交付完成。
````

## input/app.py

原文件 SHA-256：`671989fbba5664040c4e17710f1b641187f23e8a010e26687d3fffdb31b0dddd`。

````python
import argparse
import json
import os
import uuid
from pathlib import Path


def run(action, data, key='', qty=1):
    if action == 'list':
        return data['stock']
    if action == 'reserve':
        if key in data['reservations']:
            return data['reservations'][key]
        if qty <= 0 or qty > data['stock']['bolt']:
            raise ValueError('invalid quantity')
        data['stock']['bolt'] -= qty
        row = {'qty': qty, 'trace_id': str(uuid.uuid4())}
        data['reservations'][key] = row
        return row
    if action == 'cancel':
        return {'ok': True}
    if action == 'export-notes':
        return data['internal_notes']
    raise ValueError('unknown action')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action')
    parser.add_argument('--key', default='')
    parser.add_argument('--qty', type=int, default=1)
    args = parser.parse_args()
    path = Path(os.environ['STOCK_FILE'])
    data = json.loads(path.read_text())
    result = run(args.action, data, args.key, args.qty)
    path.write_text(json.dumps(data))
    print(json.dumps(result))
````

## input/manifest.json

原文件 SHA-256：`45519fa7a7f110e3cdc4d2b978193fab04b0da9901a7667905a16ef5de18804b`。

````json
{
  "revision.md": "3677f318b993191bd97e175bafe2477447b05889c3ea5d7079204a762c720a45",
  "product.md": "018c969a5784adf769309b0c5c6873544fdf877f6feb5b58c15f83c647d07616",
  "README.md": "7cbc4f8ad51723b22e302aa40c28816be1adabe7b530f648cfce494c1a9bfd88",
  "app.py": "671989fbba5664040c4e17710f1b641187f23e8a010e26687d3fffdb31b0dddd"
}
````

## output/run-guide.md

原文件 SHA-256：`2edb9a309e8cc635867ab88fb0530fcbd58eb380daf9176706681c4e6c634c4c`。

````markdown
# 离线物料预留 CLI：固定样例的复现指南

本指南由隔离评估生成，供下一位执行者复现当前实现及其缺口。它没有替换 input/README.md，没有形成真实集成候选或最终交付收据。当前实现存在幂等冲突和取消缺陷；运行命令退出 0 不能当作全部验收通过。

## 目标、环境与边界

- 源码：本评估 input/app.py，SHA-256 `671989fbba5664040c4e17710f1b641187f23e8a010e26687d3fffdb31b0dddd`。全部原始输入摘要见 input/manifest.json。
- 入口：单机 CLI。命令目录沿用 CMD-LIST、CMD-RESERVE、CMD-CANCEL；补充反例见下表。这里没有 Kernel candidate commit/run/generation，后续真实交付需由运行时绑定。
- 前置：已安装的 Python 3.11+，本次实测 Linux x86_64、Python 3.12.3。源码仅依赖标准库，无需 pip、锁文件安装、服务、账号、迁移或网络。未验证其他 Python 版本和操作系统。缺少解释器时应报告能力缺失，不把外部安装步骤冒充已验。
- 配置：`STOCK_FILE` 是本次自有 JSON 文件的绝对路径，不含秘密。本指南只写 output/，不使用作者机器数据。
- 本 CLI 无常驻进程和端口。没有批准的升级、备份/灾难恢复要求；跨进程重新读取 JSON 是本次持久化验证范围。

## 可复现验证入口

在此固定评估包内执行：

```bash
python -B /tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe.py
```

`probe.py` 检查四份输入摘要，在同级 `probe-work/` 创建源码副本和独立数据。它依次执行准备失败反例、正常路径、幂等冲突、取消、数量边界及未授权能力的证据采集，完整保存 cwd、命令、配置路径、退出码、stdout/stderr、执行前后 JSON 与字节不变断言到同级 `probe-results.json`，最后仅清理自有 `probe-work/`。该脚本成功退出表示证据采集完成；应读取 `assertion_false` 和每条 `observed_assertion`，不能解释为产品通过。正常完成后可再次运行；不要并发运行同一个评估目录。若中断留下 `probe-work/`，先确认它属于本次诊断再执行本文末尾的定向清理。

## 手工启动、初始化与业务旅程

下面给出诊断脚本对应的实际 CLI 使用流程。只使用本评估包，不访问在线状态。初始化文件必须先存在；应用本身不创建它。

```bash
export EVAL_OUTPUT=/tmp/zf-sdd-lifecycle-eval-20261031-1209/output
export EVAL_RUN="$(mktemp -d "$EVAL_OUTPUT/manual-run.XXXXXX")"
export PYTHONDONTWRITEBYTECODE=1
export STOCK_FILE="$EVAL_RUN/data/stock.json"
cp /tmp/zf-sdd-lifecycle-eval-20261031-1209/input/app.py "$EVAL_RUN/app.py"
cd "$EVAL_RUN"
python -B - <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path
assert sys.version_info >= (3, 11), sys.version
assert hashlib.sha256(Path('app.py').read_bytes()).hexdigest() == '671989fbba5664040c4e17710f1b641187f23e8a010e26687d3fffdb31b0dddd'
p = Path(os.environ['STOCK_FILE'])
p.parent.mkdir()
p.write_text(json.dumps({'stock': {'bolt': 5}, 'reservations': {}, 'internal_notes': 'synthetic-only'}))
print(sys.version)
print(p)
PY
python app.py list
python app.py reserve --key k1 --qty 2
python app.py reserve --key k1 --qty 2
python app.py reserve --key k1 --qty 3
python app.py cancel --key k1
python app.py list
```

| 顺序、命令/证据标识 | 批准规格的预期 | 当前固定样例的已测结果 |
| --- | --- | --- |
| CMD-LIST / LIST-INITIAL | `{"bolt": 5}`，证明初始读取就绪 | 成立；只证明入口和初始读取 |
| CMD-RESERVE / RESERVE-VALID | qty=2，非空 trace_id，文件库存为 3 | 成立 |
| CMD-RESERVE / IDEM-SAME | 同请求重试返回首次完整结果与同一 trace_id，状态不变 | 成立，使用新的 CLI 进程重读状态 |
| 补充命令 `python app.py reserve --key k1 --qty 3` / IDEM-CONFLICT | 应拒绝，状态不变 | 未成立：退出 0 并返回 qty=2；状态确实不变 |
| CMD-CANCEL / CANCEL-EXISTING | 取消 k1，库存返还到 5，返回 trace_id | 未成立：仅返回 `{"ok": true}`；记录和库存不变，无 trace_id |
| CMD-LIST / LIST-AFTER-CANCEL | 应显示 `{"bolt": 5}` | 未成立：显示 `{"bolt": 3}` |

trace_id 每次新预留生成的具体字符串不固定，只比较同请求重放是否与首次相同。读取 `$STOCK_FILE` 可以观察持久状态；完整自动收据已保存执行前后内容，避免只检查 stdout。

边界验证使用独立初始文件，防止先前预留影响解释：数量 6、0、-1、1.5、abc 分别拒绝；数量 5 成功使库存变 0；随后另一个 key 预留 1 应拒绝。所有拒绝都检查状态字节不变。确切命令、退出码和数据在 probe-results.json 的 RESERVE-* 记录中。

`export-notes` 不属于批准的命令目录。诊断脚本执行它仅用于证明待裁定的新增能力存在，不是推荐的产品使用步骤。原样例只含合成备注 `synthetic-only`。

## 收据、清理与后续责任

自动验证收据是 `output/probe-results.json`，覆盖本指南的 Python 初始化和 CLI 顺序；手工 shell 装配命令另见 `output/manual-guide-results.json` 的自检记录。两者都是离线固定样例证据，不是新候选的正式 Verify 准入结果。

手工执行后先把需要保留的输出/数据证据保存到 output/，再运行：

```bash
cd "$EVAL_OUTPUT"
python -B - <<'PY'
import os
import shutil
from pathlib import Path
root = Path('/tmp/zf-sdd-lifecycle-eval-20261031-1209/output').resolve()
p = Path(os.environ['EVAL_RUN']).resolve()
assert p.parent == root and p.name.startswith('manual-run.'), p
shutil.rmtree(p)
print('removed owned path:', p)
PY
unset STOCK_FILE EVAL_RUN
```

如果自动诊断中断，只有确认其 `probe-work/` 是本次运行创建后才使用：

```bash
python -B - <<'PY'
import shutil
from pathlib import Path
root = Path('/tmp/zf-sdd-lifecycle-eval-20261031-1209/output').resolve()
p = root / 'probe-work'
assert p.parent == root and p.name == 'probe-work'
if p.exists():
    shutil.rmtree(p)
PY
```

T-DELIVERY 的后续增量生产者应把批准的实际步骤纳入项目交付源码及受控输入；独立 Verify 应在修复后的固定候选上从全新数据照指南重跑，补充当前失败断言。评估者编写并自检本补充指南，不等于已有第二位独立验证者验证过它。无需等待最终 Verify 通过才允许创建指南，最终产品通过则必须等待修复和有效收据。
````

## output/probe.py

原文件 SHA-256：`20b3915a7cb0afc30515628a6763748799b8b8ae12cdcc608faab2c994788aec`。

````python
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
INPUT = ROOT.parent / "input"
WORK = ROOT / "probe-work"
APP = WORK / "app.py"
INITIAL = {"stock": {"bolt": 5}, "reservations": {}, "internal_notes": "synthetic-only"}
WORK.mkdir(exist_ok=False)
APP.write_bytes((INPUT / "app.py").read_bytes())
manifest = json.loads((INPUT / "manifest.json").read_text())
results = {"scope": "synthetic fixture only; no Kernel candidate identity or runtime receipt", "started_at": datetime.now(timezone.utc).isoformat(), "python": sys.version, "platform": platform.platform(), "cwd": str(WORK), "app_sha256": hashlib.sha256(APP.read_bytes()).hexdigest(), "input_digests": {}, "cases": []}
for name, expected in manifest.items():
    actual = hashlib.sha256((INPUT / name).read_bytes()).hexdigest()
    assert actual == expected, name
    results["input_digests"][name] = actual

def seed(name):
    path = WORK / name / "stock.json"
    path.parent.mkdir(exist_ok=False)
    path.write_text(json.dumps(INITIAL))
    return path

def execute(identifier, args, path, expected, check, declared_id=None):
    cmd = ["python", "app.py", *args]
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("STOCK_FILE", None)
    if path is not None:
        env["STOCK_FILE"] = str(path)
    before = path.read_bytes() if path is not None and path.exists() else None
    proc = subprocess.run(cmd, cwd=WORK, env=env, text=True, capture_output=True, timeout=10)
    after = path.read_bytes() if path is not None and path.exists() else None
    try:
        output = json.loads(proc.stdout)
    except json.JSONDecodeError:
        output = None
    case = {"id": identifier, "declared_command_id": declared_id, "command": " ".join(cmd), "cwd": str(WORK), "STOCK_FILE": str(path) if path is not None else "UNSET", "exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "before": json.loads(before) if before is not None else None, "after": json.loads(after) if after is not None else None, "byte_unchanged": before == after, "expected": expected, "observed_assertion": check(proc, output, before, after)}
    results["cases"].append(case)
    return output

def nochange_error(proc, output, before, after):
    return proc.returncode != 0 and before == after

execute("ENV-UNSET", ["list"], None, "原 README 未设置 STOCK_FILE；识别其实际缺失配置失败", lambda p,o,b,a: p.returncode != 0 and "STOCK_FILE" in p.stderr, "CMD-LIST")
execute("ENV-MISSING-FILE", ["list"], WORK / "not-created" / "stock.json", "仅设置配置但不初始化文件仍失败", lambda p,o,b,a: p.returncode != 0 and "FileNotFoundError" in p.stderr, "CMD-LIST")
main = seed("main")
execute("LIST-INITIAL", ["list"], main, "R-LIST 返回 bolt=5", lambda p,o,b,a: p.returncode == 0 and o == {"bolt":5}, "CMD-LIST")
reserved = execute("RESERVE-VALID", ["reserve","--key","k1","--qty","2"], main, "R-RESERVE 库存 5→3；R-AUDIT 返回非空 trace_id", lambda p,o,b,a: p.returncode == 0 and o["qty"] == 2 and bool(o.get("trace_id")) and json.loads(a)["stock"]["bolt"] == 3, "CMD-RESERVE")
execute("IDEM-SAME", ["reserve","--key","k1","--qty","2"], main, "R-IDEM 同请求跨进程返回完全相同结果且状态字节不变", lambda p,o,b,a: p.returncode == 0 and o == reserved and b == a, "CMD-RESERVE")
execute("IDEM-CONFLICT", ["reserve","--key","k1","--qty","3"], main, "R-IDEM 同 key 不同 qty 拒绝且状态不变", nochange_error)
execute("CANCEL-EXISTING", ["cancel","--key","k1"], main, "R-CANCEL 取消现有预留并返还库存至 5；R-AUDIT 返回非空 trace_id", lambda p,o,b,a: p.returncode == 0 and json.loads(a)["stock"]["bolt"] == 5 and bool(o.get("trace_id")), "CMD-CANCEL")
execute("LIST-AFTER-CANCEL", ["list"], main, "R-CANCEL 跨进程重新读取得到已返还的库存 5", lambda p,o,b,a: p.returncode == 0 and o == {"bolt":5}, "CMD-LIST")
for case, qty in [("INSUFFICIENT", "6"), ("ZERO", "0"), ("NEGATIVE", "-1"), ("FRACTIONAL", "1.5"), ("NONINTEGER", "abc")]:
    execute("RESERVE-"+case, ["reserve","--key","k1","--qty",qty], seed(case.lower()), "R-RESERVE 非正整数/库存不足必须拒绝且状态字节不变", nochange_error)
exact = seed("exact")
execute("RESERVE-EXACT", ["reserve","--key","k1","--qty","5"], exact, "R-RESERVE 数量等于库存允许预留并持久化库存 0", lambda p,o,b,a: p.returncode == 0 and o["qty"] == 5 and json.loads(a)["stock"]["bolt"] == 0 and bool(o.get("trace_id")))
execute("RESERVE-EMPTY", ["reserve","--key","k2","--qty","1"], exact, "R-RESERVE 空库存拒绝且状态字节不变", nochange_error)
execute("UNREQUESTED-EXPORT", ["export-notes"], seed("export"), "证实未获授权的 export-notes 命令可执行并返回内部备注", lambda p,o,b,a: p.returncode == 0 and o == "synthetic-only")
for name, expected in manifest.items():
    assert hashlib.sha256((INPUT / name).read_bytes()).hexdigest() == expected, name
results["finished_at"] = datetime.now(timezone.utc).isoformat()
results["input_unchanged_after_run"] = True
results["cleanup"] = {"owned_path": str(WORK), "status": "pending"}
(ROOT / "probe-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
assert WORK.parent == ROOT and WORK.name == "probe-work"
shutil.rmtree(WORK)
results["cleanup"]["status"] = "removed; evidence and probe script retained"
(ROOT / "probe-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"case_count": len(results["cases"]), "assertion_false": [c["id"] for c in results["cases"] if not c["observed_assertion"]], "evidence": str(ROOT / "probe-results.json"), "python": sys.version.split()[0]}, ensure_ascii=False))
````

## output/method-digests.json

原文件 SHA-256：`d624f7aafb440e2a06f397de774768664460b5b28d3efbd709fbf26a7f5ec8fe`。

````json
{
  "method_root": "/home/min/workspace/zaofu-worktrees/sdd-lifecycle-20261031",
  "initial_read_sha256": {
    "skills/zf-plan-execution-readiness/references/spec-revision-review.md": "5ad6c73ab0aec675c9b8c760ecda59b3e239a90099f54268cfd095aade4572c7",
    "skills/zf-plan-execution-readiness/references/delivery-validation.md": "47371c9062f7fcf9b5953bc35e34e9cc1eec3da0c0f414f2580c5dc1df3e5397",
    "skills/zf-verify-rescan-replan/references/product-convergence.md": "cd8da34b306a5c96c72c592c670568d6299e767fe9e582c8707846586420cf35",
    "skills/zf-harness-done-contract/SKILL.md": "f3a8b2c8320deb122985dc707f4676064f12961a37b1fbd5e2067e5f8b244818"
  },
  "final_read_sha256": {
    "skills/zf-plan-execution-readiness/references/spec-revision-review.md": "5ad6c73ab0aec675c9b8c760ecda59b3e239a90099f54268cfd095aade4572c7",
    "skills/zf-plan-execution-readiness/references/delivery-validation.md": "47371c9062f7fcf9b5953bc35e34e9cc1eec3da0c0f414f2580c5dc1df3e5397",
    "skills/zf-verify-rescan-replan/references/product-convergence.md": "cd8da34b306a5c96c72c592c670568d6299e767fe9e582c8707846586420cf35",
    "skills/zf-harness-done-contract/SKILL.md": "01f9d6523e55a6610be1e8abde7dbd33b4cf896f1ceb72cc24049a6ee4c46abe"
  },
  "rechecked_at_utc": "2026-10-31T12:25:53.654406+00:00",
  "reread": "zf-harness-done-contract/SKILL.md Evidence Quality delivery paragraph now Chinese; retained scope, guide ownership, self-check versus independent Verify, and no extra Compose requirements; no evaluation outcome changed"
}
````

## output/manual-guide-results.json

原文件 SHA-256：`08692d17c6956df6a775c50ae95603dd65f2c9d3e384d9835aff450ad324e984`。

````json
{
  "scope": "Guide author self-check of exact manual setup/journey/cleanup blocks; not independent final Verify",
  "utc": "2026-10-31T12:21:32.202803+00:00",
  "guide_sha256": "2edb9a309e8cc635867ab88fb0530fcbd58eb380daf9176706681c4e6c634c4c",
  "script": "set -u\nexport EVAL_OUTPUT=/tmp/zf-sdd-lifecycle-eval-20261031-1209/output\nexport EVAL_RUN=\"$(mktemp -d \"$EVAL_OUTPUT/manual-run.XXXXXX\")\"\nexport PYTHONDONTWRITEBYTECODE=1\nexport STOCK_FILE=\"$EVAL_RUN/data/stock.json\"\ncp /tmp/zf-sdd-lifecycle-eval-20261031-1209/input/app.py \"$EVAL_RUN/app.py\"\ncd \"$EVAL_RUN\"\npython -B - <<'PY'\nimport hashlib\nimport json\nimport os\nimport sys\nfrom pathlib import Path\nassert sys.version_info >= (3, 11), sys.version\nassert hashlib.sha256(Path('app.py').read_bytes()).hexdigest() == '671989fbba5664040c4e17710f1b641187f23e8a010e26687d3fffdb31b0dddd'\np = Path(os.environ['STOCK_FILE'])\np.parent.mkdir()\np.write_text(json.dumps({'stock': {'bolt': 5}, 'reservations': {}, 'internal_notes': 'synthetic-only'}))\nprint(sys.version)\nprint(p)\nPY\npython app.py list\npython app.py reserve --key k1 --qty 2\npython app.py reserve --key k1 --qty 2\npython app.py reserve --key k1 --qty 3\npython app.py cancel --key k1\npython app.py list\ncd \"$EVAL_OUTPUT\"\npython -B - <<'PY'\nimport os\nimport shutil\nfrom pathlib import Path\nroot = Path('/tmp/zf-sdd-lifecycle-eval-20261031-1209/output').resolve()\np = Path(os.environ['EVAL_RUN']).resolve()\nassert p.parent == root and p.name.startswith('manual-run.'), p\nshutil.rmtree(p)\nprint('removed owned path:', p)\nPY\nunset STOCK_FILE EVAL_RUN\n",
  "exit_code": 0,
  "stdout": "3.12.3 (main, Aug 31 2026, 10:18:26) [GCC 13.3.0]\n/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/manual-run.9TXKNw/data/stock.json\n{\"bolt\": 5}\n{\"qty\": 2, \"trace_id\": \"07ed9582-af9a-43f5-a826-ef2589c0322c\"}\n{\"qty\": 2, \"trace_id\": \"07ed9582-af9a-43f5-a826-ef2589c0322c\"}\n{\"qty\": 2, \"trace_id\": \"07ed9582-af9a-43f5-a826-ef2589c0322c\"}\n{\"ok\": true}\n{\"bolt\": 3}\nremoved owned path: /tmp/zf-sdd-lifecycle-eval-20261031-1209/output/manual-run.9TXKNw\n",
  "stderr": "",
  "input_unchanged": true,
  "remaining_manual_paths": [],
  "probe_work_exists": false
}
````

## output/probe-results.json

原文件 SHA-256：`b735579a3855fffff513dfa7425887c1e41cc5c357f1667b9454a1a78e860356`。 压紧存档 SHA-256：`477783f9139a17870d94c4adc707982eff8c495e7c0c1a15b0fb1a891677d7df`。

````json
{"scope":"synthetic fixture only; no Kernel candidate identity or runtime receipt","started_at":"2026-10-31T12:21:23.722280+00:00","python":"3.12.3 (main, Aug 31 2026, 10:18:26) [GCC 13.3.0]","platform":"Linux-6.8.0-147-generic-x86_64-with-glibc2.39","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","app_sha256":"671989fbba5664040c4e17710f1b641187f23e8a010e26687d3fffdb31b0dddd","input_digests":{"revision.md":"3677f318b993191bd97e175bafe2477447b05889c3ea5d7079204a762c720a45","product.md":"018c969a5784adf769309b0c5c6873544fdf877f6feb5b58c15f83c647d07616","README.md":"7cbc4f8ad51723b22e302aa40c28816be1adabe7b530f648cfce494c1a9bfd88","app.py":"671989fbba5664040c4e17710f1b641187f23e8a010e26687d3fffdb31b0dddd"},"cases":[{"id":"ENV-UNSET","declared_command_id":"CMD-LIST","command":"python app.py list","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","STOCK_FILE":"UNSET","exit_code":1,"stdout":"","stderr":"Traceback (most recent call last):\n  File \"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/app.py\", line 33, in <module>\n    path = Path(os.environ['STOCK_FILE'])\n                ~~~~~~~~~~^^^^^^^^^^^^^^\n  File \"<frozen os>\", line 685, in __getitem__\nKeyError: 'STOCK_FILE'\n","before":null,"after":null,"byte_unchanged":true,"expected":"原 README 未设置 STOCK_FILE；识别其实际缺失配置失败","observed_assertion":true},{"id":"ENV-MISSING-FILE","declared_command_id":"CMD-LIST","command":"python app.py list","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","STOCK_FILE":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/not-created/stock.json","exit_code":1,"stdout":"","stderr":"Traceback (most recent call last):\n  File \"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/app.py\", line 34, in <module>\n    data = json.loads(path.read_text())\n                      ^^^^^^^^^^^^^^^^\n  File \"/usr/lib/python3.12/pathlib.py\", line 1029, in read_text\n    with self.open(mode='r', encoding=encoding, errors=errors) as f:\n         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File \"/usr/lib/python3.12/pathlib.py\", line 1015, in open\n    return io.open(self, mode, buffering, encoding, errors, newline)\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nFileNotFoundError: [Errno 2] No such file or directory: '/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/not-created/stock.json'\n","before":null,"after":null,"byte_unchanged":true,"expected":"仅设置配置但不初始化文件仍失败","observed_assertion":true},{"id":"LIST-INITIAL","declared_command_id":"CMD-LIST","command":"python app.py list","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","STOCK_FILE":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/main/stock.json","exit_code":0,"stdout":"{\"bolt\": 5}\n","stderr":"","before":{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"},"after":{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"},"byte_unchanged":true,"expected":"R-LIST 返回 bolt=5","observed_assertion":true},{"id":"RESERVE-VALID","declared_command_id":"CMD-RESERVE","command":"python app.py reserve --key k1 --qty 2","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","STOCK_FILE":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/main/stock.json","exit_code":0,"stdout":"{\"qty\": 2, \"trace_id\": \"2289e27a-07f4-44fa-b40e-c1d5ace74ce9\"}\n","stderr":"","before":{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"},"after":{"stock":{"bolt":3},"reservations":{"k1":{"qty":2,"trace_id":"2289e27a-07f4-44fa-b40e-c1d5ace74ce9"}},"internal_notes":"synthetic-only"},"byte_unchanged":false,"expected":"R-RESERVE 库存 5→3；R-AUDIT 返回非空 trace_id","observed_assertion":true},{"id":"IDEM-SAME","declared_command_id":"CMD-RESERVE","command":"python app.py reserve --key k1 --qty 2","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","STOCK_FILE":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/main/stock.json","exit_code":0,"stdout":"{\"qty\": 2, \"trace_id\": \"2289e27a-07f4-44fa-b40e-c1d5ace74ce9\"}\n","stderr":"","before":{"stock":{"bolt":3},"reservations":{"k1":{"qty":2,"trace_id":"2289e27a-07f4-44fa-b40e-c1d5ace74ce9"}},"internal_notes":"synthetic-only"},"after":{"stock":{"bolt":3},"reservations":{"k1":{"qty":2,"trace_id":"2289e27a-07f4-44fa-b40e-c1d5ace74ce9"}},"internal_notes":"synthetic-only"},"byte_unchanged":true,"expected":"R-IDEM 同请求跨进程返回完全相同结果且状态字节不变","observed_assertion":true},{"id":"IDEM-CONFLICT","declared_command_id":null,"command":"python app.py reserve --key k1 --qty 3","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","STOCK_FILE":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/main/stock.json","exit_code":0,"stdout":"{\"qty\": 2, \"trace_id\": \"2289e27a-07f4-44fa-b40e-c1d5ace74ce9\"}\n","stderr":"","before":{"stock":{"bolt":3},"reservations":{"k1":{"qty":2,"trace_id":"2289e27a-07f4-44fa-b40e-c1d5ace74ce9"}},"internal_notes":"synthetic-only"},"after":{"stock":{"bolt":3},"reservations":{"k1":{"qty":2,"trace_id":"2289e27a-07f4-44fa-b40e-c1d5ace74ce9"}},"internal_notes":"synthetic-only"},"byte_unchanged":true,"expected":"R-IDEM 同 key 不同 qty 拒绝且状态不变","observed_assertion":false},{"id":"CANCEL-EXISTING","declared_command_id":"CMD-CANCEL","command":"python app.py cancel --key k1","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","STOCK_FILE":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/main/stock.json","exit_code":0,"stdout":"{\"ok\": true}\n","stderr":"","before":{"stock":{"bolt":3},"reservations":{"k1":{"qty":2,"trace_id":"2289e27a-07f4-44fa-b40e-c1d5ace74ce9"}},"internal_notes":"synthetic-only"},"after":{"stock":{"bolt":3},"reservations":{"k1":{"qty":2,"trace_id":"2289e27a-07f4-44fa-b40e-c1d5ace74ce9"}},"internal_notes":"synthetic-only"},"byte_unchanged":true,"expected":"R-CANCEL 取消现有预留并返还库存至 5；R-AUDIT 返回非空 trace_id","observed_assertion":false},{"id":"LIST-AFTER-CANCEL","declared_command_id":"CMD-LIST","command":"python app.py list","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","STOCK_FILE":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/main/stock.json","exit_code":0,"stdout":"{\"bolt\": 3}\n","stderr":"","before":{"stock":{"bolt":3},"reservations":{"k1":{"qty":2,"trace_id":"2289e27a-07f4-44fa-b40e-c1d5ace74ce9"}},"internal_notes":"synthetic-only"},"after":{"stock":{"bolt":3},"reservations":{"k1":{"qty":2,"trace_id":"2289e27a-07f4-44fa-b40e-c1d5ace74ce9"}},"internal_notes":"synthetic-only"},"byte_unchanged":true,"expected":"R-CANCEL 跨进程重新读取得到已返还的库存 5","observed_assertion":false},{"id":"RESERVE-INSUFFICIENT","declared_command_id":null,"command":"python app.py reserve --key k1 --qty 6","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","STOCK_FILE":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/insufficient/stock.json","exit_code":1,"stdout":"","stderr":"Traceback (most recent call last):\n  File \"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/app.py\", line 35, in <module>\n    result = run(args.action, data, args.key, args.qty)\n             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File \"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/app.py\", line 15, in run\n    raise ValueError('invalid quantity')\nValueError: invalid quantity\n","before":{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"},"after":{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"},"byte_unchanged":true,"expected":"R-RESERVE 非正整数/库存不足必须拒绝且状态字节不变","observed_assertion":true},{"id":"RESERVE-ZERO","declared_command_id":null,"command":"python app.py reserve --key k1 --qty 0","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","STOCK_FILE":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/zero/stock.json","exit_code":1,"stdout":"","stderr":"Traceback (most recent call last):\n  File \"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/app.py\", line 35, in <module>\n    result = run(args.action, data, args.key, args.qty)\n             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File \"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/app.py\", line 15, in run\n    raise ValueError('invalid quantity')\nValueError: invalid quantity\n","before":{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"},"after":{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"},"byte_unchanged":true,"expected":"R-RESERVE 非正整数/库存不足必须拒绝且状态字节不变","observed_assertion":true},{"id":"RESERVE-NEGATIVE","declared_command_id":null,"command":"python app.py reserve --key k1 --qty -1","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","STOCK_FILE":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/negative/stock.json","exit_code":1,"stdout":"","stderr":"Traceback (most recent call last):\n  File \"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/app.py\", line 35, in <module>\n    result = run(args.action, data, args.key, args.qty)\n             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File \"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/app.py\", line 15, in run\n    raise ValueError('invalid quantity')\nValueError: invalid quantity\n","before":{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"},"after":{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"},"byte_unchanged":true,"expected":"R-RESERVE 非正整数/库存不足必须拒绝且状态字节不变","observed_assertion":true},{"id":"RESERVE-FRACTIONAL","declared_command_id":null,"command":"python app.py reserve --key k1 --qty 1.5","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","STOCK_FILE":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/fractional/stock.json","exit_code":2,"stdout":"","stderr":"usage: app.py [-h] [--key KEY] [--qty QTY] action\napp.py: error: argument --qty: invalid int value: '1.5'\n","before":{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"},"after":{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"},"byte_unchanged":true,"expected":"R-RESERVE 非正整数/库存不足必须拒绝且状态字节不变","observed_assertion":true},{"id":"RESERVE-NONINTEGER","declared_command_id":null,"command":"python app.py reserve --key k1 --qty abc","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","STOCK_FILE":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/noninteger/stock.json","exit_code":2,"stdout":"","stderr":"usage: app.py [-h] [--key KEY] [--qty QTY] action\napp.py: error: argument --qty: invalid int value: 'abc'\n","before":{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"},"after":{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"},"byte_unchanged":true,"expected":"R-RESERVE 非正整数/库存不足必须拒绝且状态字节不变","observed_assertion":true},{"id":"RESERVE-EXACT","declared_command_id":null,"command":"python app.py reserve --key k1 --qty 5","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","STOCK_FILE":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/exact/stock.json","exit_code":0,"stdout":"{\"qty\": 5, \"trace_id\": \"539789c4-b610-4102-8db8-a01dbe0fa0df\"}\n","stderr":"","before":{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"},"after":{"stock":{"bolt":0},"reservations":{"k1":{"qty":5,"trace_id":"539789c4-b610-4102-8db8-a01dbe0fa0df"}},"internal_notes":"synthetic-only"},"byte_unchanged":false,"expected":"R-RESERVE 数量等于库存允许预留并持久化库存 0","observed_assertion":true},{"id":"RESERVE-EMPTY","declared_command_id":null,"command":"python app.py reserve --key k2 --qty 1","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","STOCK_FILE":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/exact/stock.json","exit_code":1,"stdout":"","stderr":"Traceback (most recent call last):\n  File \"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/app.py\", line 35, in <module>\n    result = run(args.action, data, args.key, args.qty)\n             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n  File \"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/app.py\", line 15, in run\n    raise ValueError('invalid quantity')\nValueError: invalid quantity\n","before":{"stock":{"bolt":0},"reservations":{"k1":{"qty":5,"trace_id":"539789c4-b610-4102-8db8-a01dbe0fa0df"}},"internal_notes":"synthetic-only"},"after":{"stock":{"bolt":0},"reservations":{"k1":{"qty":5,"trace_id":"539789c4-b610-4102-8db8-a01dbe0fa0df"}},"internal_notes":"synthetic-only"},"byte_unchanged":true,"expected":"R-RESERVE 空库存拒绝且状态字节不变","observed_assertion":true},{"id":"UNREQUESTED-EXPORT","declared_command_id":null,"command":"python app.py export-notes","cwd":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","STOCK_FILE":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work/export/stock.json","exit_code":0,"stdout":"\"synthetic-only\"\n","stderr":"","before":{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"},"after":{"stock":{"bolt":5},"reservations":{},"internal_notes":"synthetic-only"},"byte_unchanged":true,"expected":"证实未获授权的 export-notes 命令可执行并返回内部备注","observed_assertion":true}],"finished_at":"2026-10-31T12:21:24.952022+00:00","input_unchanged_after_run":true,"cleanup":{"owned_path":"/tmp/zf-sdd-lifecycle-eval-20261031-1209/output/probe-work","status":"removed; evidence and probe script retained"}}
````
