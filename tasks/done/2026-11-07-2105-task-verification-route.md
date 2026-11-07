# R56：模块检查不能被最终验收条款提前改派为候选验收

> 状态: done；实现提交：`47edd837 fix: retain module verification before final acceptance`

授权：继续监工 LS-v1、修复阻塞并记录；基于 dev 3a4702da 隔离实施，验证后本地合并，不 push。

现场 FMS-22-GAP-EVIDENCE-01 的 CHK-22 明确属于 task_verify，E2E-22 属于 candidate_verify；全部 AC 归最终候选验收。`_snapshot_verification_route` 只看 AC，将整个模块 Verify 路由为 candidate_verify；worker 正确遵循错误派发，提前要求尚未装配的真实身份/授权环境。OA/RM尚无失败触发，且错误owner已进入机械快照，不能靠语义恢复补救。

1. 复现并修复派发快照路由 -> verify: 当前任务有明确 task_verify 命令时，模块阶段运行本地检查，最终 AC 保持 candidate_verify/not_applicable，不伪造通过。
2. 保护最终验收与来源 -> verify: 纯候选验收任务、其他任务生产的检查、仅 impl 自检不能误改独立验收路由；命令及 AC 原文不变。
3. 验证真实调用边界 -> verify: Task Pipeline snapshot caller、briefing/result模板、准入及相关恢复/mock E2E回归；不直接改已派发的冻结snapshot或worker结果。
4. 受控恢复 -> verify: 新代码合并部署后，以实际终止/准入证据选择合法恢复路径，保留治理实现commit及全部原验收；记录旧operation与新派发/目标，不将本地通过当作最终E2E完成。

验证进度：RED 2 failed / 4 passed，首轮聚焦57 passed；补充最终候选门禁不可用not_applicable关闭的断言后6 passed。完整142文件闭包2239 passed / 1 failed，失败为当前dev也可复现的旧测试期望：4881ee20已要求保留verification字典（含scope），旧catalog用例仍要求压成字符串列表。仅修正该过期期望，生产保留逻辑不变。初次聚焦命令含不存在的test_verification_commands.py，未执行测试；纠正为真实路径后才记录57通过。

过期断言校正后，catalog/declarations/新路由/实际targets调用/design199 mock E2E共22 passed；串行10 passed、sentinel53 passed。142文件闭包遵循仓库影响范围原则直接执行所有selected_tests，未采用planner因>80自动扩大为全仓；非full suite、无真实Provider E2E。日志/tmp/zf-r56-{red,focused,closure,baseline-catalog,final-contracts,serial}.log。全41项任务路由预演仅治理22发生变更，其他任务及原AC/命令未变。现场尚未部署，保持active。

21:35现场更新：R56已通过47edd837实现、1c23654d合并部署，保持原会话冻结契约；R57已使旧Verify以failed+abstained准入。只读预演证实新路由快照仅owner/tier变化，但旧自检ref严格不匹配，R58补充受控刷新恢复。第4项真实正确路由派发尚待验证，继续active。


22:12 UTC 现场闭环：R58受控恢复保留原工作区及2ef1f908产品实现，新增证据提交372ffb483f0f8e4ec78e06bc4131cbfdf31a9dd6；原样CHK-22在精确提交完成62模块+10隔离PostgreSQL检查。新自检通过evt-1af7e2391173准入（dev.build.done evt-767b3b8873a9），Kernel自动派发独立Verify `wop-verify-tp-FMS-22-GAP-EVIDENCE-01-86614e-2867f9b8e638`，dispatch evt-92d290ecf838，attempt ta-7b73b3f80181870f9b00。

核对真实派发：target=372ffb48，原工作区不变；新snapshot `contract-r39e38b66697d-47046fcab725fad7.json` / sha256 9e33a7cb9ca65b595517f1dcd39823fd0575f9137390d1aed5f09d38dd5eb6a6，owner=task_verify、tier=task_non_smoke。与旧冻结快照逐项对比AC/verification_commands完全相等，八项最终AC与E2E-22仍由候选验证。旧失败结果和旧自检保留，未改写、未复用为新证据。现场完整证据：LS-v1 `docs/records/2026-11-07-r56-r58-live-verified.json`。这关闭运行时恢复修复，不表示治理产品独立Verify或最终E2E已经通过。

归档验证：docs-only dev-verify计划与执行通过，test_instruction_stack_contracts 8 passed；未重复运行实现阶段已完成的影响闭包，不将此记为全仓/真实Provider E2E。
