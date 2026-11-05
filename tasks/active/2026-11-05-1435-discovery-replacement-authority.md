> 状态: active

# 保留 Discovery 的显式替代决定

用户授权的断点修复中，P16B 的 `evt-4e10a21d6f6e` 证明旧恢复请求 P16B 被混入当前 Reader 的两个替代节点，导致 unknown task id 拒绝。只修复 flow discovery bridge 的字段优先级，不放松 task-map 身份校验。

- Reader 显式声明替代图（含 additive 空数组） -> verify: 采用该图，不补历史 envelope ID。
- 旧 Reader 不声明替代字段 -> verify: 保留 envelope 兼容路线。
- Reader 引用不存在的 ID -> verify: 仍拒绝 amendment，不创建任务。
- 实际 Orchestrator 入口 -> verify: 新回归先 3 failed / 1 passed；修复后与 gap runtime/graph 测试 56 passed。

测试闭包：module gap runtime、module gap graph、reader stage replan、控制动作、sentinel 和确定性 flow mock。自动 planner 因 Orchestrator 广域路由升级全套；按 AGENTS 仅执行本次明确闭包，不把 focused 当全量验证。
