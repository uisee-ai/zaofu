# Hello World：V5 受控装配与真实浏览器续测

关联：[任务](../../tasks/active/2026-10-30-0604-hello-prd-playwright-smoke.md)、[入口实现](../impl/registered-controller-assembly.md)、[首次阻塞记录](2026-10-30-hello-prd-v5-playwright-smoke.md)。

## 范围与结论

受控入口已补齐并经过真实运行；本轮在 Task Verify 证据提交前触发预算门禁，**未完成端到端交付闭环**。最小产品仅为标准库 Hello World CLI 和非空 unittest；完整 V5 路线与门禁保持不变。单实现任务不证明多 lane 并行或休眠后同 session 恢复。

实现 commit：`e2bad542 feat: assemble registered controllers through workflow approval`。
图修复 commit：`1ec1c5cf fix: bind task pipeline graph stages to admitted generations`。
代码位于隔离分支 `wip/codex-20261030-hello-prd-smoke`，没有合并或推送。

## 真实执行身份

| 项目 | 值 |
| --- | --- |
| 隔离目录 | `/tmp/zf-hello-v5-assembly-20261030-0654/` |
| UI | `http://127.0.0.1:8002/`，服务监听 `0.0.0.0` |
| 浏览器 | Docker `mcp/playwright:latest`，host network |
| 新项目 | `hello-v5-final` / `project-v2-aa435286` |
| Task | `TASK-FC280E` |
| Request / Run | `prepare-TASK-FC280E-35d3f2786e69bf23` |
| Proposal digest | `708bd77ab4b76460d8f72b1c3ffe16244ccb4878f2e82ebfd5f77c62cfe481bc` |
| 配置 checkpoint | `31e7444 chore: apply approved workflow configuration` |
| Controller | `zaofu-prd-v5-production-codex` |

项目创建、Kanban 消息、Task 确认、完整 YAML 审核和 Start Workflow 均由 Playwright 页面操作。只读事件、Git 和配置核验用于诊断；没有手写 Hello World 业务代码或伪造运行状态。
新项目创建不会自动切换当前 project；脚本曾把相同需求发到旧项目，Agent 识别已有 Task、没有重复创建。之后通过 Project 下拉框选中 `hello-v5-final`，确认 URL 后重新提交。

## 本轮发现并修复的入口问题

1. V5 缺少完整控制器受控选择路径。新增 metadata opt-in、ID/digest 绑定、能力闭包、项目适配与精确候选批准。
2. 首次批准后，已跟踪的 `zf.yaml` 变脏，被正常 Git 交付基线检查拦住。Apply 现在只 checkpoint 精确批准的配置，并保留 hooks、CAS、幂等重试和干净 index 检查。
3. 整包控制器技能来源与项目已有技能重名，严格校验使 runtime 启动失败。现在只快照缺失技能，Preview 使用启动同款技能校验，Apply 核验资产 manifest/digest。
4. Start 重试曾命中 HTTP 旧响应缓存。前端让外层 HTTP 请求使用新键，内层 Apply 仍按 proposal digest 幂等，确保真正重验和补全 checkpoint。

失败项目 `project-35e00770` / `TASK-634FC1` 的事件与启动日志保留；没有把失败 Run 改写成成功。

## 真实阶段证据（UTC）

| 时间 | 观测 |
| --- | --- |
| 07:35:11 | 浏览器 create-task，生成 `TASK-FC280E` 与 Workflow Request |
| 07:36:22 | 真实 Codex Synthesis 结果被接纳 |
| 07:36:28 | 完整 V5 config-change proposal approvable，无 blocker |
| 07:36:57 | 浏览器 Start Workflow，精确配置 Apply/checkpoint |
| 07:36:59 | Request approved/submitted，runtime 自动启动 |
| 07:37:04–11 | 28 个 on-demand 角色登记 dormant，不启动 Provider |
| 07:37:14–19 | 常驻 OA / Run Manager、运行时与 Scan 调度建立 |
| 07:37:24 / 30 | product-scan / tech-scan 按需 ready |
| 07:39:02 / 07:40:13 | 两个 Scan child 结果独立被接纳 |
| 07:40:14–20 | Scan 汇总完成，Planner 按需启动，页面同步显示 Plan 执行中 |
| 07:42:52 | Planner 工件通过校验并被接纳；两次脚本路径检查拒绝后，Agent 自行改用允许的 apply_patch，无人为绕过 |
| 07:45:05–09 | Critic 接纳、Task Map ready、OA pre_impl 开始；门禁期间 Impl 未提前启动 |
| 07:47:16–23 | OA 决策接纳并应用；单任务 HELLO-001 分配到 dev-lane-0 |
| 07:51:24 | Impl 结果接纳，源提交为 272aadc1 |
| 07:51:40 | verify-lane-0 独立执行 Task Verify |
| 07:53:57 | dev-lane-0 按空闲策略 suspended，preserve_session / preserve_workdir 均为 true |
| 07:54:15 / 07:55:40 | tech-scan / product-scan 按空闲策略 suspended |
| 07:55:58–07:56:05 | Verify 实际运行 2 项 unittest 和两条 CLI 命令，均通过 |
| 07:57:14–17 | Verify 操作超过 token 上限，被 Kernel 阻塞，Run 同步阻塞 |
| 07:58:12 | Planner 空闲后 suspended |


## 自动化验证

- `tests/test_workflow_registered_controller.py` + `tests/test_workflow_config_checkpoint.py` + `tests/test_workflow_config_apply.py`：32 passed。
- `tests/test_control_actions_workflow_request.py` + `tests/test_profile_catalog.py` + `tests/test_project_profile.py` + `tests/test_prd_v5_config.py` + registered-controller：63 passed。
- synthesis / synthesis-support / proposal / prepare / Web proposals / intake semantic handoff mock E2E：67 passed。
- `bash scripts/dev-premerge-gate.sh`：51 passed。
- 前端 build / TypeScript / model unit tests，以及 `dev-verify run` 的 web tier 通过；ruff、diff whitespace、skill 三份同步检查通过。
- 上述集合有重叠，不累加为独立用例总数。并发回归一次出现 enqueue 小于 1 秒的计时断言失败；移除注册目录重复解析后，该边界整组串行通过，未放宽阈值。
- `dev-verify plan --base dev` 的保守集合超过 80 个测试文件而升级全量；按仓库规则采用上述模块、直接调用方、控制动作、配置与 Web 边界闭包，没有运行全量 pytest 或覆盖率。

真实 Provider / Playwright 与 mock E2E 分开认定；后者不能替代真实业务交付。

## 预算阻塞与证据边界

Kernel 事件 `evt-4bc9bca87860`（`workflow.budget.exceeded`）记录：

| 计量 | 值 |
| --- | --- |
| 作用域 | 单个 Verify operation |
| 上限 | 1,500,000 tokens / 900 秒 / 60 usage samples / $8 |
| 本次计量 | 1,510,948 tokens / 334.009 秒 / 21 samples |
| 新输入 / 输出 | 51,770 / 2,922 tokens |
| 缓存读取 | 1,456,256 tokens，约占累计 96.4% |
| USD | 21 条 unpriced entries；$0 计量不代表免费或实际账单为零 |

上限来自 `examples/prod/controller/common/profiles.yaml` 的 `bounded-direct-v1`，被候选配置与
operation 快照绑定。没有修改预算、重置计量或伪造 Verify 通过。`evt-8e50462640b9` 阻塞该操作，
`evt-474e72f627b4` 为 `run.goal.blocked`。没有 `test.passed`、`candidate.ready`、`judge.passed`、
`run.delivery.completed` 或 `run.goal.completed`；Candidate Verify / Discovery / Judge 未执行。

因此本轮证明了受控装配、浏览器批准、自动启动、按需角色激活、Scan/Plan/Critic/OA/Impl 接续、
独立 Verify 的实际测试和空闲休眠；没有证明最终交付、多 lane 并发或休眠后的同 session 重启。

建议先维持预算，优化 Verify 的上下文体积与必读工件的批量读取，再经新测试验证。提高测试预算
需要新的受控配置预览；本轮已向用户提出这两个后续选项，尚未取得选择。当前 Run 不可直接改写。

## 产物独立核验与图修复

从已接纳的 Impl 源提交 `272aadc1a28128431a14adb9168fcca944923791` 导出两份产品文件到隔离验证目录，
独立执行 `python hello.py`、`python hello.py --name ZaoFu` 和要求测试数大于零的 unittest discovery。
输出分别为 `Hello, world!` / `Hello, ZaoFu!`，退出码均为 0，2 个测试通过。此提交尚未成为冻结候选，
该核验不能替代未完成的 Runtime Verify / Judge。

真实 UI 暴露 Task Pipeline 操作没有归入编译 DAG 阶段的问题，已按 Kernel generation 绑定修复；
原始 parent_stage_id 保留，跨 Run / generation / 非 Kernel 声明不能冒领阶段。Impl 调用结束后，
容器等待该 generation 的 candidate.ready；不提前宣称流水线结束。

图修复验证：后端图 / 状态 60 passed，直接 Web / cache / terminal / preview 调用方 50 passed，
两个前端 model 测试及 build/typecheck/bundle check 通过，sentinel 51 passed。Playwright 重载后
断言 Impl 和 Task Verify 均属于正确容器、没有重复的独立 Impl 角色、阻塞终态可见、浏览器错误为零。
测试集合有重叠，不累加为独立用例数。

## 证据文件

证据目录：[hello-prd-v5-controlled-20261030](hello-prd-v5-controlled-20261030/)。

- [完整 V5 YAML 预览](hello-prd-v5-controlled-20261030/10-v5-yaml-preview.png)
- [Scan 实时执行](hello-prd-v5-controlled-20261030/12-scan-live.png)
- [Plan 自动接续](hello-prd-v5-controlled-20261030/13-plan-started.png)
- [修复后预算阻塞图](hello-prd-v5-controlled-20261030/15-budget-blocked-graph.png)
- [Kernel 终态结果](hello-prd-v5-controlled-20261030/16-kernel-blocked-result.png)
- [浏览器断言](hello-prd-v5-controlled-20261030/browser-graph-assertions.json)
- [产品独立检查](hello-prd-v5-controlled-20261030/independent-product-check.json)
- [筛选运行事件](hello-prd-v5-controlled-20261030/selected-runtime-events.json)
- [浏览器变更请求轨迹](hello-prd-v5-controlled-20261030/browser-mutations.jsonl)：只保留时间、method、path、status，不含认证或请求正文。

其他观测：原 UI 顶部 Task 仍显示“未绑定”；Raw 的数值 token budget 会被过度脱敏；两次消费者
锁竞争超时引发了诊断请求；无 Feishu approval route 时 owner.visible_message.failed 不影响
浏览器阻塞结果。这些没有被本轮入口和图归属修复掩盖，也未据此扩大改动。

## 清理

两个临时项目已分别通过 CLI 记录 simulation.done；正常 zf stop 停止 owned runtime、tmux 和
Run Manager，浏览器及 Docker 容器退出，8002 Web 停止。8001 保持运行，未由本任务重启或修改。

运行状态归档为临时目录中的 0600 私有压缩包，排除 Provider homes、凭据和 .env；验证归档完整性
后删除活动项目/工作区状态、Web token 和原始 Web 日志。去密证据保留在本报告目录。
[清理凭据](hello-prd-v5-controlled-20261030/cleanup.json) 记录归档 SHA-256 与进程/端口检查。
