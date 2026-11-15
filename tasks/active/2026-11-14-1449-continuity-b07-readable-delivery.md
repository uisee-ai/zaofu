> 状态: active

2026-11-15 本批：主预览轻量 envelope 接线；完整正文按需；固定 accepted_plan 章节不重建无关审阅体；
旧来源快照明确 observed；原生 SSE 重连后刷新审批或关闭已删除预览。补直接待决视图回归，
不依赖先打开工程上下文。保留统一材料目录与全入口新增约束旅程缺口，不归档整项。
证据：[本批报告](../../reports/continuity-sandbox-preview-20261115-review.md)。

2026-11-15 继续验证：修复审批刷新迟到覆盖/重开、Owner 决策后仍 pending，以及普通审批
busy id 不匹配；实际 Docker 浏览器先复现后重验。附件下载与 input-review 现在复用
轻量来源绑定，不重建无关完整 Plan；章节读取保持同版校验。主 preview 整包与 SSE
全旅程仍未关闭。见[继续验证报告](../../reports/continuity-validation-20261115-review.md)。

# ZF-SPEC-001 预览与分层阅读

用户于2026-11-14明确授权基于 spec 在新 worktree 实施；覆盖 F14/F15/F16。
基线：dev `0625d001`；spec SHA256：`f5a033210875e90fe059236974ca0c4ff2737b7b7fbbf6b7c62f6238bce8c52a`。
来源：`docs/spec/zaofu-continuity-and-handoff.spec.md`；207–209 是设计背景，非目标不自动扩大。

## 验收

- Kanban/Channel 预览绑定同版输入；慢详情不阻塞其他内容；材料分页和完整原文、未知/删除/无权状态区分；浏览器阅读不计 Agent 恢复。 -> verify: 真实 caller + 定向回归和异常负对照，逐项留原始命令/退出码；已有能力标记已验证保留，不伪称新实现。
- 影响面验证 -> verify: 按 AGENTS.md 执行后端/共享合同/mock E2E 或前端构建与 Docker 浏览器对应层，不用截图替代业务断言。
- 完成对账 -> verify: 记录代码位置、测试证据、资格限制及未完成项；未验收不标 done。

## 执行记录

本轮：Plan 首批分页渐进渲染、底部加载与手动降级、目录定位；pulse 提供版本差异与任务采用未知。

验证：Docker Chromium 9 个浏览器业务断言及 build/typecheck/unit/bundle。

未关闭：仍需全入口约束/版本旅程、SSE 重连/删除语义、后端按章节取文与统一人类材料目录。

对账报告：[207–209 / Spec 覆盖](../../reports/continuity-handoff-20261114-review.md)。
实现入口：[实现记录](../../docs/impl/continuity-handoff-spec-001.md)。
首批实现 commit：`2313bf61 feat: harden task continuity and preserve handoff evidence`；未 merge / push / 部署。

## 2026-11-15：剩余规格与真实验证

承接用户本轮授权的 [ZF-SPEC-002](../../docs/spec/zaofu-continuity-remaining-qualification.spec.md) R05；原验收与状态 active 保留。

实际 API + Docker Chromium 的 Plan 内容与人工审阅 16 场景通过，保留 WebM/trace/截图。修正现有镜像 executablePath 和实际 UID 的测试入口；服务端章节取文、材料目录、全入口新增约束与 SSE 重连删除语义仍未关闭。

实现与验证摘要见 [实现记录](../../docs/impl/continuity-handoff-spec-001.md)，本地完整 [缺口与对账报告](../../reports/continuity-qualification-20261115-review.md)。不把局部真实资格当整项完成。

## 2026-11-15：R05 来源按章读取增量

- 已实施：绑定项目/Plan/来源摘要的服务端目录与正文分片；缓存损坏重建；来源变更、缺失、越界显式拒绝。
- 已实施：来源 Markdown 阅读、目录分页/全文搜索、章节书签、长章分片及完整原文入口；迟到响应隔离。
- 验证：后端影响闭包 251 passed；原有 Docker 浏览器 16 场景、新增 4 场景；build/typecheck/unit、sentinel 53、Flow mock 10 通过。最终记录见 [本批验收报告](../../reports/continuity-reading-20261115-review.md)。
- 尚未关闭：主 Plan 首次整包和 accepted_plan 兼容路径；统一人类材料目录；全入口约束旅程与 SSE 重连/删除语义。冷索引仍需全文件摘要校验，不能宣称全链路无全文 IO。
- 当前仅完成 R05 子集，保持 active，不将 207–209 或 ZF-SPEC-002 整体标为完成。

## 2026-11-15：按成功条件收口

用户已批准修正后的实施顺序；本任务保持 active，逐条验收以 [剩余关闭清单](../../docs/impl/continuity-handoff-spec-001.md#2026-11-15按成功条件推进的剩余关闭清单) 为索引。只关闭有真实调用方和适用证据的条款，其他剩余项不因本批提交自动完成。
