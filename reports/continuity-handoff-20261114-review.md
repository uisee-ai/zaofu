# 207–209 与 ZF-SPEC-001 实施对账

> 状态：首批实现完成验证，完整 Spec 仍在实施；不得作为全部完成或部署证明。

首批实现 commit：`2313bf61 feat: harden task continuity and preserve handoff evidence`。未合并、未推送、未部署；临时浏览器 API 已停止。

## 1. 基线与统计口径

- 来源：207 §30 的 8 个 P0/P1 包，208 的增量验收，209 §9–10；产品分母为 Spec F01–F18。
- 分支：`wip/codex-20261114-continuity-handoff`；基线：dev `0625d001cca3ca270bea0016c40ce729927ae130`。
- Worktree：`/home/min/workspace/public/zaofu-worktrees/continuity-handoff-20261114`。
- Spec r1 SHA256：`f5a033210875e90fe059236974ca0c4ff2737b7b7fbbf6b7c62f6238bce8c52a`；原文件保留，批准依据为本会话明确实施指令。
- 本轮 13 个有真实 caller 的修复/增强已实现；对应 [实现记录](../docs/impl/continuity-handoff-spec-001.md)。测试修正和已有能力回归不另算新增功能。
- 18/18 功能已对账。**F08 的关键确定性验收已通过（既有能力保留），其余 17 项部分覆盖；18 项的完整产品资格关闭数为 0。** 不能把 18 项“有代码/有测试”写成 100% 完成。
- 设计 8 个包：7 个部分落实，P1-D 的 Datal 实际产品验收尚未执行，完整关闭 0/8。207/208 的研究、非目标和条件候选不混入分母，不分别按文档篇幅计算百分比。

这些数字区分“首批修复完成”和“整份规格完成”。没有将待验收项改成 optional，也没有把未通过项标 done。

## 2. Spec 逐项覆盖

| ID | 本轮变化 / 已有能力 | 本轮核查入口 | 状态与仍缺的证明 |
| --- | --- | --- | --- |
| F01 运行概况 | 新增 checkout / last startup 版本差异；task adoption 明示 unverified；当前身份进展 | `test_runtime_version_projection`、`test_run_manager_progress_identity`、dashboard 浏览器 | 部分：缺每个活跃 worker/任务实际采用版本的生产回执及统一多页面核对 |
| F02 任务输入 | planner readiness 方法补责任、共享约束、依赖、别名及禁止无界尾项；保留 input partitions | `test_gap_package_input_partitions`、`test_plan_package_requirement_continuity`、skill 测试 | 部分：未对 P20Q 同类真实大输入做独立语义反例验收；活跃输入修订的完整交接尚未闭合 |
| F03 恢复出口 | 恢复说明明确原授权不受撤销、resume 当前交付；分页结束回工作步骤 | `test_input_recovery_exit`、required resume / snapshot / capability | 部分：机械分页/身份已验，尚缺多次真实 compaction 后形成下一业务成果的受控对照 |
| F04 交接材料 | 修复检查点捕获实际 Task workspace，旧 role fallback 被拒；保留 StatePacket/work record | `test_checkpoint_task_pipeline_workspace`、maintenance / work record dispatch | 部分：还没有通用的保存→交付→接手确认与 pending 接收消费闭环 |
| F05 会话/Provider | 接管前确认旧 worker 退出；保留 dirty/untracked 与现有 session 资格 | `test_task_stage_writer_fence`、transport binding / checkpoint | 部分：Codex↔Claude Code 便携事实接力、目标权限与正文接收仍需端到端实现/资格；adapter is_alive 不是脱离父进程 writer 的完整隔离证明 |
| F06 持续推进 | idle sweep 消费已结束回合；同 attempt 续接；发送前 claim、未知效果留责任 | `test_completed_turn_continuation`、pending handoff / recovery storage / RM admission | 部分：模拟重启/漏通知通过；unknown 交付由 RM 核查，未实现全 Provider 自动效果核对；未做宿主进程全退出后的独立 watchdog 验收 |
| F07 有效进展 | 当前 identity 限定活动、输入观察、work record、验证和真实准入；待工具/审批/求助保持等待 | `test_admitted_result_progress`、progress identity、completed turn、work continuation | 部分：Codex 当前 attempt 路径已验；未知异步子工具、其他 Provider、长期无进展的语义诊断仍需完整验证 |
| F08 暂停/审批 | 保留用户暂停、run pause、维护与旧 Plan 批准边界；新接续不绕过 | paused reader restart / control-plane reload / plan approval identity / completed turn | 关键确定性验收通过（既有保留）：用户暂停保持、旧 package 批准无效、同 attempt 继续；跨 Provider 和真实宿主组合资格仍跟随 F05/F13 |
| F09 局部恢复 | 保留 reader result 先回放、局部 rework、固定目标、未知效果不盲重发 | reader result before recovery、recovery handoff effects、run continuation | 部分：未重放 Datal 缺 WebM、上传失败与外部效果已发生的完整恢复旅程 |
| F10 切分/并行 | 方法新增写域/输入/验收责任；新增实际 mock 子进程 8 Impl、8 Verify、8+8 与窄前沿区间 | `test_continuity_worker_overlap_mock_e2e`、eight slots / capacity / backpressure | 部分：模拟实际重叠已证；不证明真实 Provider 吞吐，也未独立审阅 Datal 全部 DAG 与尾部任务 |
| F11 环境复用 | setup marker 锁、重建前失效、准备后输入重核、atomic readiness；原任务工作区保留 | setup concurrency、worktree env、task workspace binding | 部分：没有工具/数据全身份、实际执行者持有者锁；bootstrap uv 与共享 node_modules 的完整互斥仍待实现 |
| F12 独立/同版证据 | 保留 attempt handoff 独立关闭、candidate 固定目标、类型化准入与 goal receipt | attempt handoff reducer、candidate verification authority / freeze receipts、call result admission | 部分：关键机械边界通过；未完成真实同源码补视频→归档→交付、不重入 Impl 的整链对照 |
| F13 部署/收尾 | 版本差异；task/role/stop 保守清理；未合入/ignored/外层成果保留，移除 force | stop preserves candidates、workspace cleanup preservation、writer fence | 部分：缺全部实际资源使用者图、durable cleanup phases、完整部署接管/回退资格 |
| F14 Plan Preview | 大文档渐进渲染、目录/原文；保留 owner-bound approve/replan 与 stale response 拒绝 | `plan-preview-content.spec.ts`、workflow preview review、plan approval identity | 部分：选定浏览器旅程通过；Kanban 与 Channel 的追加约束/来源变化全入口组合尚未综合验收 |
| F15 Dashboard | pulse 先可用；大正文不一次挂载；迟到 Plan 不替换新选择 | dashboard pulse-first、plan preview 浏览器、web metrics | 部分：当前选定场景通过；未完整覆盖所有 SSE 重连缺口/删除语义/跨项目多页面一致性 |
| F16 材料输出 | 完整原文保留、章节定位、尾页可达、加载剩余页、部分/缺失状态 | preview 9 个浏览器用例、artifact read 系列 | 部分：后端仍整篇读取；没有完整统一材料目录/附件/权限与替代关系的人类消费旅程 |
| F17 历史复用 | 新 work record 才推进 nudge；重复/旧身份不算新进展；保留 assistance 迟到拒绝 | work continuation、work record dispatch、worker assistance、attempt handoff | 部分：决策/SOP/子调用接收和失效条件的统一读取及完整历史旅程尚未闭合 |
| F18 完整对账 | 本报告；保留 goal coverage、product acceptance 中 target/缺口/资格检查 | goal coverage graph、product acceptance、goal completion receipt | 部分：Datal 正式数据/硬件、AutoDataLoop v1.4 条款→真实 producer/consumer→同版验收未重新执行 |

测试文件均位于 `tests/`；前端浏览器文件位于 `web/tests/`。文件名为导航，实际执行结果见 §4，不能以文件存在作通过证明。

## 3. 207–209 设计工作包

| 包 | 来源 | 已落实 | 未关闭部分 |
| --- | --- | --- | --- |
| P0-A | 207 §27/29–30；209 §9 | 启动回执的 API/UI 消费、缺失/损坏不回落旧成功，暂停回归 | 任务加载回执、实际部署切换、历史全部修复对账 |
| P0-B | 207 §29–30/32.2；208 §11；209 §7 | 持久 claim、idle 消费、unknown 明确等待及 RM 可见；账本准入回归 | H21 损坏原始账本合法恢复、宿主监督故障域、未知效果的自动核对 |
| P0-C | 207 §29；208 §4/6；209 §5–6 | planner 方法、有限恢复、分页出口、Task workspace 检查点 | 真实大输入覆盖反例与受控输入修订事务、后继实际工作 |
| P0-D | 207 §25.7/29；208 §6；209 §7 | 当前身份分层进展、结果优先回放、同 attempt 续接、长工具/等待保护 | 多 Provider 覆盖、异步子工具完整生命周期、真实长任务无监工效果 |
| P1-A | 207 §28/30；208 §12/15；209 §8/10 | 切分方法、8+8 模拟子进程重叠、窄前沿及回放不增发 | Datal DAG 独立语义评审、真实 Provider 并发资格 |
| P1-B | 207 §25.3/29；208 §13/15；209 §8 | setup 准备互斥及失效；固定源码/独立准入回归 | 执行期间环境互斥、工具/数据身份、真实补视频/上传补交链 |
| P1-C | 207 §28/30/32；208 §10/14/19；209 §8 | 检查点正确目录、旧 worker 退出确认、清理保留候选/ignored 成果 | 便携交接接收状态、跨 Provider 事务、全持有者资源收尾、部署采用 |
| P1-D | 207 §31；209 §9–11 | 只核查已有产品验收/coverage 机械基础 | Datal 业务验收未执行；用户暂停保持，未宣称 AutoDataLoop v1.4 已交付 |

207 的“平台替换”、208 的原生 transcript 转译/跨宿主等条件候选不自动实施；MySQL 迁移不解决必读集合问题。本次没有废弃 Task workspace 或让角色共用可写 checkout。

## 4. 验证、失败与证据边界

原始输出保留于本目录下 `continuity-handoff-20261114/`。日志/视频/trace 为本地证据，Git 仅收录文字报告及轻量清单。

- `runtime-final.log`：226 passed，21 个文件的恢复、attempt、清理、准入/审批、assistance 影响闭包。
- `writer-fence-red.log`：2 failed，旧 writer 仍活着/状态未知却继续绑定；`writer-fence-green.log`：修复及直接 caller 51 passed。
- `admission-progress-red.log`：1 failed，真实准入事件没有进入新 progress 消费；`admission-continuation-green.log`：82 passed。
- `acceptance-foundations.log`：246 passed，最新接续/RM、暂停、审批、产品 coverage 与 context 边界。
- `contracts-final.log`：180 passed，恢复准入/损坏存储/输入/工作记录/维护/环境与 Web metrics。
- `dispatch-callers.log`：350 passed，发送 primitive、角色生命周期、reader fanout、run admission、输出修复等直接调用方。
- `eight-lane.log` / `eight-lane.xml`：8 passed；5 组子进程区间（8 Impl、8 Verify、8+8、2 Impl、2 Verify）及 3 组既有 held-transport 调度案例。
- 前端：build（包含 TypeScript）、unit、bundle gate；Docker Playwright 最终 9/9。输出见 `web-build-final.log`、`gates-final.log`、`browser.log` 及 `browser/`。
- 浏览器复跑曾为 8 passed / 1 failed：超长正文被目录/分页/连续阅读重复解析，导航未在原 5 秒断言内出现。复用 AST 与单章节分页后，原断言保持，复跑为 9 passed；失败原件保存在 `browser-before-parser-reuse/` 和同名日志。共享解析新增原文/显式锚点/代码块伪标题单元断言。
- sentinel 初次 52 passed / 1 failed：新增逻辑使已有 oversized lifecycle 文件越界。已将接续上下文逻辑移到 sibling module，未放宽 cap；复跑 `sentinel-final.log` 为 53 passed。
- Flow smoke：10 passed。`dev-verify plan --base dev` 及自动非 pytest 层均执行；自动计划的宽 deterministic/serial tiers 由上述明确路径的影响闭包替代，没有运行全仓 pytest，亦无真实 Provider E2E。

测试命令、进程退出码与源码/证据摘要见 [验证清单](continuity-handoff-20261114-verification.json)；多轮有交叉用例，**上述数量不得简单相加成独立测试数**。writer fence、admission progress 和重复解析在本报告中附有原始失败及修复后证据；其他新回归说明行为，不把本轮全部工作宣称为严格先 RED 后实现。

浏览器使用已存在的 `mcp/playwright:latest`，`--pull=never --rm --network host`、UID 1001、可写临时 HOME、端口 8003。
镜像 ID `sha256:8342e03ef3571d09afffcd00558b8067e66489a54e7f3f0d96a71a327fd45fe2`，
Chromium `149.0.7827.22`，项目 Playwright `1.59.1`。镜像完整浏览器路径显式指定为
`/ms-playwright/chromium-1226/chrome-linux64/chrome`；未安装宿主浏览器，未篡改 revision。
保留 trace、WebM、尾部截图。初次 UID 不匹配、默认浏览器 revision 不匹配是测试入口失败，未标产品通过；修复入口后原断言完整执行。

## 5. 下一步实施前沿

1. F04/F05：复用现有 manifest/operation/checkpoint，闭合便携交接正文保存、目标权限、旧 writer 隔离、目标接收、崩溃重放；不复制 Provider 内部推理或凭据。
2. F02/F03：以真实大输入和条款反例验证 producer 的任务级选择；完成仅输入修订的受控后继事务；不得绕过 AC 审批。
3. F11/F13：环境就绪身份包含工具/数据及实际持有者，区分 installer 锁与执行使用锁；收尾保留可恢复的阶段与失败责任。
4. F06/F07/F09：补其他 Provider、异步外部工具与无人监工宿主故障注入，恢复后核查具体工作推进。
5. F14–F18：完善人类材料按需读取、跨入口/重连验收；在明确正式数据和硬件资格后做 Datal 业务对账。恢复已暂停 Datal 需另有明确指令。

所有未完成条款保留在本批 active 任务，不伪造 done；未 merge、未 push、未部署到当前 Dashboard。
