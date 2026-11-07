# 防止替代任务丢失输入消费契约并退回全量读取

> 状态: active

依据：持续监工修复授权；[199](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)。

## 现场与责任

TODO-PORT 06:47 派发后约 21 分钟仍在逐页读取；它的原任务有明确 plan_input_selection，仅需要共享输入和自身分区。Discovery 生成两个替代任务时遗漏选择声明，适配器忠实保留了遗漏，按 legacy 规则全量必读：完整 Task Map 267 KB、source_index 1.90 MB 等。原 Task Map/输入正文未丢失，当前问题是消费契约退化。OA/Discovery 未对账前驱输入选择和读取成本；机械准入检查了字段合法性，却未发现声明消失；RM 看到持续读取/心跳而没有该成本异常的恢复动作。

## 验收

1. 对已声明输入消费契约的前驱，替代任务必须显式提供重新审核的选择 -> verify: 真实 build_gap_task_map_amend caller 拒绝遗漏，不静默继承或缩减；原 legacy 任务兼容、显式选择保持原样。
2. 补齐 Discovery/Replan 方法 -> verify: 写明来源/AC/验证责任与字节成本对账，声明可明确扩大到全部需要的端口，不改变 Candidate 全量验收。技能 source/distribution 一致。
3. 真实 TODO 两任务按现有审批/新代次路径修正消费声明 -> verify: 完整 35 项地图保留，仅两任务重新绑定；新 attempt 使用有摘要的共享与前驱分区，原读证据和失败事实保留，其余任务继续执行。不得改写已准入 map/manifest/required policy，不能伪造读取收据。
4. 记录测试、合并加载与实际恢复效果 -> verify: 完成源代码与现场两类证据后归档；若现有控制路径不支持，补合法有界入口，不直接改 canonical JSON。

## 软件验证与现场准备

- 基线回归 **1 failed / 3 passed**，遗漏选择未拒绝；修复后聚焦 **51 passed**，前驱/消费/代次/package caller **44 passed**。两次测试命令误引用不存在的文件而零测试退出，已改用实际路径并重跑，不计通过。
- dev-verify plan 选择 191 个文件，显式运行全部影响闭包 **2,755 passed / 1 skipped**（114.19 秒）；未升级为全量。sentinel **53 passed**、serial **10 passed**、mock flow **10 passed**，ruff/diff check 与技能三份字节一致性通过。
- 现场已准备不可变新 Task Map 与 task_execution_input：35 项全保留，仅两个 TODO task 增加显式选择；保留原输入全部 34 个分区和 shared，新增两个包含当前任务完整定义及前驱正文的分区。原 AC/依赖/allowed_paths/命令逐对象相等。source_index 仅在 DERIVED-02-TODO 增加两个消费者，原记录均保留。尚未发布/批准/派发。
- inspect-inputs 中原 shared refs 未全部枚举到 traceability 的 unknown 继续记录；未宣称全局语义审核全部通过。当前修复通过完整旧正文保留与新合同完整绑定证明没有删除原上下文。真实证据位于 LS-v1 docs/records/2026-11-07-todo-input-selection-proposal.json。
