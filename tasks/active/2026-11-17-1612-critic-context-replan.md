# Critic 上下文、矩阵及 replan 同步优化

> 状态: active

用户授权调研、补充 spec、实施和真实测试。沿用 `wip/codex-20261117-critic-scope`，基线 `22b9c83e`；范围见 [208 第三批](../../docs/design/208-demand-critic-input-and-convergence.md)。本批不 merge/push，不审批测试产品实施。

- [x] C1：核验压缩阈值与 v5 预算取舍 → verify: 模板加载与实际启动参数，显式环境覆盖保留，首次和 replan 原生上下文/压缩记录。
- [x] C2：矩阵唯一正文、等价参数组及方法选择 → verify: 既有消费者兼容，正反例保留独有边界、跨工件验收责任，真实生成与完整来源对账。
- [x] C3：replan 当前范围与历史增量分开 → verify: 当前 Plan/Task Map、原需求、旧 finding 与执行事实必读；full/显式/缺证/改需求/失败证书保持保护；实际派发及读取回执覆盖。
- [x] C4：实际 Channel → Plan Preview → Owner 退回 → replan → 新版 Preview → verify: Docker + 真实 Provider、受控退回、版本更新、Critic 独立 verdict、无实施与旧版审批复用。
- [ ] C5：固定输入/缺陷校准、影响闭包、提交归档 → verify: 分列 fresh/cache/output/耗时/压缩，保留失败、重试和质量限制，本地实现及归档两次提交。
- [x] C6：真实预览身份冲突及矩阵分支修复 → verify: 当前绑定不得被产品代号替换；新增机械预览审计拒绝来源冲突；真实 Web 再次退回产生新候选，Task/AC/命令/依赖不变，两入口 roles 场景分别说明成功与拒绝结果，最终 preview/download/refresh 与身份诊断一致。保留原失败，禁止就地改写准入工件。

## 验证结果

- C1：六次固定输入预算实验未支持提高默认值；保留 120000 / 84000，覆盖首次/恢复启动与显式覆盖。两条真实 Workflow 实际应用后的预算均核验为 120000。
- C2/C3：标准具名引用和 replan 当前导航已接入真实 prepare_call_operation；固定真实候选 ABBA 各两次均识别阶段缺陷，必读字节减少 38.1%，累计输入减少 26.9%、新输入减少 17.9%。不是通用召回或账单证明。
- C4/C6：真实 Provider + Docker Playwright：Kanban 通过；Channel 原审计失败保留，第三版经真实 Web 退回、Planner、独立 Critic、浏览器复验通过。实际 targeted_replan_navigation 字节减少 32.1%，无历史批准继承；任务合同不变，身份/roles 成功拒绝分支修复，原文/下载/刷新一致。产品实现 NOT_RUN。
- 本批 Runtime 影响闭包 35 文件：848 passed / 1 skipped；随后预览完整性回归 56 passed（有重叠，不相加）。最终 sentinel 53 passed；Web build/typecheck/unit、serial、mock flow、fake Docker replan 通过。未运行全库 pytest，前两批已知基线失败仍单列。
- 技能源与两种分发一致，来源校验无警告。原生输出按角色和 operation 核对全部必读项；额外未完整观察的返回单独保留，不计为有效阅读。
- 真实、fake 及配对实验均归档并核对摘要后清理临时状态；仅保留报告服务。矩阵重复与长链路延迟仍为后续观察，不宣称完全解决。

报告：`reports/critic-replan-20261117/evaluation.html`。精确验证命令见同目录
`reference-closure-command.json`、`dev-verify-receipt.json`、`preview-integrity-tests.log`；
真实修订绑定见 `final-runtime-binding.json`，清理见 `cleanup-real-channel.json` / `cleanup-real-default.json`。
后续合并须保留本批与前两批报告；本批不合并、不推送。
