> 状态: active

# 按项目关闭选定资源上限

Owner 要求关闭集成风险审查、Autoresearch、原生子 agent 结果准入以及研究角色的费用和超时限制。
本次提供显式零值关闭语义并应用到 dl，保留其他项目默认值、功能开关、身份/证据/结算校验。
Autoresearch 范围为已列出的触发冷却、每小时/每天次数和每轮动作数量；实验自身声明预算单独保留。
原 Plan Critic 的 usage_samples=60 阻塞不属于本次四类变更，不假称已恢复。

- 配置零值 -> verify: 规范化配置保留 0，不回退默认值或解释为禁止执行。
- 原生结果数量不限 -> verify: 经真实 prepare/Operation/结果准入链允许超过 6 个子项，错误身份、未结算子项仍拒绝；旧请求的默认值保持 6。
- 风险审查零费用/时间 -> verify: 显式全零 Operation limits 不回落到其他 profile，非零额度仍约束。
- Autoresearch 零次数 -> verify: 超过原小时/日/每轮数量仍允许，关闭功能与问题身份约束仍有效。
- 应用 dl -> verify: 精确配置差异、加载验证、重启后的进程来源及配置收据；主 Run blocked 状态真实保留。

验证：70 个直接影响测试文件分四批通过 1,685 项（7 跳过）；Task Pipeline
调用闭包 46 项通过；pre-merge sentinel 51 项、确定性 flow-smoke 10 项通过。
另有首轮 45 项专项测试通过，与上述闭包部分重叠，不累计为独立测试数。
dev-verify 自动规划因共享配置入口扩张至全量；依据 AGENTS.md，执行明确的
模块/调用者/契约闭包替代全量 pytest，未执行真实 Provider 测试。
收据保存在 `/tmp/zf-dl-supervision-20261031/optional-limits-*.json`。
部署验收待应用到 dl 并核对新进程。

部署发现：关闭中的 risk_review 参数变化仍进入 profile_digest，93 个既有任务
因此触发 policy mismatch。只读重构证明，恢复三个历史默认值后的完整摘要
精确等于所有 93 个任务的已准入摘要。增加仅适用于此无执行效果差异的确定性
兼容绑定；保留旧身份，并拒绝启用、容量、默认门禁、fallback 和部分额度修改。
