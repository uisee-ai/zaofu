> 状态: active

# 产品任务断点修复

用户批准按上一轮分析实施。框架前置已在 4f23798c 修复，见 [实现说明](../../docs/impl/task-recovery-20261105.md)。

- P04 五项 Verify 缺陷 -> verify: 保留原合同命令，独立 Verify passed，候选集成门禁。
- P06 注册 adapter 与输入/版本/回执绑定，缺模型 fail closed -> verify: 两条负例通过，成功路径明确注册测试 adapter；不冒充真实模型验收。
- P18 两项状态修复 -> verify: 暂停待确认失败落库，历史 attempt 完整不变，旧代次发布拒绝，Docker browser 回归。
- P16B 上游合同缺口 -> verify: 受控 gap plan 补私有结果和发布/读守卫，再实现隐私审核与受控导出，撤权回归。

受控重试：P04 evt-7ec18155ba26（唯一额外 g4）；P06 evt-40aa1996aaa2（g3）；P18 evt-9ab57fa85070（g3）。运行 workflow-0ea133e63454375e，保留已接受的 15 个任务；不得修改规范状态 JSON。
