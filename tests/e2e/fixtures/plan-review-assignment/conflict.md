原始需求 R-H：每片交付可复现用法、相关测试记录和独立阅读结论；最终 README 及全量集成属于后继，不要求各片运行未交付入口。产品尚未实现，全部验证 NOT_RUN。
当前 Plan：T-A 交付 CLI，T-B 依赖 T-A 交付 HTTP 与最终集成，两片路径不重叠；每片 HANDOFF AC 映射 R-H，verify 阶段独立核对用法符合本片实际行为、相关测试记录以及本片限制。
Task Map goal_claims：AC-H，对应 R-H，verification_owner=task_verify，verification_tier=manual_evidence，command_ids=[VC-A-USAGE,VC-B-USAGE]；人工阅读结论必需。
命令登记：VC-A-USAGE/VC-B-USAGE 分别检查本片用法文件存在、非空，只是阅读准备，不证明语义；两片 Verify 是各自人工结论生产者，使用受控证据端口记录。
acceptance_matrix：AC-H 的 owner=task_verify，tier=manual_evidence，引用两片 HANDOFF 阅读结论及相关测试结果；不冒充自动验收。
product_acceptance_spec：AC-H，mandatory=true，verification_tier=e2e，assertion=CLI命令成功即可证明完整交接，evidence=VC-A-USAGE/VC-B-USAGE退出零，忽略人工结论。
