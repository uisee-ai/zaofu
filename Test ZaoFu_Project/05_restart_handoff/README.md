# 重启交接与临时证据保存

保存日期：2026-09-30（Asia/Shanghai）。本部分只用于重启前保存和交接，不代表新一轮测试或验收。

## 重启后审核结果（2026-09-30）

- 本目录的备份说明保留保存时点口径；本次文档修订及逐文件结果见 [DOCUMENT_REVIEW.md](DOCUMENT_REVIEW.md)。
- 已逐一重新读取 112 个备份文件：大小与 SHA-256 全部匹配 `evidence_manifest.json`，合计 10,966,887 字节；31 个审计 JSON 均可解析。校验备份不等于重跑其记录的测试。
- 原始 `attachments/` 按不跟随符号链接、包含隐藏项、排除根目录自身的口径统计：18,649 个普通文件、8,681 个目录、189 个符号链接，共 27,519 条目；普通文件逻辑大小为 1,974,460,927 字节。该总数与早期 27,519 一致，不能用另一种扫描的 18,952 直接认定丢失，也不能仅凭总数证明全部内容未变。摘要口径与边界见审核清单。
- 只做文档、引用、JSON 和备份完整性检查；未重跑测试，未启动 Provider/VM/浏览器，未恢复构建。

## 从哪里继续

1. 先阅读本文件，再阅读 [ZaoFu 实施步骤](../03_plans_and_steps/steps.md) 顶部的 K613 暂停快照。
2. 完成情况及待办见 [ZaoFu 框架梳理](../03_plans_and_steps/zaofu_done_part.md) 和 [通用产品框架梳理](../03_plans_and_steps/productOfzaofu_done_part.md)。
3. 产品框架历史实施记录见 [Product_steps.md](../03_plans_and_steps/Product_steps.md)；问题归因以 [问题台账](../04_audit_and_issue_ledger/ZaoFu_issue.md) 为准。
4. 本次仍保持测试构建暂停。不要因为重启恢复上下文就自动运行测试、Provider、VM 或破坏性操作。

## 本次已保存

快照目录：[20260930-1116](20260930-1116/)。

| 内容 | 位置 | 保存边界 |
| --- | --- | --- |
| 临时测试报告、JUnit、日志及同目录证据 | `20260930-1116/tmp_evidence/` | 从方案、实施记录和审计资料中提取仍存在的 `/tmp/zf-*` 路径，复制 18 处来源、112 个普通文件，共 10,966,887 字节；无符号链接或特殊文件 |
| 原路径、备份路径、大小、逐文件 SHA-256 | [evidence_manifest.json](20260930-1116/evidence_manifest.json) | 复制前计算摘要，复制后逐文件核验，112 个文件一致 |
| 审计清单的临时路径现状 | [audit_tmp_reference_inventory.json](20260930-1116/audit_tmp_reference_inventory.json) | 对 JSON 显式 `ref` 字段去重，共 49 个 `/tmp` 文件引用：保存时 1 个存在、48 个已缺失；这不是新增测试失败数 |
| 方案、研究报告、问题台账、覆盖矩阵和已有附件 | 上级四个分类目录 | 已位于 `/home/uisee/zaofu_v7` 持久磁盘；保留原文件，不重复复制已有约 2 GB 附件 |
| 框架源码和测试实现 | 仓库现有 `tests/testkit/`、`tests/product_testkit/`、`tests/conformance/` 等原位置 | 现有已保存文件继续留在磁盘；本次不是整个仓库或所有运行状态的灾备快照 |

临时证据包括 K613 readiness、K612/K608 S1、K598 MAINLINE、K599 审计等仍存在的报告。详细来源以清单为准，不用目录名推断通过或失败。

## 目录整理已完成

- `attachments/`：既有原始证据，未移动、未改写、未删除。
- `02_research_reports/`：47 份调查/研究报告。
- `03_plans_and_steps/`：9 份方案、实施步骤、完成梳理及模板。
- `04_audit_and_issue_ledger/`：31 个 JSON（含覆盖矩阵）和 1 份问题台账。
- 根目录 `ZaoFu_issue.md`、`coverage_matrix.json`、`product-test-manifest.example.json` 是兼容符号链接，不是另一份独立副本。
- 审计目录的 `attachments -> ../attachments` 保留清单中的相对证据路径。

## 当前状态和安全边界

- 保存时分支：`v7`；HEAD：`142a62ac763b148cbe2b401ac1cd7ae0f8d1c072`。工作树有大量未提交/未跟踪修改，不能用该提交号代表当前全部源码。
- 本次只整理和备份，不运行测试，不提交、不 merge、不 push、不清理工作区、不停止服务，也不执行重启。
- 重启本身不会删除已保存的仓库文件，但 `/tmp`、进程、tmux 会话和内存状态不保证保留。不要执行 `git clean`、`git reset --hard` 或覆盖式恢复。
- ZaoFu 实施停在 K613；原生 Codex/Claude 两项按操作者要求只保留方法、跳过执行。K552-F2 长样本、K530/K584-R 新现场、破坏性 OS 实际验收、历史缺证和最终同源验收仍开放。
- Feishu、`/issue-merge` 和明确降级项继续保持排除，不因重启而自动恢复构建。
- 备份原样保留报告内容、结论和历史绝对路径，没有改写摘要、补造缺证或重算完成率。复制证据不等于重新测试，也不等于重新验收通过。
- 共享 `/tmp/pytest-of-uisee` 不整目录复制，避免混入其他会话和无关临时状态；只保存上表明确选中的测试证据目录。
- 本地备份不是异地备份；硬盘故障不在本次保护范围内。

## 重启后核验

在 `/home/uisee/zaofu_v7` 执行下列只读检查，不会启动任何测试：

```bash
node - <<'NODE'
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const base = 'awesome-zaofu/New_function/Test ZaoFu_Project/05_restart_handoff/20260930-1116';
const manifest = JSON.parse(fs.readFileSync(path.join(base, 'evidence_manifest.json')));
for (const file of manifest.files) {
  const data = fs.readFileSync(path.join(base, file.backup));
  const digest = crypto.createHash('sha256').update(data).digest('hex');
  if (data.length !== file.bytes || digest !== file.sha256) {
    throw new Error('备份校验失败：' + file.backup);
  }
}
console.log('备份校验通过：' + manifest.files.length + ' 个文件');
NODE
```

报告中可能仍引用原 `/tmp` 路径。阅读证据时按 `evidence_manifest.json` 找到备份，不要直接把备份根目录当作可运行的测试现场；如要重跑审计，应明确提供新的路径映射/输入清单，并保留原清单不变。不要自动覆盖重启后新产生的同名 `/tmp` 文件。
