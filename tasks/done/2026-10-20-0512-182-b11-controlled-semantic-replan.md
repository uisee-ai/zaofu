# 182-B11 · 洛书语义补审修订与本批收尾

> 状态: done；实现 `00d2442 fix: relocate referenced plan ports for downstream consumers`，验收 `a0c7682 docs: record approved task map and preview verification`。
> 来源: 设计182及本轮用户批准的补充修复；沿用独立worktree，不自动批准产品Plan。

## 范围

- 通过zf plan reject触发已有工作流，保留旧Plan和Package，绝不手改canonical运行态。
- 审查修订版对B08/B09的实际消费；修正上轮案例充分性的过宽结论。
- 聚焦验证、本地提交、逐项归档；Dashboard保持最新可读，不批准产品开发、数据库或部署。

## 验收

- 正式Planner/Critic与Kernel链 → verify: 新Plan待人工审批；原范围不缩减；缺口有逻辑责任与关闭条件，未知真人负责人显式待指定。
- 最终实际Preview → verify: 格式化正文及附件同版可读、无非GET审批请求；报告解释Critic与执行图。

## 当前结果

原 reject 返工与后续受控 generation 历史完整保留，没有清零预算或改写 verdict。
正式 Critic `evt-4d9584e71768` 通过，新 Plan `evt-d2cc2bafecf4` 待人工审批，
保留 54 任务、237 AC、171 命令与 Q01–Q15 未决条件。
Docker 实测新 Preview：7 页、28 附件，源码/下载/摘要一致，桌面及手机通过，
0 非 GET API 写入、0 JS 错误。实际 Package 和 27 个必需下游输入读取通过。
工程影响闭包 619 passed、8 skipped；未启动产品 writer、数据库或部署。
证据、角色图及具体命令见[验收记录](../../docs/records/2026-10-20-0847-taskmap-repair-verification.md#最终正式验收)。
