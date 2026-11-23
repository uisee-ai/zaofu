> 状态: done · 24eb7bbe fix: bind critic evidence and close recovery handoffs

# 审核纠错、诊断调度与恢复交接 P0

用户批准执行四项 P0。沿用 autonomous-closeout 工作分支，合入现场 7d1edef4 的既有修复后实施；不修改 dev 脏文件或真实 Run 账本。旧无人资格 closed_failed 保留，真实交付仍由前轮 A6 跟踪。

## 范围与验收

- [x] P0-1 当前审核依据：扩展现有 finding/证据绑定与技能，区分历史与当前；语义由 Agent 判断，runtime 只核引用/版本/结构化事实 -> verify: 当前 confirmed、历史 pending 反例；错摘要、缺失依据、不可读引用拒绝；准确引用不强迫通过。义务适用性和新需求越界仍由 Agent/技能判断。
- [x] P0-2 原候选独立复核：沿既有受控动作新增有界 review-only 恢复，保留原工件/旧拒绝与次数；精确反证与授权必需 -> verify: 实际受控入口到 Critic dispatch 不启动 Planner、不改 Task Map；重放/重启幂等、候选/生产者改变、无依据、活跃 operation、重复复审拒绝；有依据的再次拒绝仍阻塞。
- [x] P0-3 独立诊断调度：先完整收集再按故障身份/可执行性/资源仲裁；同故障去重 -> verify: 不可执行候选不遮蔽独立 attention、双路同源一次派发、resident 忙时持久等待及恢复、生产告警去重。
- [x] P0-4 AR 失败后续：现有 consumer 保留阶段、证据与下一 owner/action；先复现 briefing/cleanup 根因，分开诊断失败和清理失败 -> verify: timeout/报告缺失/写回/清理故障均有绑定后继；成功诊断不重放旧失败；负责人不可用明确升级；fresh CLI 清理调用边界及错误路径提示已验证。

## 验证与交付

先跑 RED 再 GREEN；聚焦调用闭包、dev-verify 选择计划与增量执行、premerge sentinel、确定性 mock 恢复。真实 Provider 与旧断点交付单独记录，不用单元通过替代无人资格。实施与任务归档按明确路径提交；未经本轮 merge/push 指令不合入 dev 或推送。

来源：docs/impl/autonomous-recovery-closeout.md；现场 reports/eight-unattended-20261121T132814Z/supervision-20261123/assessment.md；研究 /tmp/zf-loopx-loopforge-recovery-conclusions-20261123.md。

## 记录

- 当前批次只维护已有状态机和受控入口，不增加平行任务合同或语义关键词判决。
- 已纳入现场 event_problem_registry 的原生修复，避免后续部署回退。

## 故障与修复记录

1. Critic 原合同只绑定候选身份，未核验 finding 的字面事实，旧 pending 可被误写为当前值。
   新请求冻结 current-evidence-v1，validate/submit/admission 三入口校验；旧冻结请求不追补合同。
   旧合同对反例仍可预校验通过的回归保留，用于证明原缺口与迁移边界。语义推论仍由 Agent 负责。
2. 原恢复要求重新规划，无原候选独立复核动作。增加同候选一次的 review-only，沿既有
   controlled action、fanout aggregate 与 operation 派发；当前 byte/生产者/结果/Run 绑定都重验。
   纠错后补上主 Task reopen、拒绝旧结果重放和 pre-dispatch failure 收口；历史计数不清零。
3. RM 在其他候选存在时不收集 attention，又按 Task 去重，独立故障会饥饿。
   改为完整收集、精确 source event 去重和已有 Case/队列仲裁；resident 忙时等待有界回复。
4. AR consumed/next_route 字符串不保证后继执行。loop.failed 现在投递有绑定的 RM 诊断 Case，
   保留 parent Case、阶段、partial/checkpoint、archive/cleanup；不可用时明确升级，禁止递归盲重试。
   清理失败研究复现为旧 parser 不识别新 host_verification 字段；定位与全量业务校验分离，
   fresh CLI 继续严格校验并只停止 owned runtime。观察失败显式 incomplete。
   briefing producer 路径正确，worker 丢掉嵌套 AR 目录；改进精确 locator 方法，不声称文件系统沙箱。
5. 本批扩大验证发现 7 reds：6 项为轻量运行时无 Orchestrator 私有告警方法，1 项为恢复文件 1002 行。
   告警提取共享函数，复审处理移入专属模块；未改旧测试期望来掩盖异常、未扩大结构豁免。
   7 项所在调用闭包 62 passed；最终相关模块及直接调用者 251 passed。

## 验证证据

- `PYTHONPATH=src <部署 venv>/bin/python scripts/dev-verify.py plan --base dev --workers 4 --json`：解释全分支与 dev 差异；同时记录 72c07d63 增量计划。
- `... scripts/dev-verify.py run --base dev --workers 4 --keep-going --receipt reports/review-recovery-p0/dev-verify.json`：首次 17387 passed / 7 failed / 18 skipped；原失败 receipt 保留，不改写为通过。
- 同次 serial 4 passed，Web typecheck 与 unit passed，流程 mock 10 passed；真实 Provider tier 未运行。
- 修复后最终定向闭包：`tests/test_plan_review_claims.py tests/test_plan_critic_reassessment.py tests/test_autoresearch_recovery_handoff.py tests/test_run_manager_independent_attention.py tests/test_control_actions_recovery.py tests/test_workflow_managed_task_lifecycle.py tests/test_run_manager_diagnosis_consumer.py tests/test_autoresearch_child_supervision.py tests/test_autoresearch_child_outcomes.py tests/test_autoresearch_runtime_cleanup.py tests/test_plan_candidate_preflight.py tests/test_plan_synth_recovery_refs.py tests/test_task_pipeline_shadow.py tests/test_wrc_frontier_consumer.py tests/test_orchestrator_housekeeping_visible.py tests/test_runtime_injection.py tests/test_state_recoverability.py`：251 passed。
- 最终 `bash scripts/dev-premerge-gate.sh`：53 passed；`bash scripts/run-flow-smoke.sh`：10 passed；技能三份同步，`git diff --check` 通过。
- 以上均为确定性/契约验证；扩大回归不是所有用例最终版本的重新全跑，末轮改动以明确调用闭包复验。
- 真实断点只读核对：fanout-prd-plan-evt-d2bddb78 为 failed，当前 synth evt-95cad686569e，14 个绑定输入仍存在，主 Run 活跃 operation 数为 0；未触发新审核或改写真实 Run。
- reports/review-recovery-p0 保存首轮失败、最终验证与调研；AR before-fix 2 failed 复现保留。真实无人资格、最终 Judge/Goal/交付仍由前轮 A6 跟踪。
