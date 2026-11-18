# 208 第四批：生成经济性、任务输入与执行中 Replan

> 状态: active

用户批准上一轮三批七项方案。基线 `bf6eccf6`，沿用 `wip/codex-20261117-critic-scope`；
包含隔离小产品的真实 Provider、Web 审批、实施及恢复验证。沿用 208，不增加第二套任务合同。
本轮不合并、不推送；证据在 `reports/plan-economy-20261118/`。

- [x] P1 信息权威与消费者核验 → verify: 对矩阵、source_index、Task Map、execution_spec 列实际消费者；未知/配置必需字段保留。
- [x] P2 重复生成收敛 → verify: 单一场景预期、稳定 ID 与机械引用；固定 PRD 新生成及实际 reader 读取减少重复，独有行为不丢失。
- [x] P3 首稿一致性 → verify: 阶段验收、人工/自动责任、身份、成功/拒绝已知正反例，增加未见样本；不把模型 approve 当 oracle。
- [x] P4 任务输入分配 → verify: 同包精确 Task 合同、共享规则和本片完整正文，实际 Impl/Verify 派发及受控读取；candidate 保留完整包，陈旧/未绑定选择拒绝。
- [ ] P5 Replan 范围及恢复 → verify: 语义修订与基础设施恢复区分；旧 finding、执行事实、版本与影响责任保留；局部不冒充全局通过，缺证保持原保护。
- [ ] P6 执行恢复测试入口 → verify: 原 pending-preview 旅程保持；新增执行中故障、需求变更、重启/重复/上限场景，经真实受控入口、新版本准入、恢复执行及独立最终验收。
- [x] P7 真实对照评测 → verify: Channel/Kanban 同需求；读取 A/B 固定候选、生成对照固定需求及模型、执行恢复单列；fresh/cache/output/耗时/返修和缺陷分别报告，保留失败与清理证据。

质量通过是硬要求；新输入降低 20%、耗时降低 15% 是实验目标，未达到则明确报告，不删除义务或提高预算换取通过。每批先确定性测试，再真实层。

已验证：consumer-audit.md 与 baseline-repetition.json；任务输入原回归先红后绿，
定向 93 passed；实际 Impl→Verify 派发及恢复 mock 4 passed。影响闭包 103 个测试
文件执行 1578 passed / 1 skipped。dev-verify 的基于数量全库扩展层明确跳过，以该
完整选中文件闭包替代；其余自动 tier 均通过。真实验证仍在运行，不能标整批 done。

首稿方法校准：Unicode 未见正反例与成功/拒绝已知正反例 4/4 符合预期，完整输入未改动；不推导通用召回率。恢复闭包补充 86 passed。

真实 Channel 首稿曾把流程限制写成空 AC 的 covered 行，正常触发一次返修。补充 synth
方法后，对原失败体进行真实作者修订校准：仅移出 SRC-STAGE 并保留来源/阶段记录，
15 个产品 AC 与完整 Markdown 未变，原结构错误消失。该校准不冒充 live 旅程独立签收。

基于实测增加机械 briefing 优化：完整策略与 Run/attempt/全部 required_reads 一致时，
以已有 policy ref/digest 和批量读取/index 代替重复的逐项命令；不改必读范围。冻结真实
65 项派发从 32363 降至 13809 字节。新增路径/调用者闭包 68 passed，fake Docker
Kanban→Plan→Web reject→Replan 通过。没有把字节变化宣称为 token 收益。

真实 Channel 另外复现了“准备时已 pin，最终汇总丢回项目绝对引用”的路径协议问题。
在 P3 的机械预检/交接范围内补充修复：最终汇总复用摘要校验后的固定引用，preflight
对显式 descriptor 采用 Package 相同路径/摘要合同，直接 *_ref 保持可物化。回归先红；
审查后原内容变化继续拒绝。该协议失败及原 60 分钟浏览器 timeout 都保留，后续同 Run
正常启动恢复并单列补充结果。


本轮最终核验：P1/P2/P3/P4/P7 实施及分层评测完成；P5/P6 仍 active，不归档为整批 done。
Kanban 真实旅程通过；Channel 原 60 分钟失败保留，同 Run 补充 Web Replan 通过，
Task 合同未变且新增34行恢复说明，续跑27.3分钟。实际字节缩短与 token 分开：读取
对照 fresh -3.7% / 耗时 -5.8%；生成对照 fresh +14.3% / 耗时 -1.8%，未达实验目标。

执行恢复真实发现外部 v2 与原批准 v1 冲突，但旧 Verify 仅文字要求重规划，结构化
结果为源码修复拒绝；旧候选重复验收。已补方法与 admitted Plan-port candidate
路由优先级，144 passed；调用者/机械E2E 117 passed。操作员正常请求 OA 后，
其识别重规划但无法将候选 reader 绑定到当前 writer-only return_to_plan；真实
human.escalate `evt-ccebfabaafa4`，无新版审批/执行/最终完成。不能以 mock 或人工
请求冒充真实闭环，仍保留下面的必要工作。

P5/P6 剩余实施验收（继续沿本任务，不另起重复候选）：

1. 先复现候选 reader semantic checkpoint 无合法 writer 目标 → verify: 原始事件
   与失败结果在 reports 的 real-execution 归档中；不能把候选父 Task 伪装成 writer。
2. 为 candidate 阶段定义精确 Run/候选/operation/generation/失败 refs 绑定的修订
   意图路径，限定为 Plan 请求，不授予 reader 实现写权 → verify: 陈旧/跨Run/
   重放/普通实现错误均拒绝错误路由，真实 runtime caller 有回归覆盖。
3. Planner 判断真正受影响 Task，保持已有 commit 与义务，Owner 审批新版本后执行
   → verify: 新 generation dispatch、唯一 goal completion、精确 frozen commit 的
   v2 成功/拒绝旧字段等8例黑盒全部通过，才能关闭 P5/P6。

最终机械 tier：103文件1578 passed / 1 skipped；恢复补充86；聚合/preflight闭包270；
候选路由144 + 调用者117；独立执行oracle审计13；sentinel53与flow smoke10通过。
上述有重叠，不累加成“独立测试总数”。系统python缺pytest-cov的验证启动失败保留，
随后使用项目.venv完成自动tier；未运行全库pytest。原始 Provider 失败和部分通过单列。
