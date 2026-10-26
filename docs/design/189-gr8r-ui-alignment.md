# 189 · Inbox / Tasks / Settings 与 GR8R 对齐

> 状态：已实施。来源：2026-10-26 用户批准的 GR8R 源码与浏览器对照 review；参考 `/home/min/workspace/gr8r-studio`、https://gr8r-taskmanagement.vercel.app/ 。本设计只改变 Web 呈现和本地偏好，不改变 Kernel 的业务状态所有权。

## 目标与边界

对齐 GR8R 的信息密度、对齐线、分栏和渐进披露：Inbox 是处理队列，Tasks 是共享筛选的 Board / List / Table，Settings 使用 General 二级导航。保留 ZaoFu 的运行状态、任务终态、审批、鉴权、事件及投影语义。不得复制参考站的 seed 数据或 localStorage 业务状态。前端选择、查看、排序均不能写任务状态或确认通知。

验收尺寸为 1440×1000、1024×768 与 390×844；浅色、深色、系统主题以及紧凑密度均可操作。标题、工具栏、正文使用一致左边线。键盘可访问所有控件，焦点清晰，减少动效遵循系统或显式偏好。

## 视觉基线

使用温暖的中性色背景、白色/深色 surface、细分隔线和少量强调色。保留 Inter 字体。桌面页边距 24px，窄屏 16px；主标题约 23px，设置页约 19px，正文 13.5–14px，次要元数据 12px，任务编号 11px。颜色、字号、密度均走 token。语义警告/错误颜色不能随强调色改变。

## Tasks

一个工具栏：搜索、Filter、Sort、Group 靠左，List / Board / Table 切换靠右；筛选展开后有完整标签，已选条件显示可移除 chips。Focus 预设整合进筛选区域。New task 调用既有 Task Agent 入口，继续遵守控制动作权限。

三种视图共用筛选、排序与详情选择；URL 支持 `?page=board&view=table`。Board 保持 Todo / In Progress / Verify / Blocked / Done 五个语义列，列宽约 284px，间距 12px，横向滚动限制在板内。卡片标题优先，编号与所有者/优先级/最近活动收敛为 footer；保留重要风险，遥测和路由放在既有详情中。拖动仍走原受控更新路径。

List 为全宽可折叠分组列表，舒适行高 40px，紧凑 34px，移除常驻 Inspector。Table 有粘性表头和标题列，列排序、列显隐与可调宽度；默认标题、状态、所有者、优先级、阶段、最近活动，可选技能、依赖、证据、重试和成本。三视图打开相同的任务详情入口；不制造 due date 或 estimate 等不存在字段。

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
  -v /home/min/workspace/public/zaofu/web/node_modules:/work/web/node_modules:ro \
  -w /work/web \
  -e ZF_WEB_BASE_URL=http://127.0.0.1:8020 \
  -e ZF_E2E_CHROMIUM_EXECUTABLE_PATH=/ms-playwright/chromium-1226/chrome-linux64/chrome \
  --entrypoint node mcp/playwright:latest \
  node_modules/@playwright/test/cli.js test \
  tests/ui-alignment.spec.ts tests/kanban-toolbar.spec.ts tests/needs-input-ux.spec.ts \
  --workers=1 --reporter=line --output=/tmp/zf-ui-browser
```

该命令的 node_modules 挂载位置和 Chromium 路径需与当前机器/镜像一致；此次使用镜像 `sha256:8342e03ef3571d09afffcd00558b8067e66489a54e7f3f0d96a71a327fd45fe2`，不安装宿主浏览器。

## 本批验证结果

- `python scripts/dev-verify.py plan --base dev`：归类为 `docs, web_ui`，未触发全量后端测试。
- `python scripts/dev-verify.py run --base dev`：通过（8 个 instruction contract 检查、Web typecheck、63 个 Web 单元测试文件）。
- `npm run build --prefix web`：通过；39 个 chunks，最大 437701 bytes，低于 500000 bytes 预算。
- `npm run lint:tokens --prefix web`、新增 workbench CSS 裸字号扫描、`git diff --check`：通过。
- Docker Playwright：`ui-alignment.spec.ts`、`kanban-toolbar.spec.ts`、`needs-input-ux.spec.ts`，最终生产构建 **8 passed**。覆盖 1440×1000、1024×768、390×844，以及浅/深/系统主题。紧凑 List/Table 实测行高 34px、Board 内边距 8px。
- 证据目录：`/tmp/zf-ui-20261026-1408/verified/`（截图）；同级 `browser-verified.log`、`build-final.log`、`dev-verify.log`。先前测试发现的 mock 路由范围、容器目录权限及 Table 行高问题均已修复并复验。
- 只在 mock API 上验证写请求内容与鉴权边界；未调用真实运行态的审批、Ack、拖动更新或 Provider。未运行依赖真实项目数据的全量浏览器 suite。
