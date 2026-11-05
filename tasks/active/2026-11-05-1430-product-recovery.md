> 状态: active

# 产品任务断点修复

用户批准按上一轮分析实施。框架前置已在 4f23798c 修复，见 [实现说明](../../docs/impl/task-recovery-20261105.md)。

- P04 五项 Verify 缺陷 -> verify: 保留原合同命令，独立 Verify passed，候选集成门禁。
- P06 注册 adapter 与输入/版本/回执绑定，缺模型 fail closed -> verify: 两条负例通过，成功路径明确注册测试 adapter；不冒充真实模型验收。
- P18 两项状态修复 -> verify: 暂停待确认失败落库，历史 attempt 完整不变，旧代次发布拒绝，Docker browser 回归。
- P16B 上游合同缺口 -> verify: 受控 gap plan 补私有结果和发布/读守卫，再实现隐私审核与受控导出，撤权回归。

受控重试：P04 evt-7ec18155ba26（唯一额外 g4）；P06 evt-40aa1996aaa2（g3）；P18 evt-9ab57fa85070（g3）。运行 workflow-0ea133e63454375e，保留已接受的 15 个任务；不得修改规范状态 JSON。

## 2026-11-05 14:49 UTC 检查点

- P18 已完成：产品 commit `032d1636`（`feat: preserve recovery attempt history across pause failure and cancel`）；独立 Verify 接纳 `evt-0f654e1e331e`，候选集成 `evt-41528ac85fd1`，Task done `evt-c3dd68d798a3`。候选前进到 `fa984630d17145583025bffbeb730165064f02a6`；runtime 42、browser 5，自检与独立多轮历史/旧代次/取消探针均通过，真实模型硬件资格未签收。
- P04 原五项修复已提交 `f73aac3`，runtime 35 和 Docker browser 通过。独立 Verify `evt-b1a91883b7e3` 新发现 review 幂等缺陷：旧请求重放可覆盖新复核。受控批准唯一 g5 `evt-69a88e826e4c`，只补幂等和并发、保持原成果，不重建已完成实现。
- P06 原假成功已修复 `6c10096a`，runtime 23 与 Docker browser 通过。独立 Verify `evt-faff3699ba6f` 新发现无资源 grant 可推理和非法类别 HTTP500；受控唯一 g4 `evt-2027e57bb6ee`，复用 access.authorize 并补接纳/执行/发布边界及精确 4xx 回归。真实模型配置仍缺，不能宣称生产模型资格完成。
- P16B Reader 已产出两个串行任务；`65f1103e` 修正 bridge 历史 ID 污染。第一次受控重放被源分片校验拒绝，第二次将 parent 绑定当前 Plan 节点后，`evt-253ba4414ad1` 应用成功，新 Plan Package `5f47133546cc1dc038f5938c56a0e8f88a342aae4de1b3cc42c8683ec11a0d60` 待批。未批准：独立语义检查发现矩阵正文仍引用旧任务，且旧 P16B/下游依赖尚未对账。需修订完整端口，不能将 task_input_policy 能读等同于 SDD 语义一致。

当前产品记录保持 active；不把已提交、已入包或重试派发计为通过。完整 canonical 证据在产品 `.zf-datal`，不提交运行态到 ZaoFu 源码。
