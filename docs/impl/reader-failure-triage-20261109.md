# 技术输入失败的后续恢复（2026-11-09）

任务：`tasks/done/2026-11-09-0900-reader-failure-recovery.md`。

P12B 的 reader_execution_failure 已通过结果准入并结算 operation，但后续恢复链没有识别该结构化分类。
文本启发式把 missing reads 当作 evidence_payload_gap，人工证据路由又把未执行验收的 blocked 占位行
识别成 manual_evidence_required，从而制造 owner decision。datal 的 normal_frontier_owner=run_manager，
而原 pending_failure_diagnoses 只在 wrc 启用，单修分类仍不能使诊断自动到达 owner。

修复保留结构化事实，先于 YAML 产品返工和文本匹配返回 request_diagnosis；原因尚待 agent 诊断，
不判产品不合格、不声明合同不可满足、不消耗产品重试，也不自动重跑相同输入。
manual_evidence_gate 对该类型不从未执行验收行推导人工失败，旧错误 triage 也适用。
真正已失败的人工证据命令和 external_gate 仍保持原门禁。
非 wrc 模式仅新增此已知技术失败的诊断入口，其他旧路由保持原行为；后来的失败会取代旧诊断候选。

验证覆盖实际 Run Manager projection/tick、同一故障幂等来源、旧 triage、最新失败、busy/unavailable/human
边界与真实人工证据正例。真实状态只能通过受控 action 修复：dismiss 已证实错误的 decision，保留失败
原文及产品验收责任，再由 Run Manager 给出有证据的恢复方案。不得把 dismiss 当作人工验收通过。

验证记录：初始 5 项针对性失败复现；修复后直接影响域 105 passed。dev-verify 自动选择
broad deterministic：15544 passed、24 failed、20 skipped；24 个失败节点与已部署 P0 和原 dev
基线完全相同，无新增失败。未将广泛套件报告为全绿。自动运行在该层失败后停止，另行完成
serial 10 passed、premerge sentinel 53 passed、mock flow smoke 10 passed。
真实事件只读回放：evt-02b0e42c9751 在 run_manager 模式产生唯一 diagnose-attention，
同一旧 triage 不再派生人工门禁；记录见 datal diagnostics/reader-triage-20261109/live-reclassification.json。
部署后的受控恢复入口已验证，业务诊断效果和工作流进展继续跟踪。

实机闭环：dev 6e28c63c 已加载（PID 388578），workers/config 保留；错误 decision 的受控撤销
事件 evt-6858b7b45928，实际 Run Manager 自动派发 evt-7666ced8dbc8，派发验证
evt-166a7abae525。派发效果仍 pending，不等于产品实现或验收成功。原始证据和监工记录
位于 datal diagnostics/reader-triage-20261109/。
