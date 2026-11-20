# Codex 真实恢复与 Owner UI 验证

本轮基于修复分支 `4a2f93ed`，补充
[恢复修复验证](2026-11-10-prd-ui-recovery-validation.md) 中的真实 Provider 证据。
用户授权真实 Codex、提交、合并 dev 和清理本轮 worktree。Claude Code 不在本轮范围内。
测试使用独立 `/tmp/zf-prd-ui-*` 项目、Docker Playwright，不操作真实 dev 的 8001。

## Channel 原回复恢复：通过

命令：

```bash
PYTHONPATH=src:. .venv/bin/python -m tests.e2e.prd_ui.run \
  --provider real --journey channel-recovery --skip-build --timeout 900 \
  --run-root /tmp/zf-prd-ui-codex-recovery-20261110-2
```

证据：`/tmp/zf-prd-ui-codex-recovery-20261110-2/evidence/`。
Playwright **1 passed（47.2 秒）**，独立 ledger/native 审计通过。
原生会话确认模型为 `gpt-6.1-sol`，effort 为 `high`。

测试 fixture 通过 ControlledActionService 建立两个只读单成员 Channel；用户发消息及
核验重试均从真实 UI 触发，没有 page.route 伪造后端结果。`codex_fault_relay.py` 转发
真实 Codex JSON-RPC，仅在明确故障点中断传输，不生成回复、native 回执或 canonical 事件。
这是 Channel 单回复恢复验收，不是重新运行完整多角色 PRD 综合与交付。

| 场景 | 实际证据 | 结果 |
| --- | --- | --- |
| native turn 中止 | `01a1c607-5f3b-7a81-807e-c7b23fcaa125` 的旧 turn 在 15:15:36.560Z 记录 `turn_aborted`；UI 15:15:39 申请恢复 | 同 request `reply-8d26041fb6f9d043` 从 generation 1 到 2；15:15:52.943Z 完成。原失败保留，native 第二个 turn 确实完成，UI 显示真实回复。 |
| 完成通知丢失 | `01a1c607-b5db-7d33-bb2f-a3a4045ef904` 在 15:16:12.910Z 记录 `task_complete`；relay 丢弃完成通知后断开 | UI 核验返回 409，显示禁止重复投递；同 request 仍仅 generation 1，没有第二次投递。 |

故障元数据、UI 截图、trace/video、API action 响应和 native metadata 分别保存。
`audit-native-completion.json` 补充核对新 turn 的真实完成，仅读取 metadata，不复制推理
正文或认证文件。最终项目 Git worktree 干净，未创建 Task、Workflow 或实施审批。

首轮 `/tmp/zf-prd-ui-codex-recovery-20261110-1/` 保留 failed：测试错误地从轻量事件 API
读取 `failure_status`，该字段未暴露。canonical 账本实际为 `delivery_unknown`，已改为
从 Channel detail 核验。没有修改生产 API 投影来迁就测试，也没有把原失败改成 passed。

## Owner 与阶段授权

执行：

```bash
PYTHONPATH=src:. .venv/bin/python -m tests.e2e.prd_ui.run \
  --provider real --journey kanban --owner-checkpoint --skip-build --timeout 5400 \
  --run-root /tmp/zf-prd-ui-codex-owner-20261110-1
```

场景要求真实 Kanban 生成 PRD，登记 `OWNER-GIT-01`：候选计划定稿前必须向 Owner
确认未来受控 Git 交付范围。Critic 应产生真实检查点，UI 展示同版候选，Owner 回答
当前阶段禁止 Git/实施、未来实施先单独批准 Plan，再按受控流程交付。
完整性要求是同 Run 重规划、只生成一次 invoke、最终完整 Plan 待审批且无 writer 派发。

首段实际执行到 15:37:25Z：Critic 对 `OWNER-GIT-01` 提交语义拒绝，生成
`plan-owner-ef5b47b912d9c831`。UI 正确展示待澄清候选，但浏览器测试错误地要求候选
不能声明 `derived_plan_full`，因此在提交答复前失败。该字段描述正文完整性，不是审批
状态；改为核对候选警示、`replan-approve` action，以及没有最终 Plan 审批/实施事件。
原 `evidence/run.json` 和失败 trace 保留，状态没有改成通过。

随后使用同项目、同 Run 的待处理检查点继续；续跑脚本、实际测试源码及其摘要保存在
`evidence/owner-resume/`。该脚本由当前旅程的 Owner/Plan 段生成，通过正常 `zf start`
恢复 runtime，不伪造检查点，也不重新 invoke。
15:39:41Z UI 答复经 `replan.owner_decision.rejected` 路径提交，拒绝旧候选并要求重规划；
runtime 追加 `plan.synth.owner_decision.resolved`，15:39:47Z 在原 Task/Run 派发新 Planner。
这是答复/重规划，既不是否定用户授权，也不是最终 Plan 批准。

15:54:13Z Critic 从原始决定事件确认时间 `2026-11-10T15:39:41.461712+00:00`，
随后提交通过结果。15:55:09Z 完成综合，最终 Plan 为 `evt-89f7f97fa72f`，正文
17110 bytes，SHA-256 `bf523ca982381d0c1e0b5259b0b8afdb928b466195204d97c440be43a49abbf9`。
浏览器续跑 **1 passed（15.7 分钟）**，核对全文、Markdown 下载逐字一致、刷新保持同版。

续跑末尾的新增审计最初错误要求每个事件都有相同非空 `task_id`；Web 答复和最终
Plan 审批实际上是 Run 级事件。修正为经唯一 invoke、同 checkpoint/resolution、同 Run
关联原 Task，并拒绝任何显式矛盾身份；补充反例后 40 个 fixture/oracle 测试通过。
`owner-resume/run.json` 保留 failed，不覆盖历史。修正后的只读独立审计保存为
`owner-resume/audit-corrected.json`，与浏览器 passed 共同构成最终证据。

审计确认：原 Task `TASK-4B1633`、Run `prepare-TASK-4B1633-6c19107f8e8f29df`，
仅一次 invoke、没有 `plan.approved`，所有派发仅为 reader（product-scan、tech-scan、
planner、plan-critic），且仅一个父 Task。暖交接也通过：旧/新 runtime PID 为
993369/1023891，配置摘要改变，新 consumer 就绪后才发布 invoke。

新观察：Owner 确认 sidecar 不含决定时间，下游必须沿事件 ID 回查。真实 Critic 已完成
核验，所以本轮没有因此阻塞；可后续改善证据传递效率。没有伪造时间或降低 PRD 验收。

## 真实页面发现并修复：当前阶段在视野外

首次只读观察 `progress-planning.png` 发现运行中的规划阶段位于画布外；旧逻辑聚焦
目标/Run/首阶段，并限制最低 0.8 倍，导致节点裁切。现在初始概览优先当前阻塞或
执行阶段，包含 Critic 的 `aggregating` 状态，允许按画布尺寸缩小，并提供
「查看当前阶段」。保留实时刷新时的用户拖动/选择。

回归包含运行中、汇聚中、阻塞三种状态，拖动后返回和窄屏；旧代码的有效反例在
`graph-baseline-confirmed/`，最终 20 条执行图测试通过。最初两次 graph 测试的 fixture
误用了 execution schema，identity guard 拒绝，不能作为产品 bug 的红测；修正 fixture
后重新验证旧代码仍在初始可见性断言失败。其他 Graph 调用方 23 passed、8 skipped。

生产页面修后截图 `progress-planning-final.png` 确认 Critic 阶段完整可见，观察脚本
记录 `writes: []`。运行期间仅前端 build 更新，来源记录在 `candidate-ui-update*.json`；
不将本轮描述成源码始终不变的一次连续绿跑。

## 证明边界

原生已完成但结果未入账时，本轮验证的是阻止重复投递；自动拾取/重新准入该结果仍未实现。
Claude Code 原生终止恢复未执行。Owner 答复当前为自由文本；逐问题结构化校验、主路径
统一汇总阶段耗时/最后活动、分阶段授权专门展示仍是后续交互优化，不能由本轮测试冒充实现。
本轮会记录现有 UI 的真实行为及阻塞，不把“没有额外功能”报告成测试通过。

## 回归与来源

扩展原 `tests/e2e/prd_ui/run.py`，新增 `channel-recovery` 与 `--owner-checkpoint`，
不再另建独立运行器。real-only 场景拒绝 fake 参数组合，常规浏览器套件不会隐式触发
真实恢复测试。unit oracle 使用明确 fixture，验证重复请求、旧 generation、错误回执、
缺少 native 完成、已完成又重投递等反例，并确认取证不复制 reasoning/prompt。

新增检查执行 `dev-verify run --base 4a2f93ed --tests tests/test_prd_ui_recovery.py
--tests tests/test_prd_ui_audit.py --tests tests/test_prd_ui_provider.py`。
自动层包含 instruction 合同和三个 fixture/oracle 模块，最终 48 passed，typecheck 与 Web
unit 通过；`npm --prefix web test`、build/bundle budget 通过。普通 fake Kanban 旅程
1 passed（1.1 分钟），证据 `/tmp/zf-prd-ui-codex-verification-fake-20261110/evidence/`。
一次手工 pytest 忘记设置工作树 `PYTHONPATH`，被源位置守卫拒绝；补正确绑定后通过，
没有采用错误 checkout 的结果。

合并基线 planner 列出 396 个影响模块，无 errors；本轮新增模块和修改 oracle 已补跑。
既有修复批次的影响范围与两项 dev 基线失败沿用前轮报告；不重复声称全仓通过。

## 收尾与合并范围

本轮临时服务、容器和 tmux 已停止，项目状态归档为各 evidence 下的
`diagnostic-project.tar.gz` 后删除，保留原失败与续跑证据。归档排除 provider home、
凭据和私有目录。真实 dev 的 8001 未参与本轮测试。

合并范围包含前轮三个恢复修复及本轮真实验证、当前阶段视野修复。整体跨 backend、
Web、tests、docs，按 high 范围审阅；不是无法审阅的混合提交。沿用前轮闭包证据，
补充增量自动层、真实 Provider 与 Graph 调用方测试，并要求合并前后哨兵通过。
主 dev 上其他会话的文档修改和 `sima/` 不属于本批，合并时保留且校验未变。
