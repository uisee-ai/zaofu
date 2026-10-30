# Hello World PRD 真实浏览器闭环验证

> 状态: defer；触发条件：补齐 v5 的受控配置选择、项目适配和执行 Profile 准入后，重跑本任务的真实浏览器验收。

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

- 项目 `project-b8ad9c68`、Task `TASK-5CB1FC` 已通过 Playwright 创建。
- Request `prepare-TASK-5CB1FC-bcace73e47b63f32` 经真实 Codex 合成后停在 `clarifying`。
- 确认阻塞：当前合成合同不能绑定 v5 模板，当前项目只允许 `direct-v1`，缺少 v5 要求的 `bounded-direct-v1`。
- 无可批准 YAML、无 Run、无 Impl / Verify / Judge 业务执行、无 Hello World 代码交付；不将本轮标记为成功闭环。
- 详细报告：[真实浏览器验收记录](../../docs/records/2026-10-30-hello-prd-v5-playwright-smoke.md)。
