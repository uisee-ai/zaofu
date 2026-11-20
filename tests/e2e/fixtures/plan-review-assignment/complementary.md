原始需求 R-H：每片交付可复现用法、相关测试记录和独立阅读结论；最终 README 及全量集成属于后继，不要求各片运行未交付入口。产品尚未实现，全部验证 NOT_RUN。
当前 Plan：T-A 交付 CLI，T-B 依赖 T-A 交付 HTTP 与最终集成，两片路径不重叠；每片 HANDOFF AC 映射 R-H，verify 阶段独立核对用法符合本片实际行为、相关测试记录以及本片限制。
Task Map goal_claims：AC-H，对应 R-H，verification_owner=task_verify，verification_tier=manual_evidence，command_ids=[VC-A-USAGE,VC-B-USAGE]；人工阅读结论必需。
命令登记：VC-A-USAGE/VC-B-USAGE 分别检查本片用法文件存在、非空，只是阅读准备，不证明语义；两片 Verify 是各自人工结论生产者，使用受控证据端口记录。
acceptance_matrix：AC-H 的 owner=task_verify，tier=manual_evidence，引用两片 HANDOFF 阅读结论及相关测试结果；不冒充自动验收。
product_acceptance_spec：AC-H，mandatory=true，verification_tier=manual_evidence，assertion=人工逐片确认用法和本片实际行为一致并核对测试证据，evidence=上述两片独立阅读结论与测试记录。

互补自动义务 AC-H-AUTO：两片 task_verify 的 E2E 分别实际运行当前片用法命令，与产品行为比较；由 VC-A-BEHAVIOR/VC-B-BEHAVIOR 生产测试证据。product_acceptance_spec 另有同来源 R-H 的 AC-H-AUTO，tier=e2e；不替代 AC-H 的 manual_evidence 及两片独立阅读。最终集成仍属于 T-B 后的 candidate_verify。
