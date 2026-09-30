# ZaoFu 测试资料目录

本目录按资料用途整理为四个部分，另设重启交接区。目录整理只移动根目录资料；已有
`attachments/` 保持原位置、原内容和原文件名不变。

## 当前阅读入口（2026-09-30 重启后审核）

- ZaoFu 自身：[现行方案](03_plans_and_steps/ZaoFu_TestKit.md)、[实现与待办](03_plans_and_steps/zaofu_done_part.md)、[K613 暂停快照](03_plans_and_steps/steps.md)。
- 通用产品：[现行方案](03_plans_and_steps/ZaoFu_Product_TestKit.md)、[实现与待办](03_plans_and_steps/productOfzaofu_done_part.md)、[实施历史](03_plans_and_steps/Product_steps.md)。
- [逐文件审核清单](05_restart_handoff/DOCUMENT_REVIEW.md)：记录修订、保留的历史证据、只读校验及未验证边界。

调研建议不等于当前缺口，历史测试结果不等于重启后重新验收。框架完成度、被测对象质量、环境可用性和证据完整性分别判断；本轮文档审核不解除测试构建暂停。

## 第一部分：attachments（原始附件）

`attachments/` 是测试运行产生的原始证据和归档材料，包括运行报告、
日志、快照、VM/浏览器证据及其摘要。该目录本次未移动、未改写、未删除。

## 第二部分：调查报告

目录：[02_research_reports](02_research_reports/)

存放技术调研、现状审查、需求审计和方法论报告。报告中的历史证据路径
按原记录保留；相对本目录的活跃文档链接已随目录调整。

## 第三部分：方案和实施步骤

目录：[03_plans_and_steps](03_plans_and_steps/)

存放 ZaoFu TestKit、通用产品 TestKit、P0 方案、实施步骤、已完成部分
梳理和产品 manifest 示例。这里描述“如何测试”和“如何实施”，不等同
于某一次测试运行的结果。

`product-test-manifest.example.json` 是模板，不是可直接执行的合同：使用前需替换 commit、命令和验收占位符，按产品能力逐域提供实际测试。示例把七个核心域映射到一个占位命令只展示字段关系，不代表单个 unit 命令足以证明七个域通过。

## 第四部分：审计与问题台账

目录：[04_audit_and_issue_ledger](04_audit_and_issue_ledger/)

存放 `requirement-audit-*.json`、`native-acceptance*.json`、
`coverage_matrix.json` 和 `ZaoFu_issue.md`。

截图中的 `requirement-audit-*.json` 属于审计输入清单：它声明需求、
测试节点、报告引用和 SHA-256，不是测试执行结果本身；因此单独归入本
部分，而不是 `attachments/`。审计清单目录内保留了
`attachments -> ../attachments` 兼容链接，使其中既有
`attachments/...` 引用不因移动而失效。

## 第五部分：重启交接与临时证据保存

目录：[05_restart_handoff](05_restart_handoff/README.md)

保存重启交接说明、仍存在的临时测试证据、逐文件 SHA-256 和缺证清单。
重启后优先阅读该说明；不自动恢复测试构建，不把备份视作新测试结果。

## 兼容入口

根目录保留以下符号链接，兼容现有测试代码和历史命令：

- `ZaoFu_issue.md` → `04_audit_and_issue_ledger/ZaoFu_issue.md`
- `coverage_matrix.json` → `04_audit_and_issue_ledger/coverage_matrix.json`
- `product-test-manifest.example.json` →
  `03_plans_and_steps/product-test-manifest.example.json`

这些链接不是重复副本，不会产生两份可分叉的台账或矩阵。
