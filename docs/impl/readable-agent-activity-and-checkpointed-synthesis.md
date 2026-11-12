# 可读 Agent 活动与分段综合实施记录

关联设计：[202 — 可读 Agent 活动与可恢复的分段 PRD 综合](../design/202-readable-agent-activity-and-checkpointed-synthesis.md)。

> 状态：实现已接入并通过确定性、浏览器与真实正式发布验收；独立 PRD 内容评估单独记录。

## 入口与状态所有权

- `channel.synthesis.request` 为新 libre_discuss 请求绑定 `checkpointed.v1`，冻结材料并调用现有 Channel Adapter；旧模式和历史请求沿用原协议。
- Adapter 顺序执行文稿、需求映射、决定、开发交接和最终复核五部分。每部分由 Agent 判断和撰写，Runtime 仅验证分组字段、完整交付、绑定和活动状态，再用 sanctioned sidecar writer 原子保存。
- 完成凭据使用现有 `agent.session.part.delta`；EventLog 拥有顺序、取消和恢复事实，不创建另一套 canonical Store。
- 完整结果仍进入 `emit_structured_reply_events`、正文展开、来源消费、质量合同、PRD 版本、独立评估和 Owner 门禁。检查点不能创建 Task、启动 Workflow 或代替批准。
- 沿用原材料重试只接受相同冻结输入、来源/题卡摘要、角色/权限/技能/方法合同和明确重试谱系的已保存业务部分。跨请求最终来源声明重新生成；篡改、跨群/线程、取消后的迟到凭据以及旧 generation 不能作为新交付。

## 来源和可读性

历史生成的 synthesis_attempt 保留可核验审计引用，不再作为新请求的全文需求重复输入。原始 Owner 输入、纠正、异议、研究与选择的文件保留；明确回复/引用的旧草稿继续作为候选来源。完全相同的来源正文只在提示视图中复用一份，原来源身份和 canonical pack 不变。

Channel 与 Kanban Agent 是产品内共享时间线的两个实际入口，均使用默认收起的活动组件。公开 commentary 合并为有稳定身份的快照；Kanban 的 ephemeral bridge 和持久 Agent session 分别接入，避免逐字尾片替代完整说明。普通正文不再展示原生会话号和 text 标签；完整 Markdown、复制、文件、证据、问题和批准卡片保留。

步骤只报告已原子保存的部分。未确认结果的工具不会因整轮完成而获得成功标记。过程计时在结束后冻结，无虚构百分比或剩余时间。草稿在综合控制区只读预览，明确尚未发布；原 PRD 阅读器保持既有产品/架构/开发视图，材料表单不进入阅读器。窄屏工具栏换行以保留全屏/下载入口。
真实最终截图还发现原风险区直接序列化结构化 gap/risk 对象。现在优先展示其说明文字，原字段保留在可展开详情；只调整原区块的内容呈现。8 项结构化显示、风险接受及版本绑定浏览器回归通过。

## 验证中发现的边界

1. 实际调用者测试发现 Provider 会话线程与 Channel 业务线程不同；恢复改用持久请求谱系与完整业务绑定，保留 Provider generation fence。
2. 真实前端发现轻量聊天投影会丢弃全部活动 refs，且完成后移除 Provider run。分页接口现在只带公开字段、有界活动尾部和五个阶段凭据，完整正文仍取原消息；不输出私密原生载荷和检查点内容。
3. 普通 Channel 与 Kanban 的公开输出有不同运输路径。Kanban bridge 单独验证稳定快照；不以只有组件 fixture 通过冒充实际调用者已接入。
4. 手机宽度长 PRD 全屏工具栏溢出可重复出现；换行后原用例通过。
5. 首次完整五部分交付后，原发布 schema 拒绝成功指标缺少 `statement`。没有放松原门禁；将原指标检查复用到文稿阶段，提示明确字段，完整但无效的缓存仅重做该部分。
6. 实际停止按钮暴露了两层既有接线缺陷：Web allowlist 未注册 cancel，以及通用 HTTP `request_id` 提取误吞业务生成身份。修复并覆盖实际 token-gated HTTP 调用和两种 action 名称。
7. Web 与 Harness 的进程注册表相互独立，Web 端返回未找到子进程不能证明 Harness 已停止。取消意图由持有子进程的 Adapter 增量读取 EventLog 执行，真实 spawned-process 测试证明跨进程停止，旧 generation 和迟到输出被围栏拦截。旧运行尚未加载此监视器，实测时先走前端取消，再按授权重启 Harness，并从前端恢复未知投递；未把该次 CLI 收尾写成前端物理取消通过。
8. 普通运行失败但没有工具时，错误正文保留原回复，过程组件也保留终止状态。公开过程过滤裸运输名称；回归覆盖失败只显示一份原因、不保留旋转图标。
9. 实际独立全文评估发现：旧开发交接把原上下文写成当前基线，而本轮 attestation 指向新上下文。实际调用者回归先复现仅重做 finalize；恢复校验现在跳过含原上下文摘要／身份引用的缓存部分，由 Agent 重新撰写，其他部分仍复用，旧 sidecar 不改写。不将历史 v2 的评估结果改成通过。
10. 专项评估发现开发交接沿用了分段暂存路径，如 `requirements.acceptance_criteria`，而 canonical 合同是扁平结构。共同提示与交接提示明确根路径；交接保存前拒绝确切的暂存字段别名，由当前部分重试，不能在 Runtime 静默改写 Agent 合同。两处最终补修共 148 项影响闭包检查通过；未通过真实 Provider 再生成第三版，历史 v2 的真实结果与受控调用链回归分别记录。

## 确定性与浏览器验证

以下是有交叉范围的独立命令，不能相加为独立用例总数；未运行全仓 pytest。

| 范围 | 结果/证据 |
| --- | --- |
| 综合、恢复、来源、实际调用者、原发布门禁、分页、原生取消、跨进程停止及相关 mock E2E | `backend-release-closure.log`：325 passed |
| 共享事件、注册、结构、文档与 spine 哨兵 | `sentinel-release.log`：53 passed |
| 全部前端模型单测 | `ui-unit-release.log`：通过 |
| 生产构建/类型/包预算 | `build-release.log`：通过；入口 chunk 470,320 字节，小于 500,000 |
| Docker 共享组件与 Channel 浏览器回归 | `browser-release-closure.log`：58 passed；终止状态与裸运输名称最终边界见 `browser-error-polish.log`：3 passed |
| Kanban 实际运输 bridge 与原生边界 | `kanban-stream-boundary.log`：91 passed；最终 coalescing 边界见 `kanban-coalescing-final.log`：16 passed |
| 原上下文引用、扁平交接路径与原发布调用链 | `context-bound-reuse-closure.log`：148 passed；修复前复现见 `context-bound-reuse-red.log` |
| 结构化未解决项与原风险确认门禁 | `browser-readable-readiness.log`：8 passed；最后生产构建见 `build-release.log` |

日志与截图位于仓库本地 `output/readable-agent-synthesis/`，未作为代码提交。Docker 使用 `mcp/playwright:latest`、host networking 与镜像内既有 Chromium；测试写入均为 fixture 截获。SceneProof CLI 在当前环境不可用，已用实际浏览器截图检查桌面、展开、手机布局及报告图像。

## 真实 Provider 与前端验收

项目为原“狼人杀”Channel，继续使用已经审阅的第二版文件。产品操作仅通过 Playwright 原前端；CLI 仅用于已授权的服务与 Harness 启停，未通过脚本读取或改写该项目 `.zf`。

实测记录：

- 首次新流程完成五部分约 27 分 35 秒，没有触及原 1,800 秒期限；需求部分经历一次 TLS 重试后交付单份完整合同，包含 62 条需求覆盖、41 条验收、7 个故事、28 个边界、10 个实体（64,871 字符／93,229 字节），没有重做该阶段。随后原发布门禁发现指标缺字段，拒绝候选；该次不是正式发布成功。
- 修正文稿预检后，通过前端沿用同一冻结材料重试，复用需求、决定、开发交接三部分，仅重做文稿与绑定当前上下文的最终复核。新 run 在 2026-11-12 11:49:36 UTC 完成，界面耗时 4 分 17 秒，经过原发布路径形成正式 PRD v2。不能把重试耗时当作完整首轮耗时。
- 正式产物摘要：`1e6202fe4cff5a2ce6cabf634b6e88c29952939da855d99acb040190c27c1abb`。产品、架构、开发视图已检查，原阅读器没有混入生成材料表单。Source 为三页；经前端 Download 取得 215,533 字节，与三页全文逐字节一致。
- 已审阅的第二版正文 33,731 字符完整作为正式文档前缀且仅出现一次；其后是需求追溯、决定、验收及开发交接。保留六身份模板、`agent.output.v2`、本地 Codex 正常登录、独立记忆、预算、规则和 NOT_RUN 声明。正文完整性不等于独立内容评估通过。
- 刷新后五部分与三项恢复凭据可回放；Kanban 的公开说明和工具活动也可回放，完成后无持续计时和旋转。正式 v2 的全文及权限验收专项已从原阅读器请求，结果按同一产物摘要单独记录。
- 同版全文评估于 12:17:26 UTC 完成，结论 FAIL：184 项矩阵中 179 PASS、3 FAIL、2 INDETERMINATE。缺口包括狼人真人视图接口样例、逐原子命名场景与验收绑定、tasks 后的 Spec-kit analyze 检查点；另有上下文基线和原题卡／合并记录证据边界。生成、下载及恢复功能通过不代表这份 PRD 内容已达开发门槛。上下文缓存问题已补修并回归，其他内容缺口如实保留，不自动修改已发布 v2 或批准实施。
- 同版权限与验收专项已完成，结论 FAIL；246 项矩阵，5 条发现：原子场景、交接路径、上下文绑定及独立评审／原题卡证据边界。交接路径问题在最终补修中加入阶段内检查；评估原报告保留。两项真实评估均已完整返回，没有以重启取消未完成评估来提前收尾。

真实网络没有主动注入故障；可控 Provider 替身验证了中断与未知交付。狼人杀游戏尚未开发，其性能、真实模型、角色质量及 CLI 接入测试不能据本批改为通过。没有创建开发 Task、启动 Workflow 或批准实施。
