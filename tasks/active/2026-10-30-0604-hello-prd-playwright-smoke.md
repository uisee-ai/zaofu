# Hello World PRD 真实浏览器闭环验证

> 状态: active；用户已批准补齐 V5 受控装配入口并重跑真实浏览器验收。

用户授权：通过 Playwright 创建任务、提交并执行最小 Hello World PRD。基线为 dev / b33b6b9f，固定使用 zf-prd-v5 对应的完整 PRD 路线。

## 需求

- `python hello.py` 输出 `Hello, world!`。
- `python hello.py --name ZaoFu` 输出 `Hello, ZaoFu!`。
- 两种调用退出码为 0，提供自动化测试，仅使用 Python 标准库，一个实现任务。

## 验收

1. Docker Playwright 创建隔离项目并在 Kanban Agent 提交需求 -> verify: 页面、项目与 Task 身份和真实 Provider 事件对应。
2. 浏览器审核配置并启动 v5 PRD -> verify: 展开配置与批准、Run 绑定一致，未降级为 light/direct。
3. 观察 Scan、Plan、Critic、OA、Impl、Task Verify、Candidate Verify、Discovery、Judge -> verify: 实际执行与证据接纳链闭合。
4. 核验产物 -> verify: 对最终交付 commit 独立运行两条命令及自动测试。
5. 核验页面 -> verify: Workflow 图与任务、Run、交付证据一致。
6. 保存去密证据并清理 -> verify: 无本次拥有的临时进程残留，8001 不受影响。

本轮单任务不验证多 lane 并行或同 session 休眠恢复。不得以截图、Agent 自述或 mock 通过代替真实交付。

## 隔离与执行边界

- 运行根目录：`/tmp/zf-hello-prd-20261030-060443/`。
- Web 端口：8002；项目、Task、配置确认和启动均通过 Playwright 页面操作。
- 只读 API、事件、Provider 和 Git 查询用于验证；任何额外恢复必须记录。
- 不代写 Hello World 产物，不修改 canonical runtime state，不推送或合并。

## 本轮结果

以下为首次阻塞记录；保留原证据。本次续作增加注册控制器选择、摘要绑定、项目适配、预览/批准接线及针对性回归；通过后使用新隔离项目重跑下述链路。代码只在当前 worktree 修改，不合并或推送。

实现说明：[注册控制器受控装配](../../docs/impl/registered-controller-assembly.md)。

- 项目 `project-b8ad9c68`、Task `TASK-5CB1FC` 已通过 Playwright 创建。
- Request `prepare-TASK-5CB1FC-bcace73e47b63f32` 经真实 Codex 合成后停在 `clarifying`。
- 确认阻塞：当前合成合同不能绑定 v5 模板，当前项目只允许 `direct-v1`，缺少 v5 要求的 `bounded-direct-v1`。
- 无可批准 YAML、无 Run、无 Impl / Verify / Judge 业务执行、无 Hello World 代码交付；不将本轮标记为成功闭环。
- 详细报告：[真实浏览器验收记录](../../docs/records/2026-10-30-hello-prd-v5-playwright-smoke.md)。


## 续作：V5 受控装配入口

- 注册目录加入显式 opt-in 的完整控制器，Synthesis 选择 ID + digest + 项目验证命令；能力扩展限于被选中的包。
- 项目身份、状态目录、PRD、Git 分支、工作目录 provision 和本地 skills 由受控编译绑定；保持完整 V5 门禁和生命周期。
- Profile 内联，缺失技能冻结为内容寻址资产；Preview / Apply 校验配置、技能与摘要。
- Playwright 实测暴露两个接线问题并修复：Apply 后 tracked zf.yaml 使交付基线变脏；整包技能源与项目已有技能重名。现在精确 checkpoint 配置、重试重验 Apply、只装配缺失技能。
- 新隔离根目录 `/tmp/zf-hello-v5-assembly-20261030-0654/`，第二项目 `project-v2-aa435286`，Task `TASK-FC280E`，Request `prepare-TASK-FC280E-35d3f2786e69bf23`。首次失败项目与事件保留用于审计。
- 验证：controller / checkpoint / config-apply 32 passed；修订后 request / catalog / V5 / registered-controller 63 passed；synthesis / proposal / prepare / Web / intake mock E2E 67 passed；sentinel 51 passed；前端 build / typecheck / model tests 通过。
- 并发测试曾触发 enqueue 小于 1 秒的计时断言；移除目录重复解析后，按串行运行该边界整组通过，未放宽断言。真实 Provider 结果独立记录，不据此宣称交付完成。
