# Critic 阅读成本与 Channel 交付收敛

> 状态: done
> 实现提交: `5536738d` — feat: reduce scoped critic delivery and channel generation cost

用户批准结合 208 评估报告及 2.30 倍成本分析继续实施。基线 d026a57f，沿用原 worktree；不 merge/push。依据 [208 Spec](../../docs/design/208-demand-critic-input-and-convergence.md) 的第二批实施范围。

- [x] B1：绑定来源索引按需导航、去重写作方法 → verify: full/显式/未知输入仍保护，targeted 保留原文可展开；实际派发与多产品缺陷样本验证。
- [x] B2：有界较大页、正文无外层 JSON 转义 → verify: 固定工件分页数下降，内容可逐字重建、授权/逐来源回执/截断/续读拒绝反例通过；真实 provider 交付不截断。
- [x] B3：Channel 单一 PRD 正文载体、结构修复和固定模型/推理 → verify: 实际 prompt/适配调用者与真实 Channel 发布，类型错误诊断、正文及需求完整。
- [x] B4：Planner quoted heredoc 写入误判 → verify: 当前 HEAD 重现，合法写自身目录通过，越界/符号链接/替换/执行 heredoc 负例仍拒绝。
- [x] B5：冻结输入配对成本与新增多产品质量样本 → verify: 分列新输入/缓存/输出/耗时/交付次数，oracle 提前冻结，质量不过则修复；不混合不同候选作因果比较。
- [x] B6：真实 Channel → v5 → Plan Preview，收尾 → verify: Docker 正文/源码/下载/刷新同版，待审批无产品实现；影响闭包、sentinel、提交与任务归档。

成本下降是待测结果，不能用字节减少代替实际 token。方法的适用性归 Agent/Skill；Kernel 只负责身份、边界、读取/交付证据和生命周期。

## 验证与交接

- `reports/critic-cost-next-20261117/evaluation.html`：固定候选 ABBA 两次/组，51→8 页；实际输入均值 -79.5%，新输入 -41.4%；全部页完整观察，四次 approve。
- 两产品最终 8 次方法校准正例通过、负例定位；保留原退款正例遗漏和修订 oracle，不将小样本称为独立盲测或普遍召回。
- Docker 真实 Channel → PRD → v5 → Plan Preview 从头通过，body/audit passed，Critic 一次批准无语义返修；Planner/Critic 输入 1,163,317 / 1,089,413（0.94 倍），耗时 10.16 / 6.85 分钟。
- 同版 3 Task、17 条 Task AC、21 条来源 AC 完整阅读与对账；源码、下载、刷新和三个模块均通过。产品实施及测试 NOT_RUN，未审批实施。
- `dev-verify plan/run --base dev`：以 964 文件影响闭包替代文件数触发的全库层；首批 937 文件 11,641 passed，依赖/PATH 修复后相关 134 passed；两个剩余失败在 d026a57f 同样复现，保留基线日志。追加 27 文件 303 passed，最终读取 49、索引/评审/配置 152、Channel/headless 136 passed；各批重叠，不相加。
- Web build/typecheck/unit、serial、53 sentinel、10 flow smoke 通过；最终 sentinel/flow 再验。技能元数据及三份分发一致。不是全库全绿结论。
- Planner 原 heredoc 误判在基线复现；合法写入通过，实际目标越界、符号链接、替换、花括号及执行尾部负例保持拒绝。
- 两次真实 Critic compaction 及方法重读仍有成本；后续可评测上下文预算、方法路由、矩阵去重，不在本批自动扩大范围。

本批本地提交并归档，不 merge/push；证据保存在 gitignored reports，合并者需先按交接归档再清理 worktree。
