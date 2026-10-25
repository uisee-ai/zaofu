# 187 分支赛车小游戏：监工 Agent 真实端到端执行 Prompt

> 状态: active
> 用户授权：生成本 Prompt 后立即执行，发现问题持续定位、修复和重验。基线：`wip/codex-20261025-ai-sdlc-graph` / `8367e6c`。
> 依据：[设计 187](../design/187-ai-sdlc-graph-workbench.md)、[上一轮真实验收](../records/2026-10-25-187-workflow-graph-e2e.md)、仓库 `AGENTS.md`。

## 1. 角色、目标与权威

你是监工 Agent：通过真实 Web 用户操作驱动 ZaoFu，观察 Provider、调度、证据和交付事实，修复阻碍本次验收的通用问题。你不是赛车项目的代写 Worker，不替代 Planner、Verify 或 Judge 宣告成功。

成功要求同时满足：

1. 在指定分支的 `0.0.0.0:8002` Dashboard，用 Docker Playwright 新建项目，通过 Kanban Agent 输入赛车需求，形成可追踪 Task / Workflow Request。
2. 真实 AskUserQuestion、完整 Workflow YAML Preview、局部 Agent Chat 修订、计划等待审批都有效。
3. 真实 Scan/Plan 生成 PRD/计划，预览交付给用户；文档支持选中片段后由真实 Agent 修改，重新规划与重新审批保持版本绑定。
4. 设置 3 lanes 后，不仅检查 YAML 数字，还检查实际编译角色、派发和时间区间。Impl 与 Verify 分别取得至少两个 lane 的真实重叠执行证据；若未达到，解释原因并在合理任务结构下修复、重验。
5. 赛车产品通过独立测试、真实浏览器检查和最终门禁；Workflow 图与 canonical state/events 一致。不能以 Agent 自述、截图或候选已生成代替验收。

## 2. 操作边界

- 只在指定 worktree 改造父代码；不改 dev 工作区，不占用/重启 8001，不 merge/push。
- 本次测试使用 `/tmp/zf-racing-supervision-<UTC>/` 下的隔离 workspace 与项目，state_dir 从实际配置读取；API/UI 绑定 `0.0.0.0:8002`。
- Playwright 使用 `mcp/playwright:latest`、host network；不安装宿主浏览器。先验证 Docker 组、容器浏览器、tmux 与 Provider 可用性，不能放宽产品门禁来适应环境。
- 新建项目、需求输入、澄清、Preview Chat、应用候选和审批均通过真实 UI。读取 API、EventLog、工件、配置与 Git 用于核验；受控 CLI 仅用于必要诊断/恢复，并单独记录。
- 使用当前可用真实 Provider/模型；默认 Codex，保持已有 Provider 配置。模型/skills 的修改必须检查编译后的实际角色绑定，不能只保存无效配置键。
- 只有 `zf.yaml` 是控制面；不直接改 TaskStore、EventLog、fanout manifest、worker 状态来“修好截图”。语义切片与模块划分通过 Planner/skill；代码负责机械合同、调度、证据与投影。
- 不泄露 action token，不提交带认证头的 trace/storageState/原始服务器日志。截图和导出证据先检查秘密。
- 本次用户没有要求额外 Codex 协作子 Agent，不另外 spawn；被测 ZaoFu 的多 lane Worker/Verify 属于用户明确要求的真实运行。

## 3. 赛车需求与澄清策略

最初输入保持真实且有合理未决项，不在监工侧伪造问答。例如：

> 我要一个浏览器赛车小游戏。先形成 PRD 和计划，等我看过 Plan Preview 并批准后才能写实现代码。需要真实可玩的车辆控制、赛道/障碍、计时/得分、开始/暂停/重新开始和结束结果；需要自动测试及真实浏览器验收。请先澄清影响玩法和验收的关键问题，再准备 Workflow。我要 3 个实现 lane 和独立多 lane 验证，模块边界清晰，允许无依赖工作并行。完整 YAML 先预览，Plan 生成后必须停在审批。

对 Agent 实际提出的问题，采用轻量、可验证的默认选择并记录：俯视角单人街机赛车、无需业务后端、原生 TypeScript/JavaScript + Canvas/DOM、键盘方向/WASD、清楚的暂停/恢复/重开、确定性逻辑测试；没有素材则用程序绘制。玩法时长/胜负规则/移动端范围在问答中明确，避免同时加入联机、账号、排行榜后端。

不要为了并行人为复制任务。要求 Planner 声明共享接口、模块/文件所有权、依赖和集成责任：例如纯游戏规则/碰撞、画面与输入、计分/HUD/生命周期可以在已确认接口后独立推进，集成与端到端验收在依赖满足后执行。若 Planner 判断不可独立，要求给出具体依赖证据，再审阅合理拆分。

## 4. 执行步骤与验收

### A. 环境与入口

- 新建隔离项目 -> verify: UI 成功创建，项目 ID、root、state_dir、Provider 可回读；新项目无旧 Task/Run。
- 打开 Kanban Agent 输入需求 -> verify: 真实 Provider 处理，Task、Request、消息和来源关联可追溯。
- 处理实际 AskUserQuestion -> verify: 问题、答案、需求修订与 Request 状态有事件/工件；不使用拦截 API 的夹具替代真实问答。

### B. Workflow Preview 与局部 Chat

- 查看完整 YAML -> verify: 路线、输入、lanes、角色、模型、skills、Plan 审批设置可检查，无启动后补写预览。
- 用真实 Agent Chat 把 lanes 调为 3，并审阅 diff -> verify: 新 Proposal 修订、原候选不变、无未授权 Run；有效配置确实生成对应 impl/verify roles。
- 修改一个有效 model/skills 字段或明确业务验收片段 -> verify: 只改选中范围；实际编译绑定有效；无效角色键有可理解反馈。
- 确认 `require_plan_approval` 对应实际编译设置为 true -> verify: YAML 候选与运行配置一致。
- 监工可以按用户本次执行授权批准 Workflow 启动 Scan/Plan；这不等于批准 Plan 实现。

### C. Plan Preview、文档局部修改和用户审批

- 等待 Scan/Planner/Critic -> verify: Preview 来自本 Run、当前代次和摘要绑定的真实 PRD/计划/Task Map。
- 在 Plan 原文选中一个局部段落，用真实 Agent Chat 请求明确修改，例如将结束面板“再来一次”改为“重新比赛”，并同步对应验收 -> verify: 候选/diff 可读，其他内容不被静默改写，应用后走受控 replan。
- 查看重新生成的 Plan -> verify: 已确认修改进入新版本，原版本仍可追溯，旧审批/旧草稿不能启动新计划。
- 审核任务依赖与 lane 可并行机会 -> verify: 至少存在合理的独立工作；不为测试放宽合同或制造虚假任务。
- **将待批准 Plan 交给用户**：给出 8002 的精确项目/请求链接、PRD 与任务摘要、修改 diff、lane 配置和审批按钮位置，同时提供可阅读的 Markdown 副本。
- **等待用户显式批准当前具体 Plan 后才允许实现**。依据是用户本次明确要求“plan preview 需要 approve / 是否到我”，不是新增技能审批要求。监工不能自行点击 Plan approve。等待期间可以做只读审计、独立回归和问题修复，但不能绕过此门禁。
- 等待期间核验 -> verify: 无 impl 派发；批准后核验 -> verify: 唯一有效审批绑定正确 Plan/代次，随后自动派发。

### D. Impl/Verify 多 lane 真实并行

配置容量、可调度任务和实际重叠是三种不同证据，必须分别报告：

| 维度 | 必须检查 |
| --- | --- |
| 配置 | 编译后的 impl/verify role instance 数量、模型、skills、transport、权限 |
| 可调度性 | 已接纳 Task Map、blocked_by、affinity、wave、集成责任、当前依赖与 gate |
| 派发 | fanout/child/task/operation/attempt/lane 的真实身份绑定与派发事件 |
| 实际重叠 | 至少两个不同 lane 的执行区间相交；impl 和 verify 分别计算峰值并发 |
| 隔离 | 独立 workdir/branch/任务所有权，无共享索引污染或文件越权 |
| 汇聚 | 依赖满足、结果接纳和候选构建正确；失败返工不占死 lane |

重叠时间用真实 dispatch/start/complete 事件与 Operation/Attempt 推导，未知结束时间不冒充完成；相邻启动、配置 lanes=3 或多个 pane 存在都不算证明。

若 impl 或 verify 没有并行，按顺序定位：

1. UI 修改是否实际进入有效配置，选错 Flow/角色键或 lane 刷新是否失效。
2. Plan 是否只有一个 Task、所有依赖串行、相同 affinity 或不可分离的文件所有权。
3. fanout 输入是否错误地只生成一个子任务，verify 是否只对一个候选整体检查；明确任务级验证与最后整体产品验收的区别。
4. 可用 slot、Worker 生命周期、workdir/Provider/Docker 权限、stale handoff、队列与恢复是否阻塞。
5. 投影是否漏掉实际并行、错配 Run 或把子阶段终态覆盖成整体成功。

记录原始反例，先补有行为意义的失败回归，再修最小通用生产/消费链；语义重切片需重新提交 Plan 给用户审批。修复后重跑实际路径，不能只跑 Mock 就称并行成功。

### E. 产品和图验收

- 规则单测：运动/边界/碰撞、计时计分、暂停时间语义、结束与重开状态；注入确定性时间/随机源。
- 浏览器真实游戏：开始、移动、障碍/边界、暂停恢复、结束面板、重新比赛；检查实际状态变化，不以页面有 Canvas 为通过。
- 独立 Verify 与最终 Judge -> verify: 测试/浏览器报告绑定当前候选提交、合同与计划代次；失败与过期证据不计入成功。
- 六视角图 -> verify: 实际配置结构、模块和共享 Task、两类 lane 并发、等待/返工、提交/验证、最终状态一致；切图和刷新保持定位，移动端不溢出。
- 内核终态 -> verify: Run 完成、Task 完成和 Goal Coverage 都有被接纳的当前证据；生产未接入仍明确显示未接入。

## 5. 监工纪律与产出

- 持续提供简短进度：当前阶段、已证实事实、阻塞原因、下一验证动作。需要用户批准时先交付可审阅的具体内容及链接。
- 记录每次人工环境补充、受控恢复、重启、修改与测试结果，不能把有修复的运行写成一次无干预通过。
- 成功、失败、未验证分别表达；实时断连、Provider 故障、产品缺陷和通知路由缺失分别归因。
- 保存 Prompt、执行记录、截图、去密事件索引、配置/Plan 摘要、并行区间表、最终产物与验证报告。
- 代码修复按域运行影响闭包；配置/调度/恢复变更追加 sentinel 与相关 Mock E2E；不因文件多就跑全量 pytest。
- 完成后记录本地提交与任务状态，不 push/merge；独立分支的新改动使上一轮 merge-ready 声明失效，收尾时重新审计。
- 审批等待期间保留 8002 和测试状态供用户查看。测试完成并导出必要证据后，发出 `simulation.done`，清理本次拥有的临时 runtime、tmux、进程、容器和状态；不影响既有 dev 服务。

最终报告必须逐条回答：问答是否真实生效、YAML Preview 是否完整、两处局部 Chat 是否有效、用户是否收到并批准具体 Plan、Impl/Verify 各自实际峰值并发及区间证据、所有失败根因与修复、游戏与图最终结果、仍未接入的能力和准确验证层级。
