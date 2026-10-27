# Workflow 图工作台改版验收

本次按用户确认的 ASCII 布局及 `/tmp/111.png` 实施，并按后续反馈调整为横向纯文字导航与默认填满内容区域的画布。取消三个状态分类页签，以六个英文图视角为主；请求选择器、上下文审批、Raw YAML、按需 Agent Chat 保留同一 Request / Proposal 绑定。实现说明见 [187 工作台](../impl/187-ai-sdlc-graph-workbench.md)。

## 实际截图

以下截图来自 Docker 浏览器；卡片状态图使用明确的显示夹具，不能作为 Agent 真实执行证据。图截图使用最终的短名称；Raw 截图来自后述 8 项审批回归，使用隔离项目实际 API 返回的配置，当时尚未缩短图导航名称。

- [暗色图](2026-10-26-workflow-graph-ui/workflow-dark.png)
- [亮色图](2026-10-26-workflow-graph-ui/workflow-light.png)
- [手机布局](2026-10-26-workflow-graph-ui/workflow-mobile.png)
- [Raw YAML](2026-10-26-workflow-graph-ui/raw-desktop.png)
- [手机 Raw](2026-10-26-workflow-graph-ui/raw-mobile.png)
- [Raw Agent Chat 与局部 Diff](2026-10-26-workflow-graph-ui/raw-agent-chat.png)

## 验证范围

- `npm --prefix web run build`：TypeScript、Vite 和 bundle budget 通过；最大 chunk 432696 bytes，小于 500000。
- `npm --prefix web run test:unit` 和 `lint:tokens`：通过。
- `scripts/dev-verify.py run --base dev --tests tests/test_workflow_preview_review.py --tests tests/test_web_workflow_proposals.py`：34 项相关 Python 测试、Web typecheck 和模型单测通过。显式 Python 范围覆盖本次扩展的浏览器 Provider 替身及真实 Preview / Proposal 调用链；未运行完整 pytest。
- Docker `mcp/playwright:latest`、host network、`0.0.0.0:8002`：四个测试文件共 **7 passed (54.5s)**。包括 `workflow-graph-redesign.spec.ts`、`workflow-preparation.spec.ts`、`workflow-preview-review.spec.ts`、`workflow-proposal-page.spec.ts`。
- 最后修复节点首次加载与侧栏宽度变化的适配竞态后，额外复测三个画布/Raw/Plan 用例：**3 passed (21.4s)**，含 Raw 打开后全部节点处于画布内的断言；[复测日志](2026-10-26-workflow-graph-ui/viewport-browser.log)。
- 六视角、Provider 色彩、明暗背景一致、实时状态变化不重置缩放、容器变窄自动适配、请求深链接与切换、Raw 预览、局部 Chat / Diff / 新版生成、配置应用后批准、Plan Chat 与审批绑定、小屏无页面溢出均有浏览器断言。
- create-task / idea-to-product 经真实 HTTP 走澄清、准备和提交；Provider 与 runtime launch 为替身。Plan 面板与运行状态使用投影夹具；不宣称本轮运行真实 Codex / Claude 或完成新的真实产品交付。
- Channel 与 CLI 提交接口未修改；已有前端 channel workflow 单测及来源绑定测试通过，本轮没有另做真实 Channel Provider E2E。

日志：[浏览器](2026-10-26-workflow-graph-ui/browser.log)、[构建](2026-10-26-workflow-graph-ui/build.log)、[单测](2026-10-26-workflow-graph-ui/unit.log)、[验证闭包](2026-10-26-workflow-graph-ui/verification.log)。

## 布局细化复验

- 六个视角改为横向纯文字标签，移除版本与数据可用性、Live Events 区块；画布默认填满剩余窗口空间，保留节点来源与执行证据。
- Raw 顶部直接进入 Agent Chat，讨论期间收起 YAML；应用修改后返回新版 YAML。已启动 Run 仍为只读快照。
- 最新构建、主题 token 检查及 `sdlcGraphModel`、`workflowReviewModel`、`workflowProposalModel` 三个模型测试文件通过；[构建日志](2026-10-26-workflow-graph-ui/layout-build.log)。本轮仅改前端和说明，未重复运行 Python 回归。
- 临时服务 `0.0.0.0:8003`，Docker Playwright 同四个文件 **7 passed (48.3s)**；[浏览器日志](2026-10-26-workflow-graph-ui/layout-browser.log)。新增断言覆盖无导航图标、横向排列、六个视角默认画布宽高、底部区块移除、Chat 入口位于 YAML 上方、应用后回到 YAML。对应截图已被后续复验结果更新。
- 全屏展开宽度修正后单独复测六视角、展开/收回、主题、实时状态、缩放保留与手机适配：**1 passed (9.4s)**；[复测日志](2026-10-26-workflow-graph-ui/layout-expand-browser.log)。
- `8002` 为用户预览服务，已读取新版构建，继续保留；`8003` 验证结束后通过正常关闭写入 `simulation.done` 并清理临时状态。

## 自动适配与页面精简复验

- 修复固定最小画布高度、缩放下限和 Fit View 默认参数不一致：画布受实际内容容器约束，首次适配、窗口缩放与 Fit View 统一规则，小图默认最多 80%，大图完整缩入可见区域；保留用户缩放后的纯状态轮询行为。窄屏导航行不再拉伸形成大片空白。
- 40 个并排节点的适配用例在修复前失败；修复后覆盖 1280×720、1280×480、内容面板缩为 340px、390×844，全部节点可见且画布不越界。[失败复现](2026-10-26-workflow-graph-ui/adaptive-regression-before.log)。
- 8002 既有项目的只读检查中，手机窗口从仅渲染 36/44 个节点、画布底部超过视口，变为渲染全部 44 个节点且完整位于视口内；检查没有提交新任务或运行 Provider。[尺寸记录](2026-10-26-workflow-graph-ui/window-fit.json)。
- 移除 Timeline 执行区间、关键路径说明和表格；移除独立类型/修订、Review & Approve 上下文栏。顶部只有一个 Workflow 标题，选择器包含类型/状态/修订，审批和运行控制从 Raw 进入，Answer Questions 与待审 Plan 入口保留。
- 最终六个名称为 Lifecycle、Task、Dag、Trace、Delivery、Timeline。Trace / Delivery 的常驻说明行移除；图类型、事件与数据语义不变。
- 四个浏览器文件 **8 passed (49.4s)**，覆盖自动适配、澄清、Raw、Chat 局部修改、配置应用、审批、Plan 绑定与手机布局；[回归日志](2026-10-26-workflow-graph-ui/adaptive-browser.log)。随后改名及移除两行说明，针对六视角与窗口适配复测 **2 passed (11.6s)**；[最终视图检查](2026-10-26-workflow-graph-ui/adaptive-labels.log)。Provider / runtime launch 仍为替身。
- 最终构建、bundle budget、token 检查和图模型单测通过；[构建日志](2026-10-26-workflow-graph-ui/adaptive-build.log)。没有运行完整 pytest；没有修改后端接口。
- 临时 8003 测试进程停止，使用 EventWriter 记录 simulation.done 后移除状态目录；用户 8002 预览保留。

## 运行边界

已有 Run 的 YAML 保持只读；待应用修改不能直接批准启动，陈旧版本仍由原 API 拒绝。每两秒投影轮询更新图，不新增调度状态。历史回放时 Raw 禁用，返回实时后查看当前 Proposal。主题布局修改位于独立工作树；本次没有合并 dev、push 或重启正式 8001。
