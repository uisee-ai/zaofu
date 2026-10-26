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

- 移除三个模块的外层圆角框和 10px 留白；侧栏贯穿窗口，Settings 固定在底部，导航选中态改中性白底细边。保留真实项目选择和运行状态。
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
