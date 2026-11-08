# Worker 只读求助的有界 OA 简报

关联 [199 的角色交接与 C01](../design/199-worker-failure-recovery-and-role-handoff.md)、[实施任务](../../tasks/2026-11-08-1322-assistance-briefing.md)。

运行中 Worker 的 `worker_guidance` 经 RM 发布 `orchestrator.rework.triage.requested` 后，OA 仅提供绑定原请求的建议。此前真实 `dispatch_advisory_turn` 调用全局简报 assembler，虽然前段写 proposal_only，后段仍附带旧式改合同、创建任务和派发指令，并加载全局 memory、Kanban 和健康。测试只替换成短 triage renderer，未发现实际交付冲突。

现有 assembler 对这一个结构化请求类型生成有界简报：完整 Trigger（含请求、operation、attempt、lease、run、固定 sidecar ref/digest）和只读答复合同、配置的 state dir、固定基线检索说明及 round.complete。问题正文与已尝试方案仍保存在原 required sidecar，按原 request_ref 获取目标与相关证据，不用摘要取代原文。该路径不再生成全局 progress，也不附带旧式写入指令。普通 OA 唤醒、失败分诊、RM resident、状态权限与超时逻辑保持现有行为。

原请求身份属于原 Worker，并不授予 OA 写入权限。无法凭固定来源作答时，输出明确缺口和责任人；不得扩权或伪造不存在的生产接口。现有请求计时仍从请求创建计算，排队会消耗应答窗口；缩短简报不是对排队期限问题的完整修复，不能追认已过期答复。

回归使用真实 reactor → dispatcher → builder，仅替换 provider transport，验证可交付简报不加载全局投影、无旧式写指令、身份和完整ref保留、结束协议及重复tick单次投递。保留普通简报和事件/状态准入回归。现场已生成的旧简报不改写；后续新请求随受控加载生效，真实行为与耗时收益另行记录。
