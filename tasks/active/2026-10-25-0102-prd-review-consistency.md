> 状态: active

# PRD Preview 语义一致性与完整下载

## 目标

让人工审批摘要、canonical Plan、Task Map、trace 和 artifact package 来自同一组绑定证据；所有 owner decision 必须在摘要中可见，线上阅读与下载内容一致。

## 范围

- 从绑定 decision register / canonical evidence 中构造决策摘要，不能只依赖 slim payload 的 `owner_decision_items`。
- 增加 SDD 边界合同、TDD 证据路径/判定信息的可读摘要。
- 提供包含 review dossier、canonical Plan、trace 和 Task Map 引用的完整 Markdown 下载或绑定附件。
- 缺少绑定证据、digest 或决策一致性时 fail closed。

## 验收

- step -> verify: 对 payload 缺少 owner decisions 但绑定 Plan 含 decision register 的 fixture，Preview 显示全部 decision ids、问题和阻断影响。
- step -> verify: Preview 的 dossier、canonical Plan、Task Map 和 artifact refs 使用同一 plan id/digest；不一致时返回明确错误。
- step -> verify: 下载内容包含人工审批摘要与 canonical Plan，且保留 trace/Task Map 入口。
