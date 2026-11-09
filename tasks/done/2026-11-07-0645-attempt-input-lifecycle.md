# 保留在飞任务绑定的已准入输入版本

> 状态: done；实现 `5b49ce5b` — `fix: preserve current task inputs across unrelated plan updates`

依据：持续监工修复授权；[199](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)。

## 现场与责任缺口

TODO 局部 amendment 准入新 package 后，网关 Verify 仍运行、TaskStore 合同/dispatch 未变，但 artifact recover 将旧 package history 一律判成 requires_rebind。evt-cc42179f3d50 报 task_context_stale，尚未运行产品测试。结果提交的 currentness 已按 task generation 选择 package，输入生命周期没有复用该边界。WRC 保留原 attempt，OA 只负责新 TODO 开工审查，RM 未发现导航投影与任务准入规则冲突。

## 验收

1. 当前任务、合同、run、dispatch、运行 operation/attempt 完全匹配时，复用其 generation 已准入包 -> verify: 新无关包发布后 CLI index/recover 保持 bound，原输入正文与摘要不变。
2. 不扩大执行权限 -> verify: 变更合同、run、dispatch、attempt、终止 operation、撤销包、冲突包、未知任务均继续要求 rebind；authorizes_execution 始终 false。
3. 全部 lifecycle 调用点使用同一判断 -> verify: CLI、ArtifactQuery、恢复 context、work-record 调用链回归；影响闭包/sentinel/mock。
4. 合并 dev 并让真实网关续接 -> verify: 保存修复前后事实、真实下一阶段/执行证据；不直接改 canonical 状态或伪造产品验收。

## 软件验证

- 当前 dev 的实际 CLI 回归复现 requires_rebind=true（1 failed）；修复后首批 34 passed。
- 28 文件影响闭包首次 215 passed/1 failed：新增冲突包测试误把 reducer 已拒绝的重复包当作原包撤销；改为验证声称冲突包的 manifest 被拒绝，未修改 reducer 或放宽门禁。最终 216 passed，sentinel 53 passed。
- 真实只读回放：仍在运行的 WORK、LIST Verify 及 OVERVIEW g2 输入为 pinned/current_task_generation_binding；已结算 Gateway/旧 OVERVIEW 仍 requires_rebind。没有复活旧 attempt。记录 LS docs/records/2026-11-07-input-lifecycle-code-replay.json。
- 尚待合并、部署和真实受控 Verify 续接。

## 实机效果验收

dev 7fb2ac08 已加载；后续 dev 0dfc25f6 保留该修复。GOVERNANCE 在原 attempt 中 recover bound 后提交 dev.build.done evt-ff6daa5aa7d7 并自动 Verify；FMS-19 受控恢复 g2 evt-2b2bd1c1ae89 后现场输入为 pinned。Gateway 新 Verify g3 evt-8bc668d2c0c3 已派发，07:05:50 的 ta-4aa01ae3fa5facf8defd 生命周期为 pinned/current_task_generation_binding、requires_rebind=false，原 6a4790 generation 与 4928664 package 保留，已产生真实读取回执。旧失败 attempt 未复活，未重写原输入。

证据：LS-v1 docs/records/2026-11-07-gateway-verify-recovery-effect.json、2026-11-07-verify-retry-reload-after.json 和持续监工问题记录。合并后聚焦 64、sentinel 53、mock 10 通过；源 worktree 已按已合并、无活跃进程及仅缓存/空锁审计后删除。

本任务关闭的是输入恢复/阶段续接的软件义务；不代表网关产品 Verify 已通过，也不代表 RM 已自主证明源码补丁效果。监工提供修复证据后请求受控动作的事实保留。
