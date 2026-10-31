# PRD 审阅正确性与需求责任对账

> 状态: done

批准依据：用户已明确授权本批次先建 backlog 后实施，无需再次审批。

来源：2026-10-31 用户授权「基于建议1-3，先生成backlog，再实施」。基线 dev 3746d313；独立 worktree prd-review-20261031。

问题：模块切换后 Markdown 保留旧正文；页面 task_ids 与阅读器 task_id 不匹配；决定登记与审批阻塞项混淆；场景 name/source_ids 与阅读器字段不一致。ls 的 SRC-0003/0004 总览条款存在语义责任错配，结构引用完整不能代表覆盖正确。

范围：修复同版读取、决定和场景投影；将实际生产者/验证时点的对账方法补进 Planner/Critic skill；为 ls 已发现错配形成具名纠偏记录，保留冻结版本与受控 replan 路径，不直接改运行态工件。

验收：
- 连续切换两个不同任务 -> verify: Docker Playwright 标题、正文、页面与场景始终来自所选任务。
- 混合已授权、未来待定和当前阻塞决定 -> verify: 摘要、正文显示一致的分类数量；不得改变审批动作准入。
- 多任务页面、具名场景及 source_ids -> verify: 真实形状的脱敏 fixture 保留标题、来源和 AC；缺失绑定明确报告。
- 语义责任错配 -> verify: 方法要求原文→真实生产者→验证层级反向检查；反例覆盖总览误分到车辆、时长误分到分析；不硬编码 FMS 规则到 Kernel。

实施验证：相关后端/API/mock E2E 94 passed；Docker Playwright 25 passed；sentinel 51 passed；前端 build/typecheck/unit 通过。
扩展 dev-verify：858 passed / 1 skipped / 1 failed，失败为基线已复现的 Feishu 无待审请求审批测试，未放宽 gate。
完整命令、范围与基线复现见 `docs/records/2026-10-31-prd-reader-validation.md`。未修改 ls 冻结 Plan，未部署或重启真实 workflow。

实现提交：`b23a6c92 feat: add product-focused PRD review and traceability`。
