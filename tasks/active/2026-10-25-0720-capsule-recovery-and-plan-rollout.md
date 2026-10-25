# 任务上下文同步、恢复与 loshu-fms 计划切换

> 状态: active

用户已批准：修复并验证派发/恢复逻辑，更新 Dashboard，提交新 Plan Preview，经受控路径恢复并核实 Impl。

## 实施与验收

- [x] 复现 Plan 刷新后 Task Capsule 陈旧 -> verify: 真正 Task Pipeline 派发入口的回归测试先失败。
- [x] 派发前同步并校验当前任务投影，保留合同权威 -> verify: 新旧代际、首次派发、重试与失败关闭测试通过。
- [x] 将可机械识别的上下文故障与语义合同缺口区分 -> verify: 上下文故障不进入产品重规划；真实 upstream gap 保持原路由。
- [x] 影响闭包测试、sentinel、确定性 mock E2E -> verify: 记录准确命令与结果，不用单测冒充真实 E2E。
- [x] 文档与实现提交 -> verify: a52b33b2 fix: synchronize task pipeline context before dispatch；89aa15fc 已合并 dev，保留未关联 index.md 修改。
- [x] 更新 Dashboard 构建与浏览器验证 -> verify: 最新审核阅读层实际呈现。
- [ ] 受控提交 38 任务候选并审核接纳 -> verify: 新 Plan/Package/generation 收据，保留旧审计证据。
- [ ] 加载修复并恢复 -> verify: 当前 Task Capsule/snapshot/briefing 同版、真实 Impl 新派发与 worker 进展；不绕过 Verify/Judge。

## 基线

dev=83b84518；工作树 zaofu-capsule-recovery。产品 run=workflow-4fbf85bbad5fb749；07:06 UTC 已 blocked。候选位于 loshu-fms/docs/plans/parallel-taskmap-revision-20261025/candidate。

## 修复/验证记录

- 5 个新增派发回归在修复前均失败，复现缺失/旧投影、快照冲突、写入失败和选择后合同变化。
- 修复 publish_pipeline_capsule：先核 canonical Task 对 snapshot，再发布/校验说明投影；不修改合同。
- task_context_stale 明确进入 harness 修复分支，真正上游缺口保留重规划。
- 17 个影响范围测试文件：308 passed；另有真实 briefing + mock transport 派发入口检查通过。
- dev-verify plan 选择 231 文件并升级全量；遵循 AGENTS 分层要求，使用上述精确回归替代该全量/无关 serial 档。dev-verify run --skip-tier deterministic --skip-tier serial 继续执行 sentinel 与 flow smoke。
- Dashboard npm run build 通过；Docker mcp/playwright:latest 在真实 8001 确认审批概览/模块规格/验收场景/完整原文均存在，page_errors=[]。证据：zaofu-evidence/capsule-recovery-20261025。
- 说明：[Task Pipeline 上下文发布与恢复](../../docs/impl/task-pipeline-capsule-recovery.md)。

运行时接纳/恢复仍待执行，任务保持 active。

- dev-verify run 的实际自动档：premerge-sentinels 51 passed；三流 flow-smoke 10 passed。未执行全量 pytest。

## 真实恢复中的新增问题

- Runtime 以修复代码重启；session.started=evt-b3ed380d577f；受控提交 Plan 修订=evt-147284af321d。Planner 完成 evt-a425f79564c9，Critic approve 并提交 evt-6807dcbb6860。
- 正式 Package 拒绝 evt-42326828f691：task_map.status=draft，而 enrichment=fulfilled。Planner 沿用了草稿状态，预检漏掉 task_map / source_inventory readiness，最终门禁正确拦住，Impl 未提前启动。
- 新增 7 个回归：基线 5 failed / 2 passed，包含实际 SemanticResultSubmitService 对草稿错误放行。修复后 7 项通过；5 个直接影响测试文件共 96 passed（plan candidate/preflight、package、result submit、synth handoff）。保持未声明 enrichment contract 的历史输入兼容。
- 受控返工 evt-48371911cc96，新的 Planner fanout-prd-plan-evt-8aadbcff 仅修正就绪状态与产物绑定，保留已审查的 38 任务内容，不修改旧 artifact。
- 通用 stage-cap 升级 evt-3d09cd18d92e 与已派发的授权修复冲突；操作员经 CLI goal set 恢复 evt-5cb5e5022e9b。此记录是实际介入，不宣称已修复所有重试计数/恢复策略。
- 输入复制被 workdir guard 拦截时，监工仅把 17 个同字节只读输入放入 Planner 工作目录，guard 保持不变。Critic 的 completed/passed 两层状态错误由其预检后自行修正。

- readiness 补丁的 sentinel：51 passed；三流确定性 mock E2E：10 passed。dev-verify plan/run 自动选择 4 个直接测试文件：45 passed。未跑全量 pytest。

- readiness 实现 15b9ff08 fix: reject draft plan inputs before independent review；合并 8d3e689d。合并后 7 个聚焦测试和 51 个 sentinel 通过。
- 修正版 Planner 完成 evt-835e10e7270b：Task Map 仅 draft→ready，其他机器端口字节不变，待新 Critic。
- 监工拦住无效 Preview evt-ba3e052d2934：workflow_resume_batch 对被拒绝的 Plan 重发 task_map.ready，造成草稿 Task Map 与旧 Package 混绑，未审批。新增 3 个恢复投影回归在基线全部失败；修复 reader Plan 协议失败的恢复归属，仍由 stage replan 负责。
- 观察到 orchestrator trigger_lag_s 约 679 秒，需在无活跃 provider operation 的断点加载最新修复并重放；不能以“进程存活”宣称新计划已进入 Impl。

- resume guard 6 个影响范围测试文件：86 passed；sentinel 51 passed；三流 mock E2E 10 passed。dev-verify plan/run 最终选择 48 个恢复相关测试文件：887 passed（包含确定性 mock 恢复 E2E；排除 host/real_provider）。停止前 operation reducer 确认 active=[]，Planner settled；zf stop 正常完成 18 步。

- 恢复分流实现 a8b0feb1 fix: keep rejected plan packages out of batch implementation recovery；合并 b32ea032。合并后 3 个聚焦回归与 51 个 sentinel 通过。
- 第二次 restart 前 Run Contract PASS、配置摘要相同。Runtime PID=2997362，session.started=evt-259b734299ad；Dashboard PID=2997435，保留原环境和授权配置。
- 启动补处理接纳已结算 Planner：evt-b01f759b6eb6；新 Critic 派发 evt-5e98a888fc8e。未重做规划，也未重写旧事件/产物。等待该版本审核和人工 Preview 审批。

- 修正版 Critic 通过并接纳 evt-ef815717e579；Package=744d857e554f3ab36b36b40306d3ad856b6c722137630c291319dfa4113b7f7b，接纳 evt-7a19343f47ff。原 132 AC、9 Goals、36 原任务和路径均保留，新图共 38 任务/136 AC/117 命令。
- 浏览器核对发现 evt-d36d25fa769c 的执行选择器仍继承旧 35 项，虽 Package 完整却遗漏 DATA-ACCESS 和两项新任务；没有点击 Approve。监工修正自己的恢复范围请求，通过 candidate-rework-apply retrigger 同一已审 Package、无旧失败任务选择器（evt-f58c6bcb4919 / evt-90384a950896），保留新审批门。真实 load_writer_task_map 已验证 38 项完整加载。
- 事件消费延迟仍可复现，不能标为已根治。已在主工作树本地候选 backlogs/2026-10-25-0845-runtime-wake-backlog-latency.md 记录事实、诊断和批次合并唤醒验证方案；未提交该未批准候选。

- 完整 38 项请求被历史无效 Preview 的 task-ID-only 指纹错误去重（evt-685f4d46bc59），且 suppressed 重入永久返回 false。补丁把 Package/Plan digest 纳入指纹，按当前未决事实重新判断历史抑制；不批准旧 Plan，不改写历史事件。基线 3 failed / 1 passed，修复后含原审批调用方的 9 项通过。

- 审批影响闭包（Writer fanout runtime、Plan Preview API、审批 CLI、新身份用例）：181 passed；首次 sentinel 发现超大 fanout 文件增长，已提取 plan_approval_identity 小模块，不修改行数上限，提取后审批调用路径及结构检查 18 passed，sentinel 51 passed（32.83s）。dev-verify 173 文件触发全量建议，本批用上述闭包替代 deterministic/无关 serial，全量未跑。

- 审批身份修复最终 dev-verify 自动档：sentinel 51 passed、三流 mock E2E 10 passed；日志 zaofu-evidence/capsule-recovery-20261025/approval-identity-gates.log。
