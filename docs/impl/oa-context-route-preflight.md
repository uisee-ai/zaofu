# OA 上下文引用的提交前校验

LS-v1 监工发现：OA 为新计划复用旧 `context_routes`，结果提交并结算 operation
后，才因旧来源不在本次 manifest 被 Kernel 拒绝。call-result 准入只代表结果
合同获接收，不能等同语义执行准入或业务完成。本次 shadow 检查没有挡住 Impl。

`SemanticResultSubmitService.validate_plan_candidate/submit` 已调用
`validate_orchestrator_submission`。它现在同时检查恢复 directives 与
`run_plan.context_routes[].required_sources[]`，逐项绑定当前 operation 的冻结
`ref + sha256`；相同路径、不同摘要及历史计划中的合法来源也不能自动沿用。
嵌套链接或正文提及不会扩大授权清单。共享检查同时被 apply-time gate 使用。

无效提交返回 `repair_required` / `protocol_repair`，附具体 route/source 位置、
work unit 和修正方法，不生成 canonical result，不结算 operation。agent 仍需
自己选择正确来源并修正原 scratch；runtime 不自动替换语义引用。修正后原
operation 可以提交一次。校验不替代来源读取、当前计划/配置有效性或语义判断，
apply-time gate 保留以保护历史结果和校验后变化。

简报提醒复用旧计划时重新核对每条来源。历史已经 settled 的拒绝不被改写或
伪造成成功；本修复不重开已拒绝的 shadow，也不重跑业务计划。

验证入口：`tests/test_orchestrator_context_preflight.py` 经实际 submit 服务证明
旧版本/未知路径/错误摘要均可在同 operation 修正；
`tests/test_orchestrator_agent_run_plan.py` 保留历史结果 apply 拒绝、合法计划编译
与下游上下文交付；`tests/test_orchestrator_directive_preflight.py` 保留恢复指令
边界。真实 LS 只读复验和部署记录保存在其 `docs/records/`，不写入源码状态。

## OA 必读输入发布与完整页交付（198 接线）

OA 首次发布 checkpoint context 时，沿用已有 exhaustive / compact / execution
规则先确定 `required_reads`，再按 `source_id + artifact_id + sha256` 选择来源，
调用通用 `pin_source_versions` 与 `pin_read_selections` 后发布 source manifest。
目前 OA 声明的 required selector 均为 `$`；完整选择文本仍采用 198 的 lossless
编码和固定块。execution 模式可检索但未列为 required 的 plan ports，以及 compact
模式的可选完整 config / checkpoint-input，不因本接线生成额外全文或选择副本。
它们继续走既有受控按需读取路径，授权和可用范围不变。

新 manifest 的连续分页使用固定 selection 元数据和所需块，避免每次 preview
重新读取所有必读原文件；每个 occurrence 仍单独授权。块损坏时，只能从原始摘要
匹配的来源或固定完整版本恢复。这里增加首次发布的固定副本开销，不改变正文范围，
也不保证消除模型、调度及工具往返造成的等待。

已在飞 manifest 不迁移、不补写 pin。operation replay 仍直接加载原始发布物；
旧 manifest 的 legacy fallback 和原 cursor 继续有效，不改变 attempt / operation /
source / bundle 身份。复用 provider session 不代表新 attempt 可继承旧读取账本，
已知 compaction 后也不能据历史输出认定当前上下文仍完整。

真实 OA briefing 现与通用 Task briefing 一致：`Return each raw page unchanged; do
not filter fields or batch pages into a summary.` 每页应保留身份、digest、items
和 cursor 的完整 envelope。既有严格完整输出 matcher 不放宽；只输出正文片段
即使可以独立证明正文逐字交付，也不能自动回填完整 delivery witness。此次修复
不追溯改写旧会话的读取义务或账本，更不以正文交付推断理解、语义判断或执行采纳。

回归 `tests/test_orchestrator_agent_required_delivery.py` 通过真实 checkpoint
请求与生成的 CLI，覆盖三种 mode 的完整正文、发布后禁止整原文读取、optional
不 pin、逐 occurrence 授权、冒用身份 continuation 拒绝、legacy replay/cursor
保持，以及真实 briefing 原页要求。共享 snapshot、lossless codec、resume 和
output-identity 测试保护现有恢复/授权边界。设计依据见
[198 按需输入与恢复](../design/198-context-index-write-read-resume.md)。
