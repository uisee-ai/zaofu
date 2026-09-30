# ZaoFu 浏览器真值链补充调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/e2e/test_testkit_browser_truth_chain.py`。已有 browser truth-chain 入口；必须在 Docker 执行并绑定后端真值，本次不启动浏览器。

日期：2026-09-10。范围：tests-only 的 Docker Playwright 验证；仅检查指定测试、配置、浏览器技能与 Playwright 官方文档。读取时仓库 HEAD 为 `11f423e451cbb556bfcabaca4098898d0a3941b4`；以下代码证据指本次读取的工作区文件，不代表已执行验证。本文是建议，不是实现或测试通过声明；本次未启动服务、运行浏览器、调用 Provider 或提交。

## 已有事实与证据缺口

| 范围 | 指定文件中的事实 | 不能据此声称的覆盖 |
| --- | --- | --- |
| 页面就绪 | `expectWorkbenchLoaded` 等待 `.status-pill.status-live`，超时 90 秒。 | 该标识不能证明某次任务变更的 SSE 已送达并被应用。 |
| 控制路径 | 建任务走 `/api/actions/create-task`，携带 `X-ZF-Web-Token`；缺 token 时相关写测试 `skip`。 | skip 不等于受控写验证通过；测试没有直接读 Store。 |
| 拖拽状态 | 最后一个用例拖拽后，以 `expect.poll` 查询 `/api/tasks/${taskId}` 的 `in_progress` 状态。 | API 投影成功不独立证明 Store 与事件账本。 |
| DOM | 同一用例在 API 成功后再次 `page.goto('/')`，填过滤框并断言目标列卡片。 | 证明的是重新加载后的可见状态，不能证明原页面通过 SSE 自动更新。 |
| 截图与 trace | Loop 桌面/移动视口、任务摘要有显式截图；配置为 `trace: 'retain-on-failure'`。 | 没有为每条链统一保存成功截图、成功 trace 或 JSON 报告的配置。 |

仓库来源：[就绪标识](../../../../web/tests/workbench.spec.ts#L7)、[受控建任务与跳过](../../../../web/tests/workbench.spec.ts#L343)、[拖拽/API/刷新/DOM](../../../../web/tests/workbench.spec.ts#L429)、[Loop 截图](../../../../web/tests/workbench.spec.ts#L180)、[任务截图](../../../../web/tests/workbench.spec.ts#L230)、[Playwright 配置](../../../../web/playwright.config.ts#L5)。上述文件未声明测试 `retries`；Playwright 默认不重跑失败测试，但实际命令仍可能覆盖配置。[官方 retries](https://playwright.dev/docs/test-retries#retries)

## 建议的最小真值链

以下为待实现测试设计。Store/API/SSE/DOM 是证据层次，不要求不同读取通道严格按这个时间顺序返回。以一个隔离运行的 `run_id`、同一个 `task_id`、唯一标题和明确的状态变化关联证据；不能仅靠页面出现任意卡片或全局序号增长。

| 层 | 建议断言 | 证据边界 |
| --- | --- | --- |
| Store | 在配置指向的临时 `project.state_dir`，由测试侧调用既有 Store 读取接口核验任务 ID、标题、状态。 | 读取必须面向本次临时项目；不能直接编辑 canonical JSON，也不能拿 API 返回值伪装成 Store 读证据。具体读取接口需实现时核对源码。 |
| API | GET 同一任务详情，断言响应成功与字段；必要时交叉核验 snapshot。 | 证明对外投影一致；保留实际响应，不只记录 HTTP 200。 |
| SSE | 先建立观察并确认就绪，再触发受控变更；记录实际浏览器连接收到的、可关联本次任务的事件或快照。 | 连接成功、live 标识、独立 SSE 客户端收包都不能单独证明页面消费。URL、事件名、序号语义未在本次限定文件中验证，不能凭空补出。 |
| DOM | 保持原页面，自动等待唯一任务在正确列中出现，并比较变更前后。 | 不插入 `goto`/reload 来获得通过；另设刷新检查验证持久性。 |

建议拆成两个小旅程：其一验证真实 UI 输入、提交请求及持久化结果；其二在页面已订阅时，由测试侧通过受控 API 改变任务，再核验 SSE 与原页面 DOM。第二条使被测页面没有本地拖拽的乐观状态，从而更清楚地识别推送更新是否工作。这是依据当前“拖拽后刷新”的缺口作出的设计推论，不是已证明的产品缺陷。[现有用例](../../../../web/tests/workbench.spec.ts#L475)

SSE 的测试观察应保留真实传输，不使用 `route.fulfill` 伪造本链数据，也不写前端状态来制造 DOM。记录消息与 DOM 同时吻合仍不是对前端内部因果关系的形式证明；若页面还有后台轮询，需核对实际刷新机制后收紧断言。当前范围未读 Web 实现，因此不能声明已排除该替代路径。网络等待应在触发动作前注册；官方示例也是先保存 `waitForResponse` Promise，再点击。[官方网络等待](https://playwright.dev/docs/network#network-events)

## 输入同步与重试边界

默认使用 `locator.fill()`：它聚焦并触发 `input` 事件。仅在产品确实依赖逐键处理时用 `pressSequentially()`，不能把人为输入延时当作普遍修复。[官方输入](https://playwright.dev/docs/input#text-input)、[逐键输入](https://playwright.dev/docs/input#type-characters)

建议先等待页面已有的可用状态，再填入唯一值，通过可重试的 `toHaveValue` 核对输入；随后观察按钮状态、实际请求载荷或过滤结果，以证明业务逻辑已接收输入。DOM 值相同本身不能证明框架内部状态已同步。以下只是现有过滤框的局部示例：

```ts
const filter = page.getByPlaceholder('filter tasks');
await expect(filter).toBeEditable();
await filter.fill(title);
await expect(filter).toHaveValue(title);
await expect(
  page.locator('.board-column[data-column-id="in_progress"] .task-card')
    .filter({ hasText: title }),
).toBeVisible();
```

输入被后续初始化重置时，应收集 trace、就绪状态和提交内容，判断是否真实产品问题。官方说明：可见且已启用的控件仍可能尚未绑定事件处理器；页面 hydration 不良时输入可能丢失。本文没有证据证明 ZaoFu 使用该机制或存在该问题，因此它只是排查假设。[官方 hydration](https://playwright.dev/docs/navigations#hydration)

| 机制 | 官方行为 | 本场景建议 |
| --- | --- | --- |
| Locator 断言 | 如 `toHaveValue`、`toBeVisible` 会重复读取直到满足条件或超时。 | 等待当前输入、DOM 的可观察结果。 |
| `expect.poll` | 在同一个测试中重复调用函数并断言结果，可设置超时、间隔。 | 回调只做 GET、Store 读取或消息缓冲读取；创建、提交、拖拽只执行一次。 |
| `expect(...).toPass()` | 重跑整个代码块；默认超时为 0，不采用一般 `expect.timeout`。 | 不把含写操作的整段旅程包进去；确需使用时显式设限。 |
| 测试 `retries` | 失败后重跑测试，新 worker 会重建；先失败后重试通过归为 flaky。 | 本条验证链建议显式 `retries: 0`，首次失败留证。若额外诊断重跑，单独保留首次失败，不合并成首次通过。 |

机制来源：[官方断言](https://playwright.dev/docs/test-assertions)、[官方 retries](https://playwright.dev/docs/test-retries)。只读回调、单次写和零重试是本调研建议。现有用例已经用只读 GET 轮询，单次请求超时 30 秒、poll 45 秒；建议未来为每次读取设置短于整段等待预算的超时并保留最后一次状态，以免一次卡住的读取消耗大部分证据等待时间。[现有 poll](../../../../web/tests/workbench.spec.ts#L421)

不要使用固定 sleep 或 `networkidle` 作为业务就绪条件；Playwright 官方明确不推荐用 `networkidle` 做测试就绪判断，而推荐 Web 断言。[官方 waitForLoadState](https://playwright.dev/docs/api/class-page#page-wait-for-load-state)

## trace、截图和 JSON 报告

当前 `retain-on-failure` 适合无重试的失败定位：每次录制，成功后删除。`on-first-retry` 在 `retries: 0` 时不会产生重试 trace。若本条审计链要求成功 trace，建议仅在它的专用测试配置或执行参数启用 `trace: 'on'`，限制用例范围。[官方 trace](https://playwright.dev/docs/trace-viewer#tracing-on-ci)

建议每个关键旅程在断言前后保存截图，并启用 `screenshot: 'only-on-failure'` 兜底；失败截图不能代替成功的验收截图。截图证明视觉状态，业务断言证明状态与交互结果。证据保存到该次运行的独立输出目录并记录文件 ref/digest，沿用浏览器技能的证据要求。[官方录制配置](https://playwright.dev/docs/test-use-options#recording-options)、[仓库证据要求](../../../../skills/zf-browser-e2e-contract/SKILL.md#L177)

JSON reporter 可与控制台 reporter 并用并指定 `outputFile`。建议报告同时保存 Playwright 原始 JSON 与项目链收据：运行 ID、实际 HEAD、命令、服务 URL、浏览器 origin、Docker 镜像 ID、runner/browser 版本、每层断言、执行次数、passed/failed/flaky/skipped 数量及证据路径。自定义链收据是测试产物，不应扩展 canonical Task schema；Playwright JSON 也不自动替代 ZaoFu 的验收报告。[官方 JSON reporter](https://playwright.dev/docs/test-reporters#json-reporter)、[仓库报告归属](../../../../skills/zf-browser-e2e-contract/SKILL.md#L194)

## Docker 与环境失败判断

沿用已固定的 Task/PRD 命令；没有固定命令时才采用技能默认的 `mcp/playwright:latest`、host networking、宿主 UID/GID、受限时长的浏览器安装。容器要访问的 API/UI 绑定 `0.0.0.0`，记录真实服务 URL 和测试 origin；临时状态、端口和清理由仓库 AGENTS.md 的临时模拟约束决定。本次未探测 Docker、依赖或服务，不能声称环境可用。[仓库命令边界](../../../../skills/zf-browser-e2e-contract/SKILL.md#L26)

官方 Playwright Docker 文档提示：项目和镜像内 Playwright 版本不匹配可能找不到浏览器。这里应将其作为版本核验依据，不能据此擅自把仓库规定的 `mcp/playwright:latest` 换成另一个镜像；在收据里保留实际版本和解析后的镜像身份。[官方 Docker](https://playwright.dev/docs/docker#image-tags)

| 观察证据 | 建议判断 |
| --- | --- |
| Docker 不可用、目录不可写、浏览器安装失败、缺依赖、目标 HEAD 不符、镜像/工具不匹配且未到产品断言。 | 环境/runner 问题；记录实际失败步骤、原始错误与拟执行命令，不记为产品回归。 |
| 必需 action token 或受控 fixture 不存在，写旅程被 `skip`。 | 此验收条款未执行；报告覆盖缺口，不能用其他只读测试通过替代。 |
| runner 健康、fixture 正确，而 Store/API/SSE/DOM 的任务字段或可见行为违反验收。 | 产品行为失败候选；按证据定位断裂层，保留首次失败。 |
| 只有 timeout、截图空白或一句“未加载”。 | 信息不足；结合服务日志、网络、trace 判断，不从错误文案直接推断责任层。 |

分类沿用 [浏览器技能的摘要](../../../../skills/zf-browser-e2e-contract/SKILL.md#L145)。该技能明确把完整分类和结构化报告所有权交给 `yoke/verify-review/SKILL.md`；本次未读取其正文，不复制或宣称验证完整 taxonomy。上表的 skip/信息不足处理是本调研对当前测试的建议。退出 137 也不能单独证明具体 OOM 原因，需保留容器/安装阶段证据后判断。

建议验收写成：隔离数据与版本固定 → 验证配置、目标 HEAD 和 origin；一次受控变更 → 验证相同任务的 Store/API/真实 SSE/原页面 DOM；刷新 → 验证同一状态仍在；收据归档 → 验证 mandatory 用例已执行、首次结果未被重跑覆盖、证据路径可读。这些仍是待实施验收条件。
