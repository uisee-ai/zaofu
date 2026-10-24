# 184：真实入口与交付验证

本批按用户批准的三步执行：冻结环境与验收、真实 Provider 场景、失败驱动的最小改进。
入口语义背景见 [184 Intake 交接记录](184-intake-semantic-routing.md)。
本记录区分真实模型选路、受控 API 批准、Workflow 执行和独立产品验收；前一层通过不代表后一层通过。

## 环境与可复核证据

- 开发 worktree：`/home/min/workspace/public/184`。
- 冻结基线：`5dad3cb7`，独立 checkout 位于 `/tmp/zf-184-live-20261023T120000Z/harness`。
- 原始报告：`/home/min/workspace/public/zaofu-evidence/184-live-20261023T120000Z/`。
- Provider：Codex CLI `0.163.0`，请求 `gpt-6-astra` / `high`；ChatGPT 登录。
  请求模型与 Provider 实际报告字段分开保存，未报告的 token/成本不记为零。
- `registration.json` 固定案例摘要、源码、技能、目录和预算；每次改变场景保留独立登记与结果目录。
  语义用例每轮最多 13 个 turn，每 turn 180 秒；交付运行 1800 秒、单角色 600 秒。
- 通过真实原生 Provider 调用和 FastAPI `TestClient` 走生产 API；这不代表浏览器 UI E2E。
  产品是标准库 CLI，没有浏览器产品验收。未 push 或 merge。

基线两轮来自不可变 checkout；第三轮来自迭代中的 184 候选工作树，期间新增了取消入口修复，
不是整个仓库的不可变 commit 对照实验。第三轮直接使用的评估器、headless Agent、路由提示词和技能保持基线内容，
配置加载器使用本批提前拒绝修复；相关摘要另存 source audit，不据此作模型或版本优劣比较。

## 可重复入口

`tests/e2e/intake_live_validation.py` 是显式启用的真实运行器，不纳入默认 pytest：

```bash
PYTHONPATH="$HARNESS/src" python tests/e2e/intake_live_validation.py prepare \
  --root "$RUN_ROOT" --harness "$HARNESS" --evidence "$EVIDENCE"
PYTHONPATH="$HARNESS/src" python tests/e2e/intake_live_validation.py kanban \
  --root "$RUN_ROOT" --harness "$HARNESS" --evidence "$EVIDENCE" --confirm-real
PYTHONPATH="$HARNESS/src" python tests/e2e/intake_live_validation.py channel \
  --root "$RUN_ROOT" --harness "$HARNESS" --evidence "$EVIDENCE" --confirm-real
```

目录变量必须指向独立测试项目；`--confirm-real` 同时启用测试用 Task/路线提案批准，不能指向生产项目。
准备阶段建立有 Git 基线的产品、配置摘要和预检报告；使用标准 Issue/PRD 流程，保留 blocking 验收。
需要在产品目录运行真实 `zf start` watcher 才能消费启动请求和执行恢复。
`--simulation` 只接受直接位于 `/tmp/zf-*` 的项目根；本轮产品目录嵌套，改用有总时间上限的普通 watcher，结束时单独记录模拟终态并清理。
Channel 阶段只保存模型实际生成的 PRD；必须阅读完整产物后才能由测试操作者确认，不能自动替模型宣称通过。

独立产品 oracle 位于 `tests/e2e/intake_live_acceptance.py`，运行前副本和摘要保存到外部证据目录，
不交给实现角色。Issue 种子缺陷已产生实际红测；PRD 验收包含金额、Unicode、退款、非法输入、真实 CLI 退出码及文件不变性。

## 已观察到的结果与边界

| 场景 | 观察 | 解释 |
| --- | --- | --- |
| 首轮固定 12 例 | 9 通过、3 未通过 | 缺少登录/订单源码，模型要求补充可复现输入；不能改提示词强迫其少问问题 |
| 补齐源码后第二轮 | 12/12 通过 | 标签、模型、技能和评分器不变；只证明决策和目录绑定，不证明交付 |
| Kanban Issue | 真实建单、路线 Plan、启动批准、重复批准仅一次 invoke | 实际执行与独立验收另计 |
| 原轻量 Issue | `run.goal.blocked` | blocking Product Acceptance 的必需产物无人生产；保持失败，不降低验收模式 |
| 标准 Issue | triage 首次提交需人工补 Enter；600 秒角色预算耗尽，`run.goal.blocked` | 未完成实现/Verify/Judge；保留 `operator-intervention.json` 和终端记录 |
| 原 Channel | 180 秒超时，记录 `delivery_unknown` | 未生成可确认 PRD，没有伪造成功或确认 |
| Channel 重试 | 新请求被接受但被旧未知结果阻止调度 | 属于入口与调度条件不一致；新增提前阻断 |

第二轮的混合目录也含上述不兼容轻量组合，因此进一步登记标准 Issue + PRD 的可执行目录，
预检为 GO；在候选源码上重复固定 12 例，12/12 通过。结果单独存入 `semantic-round3/`，不覆盖第二轮。
新 Channel 使用新的讨论身份，保留旧请求未知状态；生成预算显式登记为 600 秒，不能与 180 秒首轮直接比较耗时。
新 Channel 实际生成了 canonical PRD，保留完整原需求和业务 AC；Owner 评审发现交接的
`explicit_non_goals` 包含“本轮实现、建单或工作流启动”，将流程审批说明混入产品非目标。
已调用 `channel-consensus-block` 驳回，未确认、建单或启动该 PRD 的 Workflow。
尚未证明 Planner/Critic、Plan approval、实现、Verify/Judge 和独立产品验收闭环。

一个“过期提案”实验只改变了不参与路线绑定的返工配置、没有改变 Task 合同或路线，实际获得接受并新增 invoke。
该实验的断言错误保存在 `faults/stale-proposal/`，不能当作过期拒绝通过，也不能据此声称绕过了版本门禁。
随后从真实建单事件恢复当时的 Task 和模型 Plan，分别注入合同变化与路线变化；受控 API
返回 `workflow_task_stale` / `workflow_route_stale`，均未新增 invoke。
证据位于 `faults/stale-{task,route}-replay-round3/`；这是实际模型输出的日志重放，不是再次实时调用模型。

## 本批改进

1. `IssueFlow` / `PrdFlow` 的 `topology: light` 不支持生产 `product_acceptance_spec`，
   因此现在在配置加载时拒绝 `productAcceptanceMode: blocking`（包含蛇形别名）。
   需要该验收时使用 `topology: fanout`。没有自动降为 shadow/off，也没有由内核编造语义验收产物。
2. 已失败且交付结果未知的 Channel 成员仍受调度隔离。
   `channel-synthesis-request` 现在返回 `409 provider_delivery_unknown`，标明原 reply，
   避免创建永远等待的新请求；投影也不再提供可重新生成的状态。
   未把未知结果改为已取消/成功，正常 timeout 耗尽后的重试仍可用。
3. 运行器增加配置预检、完整源码场景、原始请求/响应保存、独立 oracle、按 Channel 区分幂等键，
   并在 Provider 已失败时提前结束等待。
4. 真实取消复现 Kanban 公共 turn ID 与 Provider attempt ID 不一致：旧入口返回已记录取消，
   却找不到实际进程。现在通过现有 `AgentTurnStore` 绑定当前 attempt，先持久化取消、隔离迟到结果，
   再停止进程；线程不匹配时拒绝操作。Channel 的直接 run ID 路径保持原有机制。
   候选源码的真实 Codex 复测返回 `process_found=true`、`process_terminated=true`；
   新建应用实例后仍为 cancelled，Provider 启动次数保持 1，未创建任务。

原轻量配置已在候选源码上复测为调用前拒绝。
Channel 修复使用原始失败日志的前缀通过 `EventWriter` 重放，实际受控 API 返回未知结果阻断且不新增 synthesis。
这是日志重放回归，区别于再次调用模型。

## 验证记录

- 第一组影响闭包：104 passed（配置、控制器 mock E2E、Task Plan、Channel 恢复、进程取消和总截止时间）。
- Channel 修复组：73 passed（受控重试、类型合同、投影、文件 PRD 和路由）。
- 取消调用闭包：16 passed；补充线程不匹配及旧入口兼容检查 2 passed；Store/路由合同 31 passed。
- 最终自动层：Web typecheck/unit 通过、sentinel 51 passed、flow smoke 10 passed。
- `dev-verify.py plan --base dev` 因整条分支累计变更选中 507 个测试文件并升级全量；
  本次遵循领域闭包，明确跳过自动全量/serial，以上述精确测试及后续必要调用者回归替代，其他自动层照常运行。

真实交付未闭合前，交付任务保持 active；不能把本文件中的配置修复、模型路由通过或 mock E2E 当作产品已交付。

## 后续闭环条件

- 原生 tmux 输入提交必须有接收证据，不能把已发送按键当作模型已开始，也不能盲目重复发送任务。
- PRD 作者方法需要把审批阶段限制与产品非目标分开；修订同一原始需求后重新评审。
- 上述阻塞解决后，以新的冻结登记重跑 Issue/PRD 全链路，再执行外部 oracle；原始红测和预算失败不删除。
- 本轮 API 验证没有覆盖浏览器点击、页面刷新或多窗口行为；这部分仍需 Docker Playwright 单独验收。

本轮运行进程和 tmux 已清理。产品、未提交角色产物、事件和 sidecar 先归档再清理，
归档摘要见证据根的 `archive-manifest.json`；排除认证 home 和私有 token。
基线源码 checkout 保留供复核。历史绝对路径记录为运行时位置，不能直接把归档当作可恢复生产状态。
