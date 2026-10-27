# Channel 阶段导航与 Loop 时间线

> 状态：Channel 与实际时间线已实施；Plan vs Actual 按下述数据条件延期。来源：2026-10-27 用户批准视频分析方案并要求生成 backlog 实施。

## 目标与参考

- [Plan vs Actual](https://x.com/flohoeller/status/2114315756716589153)：借鉴对齐的任务列/时间轴、低饱和执行条、悬停聚焦和执行详情。
- [Release pipeline](https://x.com/a1x45h/status/2112964394825187349)：借鉴圆形节点、标签下置、状态连接线与阶段上下文。
- 字体、色板、边框沿用 [189](189-gr8r-ui-alignment.md)，保留侧栏排列。

## Channel

五步仍为 Brief / Discussion / PRD / Tasks / Workflow，继续使用 channelFlow 的当前 thread、PRD 版本和任务血缘；不修改受控审批、创建或执行路径。

节点上置、名称和状态下置，完成为勾、当前为品牌色外环、待输入/审批为琥珀色、失败为错误色。连接线只表示相邻阶段的已证实完成，不以位置推断完成率。导航下方显示紧凑摘要（输入数量、待决问题、PRD 版本、已创建任务、Workflow 状态）和上下文操作。查看历史节点与当前执行节点分别表达；长文继续使用现有抽屉，问题/计划仍跳转现有处理入口。

窄屏保留可水平滚动的五步轨道并自动显露当前阶段，摘要换行，不挤压输入框或制造页面级横向溢出。选中节点、键盘焦点、状态图标与文字共同表达状态；减少动态效果偏好下关闭过渡。

## Loop

实际执行视图默认真实时间比例。标题与统计、任务列、统一时间刻度和轨道、图例分层。任务列显示现有 ID 与阶段（当前投影没有任务标题，不新增额外任务抓取）；次数/失败作为次要信息。刻度与所有轨道共用同一网格列。

可选压缩空档模式必须明确标注，且不能压缩覆盖真实执行区间的时间。短事件用点表示，缺少开始事件、无有效结束时间或被替代的未闭合记录不能伪造持续时间。高密度短记录聚合展示但保留原始索引和详情。真实重叠执行不通过移动时间条消除。

点击执行段定位对应尝试；按任务 ID/阶段/来源和尝试起始时间/角色保留查看身份，实时重排后仍指向同一记录，证据缺失或身份歧义时提示不可用；聚合块显示其成员，展开列表仍按 100 条分页。保留业务循环成员跳转能力。时间标记以投影观察时间/结束时间为准；未闭合租约不等同于正在运行，不凭前端计时器伪造进度。

## Plan vs Actual 后续条件

当前 loop-view.v1 没有计划起止、预计时长、预算、依赖和 ETA 合同。本批不展示虚构计划条或偏差。

延期项启动条件：有经确认、带 revision/source 的计划基线及 task/run 身份映射，明确等待时间、执行时间、重试和计划变更口径；成本需真实预算及实耗来源，ETA 需可解释预测来源。满足后单独实现投影与对比层，并补 API/数据合同测试。此项不计入本批已完成范围。

## 验收

- 阶段导航 -> verify: 当前/查看分离，待决问题、计划入口和历史 PRD 可访问，导航无写请求。
- 状态表达 -> verify: 未知、等待审批、失败及退回讨论不误标完成；PRD 版本隔离保持。
- 时间线 -> verify: 刻度/轨道对齐，真实间隔与重叠保持，点记录不伪造时长，压缩模式明确。
- 执行详情 -> verify: 点击具体尝试/聚合块定位正确原始记录，875 次尝试仍有界分页。
- 布局 -> verify: 1440px、390/368px 及深色模式浏览器检查，键盘可操作、无页面横向溢出。
- 验证 -> verify: dev-verify plan/run、前端构建与影响域单元测试、Docker Playwright 受影响用例。

## 实施与验收记录

实现入口为 ChannelFlowNavigation / ChannelPage、LoopTimeline / LoopPageV2；时间映射与尝试身份由只读 loopTimelineModel 负责。补齐已存在服务端 run_id 的可选前端类型，没有修改 API 响应、审批或运行态状态机。同期追加的 Overview 卡片间距与 Execution Flow 顺序见设计 189。

- `python scripts/dev-verify.py plan --base dev` / `run --base dev`：docs / web_ui 闭包通过，8 个 instruction contract、前端 typecheck 和 64 个单测文件通过。最终身份映射补充以 `node web/scripts/run-unit-tests.mjs tests/loopTimelineModel.test.ts tests/channelFlow.test.ts` 复验。
- `npm --prefix web run build`：TypeScript、Vite 与 bundle budget 通过；最终 Map 优化经单测编译、Vite 构建和 bundle 检查复验。40 个构建 chunk，最大 450278 bytes，低于 500000 上限。token lint、`git diff --check` 通过。
- Docker Playwright 使用 `mcp/playwright:latest`、host networking、8022 隔离只读 API/静态页面。受影响的 Channel flow/chat、Loop、Overview 与 pulse-first 共 **55 个不同用例**通过。首次广泛回归 52 项中 45 项通过；修正旧横向导航的几何断言，以及测试环境缺失的 WebChatRuntime/附件上传响应后，16 项定向复验通过（包含 7 项原失败和 3 项新增 Overview/pulse 用例）。最终 Loop 身份重排与 875 次分页 2 项再次通过。
- 浏览器覆盖当前阶段/查看阶段分离、任务/Workflow 审批入口、PRD 版本与拒绝路径、问题定位、真实时间/空档、未知时长、原始尝试选择及实时重排；1440px、390/368px、深色、键盘检查通过。
- 真实 8019 Loop 只读访问渲染 15 行任务，无 pageerror；Overview 与 Channel 的布局截图使用明确的 mock 数据。未运行真实 Provider 或全量后端 pytest。
- 证据：`/tmp/zf-ui-pipeline-20261027-0157/`。主要日志：`verify-release.log`、`verify-plan-final.log`、`build-identity.log`、`build-artifact.log`、`unit-final.log`、`browser-final.log`、`browser-release.log`、`browser-identity.log`、`live.log`；截图在对应 browser 子目录。

重放：构建并启动隔离服务后，在 Docker 中设置 `ZF_WEB_BASE_URL=http://127.0.0.1:8022`、`ZF_E2E_CHROMIUM_EXECUTABLE_PATH=/ms-playwright/chromium-1226/chrome-linux64/chrome`，执行 `node node_modules/@playwright/test/cli.js test tests/channel-flow-layout.spec.ts tests/channel-chat-layout.spec.ts tests/loop-page.spec.ts --workers=2 --timeout=90000`；Overview 使用 `tests/workspace-pages.spec.ts --grep Overview` 与 `tests/dashboard-pulse-first.spec.ts`。隔离服务夹具必须提供 WebChatRuntime 的 turns/interactions/queue 等数组，不以空对象替代有效投影。
