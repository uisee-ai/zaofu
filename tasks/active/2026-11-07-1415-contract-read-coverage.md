# R37：当前任务合同必读覆盖

> 状态: active

授权：继续监工、修复阻塞并记录。基线 932b00f6。

LS-v1 Q04 合同快照保留 evidence_contract.required 四项文件，但实际 implementation / task-verify 必读只选择 acceptance_criteria 和 verification_commands；四份约定文件未找到。已有真实 PostgreSQL 探针及工具结果，不把目录缺失推断成产品行为失败。问题是交付约束未被必读覆盖，而非源快照丢失。

- 复现新 attempt 可在没有读取 evidence_contract 的情况下满足旧必读清单 -> verify: 回归测试修复前失败。
- 将当前任务的 contract 快照完整纳入 Impl / Task Verify / Candidate Verify 必读；保持计划输入按任务分区，旧冻结 policy 不回写 -> verify: 实际 canonical resolver + Required Read CLI 分页覆盖证据约束；缺页失败、完整读取通过。
- 交接方法明确核对合同声明证据与当前阶段责任 -> verify: canonical skill / 分发副本一致、元数据及方法测试。
- 聚焦影响闭包、sentinel、mock E2E；合并 dev 后验证新策略与旧策略兼容 -> verify: 记录命令、数量与真实运行范围。

范围：通用输入覆盖/交付方法；不自动承认缺失证据，不重写 LS 已接纳结果，不重新扫描完整计划。

验证：直接回归21通过；15文件闭包219通过1跳过；dev-verify plan/run --base dev --tests tests/test_artifact_read_requirements.py --workers 4：72文件确定性层1103通过1跳过，含相关mock E2E，sentinel53通过。非全仓pytest，未新增真实provider E2E。真实Q04快照9865字节，旧冻结选择AC/commands，新声明完整合同；只读对比未生成worker读取收据。记录见LS docs/records/2026-11-07-r37-contract-read-proof.json。
