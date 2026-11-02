> 状态: active

# P1：OA return_to_plan 丢失目标任务的基线绑定

Owner 已授权持续修复 blocked Task 的恢复链路。
OA 决定 evt-0725b33d8f79 被接纳并应用为 return_to_plan，目标 DL-FOUNDATION，
精确基线为 77a994dd / 802b81ab。重规划生成器却把触发 evt-ec8175478249
替换成全局基线 4e6df815 / e24218ed，导致 Planner 在进入模型前失败：
`replan OA decision differs from baseline identity`。该结构故障随后触发 Run 阻塞。
修正触发后，真实预注册还复现全局包 currentness 误拒绝；补充 Plan-domain 按输入
基线代次验证，并将回归推进到 prepare_call_operation 实际入口。
只读预览也证实 Owner stage recovery 会再次替换基线；同批改为取同 Run、
同决定引用的 Kernel 重规划记录。测试覆盖授权恢复之后的完整 operation 预注册。

## 验收

- 保留已接纳 OA 决定的基线 -> verify: 决策应用后的真实 Planner 输入合同构建通过。
- 历史错误触发 -> verify: 恢复扫描只补发一次正确触发，原记录保持不变。
- 非 OA replan -> verify: 原最新包选择和并发去重回归仍通过。
- 部署并恢复 -> verify: 精确解决本次 operator escalation；新 Planner operation 进入运行。

实现与恢复遵循 docs/impl/oa-bounded-execution.md；不改 OA 决定、任务验收标准，
不抹除已耗尽记录，不取消当前原 Plan Critic。

## 验证记录

- 聚焦核心/准入/事件/Mock 链路 204 passed；直接调用者 77 passed。
- 并发 serial 10 passed；premerge sentinel 53 passed；mock flow smoke 10 passed。
- dev-verify 规划器保守扩张为 188 文件全量层；本项仅改变重规划生成/恢复去重，
  按 AGENTS 的影响闭包执行上述测试，自动 run 使用 --skip-tier deterministic。
- 真实账本只读重放生成恰好一条纠正请求，绑定 77a994dd / 802b81ab；未写入运行状态。
