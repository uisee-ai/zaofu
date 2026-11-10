# PRD → Workflow → 完整 Plan 的 UI 验证

适用于用户要求模拟 Kanban Agent 或 Channel Group 讨论需求并查看规划结果。
两入口分别执行、分别签收；创建 Group 可以从 Kanban 的 Channel setup UI 发起，
但 Channel 的需求讨论和 PRD 确认必须在 Group 内进行。Kanban 独立路径不得转交 Group。

## 执行

从候选 checkout 运行，先构建一次 Web，再以不同临时项目运行两条路径：

```bash
PYTHONPATH=src:. .venv/bin/python tests/e2e/prd_ui/run.py --provider fake --journey kanban
PYTHONPATH=src:. .venv/bin/python tests/e2e/prd_ui/run.py --provider fake --journey channel --skip-build
```

fake 层仅替换模型：Kanban 返回对话/提案，规划 worker 消费实际派发的 operation、
读取绑定输入，通过 `SemanticResultSubmitService` 提交。不得预置目标 PRD/Plan、
伪造审批成功事件、直接修改 TaskStore 或绕过 UI 调写 API。

fake 通过后，在用户授权真实 Provider 执行的范围内，以新项目验证：

```bash
PYTHONPATH=src:. .venv/bin/python tests/e2e/prd_ui/run.py --provider real --journey kanban --skip-build --timeout 1800
PYTHONPATH=src:. .venv/bin/python tests/e2e/prd_ui/run.py --provider real --journey channel --skip-build --timeout 1800
```

需要 Docker `mcp/playwright:latest`、前端依赖、tmux 和真实层的 Codex 登录。
脚本使用 `/tmp/zf-prd-ui-*`，保留证据、清理自己启动的进程及独立 tmux socket。
端口分配串行锁定，启动后核对返回的 Project root，防止并行测试串项目。
不使用 8001，不安装宿主浏览器。`--skip-build` 仅用于当前候选已经构建的情况。

## 模拟用户与成功条件

初始小需求：从项目根目录使用系统已有 `cat README.md`，文件可读、缺失时明确报错；
不新增读取程序。讨论阶段只产出 PRD，完整 Plan 留给后续 Workflow，实施等待审批。
Kanban 可能返回多题澄清卡片，需从 UI 逐题回答；不能只等待自由文本回复。
这是控制流程基线，不证明 PostgreSQL、数据导入等产品模块已经实现或验证。
扩展复杂需求时另设输入和验收表，不把业务规则硬编码进 Kernel。

1. Kanban：需求 → 澄清 → 回复 → 实际 PRD 文件 → Owner 确认 → Task proposal。
2. Channel：建群 → 自然讨论 → 显式多视角讨论 → PRD → Owner 确认当前 revision → Task proposal。
3. 两条路径分别批准 Task，进入 Workflow 页面审阅当前 PRD v5 YAML。
4. 在 UI 审阅 PRD v5 与 `workflow.plan_approval: true`，再点击 Start Workflow。
   该选项在测试配置中预先启用，不预置 PRD、Plan 或任何成功状态。YAML 预览与
   生成后的 Plan Preview 是不同验收对象。在线改变已有 runtime 的执行配置另做重载测试，
   不将 `consumer_config_stale` 拒绝误报为模型失败，也不关闭该保护。
5. 等待真实运行时完成 Scan/Planner/配置的 Critic，核对 `plan.approval.requested`。
6. 读取本 Run 的 Plan：PRD/SDD/TDD、任务、依赖、验收映射及证据状态。
   `derived_plan_full` 是生产者声明，还需内容与来源核验；未实施项目保持 NOT_RUN。
7. 刷新、分页和下载检查使用本次生成产物。不得用固定 Plan fixture 代替。
8. 终点为等待 Owner 审批；不批准 Plan、不启动实施来凑齐成功状态。

## 证据与结果

每次运行保留 `evidence/run.json`、阶段截图、Playwright trace/video、当前 checkpoint、
PRD、Plan 和来源绑定，以及独立只读审计。必须区分：浏览器链路、fake 模型、真实模型、
语义质量、基础设施阻塞；fake 通过不等于真实 Provider 验收。

真实模型若提出新增设计确认，先核对是否来自测试输入歧义；补充明确需求仍走 UI，
保存新增用户交互证据，并把 assisted run 与无人干预运行分开标记。不要改 runtime 真相来消除等待。

只读审计必须拒绝错误 Run/Plan、正文截断、摘要冒充完整 Plan、重复启动，以及审批前实施。
真实生成的需求/验收映射仍需逐条核对，不以字符数、固定标题或状态标签替代语义判断。
出现运行失败时保留最初证据，先归因测试脚本/环境/产品，再修复并用新的独立运行验证。
