> 状态: active

# 真实 Plan 验证缺口修复

用户已批准上一轮报告建议。基线 dev@1cd237bb；依据 reports/spec207-dev-validation-20261116-0501/evaluation.md（主 checkout 保留）。

- B01 来源索引与提前机械预检 -> verify: 自定义 state_dir 的 state-relative、project-relative、绝对 ref 正常准入；缺失索引在 Critic 前拒绝，不产生任务副作用。
- B02 任务阅读合同 -> verify: 声明分区 schema 与实际正文一致；Planner/Critic 合同明确完整模块正文、精确共享执行引用及 affected_task_ids；两种合法阅读入口的浏览器回归通过，旧产物不伪造新正文。
- B03 回归与真实验证 -> verify: 影响闭包、sentinel、fake 双路径、真实 Kanban/Channel 的最终模块正文及当前阅读证据；另记录符合增量前提样本的配对成本，未知不计零，不将回退全量宣称节约。

范围：修复与方法完善，报告 Markdown/HTML。保留未通过及返工证据，不审批真实样例 Plan，不实施样例产品。提交实现与任务状态，不 push、不擅自 merge。写入守卫不放宽权限，本批只改善明确合法的工件生成指引。

阶段验证：新增测试先复现旧误拒/格式漏检，修复后基础71项、影响闭包316项通过；Web build通过；dev-verify保守扩展为全库，按仓库层级用明确调用者及共享合同测试替代deterministic/serial全库选择，其余自动层（Web检查、53项sentinel、10项flow-smoke）通过。fake两路径及逐模块正文检查通过。真实Provider与成本复验待完成，不标done。
