> 状态: active

# 非手机 PRD / Plan 阅读问题闭环

用户批准继续修复上一轮剩余问题（手机除外）。依据设计207与本地实测报告
`reports/spec207-regeneration-20261116-0120/evaluation.md`，基线 f2a96d7a。

- B01 方法交付 -> verify: 真实 Kanban headless 调用提示包含共享方法正文与摘要；Channel 合成及同版评审的 context pack 包含固定依赖正文，缺失、篡改、越界及超预算失败关闭，旧成员版本不被覆盖。
- B02 评审证据 -> verify: 方法要求检查实际最前段及反例，返工后给出当前版本复审或有版本绑定的未变段继承证据，不以有利的后文替代开头。
- B03 内容与来源 -> verify: 方法定义共享行为/AC唯一正文与任务引用边界；原始硬约束与工程细化分别登记，保持来源、覆盖和执行责任。
- B04 验证与归档 -> verify: 聚焦自动化、入口交付证据与有界真实 Provider 对照；报告区分方法交付和生成质量，本地提交归档，不 merge/push。

不修改手机样式、不覆盖历史生成物、不把语义质量加入 Kernel 门禁。无同文档 Codex Desktop 参考，不宣称像素对齐。

## 实施与验证进度

B01–B03 已完成。新增独立共享方法、headless 真实调用交付、Channel 显式依赖快照与完整上下文，所有内置模板均在96KiB预算内。旧成员固定版本不变。

聚焦回归281 passed、1 skipped（可选本地示例缺失）；串行合同10 passed；哨兵53 passed。Docker fake Kanban和Channel两条v5完整UI旅程通过，body/audit通过、Plan批准0、simulation.done已记录；真实模型样本与最终复核结果在归档时补齐。

本批自动选择器扩大至200余测试文件；保留dev-verify plan/run receipt并显式跳过全量与未改前端，采用21个直接模块/调用方/技能物化测试文件和哨兵闭包。报告目录：`reports/spec207-method-delivery-20261116-0355/`。未执行完整真实v5再生成，不以模拟通过冒充真实生成质量。
