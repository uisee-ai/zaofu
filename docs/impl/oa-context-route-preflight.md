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
