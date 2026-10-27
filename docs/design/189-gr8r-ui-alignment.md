# 189 · Inbox / Tasks / Settings 与 GR8R 对齐

> 状态：已实施。来源：2026-10-26 用户批准的 GR8R 源码与浏览器对照 review；参考 `/home/min/workspace/gr8r-studio`、https://gr8r-taskmanagement.vercel.app/ 。本设计只改变 Web 呈现和本地偏好，不改变 Kernel 的业务状态所有权。

## 目标与边界

对齐 GR8R 的信息密度、对齐线、分栏和渐进披露：Inbox 是处理队列，Tasks 是共享筛选的 Board / List / Table，Settings 使用 General 二级导航。保留 ZaoFu 的运行状态、任务终态、审批、鉴权、事件及投影语义。不得复制参考站的 seed 数据或 localStorage 业务状态。前端选择、查看、排序均不能写任务状态或确认通知。

验收尺寸为 1440×1000、1024×768 与 390×844；浅色、深色、系统主题以及紧凑密度均可操作。标题、工具栏、正文使用一致左边线。键盘可访问所有控件，焦点清晰，减少动效遵循系统或显式偏好。

## 视觉基线

使用温暖的中性色背景、白色/深色 surface、细分隔线和少量强调色。使用本地打包的 Geist Variable 与 Geist Mono；中文回退到系统无衬线字体。桌面页边距 24px，窄屏 16px；项目标题与设置标题 19px，一般页面标题 23px，正文 13.5px，次要元数据 12px，任务编号 11px。颜色、字号、密度均走 token。语义警告/错误颜色不能随强调色改变。

## Tasks

项目标题下单独设置 Board / List / Table 下划线标签栏；再下一行工具栏：搜索、Filter、Sort、Group 靠左，New task 靠右；筛选展开后有完整标签，已选条件显示可移除 chips。Focus 预设整合进筛选区域。New task 调用既有 Task Agent 入口，继续遵守控制动作权限。

三种视图共用筛选、排序与详情选择；URL 支持 `?page=board&view=table`。Board 保持 Todo / In Progress / Verify / Blocked / Done 五个语义列，列宽约 284px，间距 12px，横向滚动限制在板内。卡片标题优先，编号与所有者/优先级/最近活动收敛为 footer；保留重要风险，遥测和路由放在既有详情中。拖动仍走原受控更新路径。

List 为全宽可折叠分组列表，舒适行高 40px，紧凑 34px，移除常驻 Inspector。Table 有粘性表头和标题列，列排序、列显隐与可调宽度；默认标题、状态、所有者、优先级、阶段、最近活动，可选技能、依赖、证据、重试和成本。Table 舒适行高 36px，紧凑 34px。三视图打开相同的任务详情入口；不制造 due date 或 estimate 等不存在字段。

## Inbox

宽屏采用约 420px 队列 + 弹性阅读/操作区。队列包含搜索、类别、待办数量；行只展示类型、标题、一行摘要、引用和时间。详情包含完整文本、状态、上下文与所有既有动作。保留 Decisions / Runtime / Automation / Delivery / All / Resolved 语义及通知聚合，长 Plan / PRD 继续使用完整预览。

选择和阅读不得等于 Ack 或 Approve。J/K 切换、显式按钮动作以及错误/加载状态继续可用。窄屏在队列和详情之间切换，返回后保留筛选和队列滚动位置。任务/计划深链仍可达。

## Settings · General

二级导航宽 220px，内容最大宽 720px，桌面内容内边距约 40px。General 下包括 Workspace、Appearance、Language、Date & time。设置行左侧为名称/说明，右侧控件；移动端堆叠。

Workspace 只读展示当前项目名称、根路径、状态目录与配置来源。技术边界折叠披露，`zf.yaml` 仍是唯一控制配置，设置页不产生第二套业务配置。

Appearance 包括 Light / Dark / System 的实时预览、indigo / blue / violet / teal / rose / graphite 六种强调色、导航密度、三视图任务密度、跟随系统/减少动效。即时生效并持久化；迁移既有 `zf.themeMode`。系统主题响应 OS 切换，跨标签页同步偏好；异常存储不能导致页面崩溃。

Language 提供 English、简体中文、System。本批次翻译共享导航和这三个模块的新增交互/主要文案；后端业务正文、协议字段、运行诊断及未改造页面保持来源语言，界面明确说明覆盖范围。

Date & time 提供系统/指定时区、日期样式、12/24 小时制和实时格式预览。公共格式化函数读取偏好，对现有时间展示生效；只影响展示，不更改服务端 UTC、调度或 canonical 数据。无效日期保留原值。

## 实施任务与验收映射

| Task | 范围 | step → verify |
| --- | --- | --- |
| ui-foundation | token、偏好模型、Provider | 旧主题迁移/异常存储/跨页 → 单元与浏览器 |
| ui-settings | General 四页、Appearance | 切换主题/强调色/密度/语言/时区 → 刷新后仍生效 |
| ui-tasks | 三视图、工具栏、详情 | 搜索过滤后切视图、排序、折叠、列设置、deep link → 浏览器断言 |
| ui-inbox | 双栏与窄屏详情 | 阅读无写入、审批/拒绝/预览保留、返回位置 → 浏览器断言 |
| ui-integration | 响应式、无障碍、文案和回归 | build/typecheck/unit、Docker Playwright → 验证记录及任务归档 |

## 验证与完成定义

仅 Web 呈现改动，不运行全量 backend pytest。运行 `python scripts/dev-verify.py plan --base dev` / `run --base dev`、前端 build、token 检查及 Docker `mcp/playwright:latest` 浏览器验证。测试通过 mock API 隔离真实运行态；不调用真实审批或更新。截图检验浅/深色、桌面/窄屏。所有任务通过后先提交实现，再用实现 hash 归档任务；不 push、不 merge。

## 实现与验证入口

- 共享偏好：`web/src/lib/uiPreferences.ts`、`web/src/app/UiPreferences.tsx`。偏好键 `zf.uiPreferences.v1`；旧 `zf.themeMode` 在新配置不存在时读取；不写业务状态。
- Settings：`web/src/components/settings/SettingsPage.tsx`，二级页支持 `settings=appearance|workspace|language|datetime` 深链。
- Tasks：`TaskToolbar.tsx` / `TaskViews.tsx` / `taskViewModel.ts` / `TaskDrawer.tsx`。Board 固定语义列；List/Table 的 Group 按状态、所有者、优先级或不分组。列宽可通过表头滑块鼠标或键盘调整，布局键为 `zf.taskTable.v1`。
- Inbox：`PlanApprovalPanel.tsx` / `InboxQueueItem.tsx` / `PlanPreviewOverlay.tsx`。模态详情采用原生 dialog，支持 Escape 与焦点返回；队列阅读不发出写请求。
- 验证：`web/tests/ui-alignment.spec.ts` 与 `kanban-toolbar.spec.ts` 使用隔离的 mock API；`uiPreferences.test.ts`、`taskViewModel.test.ts` 覆盖迁移、非法值、DST/跨日和投影分组。新增模块均小于 1000 行。
- 真实后台状态转换保持原实现，本批验证不声称真实 Provider / 运行时 E2E。语言覆盖和只读配置边界按上述设计执行。

浏览器重放命令（先在 web 运行 build、preview，并将端口设为 8020）：

```sh
docker run --rm --network host \
  -v "$PWD:/work" \
  -w /work/web \
  -e ZF_WEB_BASE_URL=http://127.0.0.1:8020 \
  -e ZF_E2E_CHROMIUM_EXECUTABLE_PATH=/ms-playwright/chromium-1226/chrome-linux64/chrome \
  --entrypoint node mcp/playwright:latest \
  node_modules/@playwright/test/cli.js test \
  tests/ui-alignment.spec.ts tests/kanban-toolbar.spec.ts tests/needs-input-ux.spec.ts \
  --workers=1 --reporter=line --output=/tmp/zf-ui-browser
```

该命令使用当前 worktree 的 node_modules（按 web/package-lock.json 安装），Chromium 路径需与当前镜像一致；此次使用镜像 `sha256:8342e03ef3571d09afffcd00558b8067e66489a54e7f3f0d96a71a327fd45fe2`，不安装宿主浏览器。

## 本批验证结果

- `python scripts/dev-verify.py plan --base dev`：归类为 `docs, web_ui`，未触发全量后端测试。
- `python scripts/dev-verify.py run --base dev`：通过（8 个 instruction contract 检查、Web typecheck、63 个 Web 单元测试文件）。
- `npm run build --prefix web`：通过；39 个 chunks，最大 437701 bytes，低于 500000 bytes 预算。
- `npm run lint:tokens --prefix web`、新增 workbench CSS 裸字号扫描、`git diff --check`：通过。
- Docker Playwright：`ui-alignment.spec.ts`、`kanban-toolbar.spec.ts`、`needs-input-ux.spec.ts`，最终生产构建 **8 passed**。覆盖 1440×1000、1024×768、390×844，以及浅/深/系统主题。紧凑 List/Table 实测行高 34px、Board 内边距 8px。
- 证据目录：`/tmp/zf-ui-20261026-1408/verified/`（截图）；同级 `browser-verified.log`、`build-final.log`、`dev-verify.log`。先前测试发现的 mock 路由范围、容器目录权限及 Table 行高问题均已修复并复验。
- 只在 mock API 上验证写请求内容与鉴权边界；未调用真实运行态的审批、Ack、拖动更新或 Provider。未运行依赖真实项目数据的全量浏览器 suite。

## 2026-10-26 视觉复核与修订

用户要求以参考站 **Website Redesign → Board** 的实际渲染为基准，纠正第一批只做模块结构对齐的问题。Docker Playwright 于 1440×1000 实测：侧栏 x=0 / w=252；右侧 topbar y=0 / h=46；项目标题区 y=46 / h=68；视图标签 y=114 / h=37；工具栏 y=151 / h=44；Board y=195、padding=14px 24px 24px；列 w=284 / gap=12 / radius=10；卡片 padding=10px 12px / gap=8 / radius=8，正文 Geist 13.5px、weight450。

修订范围：

- 移除三个模块的外层圆角框和 10px 留白；侧栏贯穿窗口，保留真实项目选择和运行状态；菜单结构与自身样式按文末用户修订恢复为 ZaoFu 原有排列。
- Tasks 使用实际项目名作为标题，恢复参考站的标题 / 标签 / 工具栏三层结构。列使用 sunken 背景与细边；卡片标签、标题、单行元数据依序排布。任务类别、优先级、owner、时间仅展示实际字段；不虚构人员、截止时间或参考站数据。授权入口收进可展开的工具栏，锁定拖拽仍无写请求。
- Inbox 保持 420px 队列，标题 19px，类别改下划线标签；行以类型图标 / 正文 / 引用时间三部分排布，去除重复状态块与大面积强调色。保留显式审批、Ack、完整预览与移动端返回。
- Settings 从 topbar 下直接分 220px 二级导航与正文；正文 x=512，顶部 padding32 / 水平40，内容含 padding 最大720。Appearance 同步主题整卡选中框、Theme / Accent color / Density 分组与密度分段按钮。仅渲染已有功能，不添加不可用占位导航。

验收：固定视口测量关键几何值、确认 Geist 字体 loaded、比较实际截图；已有审批 / 拖动权限 / 跨页偏好 / List / Table / 窄屏用例必须通过。同步更新 8019 预览构建。证据目录 `/tmp/zf-ui-parity-20261026-145557/`。此节覆盖前文已被修订的视觉细节。

本次校准采用 `@fontsource-variable/geist@5.3.0`（OFL-1.1），字体随构建本地分发，不依赖运行时 Google Fonts 请求。Chrome DevTools 的 `CSS.getPlatformFontsForNode` 确认项目标题实际使用 Geist 自定义字体；中文使用系统字体回退。基线比较不要求伪造参考站的项目、成员、截止日期和任务状态。

### 视觉校准验收结果

- `python scripts/dev-verify.py plan --base dev` / `run --base dev`：通过 docs / web_ui 闭包，含 8 个 instruction contract 检查、Web typecheck 和 Web 单元测试；未运行全量后端 pytest。
- `npm run build --prefix web`：通过；最后的样式修订再由 `vite build --outDir /tmp/zf-ui-parity-20261026-145557/dist-final` 构建，完成后替换 UI worktree 的 dist。`npm run bundle:check --prefix web` 通过：39 chunks，最大 443740 bytes。
- `npm run lint:tokens --prefix web`、28/29 样式文件裸绝对字号扫描、`git diff --check`：通过。
- Docker Playwright 最终生产构建：`ui-alignment.spec.ts`、`kanban-toolbar.spec.ts`、`needs-input-ux.spec.ts`，**8 passed**。新增对侧栏、顶栏、标题/标签/工具栏、Board 列和卡片坐标、Settings 220px 导航及 Inbox 420px 队列的断言；验证字体 loaded、显式展开授权控件、搜索、设置持久化、跨标签同步和窄屏高度。
- 同步真实后台的 8019 预览进行只读访问，Board / Inbox / Appearance 均渲染成功，page errors 为 0。真实任务保留原内容；未调用真实审批或任务更新。
- 最终证据：`/tmp/zf-ui-parity-20261026-145557/release/`、`browser-release.log`、`dev-verify.log`、`build-complete.log`、`release-live-*.png`、`live-metrics.json`。早期开发服务冷启动超时、构建替换窗口的 HTTP 失败及重复标题定位均已通过最终生产回归复验；最终浏览器验证期间未修改构建产物。

## 2026-10-26 侧栏范围修订

用户明确要求「侧栏的菜单栏还是按照之前的排列，不用左侧菜单栏也对齐 GR8R，右侧 Task 对齐就行」。恢复 `c6d12388` 的 ZaoFu 菜单：品牌 ZaoFu，Workspace 下先项目选择器，再 Overview / Inbox / Tasks / Workflows / Agents / Automations；随后 Monitoring（Delivery / Loop / Traces）、Channels、System settings（Settings）。恢复原来的分组间距、项目选择器和强调色选中态；移除左侧 Projects 分组、Board / List / Table 次级入口及底部固定 Settings。

右侧 Tasks 的项目标题、标签栏、工具栏、Geist 字体、Board 卡片与 List / Table 排版继续使用已验收版本；现有 Inbox 和 General / Appearance 内容继续保留。共享外框尺寸不变，避免侧栏菜单修订移动右侧对齐线。

验收：`python scripts/dev-verify.py plan --base dev` / `run --base dev` 通过 docs / web_ui 闭包（8 个 instruction contract、前端 typecheck / unit）；Vite 生产构建与 bundle budget 通过（最大 443067 bytes）。既有 `ui-alignment.spec.ts` + `kanban-toolbar.spec.ts` 7/7 通过；最后的紧凑密度修正再运行 General preferences 1/1 通过。Docker Playwright 专项检查确认原菜单顺序、项目选择器优先、桌面 2px 选中边线、移动端跳转关闭菜单，以及舒适 30px / 紧凑 26px 行高；无业务写请求或 page errors。证据：`/tmp/zf-ui-navigation-20261026-1555/`。

## 2026-10-26 品牌与侧栏收放

用户随后补充：左上角 ZaoFu 品牌区、侧栏收起和展开参考 GR8R。此修订仅调整品牌与收放交互，保留上一节的菜单分组和顺序。

- 品牌保留 ZaoFu 名称，使用 22px 黑底圆角字标、8px 间距、Geist 13.5px / 600。
- Docker Playwright 实测参考站展开宽 252px、收起宽 52px，收起后 Logo 下方保留展开按钮，刷新仍保留收起状态。本地同步这些行为：图标可直接导航，悬停有名称提示，Inbox 待处理提示保留为圆点；项目选择器在展开后使用。
- 桌面收起状态仅存储于 `zf.sidebarCollapsed.v1`，不写业务 API。浏览器禁用存储时仍可在当前页面收放。
- 移动端菜单以 280px 抽屉覆盖内容；关闭按钮、遮罩、Escape 和选择页面均可关闭。打开时隔离背景交互、约束键盘焦点，关闭后恢复入口焦点；跨回桌面自动关闭抽屉，保留桌面收起偏好。
- 右侧 Tasks / Inbox / Settings 随可用宽度伸展，其标题、工具栏、卡片、字体和内部间距保持已验收版本。

验收：展开与收起 -> verify: 252px / 52px、顶栏与内容起点同步；刷新及图标导航 -> verify: 状态保留且原菜单顺序不变；移动抽屉 -> verify: 焦点、遮罩、Escape、导航和断点切换正确；现有三个模块 -> verify: 原浏览器用例通过。对应 `web/tests/workspace-rail.spec.ts`，证据目录 `/tmp/zf-ui-sidebar-20261026-1618/`。

验证结果：`dev-verify.py plan/run --base dev` 通过 docs / web_ui 闭包，含 8 个 instruction contract、前端 typecheck / unit；Vite 构建与 bundle budget 通过（39 chunks，最大 446084 bytes）。Docker Playwright `workspace-rail.spec.ts`、`ui-alignment.spec.ts`、`kanban-toolbar.spec.ts` 共 **10 passed**，覆盖收放、刷新、存储失败、移动焦点和既有模块回归；token lint 与 diff 检查通过。截图经目视复核后同步至 8019 预览。

## 2026-10-26 全工作区排版扩展

用户批准按 Overview → Agents → Delivery → Workflows → Automations → Traces / Loop → Channels 全部优化，并校准 Tasks / Inbox / Settings。参考只用于视觉与交互结构，保留当前项目范围、真实字段、受控动作、原侧栏菜单顺序与收放行为。

- 共用内容页采用 Geist、24px 页标题、13.5px 正文、12.5px 辅助文字、24px 内容边距、16px 分区间距、浅边框。Overview 最大宽度 1160px；表格、时间线和会话使用可用宽度。
- Overview：四项摘要（工作队列、交付进度、活跃运行、用量）；主栏为待处理与交付进展，辅助栏为运行概况、状态分布和近期动态。项目说明两行展开；时间与权限可读，技术详情折叠。保留 pulse 先于 snapshot 展示、未知值与零值区分、成本精度提示。
- Agents：摘要＋筛选后的 Fleet 表格＋选中 Worker 详情，常用列优先；效率和恢复数据进入 Advanced，空态与加载状态明确。
- Delivery：简化状态头，Attention 与进度/最近执行按主次两栏排列，保留 Overview / Runs / Graph、证据与动作入口。
- Workflows：沿用请求列表＋审批详情；扩大可读列表宽度、统一标题与标签。完整流程图默认折叠，展开后加载；含 `graph` / `node` 的深链接自动展开。审批、预览讨论和图工作台操作沿用原受控路径。
- Automations：自动化目录与当前状态为入口，先控制与执行健康，再报告/运行记录；分析矩阵折叠，原 Run now 权限和行为不变。
- Traces / Loop：统一标题、工具栏、卡片，保留有界索引、时间线、原始证据和下钻。
- Channels：统一页头和流程导航，控制消息/输入区阅读宽度；保持历史、审批、文件、消息发送及抽屉。
- Tasks / Inbox / Settings：以已验收布局为基准校准控件、焦点、深色和紧凑密度；不改变既有三视图几何和审批语义。

验收：逐页桌面 / 窄屏截图 -> verify: 标题与边距一致、无页面横向溢出；摘要和详情操作 -> verify: 原导航、筛选、展开与权限行为保留；加载 / 空态 / populated 状态 -> verify: 不以零伪装缺失数据；现有 UI 测试和新增模块测试 -> verify: mock API 覆盖动作与零意外写请求，真实预览只读。实施完成后更新 8019 生产预览，focused verification、实现提交和任务归档分别记录。

实现补充：Overview 近期动态通过当前项目的 `/events?limit=5` 独立读取，与 SSE 按 seq 合并，按新到旧显示；不阻塞 pulse/snapshot，不发起无项目范围的请求。Agents 详情使用原生 dialog，保留 Escape、焦点返回与 Worker 动作权限。Channels 流程导航、消息与输入区共用 820px 阅读列，维持审批侧栏与窄屏布局。

真实项目复核发现 Agents 读取接口超过 30 秒仍无响应。UI 为自己的读取请求增加 20 秒取消边界：加载、空列表、读取失败分开显示；失败时不显示零 Worker/健康状态，提供只读重试。切换项目或离开页面时取消旧请求，Cockpit 辅助读取不覆盖 Agent 列表的失败状态。此批不修改后台投影的计算或运行状态。

### 本批验收结果

- `python scripts/dev-verify.py plan --base dev` 与 `run --base dev`：docs / web_ui 闭包通过，8 个 instruction contract、前端 typecheck 与 63 个单元测试文件通过。
- `node node_modules/vite/bin/vite.js build --outDir /tmp/zf-ui-pages-20261026-2337/dist-complete` 与 bundle budget：39 chunks，最大 450236 bytes，小于 500000 限额；token lint、bundle budget 自测与 `git diff --check` 通过。
- Docker Playwright 共 **26 个不同用例**验收通过：`ui-alignment` / `workspace-rail` / `kanban-toolbar` 10 个；`channel-chat-layout` / `dashboard-pulse-first` / `loop-page` / `traces-page` 6 个；新增 `workspace-pages` 10 个。使用 `mcp/playwright:latest`、host network、8020 预览、1 worker；最终运行采用 90 秒单例预算。
- 初轮发现并修复 Channels 导航与输入区对齐、Worker 详情关闭后的焦点返回；Agents / Workflow 测试夹具补齐独立读取与 Preview context 字段。Traces 在默认 20 秒总预算内未完成，保持原断言以 90 秒预算复跑通过。每次只复跑修订影响的用例，最终日志见 `browser-final.log`、`browser-agents-complete.log`，既有三个模块与侧栏结果见 `browser-regression.log`。
- 截图与日志目录：`/tmp/zf-ui-pages-20261026-2337/`。逐页检查桌面、360/390px 窄屏与深色画面；Tasks / Inbox / Settings 的密度、语言、时区和偏好持久化使用原回归用例验证。
- 真实 `loshu-fms` 项目：Overview、Tasks、Appearance 正常展示；Agents 接口超时会显示未知值和重试，未伪装为空列表。只读复核无前端异常、无非 GET 业务请求、无页面横向溢出。当前后台投影读取的耗时问题仍存在，未在 UI 批次中改动运行状态。
- 8019 已更新为上述验证构建，首页内容与构建索引一致；保留旧哈希资源，避免已打开标签页加载旧模块时出现 404。
