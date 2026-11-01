# PRD Spec v1 Runtime 合同与分层导出

> 状态: active

依据：`docs/design/197-prd-spec-contract-and-readable-views.md` §5、§6、§8；当前 `channel_prd_document`、PRD sidecar 和 preview API。

## 范围

为显式声明 `spec_format: prd-spec.v1` 的新 PRD 增加轻量机械校验：`document_control`、标准章节、Requirement/Scenario/Acceptance/NFR/Success Metric ID 唯一性、覆盖映射、状态一致性和 Lite/Full profile。保留历史 PRD 的兼容读取，不新增语义质量判定器。

扩展 PRD 导出和 preview API，提供同一 revision 的产品、架构、开发阅读数据入口；正文仍保持产品语义，技术合同、来源、命令和证据进入附录/sidecar。

## 验收

1. Runtime validator -> verify: 缺少标准章节、重复 ID、covered 映射断裂、无可验证 NFR/Success Metric、无效 profile 或冲突状态时返回明确错误；旧格式不受影响。
2. Renderer -> verify: 标准 PRD 导出保留原正文，附录包含追溯、验收、验证、NFR、Success Metrics 和 provenance，且不复制完整 JSON。
3. Preview API -> verify: 返回 `spec_format`、`profile`、稳定的 role-view metadata 和现有 readiness/source/coverage；digest/ref/revision 绑定不变。
4. UI -> verify: 现有 Preview/Source/下载/Task 按钮和历史 fixture 不回归；新 PRD 可切换产品、架构、开发阅读视图或查看对应附录。

## 边界

Runtime 只做 schema、ID、引用、digest、状态和证据存在性检查；不判断需求价值、优先级或推荐方案。
