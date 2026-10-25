# Plan Preview 人工审核包完整性

> 状态: active

## 背景

现有 Plan Preview 已能展示 canonical Plan、Task Map 和短版审批摘要，但 SDD/TDD 审核所需的输入覆盖、指标/UI/配置合同和产物血缘仍依赖人工从多个 JSON/Markdown 猜测。审批入口应给出可读的分区状态和明确缺口，不能把缺失内容静默当成已覆盖。

## 实施范围

1. 在只读 Plan Preview 投影中生成结构化 `review_packet`：PRD、SDD、TDD、traceability、task_map、metrics、ui_states、config_contracts、decisions、provenance 分区；每个分区包含状态、计数、绑定 artifact refs 和 review note。
2. 由已绑定的 requirement/Plan/JSON port 计算可复核的覆盖与一致性诊断：需求 ID 到 Plan 的覆盖、M14/U14/config 显式覆盖、acceptance 数量声明与实际数量、artifact workflow/pdd 血缘不一致。缺失或不一致必须显式标为 `missing`/`warning`，不能补造 PASS。
3. 把结构化审核包的状态摘要加入人工阅读 Markdown，并补充 API 类型，保持 canonical Plan、事件和任务状态只读。
4. 补充后端回归测试，覆盖完整分区、缺口诊断和 provenance mismatch；保留现有完整正文与 Task Map 行为。

## 验收标准

- step -> verify: 通过绑定的 requirement/Plan/port 构造 preview -> `review_packet.sections` 包含上述 10 个分区，且每个分区有 `status/counts/refs/notes`。
- step -> verify: 输入含 R/T14/AT/T12/M14/U14 和 config 引用 -> M14/U14/config 的计划覆盖按实际 ID 计算；缺失时状态为 `missing` 或 `partial` 并列出缺口。
- step -> verify: acceptance matrix 声明数量与实际数量不同，或 JSON metadata workflow_run_id 与 preview workflow 不同 -> `review_packet.diagnostics` 给出稳定 code、severity 和 evidence，不能显示为无风险。
- step -> verify: 打开现有 API -> 保留完整 `markdown` 与 `task_map_summary`，并能在 `review_dossier_markdown` 看到审核包状态/缺口摘要；approve/reject 仍只走既有 controlled-action。
- step -> verify: 运行 `PYTHONPATH=... python -m pytest tests/test_operator_plan_preview.py -q --no-cov` -> 全部通过；运行 `python scripts/dev-verify.py plan --base dev` 并按其闭包运行对应验证。

## 非目标

- 不读取未绑定的任意目录，不从原始项目路径猜测 artifact。
- 不在 Plan Preview 阶段修改 runtime state、任务状态或自动批准计划。
- 不把缺失的 metrics/UI/config 内容编造为实现或验收结论。
