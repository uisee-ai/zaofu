# 来源到验收的覆盖审查与版本比较

> 状态: done

批准依据：用户已明确授权本批次先建 backlog 后实施，无需再次审批。

来源：同批次用户授权建议3，依赖同版 artifact 读取。

范围：复用 source_index、coverage_ledger、task_map、acceptance_matrix、test_matrix 等既有工件，呈现原始条款→任务规格→AC→命令→证据状态。允许搜索、按未分配/待决定/缺引用/未验证过滤及比较不同 Plan 版本；语义正确性仍由人工/独立Critic裁决。

验收：
- 已分配、未覆盖、待决定、非目标及无证据 -> verify: 表格准确分组，不把 covered 或命令存在显示成测试通过。
- source/Task/AC/command引用缺失或重复 -> verify: 有明确诊断和来源定位，不能静默选一行。
- 2,356条以上来源 -> verify: 分页或有界渲染、搜索后完整结果数量、任务关联可浏览。
- 两个已绑定 Plan 版本 -> verify: 显示新增/移除/修改条款，保留版本身份；加载失败不沿用旧版数据。
- 回归 -> verify: focused backend/API、frontend build/unit、Docker Playwright及适用的sentinel/mock E2E通过；记录实际范围。

实施验证：相关后端/API/mock E2E 94 passed；Docker Playwright 25 passed；sentinel 51 passed；前端 build/typecheck/unit 通过。
扩展 dev-verify：858 passed / 1 skipped / 1 failed，失败为基线已复现的 Feishu 无待审请求审批测试，未放宽 gate。
完整命令、范围与基线复现见 `docs/records/2026-10-31-prd-reader-validation.md`。未修改 ls 冻结 Plan，未部署或重启真实 workflow。

实现提交：`b23a6c92 feat: add product-focused PRD review and traceability`。
